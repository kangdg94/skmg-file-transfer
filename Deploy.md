# Communicator 배포 (빠른 안내)

명령 두 개로 끝난다.

```bash
./deploy/build-export.sh                        # 로컬
./deploy/cloudshell-push-communicator.sh dev    # AWS CloudShell
```

자세한 배경과 설정 근거는 [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) 에 있다.

---

## 준비물

**로컬에 `docker` 하나만 있으면 된다.** `g++`, JDK, maven, PyTorch, `bzip2` 는 전부
빌더 컨테이너가 들고 있다. `sudo` 도 필요 없다.

x86_64 호스트에서 실행한다. (스크립트가 먼저 확인하고 끊는다)

---

## 1단계 — 로컬에서 빌드

```bash
./deploy/build-export.sh
```

엔진·모델·jar 를 빌더 컨테이너 안에서 새로 만들고, 런타임 이미지를 조립해
`communicator.tar.bz2` (약 130MB) 를 만든다. 7단계가 순서대로 찍힌다.

첫 실행은 빌더 이미지를 만들면서 오래 걸린다(apt + PyTorch 내려받기). 두 번째부터는
docker 캐시가 받아 준다.

> **중간에 멈추면 그대로 멈춘 것이 맞다.** 특히 `[5/7]` 의
> `산출물이 이번 빌드에서 갱신되지 않았습니다` 는 무시하면 안 된다. 코드는 새것인데
> 바이너리는 옛것인 채로 배포되는 사고를 막는 검사다. 빌드 로그를 확인한다.

## 2단계 — CloudShell로 전송

AWS 콘솔 상단 **CloudShell** → 우측 **작업** → **업로드** 로 `communicator.tar.bz2` 를 올린다.

푸시 스크립트도 CloudShell에 있어야 한다. 둘 중 하나로 맞춘다.

```bash
# (a) 저장소를 clone 하고, 아카이브를 저장소 루트에 올린 경우
./deploy/cloudshell-push-communicator.sh dev

# (b) 아카이브와 스크립트만 같은 디렉터리에 올린 경우
./cloudshell-push-communicator.sh dev
```

스크립트는 `communicator.tar.bz2` 를 **현재 디렉터리에서** 찾는다. 스크립트가 있는
위치가 아니다.

## 3단계 — 푸시하고 배포

```bash
./deploy/cloudshell-push-communicator.sh dev     # dev | stg | prd
```

ECR에 푸시하고 ECS 서비스를 강제로 새로 배포한다. 환경에 따라 AWS 계정이 자동으로
갈린다.

마지막에 이렇게 나오면 배포가 걸린 것이다.

```
Done. Deployment triggered with task definition: arn:aws:ecs:...:tdef:N
```

## 4단계 — 확인

`<ENDPOINT>` 를 실제 주소로 바꿔 두 번 호출한다.

```bash
# 엔진 준비 상태 — ready: true 면 정상
curl -s http://<ENDPOINT>/v1/communicator/ready

# 실제 추론 — decision.type 이 execute 면 정상
curl -s --max-time 30 -X POST http://<ENDPOINT>/v1/communicator/utterances \
  -H 'Content-Type: application/json' \
  -d '{"text":"하이나무 거실로 이동해",
       "space_personalization":{"parent_entity":"<SPACE>",
         "child_entity_list":[{"child_entity":"<SPACE_SPACE1>","aliases":["거실"]}]}}'
```

`--max-time` 은 30초 이상으로 둔다. 서버 엔진 상한이 15초라 그보다 짧으면 서버 응답을
받기 전에 curl 이 먼저 끊는다.

ECS 롤링 배포가 끝나기까지 1~2분 걸린다. 바로 호출하면 예전 태스크가 받을 수 있다.

---

## 첫 배포에만 추가로 필요한 것

스크립트는 아래 셋을 **만들지 않는다.** 없으면 그 단계에서 멈춘다.

| 없는 것 | 멈추는 곳 |
|---|---|
| ECR 리포지토리 `skmg/communicator` | `[5/8]` push |
| task definition `skix-communicator-<ENV>-tdef` | `[6/8]` `no active task definition found` |
| ECS 서비스 `skix-communicator-svc` | `[8/8]` update-service |

task definition 은 바로 등록할 수 있다.

```bash
aws ecs register-task-definition --region ap-northeast-2 \
  --cli-input-json file://deploy/task-definition-dev.json
```

ECR 리포지토리와 ECS 서비스는 콘솔(또는 IaC)에서 먼저 만들어야 한다.

> `[7/8]` 에서 `initProcessEnabled` 오류로 멈추면, 이미지는 이미 ECR 에 올라가 있다.
> task definition 을 등록한 뒤 **재빌드 없이 이 스크립트만 다시 돌리면 된다.**

---

## 자주 막히는 곳

| 증상 | 조치 |
|---|---|
| `[1/7]` docker 접근 불가 | 데몬이 떴는지, 사용자가 `docker` 그룹에 있는지 본다 |
| `[5/7]` 갱신되지 않았습니다 | 앞 단계 빌드가 조용히 실패했다. `[3/7]`·`[4/7]` 로그를 본다 |
| `[6/7]` `ldd` 또는 smoke test 실패 | 엔진이 실제로 깨졌다. `./command/build-engine.sh --test` 로 확인한다 |
| 배포 후 `ready: false` | 모델이 이미지에 덜 들어갔다. `[5/7]` 에서 11개가 모두 `ok` 였는지 본다 |
| 한동안 잘 되다 느려지며 죽는다 | `cca_client` 좀비 누적. task definition 의 `initProcessEnabled` 를 확인한다 |

로그는 CloudWatch `/ecs/skix-communicator-<ENV>` 에서 본다.

---

## 참고

| | |
|---|---|
| 리전 | `ap-northeast-2` |
| ECR | `skmg/communicator` (태그 `latest`) |
| 클러스터 | `skmg-airbot-<ENV>-ecs-cluster` |
| 서비스 | `skix-communicator-svc` |
| 계정 | dev `010928196421` · stg `087432099373` · prd `779846811758` |

HTTP API 스키마(요청·응답 필드, 오류 코드)는 별도 문서가 없다. 계약이 담긴 곳은
`src/server/src/main/java/com/communicator/agent/api/dto/` 의 record 들과,
입력 규칙을 담은 `engine/EngineInputValidator.java` 다.
와이어 포맷은 snake_case 이고, null 필드도 응답에 그대로 내려온다.

엔진 자체의 텍스트 입출력 규약은 [../docs/ENGINE_INPUT_OUTPUT_SCHEMA.md](../docs/ENGINE_INPUT_OUTPUT_SCHEMA.md) 에 있다.
