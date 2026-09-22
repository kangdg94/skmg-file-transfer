# [공통] 환경·AWS 계정·배포 절차

| 항목 | 내용 |
|---|---|
| **문서 성격** | 나무엑스(namuhx) 백엔드 9개 저장소 공통 — 개별 작업 문서가 아니라 인프라·배포 지식 |
| **대상 저장소** | `backend-api-main`, `skix-security`, `skix-medcare`, `skix-openapi`, `skix-streaming`, `skix-streaming-signal`, `backend-scheduler-main`, `benjamin-lambda`, `backend-map-converter`, `db-schema` |
| **다루는 범위** | 환경 3종·브랜치 규칙, AWS 계정 구조, 저장소별 배포 형태·절차, 반복되는 배포 함정, VDI 빌드 도구, 환경별 공개 호스트 |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (로컬 git 원격 실측 기준. ECS/AWS 콘솔 상태는 로컬에서 볼 수 없어 별도 표기) |
| **인수자가 첫날 할 일** | §2-2(브랜치 규칙 예외 2건)와 §5(배포 함정 6가지)를 먼저 읽고, 실제 배포를 하기 전에 §7 호스트 표로 담당 서비스가 어떤 경로로 노출되는지 확인할 것 |

---

## 0. 세 줄 요약

1. 환경은 dev(개발계)·stg(검증계)·prd(운영계) 3종이고 브랜치 승격 방향은 `dev → stg → main`이 기본이지만, 저장소마다 기본 브랜치명·예외가 달라 이름만 보고 넘겨짚으면 틀린다.
2. AWS는 SSO가 아니라 공유 계정 로그인 후 환경별 계정으로 역할 전환(AssumeRole)하는 구조이고, 콘솔·CLI 모두 회사 VDI에서만 동작한다 — 로컬 맥은 배포 상태를 확인할 방법이 없다.
3. 배포 형태가 저장소마다 다르다(ECS Fargate 3개·EC2 단일 jar 3개·EC2 수동 2개·Lambda 1개). 가장 흔한 사고는 ECS 배포 스크립트가 태스크 정의를 등록하지 않고 기존 리비전을 재사용하는 것과, `backend-api-main`에서 SSM 값이 yml보다 우선순위가 낮아 조용히 무시되는 것이다.

---

## 1. 용어

| 용어 | 뜻 |
|---|---|
| **dev / stg / prd** | 개발계 / 검증계(스테이징) / 운영계. 계정·인프라가 완전히 분리된 3개 환경 |
| **ECS Fargate** | 서버리스 컨테이너 실행 환경. 이 조직에서는 `skix-security`·`skix-medcare`·`skix-openapi` 3개가 여기서 돈다 |
| **태스크 정의(task definition)** | ECS가 컨테이너를 어떻게 띄울지 정의한 JSON(이미지·환경변수·시크릿·리소스). 저장소의 `deploy/task-definition-{env}.json`이 원본이지만, **등록(register)하지 않으면 AWS에 반영되지 않는다** — §5-1 참고 |
| **리비전(revision)** | 태스크 정의를 `register-task-definition`으로 등록할 때마다 매겨지는 버전 번호. ECS 서비스는 특정 리비전을 가리키며 갱신하려면 새 리비전을 만들어야 한다 |
| **SSM Parameter Store** | AWS의 키-값 설정 저장소. 이 조직은 시크릿(DB 비밀번호, API 키 등)을 여기 둔다 |
| **AssumeRole(역할 전환)** | 한 AWS 계정에 로그인한 뒤 다른 계정의 역할을 임시로 빌려 쓰는 방식. SSO 없이 계정을 넘나드는 방법이다 |
| **VDI** | 회사 가상 데스크톱. AWS 콘솔·CLI 접근과 `backend-api-main`의 사내망 빌드가 여기서만 된다 |
| **NLB / ALB / API Gateway** | NLB(네트워크 로드밸런서)는 내부 서비스 간 호출에, API Gateway는 앱이 외부에서 들어오는 요청의 인증(Cognito)·라우팅에 쓰인다. Nginx는 `report-*`·`client*` 호스트에서 API Gateway를 거치지 않고 직접 프록시하는 경로에 쓰인다 |
| **CloudShell** | AWS 콘솔에서 바로 쓰는 브라우저 셸. 이 조직은 로컬에서 만든 이미지를 CloudShell에 업로드해 ECR에 푸시하는 방식으로 배포한다(로컬에서 직접 ECR에 접근할 수 없기 때문) |
| **PLATFORM_API_URL / NAMUHX_INTERNAL_URL** | 다른 서비스가 `backend-api-main`을 가리킬 때 쓰는 환경변수 이름. 저장소마다 이름이 다르다(security·medcare는 `PLATFORM_API_URL`, openapi는 `NAMUHX_INTERNAL_URL`) |

---

## 2. 환경 3종과 브랜치 규칙

### 2-1. 환경 3종

| 환경 | AWS 계정 ID | 별칭 |
|---|---|---|
| dev(개발계) | `010928196421` | SKIX_namuhx_dev |
| stg(검증계) | `087432099373` | — |
| prd(운영계) | `779846811758` | SKIX_namuhx_prd |

계정 ID는 ECS 태스크 정의(`executionRoleArn`)·CloudShell 스크립트(`cloudshell-push.sh`의 `ACCOUNT_MAP`)에서 직접 확인했다. 공유(로그인) 계정은 별도로 `010382790647`(별칭 skmg-shared)이며, 이 계정에는 IAM 사용자만 있고 애플리케이션 인프라는 없다.

### 2-2. 저장소별 브랜치 규칙

기본 흐름은 `dev → stg → main`이지만, **저장소마다 기본 브랜치명과 승격 상태가 다르다.** `git branch -r` 실측(2026-09-22) 기준.

| 저장소 | 원격 기본(HEAD) | 운영 브랜치 | dev 최종 커밋일 | stg 최종 커밋일 | main 최종 커밋일 |
|---|---|---|---|---|---|
| `backend-api-main` | `main` | `main` | 09-18 | 09-18 | 09-01 |
| `skix-security` | `main` | `main` | 09-14 | 09-14 | 08-31 |
| `skix-medcare` | `main` | `main` | 09-04 | 09-04 | 08-24 |
| `skix-openapi` | `main` | `main` | 09-04 | 09-04 | 08-20 |
| `backend-scheduler-main` | `main` | `main` | 08-25 | 08-26 | 08-26(=stg) |
| `benjamin-lambda` | `main` | `main` | 07-20 | 07-20 | 08-06 |
| `backend-map-converter` | `main` | `main` | 07-10 | 07-10 | 07-10(dev=stg=main) |
| `skix-streaming` | **`master`** | **`main`** | 08-24 | 08-24 | 06-11 |
| `skix-streaming-signal` | **`master`** | **`main`** | 08-24 | 08-24 | 05-26 |
| `db-schema` | `main` | `main`(단일 브랜치) | – | – | 09-10 |

**예외 1 — `skix-streaming`·`skix-streaming-signal`은 `git clone` 시 기본으로 체크아웃되는 브랜치(`master`)와 실제 운영 브랜치(`main`)가 다르다.** `master`는 각각 2026-05-28·2026-03-31에 멈춘 옛 기본 브랜치이고(`main`이 만들어지기 전 흔적), 지금 승격 대상은 `main`이다. `master`로 작업하면 몇 달 전 코드 위에서 일하게 된다 — 이 저장소를 clone한 뒤에는 반드시 `git checkout main`부터 할 것.

**예외 2 — `db-schema`는 dev/stg/main 브랜치 승격 개념이 아니다.** 저장소에 브랜치가 `main` 하나뿐이고, 그 안에 `dev.sql`·`stg.sql`·`prod.sql` 세 파일이 환경별 DDL을 각각 담고 있다. "이 저장소도 dev→stg→main으로 승격해야 하나"라고 생각하면 안 된다 — 세 파일을 각 환경 DB에 직접 실행하는 방식이고, 실행은 수동이다(마이그레이션 툴 없음).

**그 외 참고할 점**
- `skix-medcare`·`skix-openapi`에는 `develop`이라는 더 오래된 레거시 브랜치가 남아 있다(각각 2026-07-02, 2026-07-09 마지막 커밋). `dev`와 이름이 비슷해 혼동하기 쉬우니 작업은 반드시 `dev`에서 시작한다.
- `backend-scheduler-main`은 지금 시점 stg와 main이 완전히 같은 커밋이다(`a800341`). `backend-map-converter`는 dev·stg·main 세 브랜치가 전부 같은 커밋이다 — 즉 이 저장소는 현재 승격 대기 중인 변경이 없다는 뜻이지, 배포 파이프라인이 다르다는 뜻이 아니다.
- **각 저장소의 main 최종 커밋일이 dev보다 몇 주~몇 달 뒤처져 있는 것이 일반적이다.** dev·stg에서 검증을 끝낸 변경이 main(운영)에는 아직 승격되지 않은 상태가 상시로 존재한다. main으로 승격하기 전에는 반드시 그 사이 운영에서 의도적으로 되돌린 변경이 없는지 확인한다 — `backend-api-main`은 2026-08-27에 핫픽스 일부가 revert되어 main 트리가 2026-08-06 시점과 동일해진 이력이 있고, 그 revert가 걷어낸 기능(OASYS B015 계약 동기화 관련 파일 2개)이 dev에는 그대로 있다. dev를 그대로 stg/main에 밀어 넣으면 운영에서 의도적으로 뺀 기능이 되살아난다.

---

## 3. AWS 계정 구조

### 3-1. 로그인 방식 — SSO 없음, 공유 계정 → 역할 전환

이 조직은 AWS SSO(Identity Center)를 쓰지 않는다. 구조는 다음과 같다.

```
[개인 IAM 사용자, 공유 계정 010382790647]
        │  콘솔/CLI 로그인
        ▼
[역할 전환(AssumeRole)]
        │
        ├─▶ 개발계 계정 010928196421 의 역할
        ├─▶ 검증계 계정 087432099373 의 역할
        └─▶ 운영계 계정 779846811758 의 역할
```

콘솔에서는 로그인 후 "역할 전환" 메뉴로 환경을 바꾸고, 라벨은 "벤자민 개발계"/"벤자민 검증계"/"벤자민 운영계"로 표시된다(2026-08-11 스크린샷 확인, 정확한 역할 ARN까지는 그 자리에서 확인하지 못했다). ECS 태스크 실행 역할의 실제 이름 패턴은 태스크 정의에서 확인된다 — `arn:aws:iam::<계정ID>:role/skmg-airbot-<env>-role-ecs-task-execution`, `...-role-ecs-task`, KVS용 `...-role-ecs-kvs-master-role`/`...-viewer-role` 등 전부 `skmg-airbot-<env>-role-*` 패턴을 따른다.

로컬 CLI에서 `source_profile`(공유 계정 키) + `role_arn`(환경 계정 역할)으로 프로필을 구성하면 임시 자격증명을 받을 수 있는 구조 자체는 있지만(관련 `sts` 의존성이 `backend-api-main`에 추가돼 있다), **아래 3-2 제약 때문에 실제로는 거의 의미가 없다.**

### 3-2. 콘솔·CLI는 회사 VDI에서만 — 로컬 맥은 API 호스트만 도달

**AWS 콘솔은 회사 VDI에서만 로그인된다.** 로컬 브라우저에서는 로그인 페이지 자체는 열리지만 같은 자격증명으로 로그인에 실패한다(2026-08-11 실측). 소스 IP 기준 제한(SCP 또는 `aws:SourceIp` 조건)으로 추정되며, 정확한 제약 조건은 VDI에서 IAM 정책을 열어봐야 확인된다(미확인).

실무적 결론:
- **로컬 맥에서 할 수 있는 것**: 코드 작성·git 작업·`curl`로 공개 API 호스트(`app-api-*.namuhx.com` 등) 호출. 이 문서의 모든 git 실측·코드 실측도 로컬에서 한 것이다.
- **로컬 맥에서 할 수 없는 것**: AWS 콘솔 열람, `aws` CLI(ECS/SSM/RDS 등) 실행, DB 직결, ECS 태스크 정의 리비전·서비스 상태 확인. 전부 VDI가 필요하다.
- 따라서 이 문서에서 "배포됐다"고 쓴 항목은 전부 **CloudShell/VDI 콘솔에서 실행한 사람이 남긴 기록**(커밋 메시지, 메모리)에 의존한 것이고, ECS 서비스가 실제로 그 리비전을 running 상태로 물고 있는지는 로컬에서 재확인할 수 없다. 인수 후 첫 주에 VDI에서 각 서비스의 `aws ecs describe-services`로 현재 리비전과 이미지 태그를 한 번 대조해 보는 것을 권한다.

---

## 4. 저장소별 배포 형태와 절차

| 저장소 | 배포 형태 | 빌드 산출물 | 배포 스크립트 위치 |
|---|---|---|---|
| `skix-security` | ECS Fargate | Docker 이미지 | 저장소 `deploy/` |
| `skix-medcare` | ECS Fargate | Docker 이미지(2스테이지: 운영/디버그) | 저장소 `deploy/` |
| `skix-openapi` | ECS Fargate | Docker 이미지(기본 + Datadog 변형) | 저장소 `deploy/` |
| `backend-api-main` | **EC2 단일 jar** | Boot jar | **저장소에 없음** — 서버 박스에 `air-bot-api.sh`로 존재(§4-2) |
| `backend-scheduler-main` | EC2 단일 jar(배치, cron 기반 내부 스케줄러) | Boot jar | 저장소에 없음(추정 — 근거는 §4-2) |
| `backend-map-converter` | EC2 단일 jar(Maven) | jar | 저장소에 없음(추정) |
| `skix-streaming` | EC2 수동(`nohup java -jar`) | Boot jar | 없음. README의 수동 절차만 |
| `skix-streaming-signal` | EC2 수동(`nohup node`, PM2/systemd 없음) | 소스 그대로 | 없음. README의 수동 절차만 |
| `benjamin-lambda` | Lambda(3개 함수) | zip | 저장소 `deploy.sh` |

### 4-1. ECS Fargate 3종 — `skix-security` / `skix-medcare` / `skix-openapi`

세 저장소 모두 같은 패턴이다. 저장소 안에 `Dockerfile`, `deploy/build-export.sh`, `deploy/cloudshell-push.sh`, `deploy/task-definition-{dev,stg,prd}.json`이 있다.

**절차**
```
1. 로컬(또는 VDI)에서 ./deploy/build-export.sh 실행
   → Docker 이미지를 빌드해 <서비스명>.tar.bz2 로 저장
2. 그 tar.bz2 를 AWS CloudShell 에 업로드
3. CloudShell 에서 ./deploy/cloudshell-push.sh [dev|stg|prd] 실행
   → 압축 해제 → docker load → ECR 태그 → ECR push
   → describe-task-definition 으로 "현재 활성 리비전"을 조회
   → update-service --force-new-deployment 로 그 리비전으로 롤링 배포
4. (환경변수를 새로 추가했다면 3번 전에 반드시 태스크 정의를 새로 등록 — §5-1)
```

- ECS 클러스터 이름은 세 저장소 모두 `skmg-airbot-<env>-ecs-cluster`로 하드코딩돼 있다(스크립트에서 확인). **stg/prd에서도 클러스터 이름은 "airbot"을 그대로 쓴다** — §5-5 참고.
- ECS 서비스 이름 패턴은 저장소마다 다르다: security는 `skmg-airbot-<env>-security-svc`, medcare는 `skmg-airbot-<env>-medcare-svc`, **openapi만 `skix-openapi-<env>-svc`**(접두사가 다르다).
- `skix-openapi`는 **stg에서만 Datadog 에이전트를 사이드카로 붙인 이미지**(`Dockerfile_Datadog`)를 쓴다. `./deploy/build-export.sh dd`로 빌드해야 하고, 산출물 이름도 `openapi-dd.tar.bz2`로 다르다. `cloudshell-push.sh`는 `ENV=stg`일 때 자동으로 `-dd` 파일명을 찾도록 짜여 있다.
- 태스크 정의는 저장소마다 dev/stg/prd account ID·Redis·NLB 호스트·Cognito 정보가 전부 다르게 박혀 있다(§7 참고). **`environment`에는 평문 설정값, `secrets`에는 SSM `valueFrom` ARN만 들어간다** — 시크릿은 태스크 정의 JSON 자체에는 값이 없고 파라미터 경로만 있다.

### 4-2. EC2 단일 jar — `backend-api-main` (그리고 추정상 `backend-scheduler-main`, `backend-map-converter`)

`backend-api-main`은 저장소 전체를 뒤져도 `Dockerfile`·`deploy/`·`.gitlab-ci.yml`이 **하나도 없다.** `application-{dev,stg,prd}.yml`을 보면 로그 파일 경로가 `/opt/www/airbot/back-end/logs/app.log`, 업로드 임시 경로가 `/opt/www/airbot/tmp`, 포트가 `8082`로 고정돼 있는 등 컨테이너가 아니라 **EC2 위에서 직접 도는 프로세스**라는 흔적이 뚜렷하다. 실제 배포는 이렇다.

```
1. 대상 브랜치 머지·push (dev → stg → main)
2. VDI에서 ./gradlew clean bootJar (또는 §6의 VDI 빌드 도구로 빌드)
3. 만들어진 jar 를 대상 EC2 로 전달
4. EC2 에서 ./air-bot-api.sh start {dev|stg|prd}
```

`air-bot-api.sh`는 **저장소에서 이미 제거되어 있고 EC2 서버 박스에만 존재한다.** 즉 이 배포 스크립트 자체는 형상관리 밖에 있다 — 인수자가 처음 EC2에 접속하면 스크립트 내용을 그 자리에서 읽어야 한다. 프로필(`dev`/`stg`/`prd`)은 **jar 실행 시점에 인자로 결정**되고, 그 프로필이 활성화되면 `application-<profile>.yml`의 `spring.config.import: aws-parameterstore:/backend-api-main/<profile>/`가 해당 환경의 SSM 파라미터를 읽어온다. EC2 인스턴스에 붙은 IAM 인스턴스 프로파일이 그 SSM 경로에 대한 읽기 권한을 가지고 있어야 기동이 된다.

**`backend-scheduler-main`과 `backend-map-converter`는 저장소 안에서 배포 스크립트를 찾지 못했다.** 다만 정황은 `backend-api-main`과 같은 방향을 가리킨다.
- `backend-scheduler-main`: `README.md`가 `./gradlew clean bootjar -Pprofile=local -Pversion={VERSION}` → `nohup java -jar air-bot-batch.jar`를 명시한다. 프로필 이름 체계가 특이하다 — `dev`가 아니라 **`local`/`vdi`/`stag`/`prod`**이고, 소스 폴더 이름도 `src/main/resources-{profile}`로 나뉜다(`-Pprofile` gradle 프로퍼티로 컴파일 시점에 리소스 폴더가 정해지는 구조라, ECS처럼 런타임에 프로필을 바꿔 끼울 수 없다 — **환경별로 다시 빌드해야 한다**). 여기서 `vdi`가 사실상 개발계다.
- `backend-map-converter`: Maven 프로젝트(`pom.xml`, `mvnw`)이고 `application.yml`에 `localhost`/`dev`/`stg`/`prd` 4개 프로필이 한 파일에 `---`로 구분돼 있다. 포트 `28089`, 로그 경로 `/log_data/...`, 리소스 경로 `/app/refiner/...` 등 EC2 프로세스로 도는 흔적이 있고, `backend-api-main`의 `map.convert.url`이 사설 IP(`10.119.118.36:28089`)를 직접 가리키는 것도 같은 결론을 뒷받침한다.
- 이 두 저장소의 정확한 배포 명령·서버 위치는 **미확인**이다. 확인하려면 VDI에서 담당 EC2 인스턴스에 접속해 실행 중인 프로세스와 배치 스크립트(있다면 crontab, systemd unit, 또는 홈 디렉터리의 셸 스크립트)를 봐야 한다.

### 4-3. EC2 수동 — `skix-streaming` / `skix-streaming-signal`

이 둘은 ECS가 아니라 **한 EC2 인스턴스 위에서 여러 프로세스가 함께 돈다**(streaming·signal·그리고 저장소가 없는 `skix-streaming-kms`까지 3개). CI/CD가 전혀 없다.

- `skix-streaming`: `git pull origin master`(또는 사용 중인 브랜치) → `./gradlew clean bootjar -Pprofile={env}` → `nohup java -jar streaming-0.0.1-SNAPSHOT.jar &`. **주의 — 이 저장소는 빌드 시점에 프로필이 정해진다.** `dev` 프로필로 빌드한 jar를 운영 박스에 올리면 운영 트래픽이 개발 설정(DB, IoT 엔드포인트 등)으로 뜬다. README는 `master` pull을 예시로 들지만 실제 승격 대상 브랜치는 §2-2의 `main`이다.
- `skix-streaming-signal`: Node.js(Socket.IO) 서버. `.env` 파일을 직접 서버에 두고 `cd signaling && nohup node src/server.js > app.log 2>&1 < /dev/null & disown`으로 띄운다. 배포 시 코드 3개 파일만 교체하는 방식이 쓰인 적이 있고(과거 기록), 그때 서버의 `.env`는 덮지 않는다 — 새 환경변수가 필요하면 `.env`를 직접 고쳐야 한다.
- 세 프로세스(streaming·signal·kms) 전부 **systemd·pm2·cron·rc.local이 없고 `nohup`으로 떠 PPID=1이다.** 즉 EC2가 재부팅되면 아무것도 자동으로 살아나지 않는다. 인수자가 이 서비스를 맡는다면 이 부분부터 정리하는 것이 첫 우선순위 후보다.

### 4-4. Lambda — `benjamin-lambda`

Redis/RDS/Router 세 개의 Lambda 함수. 저장소 루트의 `deploy.sh`가 하는 일은 이것뿐이다.

```bash
./deploy.sh
# → RDS/Redis/Router 세 폴더에서 npm ci --omit=dev
# → 각각 zip (*.mjs, package.json, node_modules)
# → 세 zip을 lambda-zips.tar.bz2 하나로 묶음 (CloudShell 업로드 편의용)
```

**환경 구성(트리거, 환경변수, 메모리/타임아웃 등)은 전부 AWS Lambda 콘솔에서 직접 관리되고 저장소에는 없다.** 즉 dev/stg/prd 세 환경에 대응하는 Lambda 함수 3벌(또는 9개 함수)이 콘솔에만 존재하며, 이 저장소의 `dev`/`stg`/`main` 브랜치 구분은 순수하게 코드 승격 단계일 뿐 배포 대상 함수를 결정하지 않는다 — **어떤 zip을 어떤 함수에 올릴지는 사람이 CloudShell/콘솔에서 직접 선택**해야 하므로 실수로 dev 코드를 prd 함수에 올릴 위험이 있다. 배포 전 함수 이름과 환경을 반드시 육안으로 재확인한다.

---

## 5. 반복되는 배포 함정

### 5-1. ECS 배포 스크립트는 태스크 정의를 등록하지 않는다

`skix-security`·`skix-medcare`·`skix-openapi`의 `cloudshell-push.sh`는 전부 이 순서로 동작한다.

```bash
TASK_DEFINITION_ARN="$(aws ecs describe-task-definition --task-definition "${TASK_FAMILY}" ...)"
aws ecs update-service --task-definition "${TASK_DEFINITION_ARN}" --force-new-deployment
```

`describe-task-definition`은 **그 패밀리의 현재 활성(latest active) 리비전을 조회할 뿐, 저장소의 `task-definition-{env}.json` 파일 내용으로 새 리비전을 만들지 않는다.** 즉 저장소의 JSON 파일을 아무리 고쳐도, 그 파일로 `register-task-definition`을 직접 실행하지 않으면 배포는 **예전 리비전 그대로** 다시 뜬다.

**환경변수를 새로 추가했을 때 실제로 벌어졌던 일**(2026-09-04, skix-openapi 파트너 API 작업): 새 플래그가 반영되지 않아 요청이 실패했는데, 에러가 "설정 누락"이 아니라 `404`가 `INVALID_REQUEST`로 나가는 형태였다. 원인 진단에 시간이 두 배로 걸렸다 — **증상이 항상 명확한 설정 오류로 보이지 않는다.**

**올바른 순서**
```
1. deploy/task-definition-{env}.json 을 원하는 내용으로 수정(또는 리뷰)
2. (새 시크릿이 있다면 먼저 SSM 에 파라미터 등록 — §5-2)
3. VDI/CloudShell 에서 그 파일로 register-task-definition 실행
   aws ecs register-task-definition --cli-input-json file://task-definition-{env}.json
4. 그 다음에 cloudshell-push.sh [dev|stg|prd] 실행 (이제 방금 등록한 리비전을 물게 된다)
```
**파일 수정이 없어도** — 예를 들어 이미지만 새로 빌드해서 올리는 일반적인 배포라도 — 리비전 자체를 새로 등록해야 하는 경우가 있다(2026-09-03 검증계 배포 라운드에서는 "파일 수정 없이 현재 파일 그대로 재등록"이 필요했다). **"코드만 바꿨으니 태스크 정의는 그대로 두면 된다"는 판단이 틀릴 수 있다** — 새 이미지를 pull 하려면 어차피 서비스가 새 배포를 트리거해야 하고, 리비전이 그대로면 이미지 태그도 `:latest`로 고정돼 있어 실제로는 `force-new-deployment`만으로도 최신 이미지를 다시 받아오긴 하지만, **환경변수·시크릿·CPU/메모리 등 리비전에 박힌 값이 바뀌는 배포라면 반드시 리비전을 새로 등록해야 한다.**

### 5-2. `backend-api-main`은 SSM 값이 yml보다 우선순위가 낮다

ECS 3종(security/medcare/openapi)은 환경변수·시크릿이 **태스크 정의에 직접** 들어가므로 이 문제가 없다. 하지만 `backend-api-main`은 다르다 — `application-{env}.yml`에 `spring.config.import: aws-parameterstore:/backend-api-main/<env>/`가 있어서, EC2에서 기동될 때 그 SSM 경로 아래 파라미터들을 통째로 읽어와 설정에 병합한다.

**함정**: Spring Boot에서 `spring.config.import`로 들어오는 설정은 **먼저 로드된 프로필 yml보다 우선순위가 낮다.** 즉 같은 키가 `application.yml`(또는 프로필 yml)에도 있고 SSM에도 있으면, **SSM 값이 조용히 무시되고 yml 값이 이긴다** — 에러도 로그도 없다. 저장소에 실제로 남아 있는 사고 기록(`app.internal.enforce-api-key` 주석): 이 값을 SSM으로 옮기려다가 yml의 `false` 기본값에 계속 덮여서 한참 헤맸다.

**적용 규칙**: `backend-api-main`에 새 설정값을 추가할 때는
- 시크릿(비밀번호, API 키)만 SSM에 둔다. yml에는 **그 키를 아예 적지 않는다**(빈 기본값도 적지 않는다).
- 시크릿이 아닌 값(호스트명, URL, 플래그 등)은 SSM에 넣지 말고 프로필 yml에 리터럴로 둔다.
- 이미 yml에 있는 키를 SSM으로 "옮기고 싶다"면, yml에서 그 키를 완전히 지워야 한다 — 값만 지우고 키를 빈 문자열/기본값으로 남기면 그 기본값이 SSM을 덮는다.

### 5-3. 환경변수 추가 시 순서 — SSM 등록 → 태스크 정의(또는 EC2 SSM 경로) 등록

새 시크릿이 필요한 변경을 배포할 때는 순서가 고정이다.

```
1. 대상 환경(dev/stg/prd)의 SSM Parameter Store 에 값 등록
   (ECS 3종: /skix-{security,medcare,openapi}/<env>/<KEY>
    backend-api-main: /backend-api-main/<env>/<KEY>)
2. ECS 3종이면 그 파라미터를 가리키는 secrets 항목을 task-definition-{env}.json 에 추가하고
   §5-1 순서대로 리비전을 새로 등록
   backend-api-main 이면 §5-2 규칙대로 yml 에는 손대지 않고 재기동만 하면 된다
3. 배포
```
2번을 건너뛰고 1번만 하면(SSM에는 있는데 태스크 정의에 `secrets` 항목이 없으면) 컨테이너에 환경변수 자체가 주입되지 않는다 — 이 경우도 "설정 오류"가 아니라 `NullPointerException` 계열의 기동 실패나 조용한 기본값 사용으로 나타날 수 있다.

### 5-4. DDL 선행 원칙

이 조직의 반복 원칙: **스키마 변경이 필요한 배포는 DDL을 먼저 적용하고, 그 다음에 애플리케이션을 배포한다.** `db-schema` 저장소의 `dev.sql`/`stg.sql`/`prod.sql`을 대상 환경 DB에 먼저 실행한 뒤에 앱을 올린다. 반대로 하면(앱을 먼저 올리면) 새 컬럼·테이블을 참조하는 코드가 즉시 SQL 예외로 죽는다.

특히 주의할 상황 두 가지.
- **개발계는 플래그가 커밋 시점에 이미 켜져 있는 경우가 있다.** 예를 들어 큐 발행 플래그가 `local`/`stg`/`prd`는 `false`인데 `dev`(코드상 프로필명 `vdi`인 저장소도 있음)만 `true`로 커밋돼 있으면, "배포 후에 나중에 켠다"는 선택지가 없다 — **배포 = 즉시 활성화**이므로 DDL이 반드시 배포보다 먼저 끝나 있어야 한다.
- **환경 간 스키마 drift가 실재한다.** 같은 `db-schema` 파일이 세 환경 모두 같은 정의(`DEFAULT NULL` 등)를 담고 있어도, 실제 운영 중인 DB는 과거에 수동으로 컬럼 제약을 바꾼 이력 때문에 환경마다 다를 수 있다(실측 사례: `history_control.contract_no`가 파일상으로는 세 환경 모두 nullable인데 실제로는 dev만 `NOT NULL`이었다). **같은 코드가 dev에서는 예외로 터지고 stg/prd에서는 조용히 NULL을 기록하는 식으로 환경마다 다르게 반응할 수 있다.** DDL을 적용하기 전에 대상 환경에서 직접 `SHOW CREATE TABLE`로 실제 제약을 확인하는 습관이 필요하다. 로컬 맥은 DB에 직결할 수 없으므로 이 확인도 VDI에서 한다.

### 5-5. 환경별 인프라 명명이 일관되지 않는다

이 프로젝트는 원래 "airbot"이라는 이름으로 시작했다가 도중에 "benjamin"으로 개명한 흔적이 인프라 리소스 이름에 그대로 남아 있다. VDI 콘솔에서 리소스를 찾을 때 이름 규칙을 잘못 짐작하면 못 찾는다.

| 리소스 | dev | stg / prd |
|---|---|---|
| ECS 클러스터 | `skmg-airbot-dev-ecs-cluster` | `skmg-airbot-stg-ecs-cluster` / `skmg-airbot-prd-ecs-cluster` (**stg/prd도 "airbot"**) |
| 내부 로드밸런서(NLB) | `skmg-airbot-nlb-dev-*`(서비스별 포트 8080/8081/8082/8084/4000 분리) | `skmg-benjamin-{stg,prd}-was-nlb-*`(포트 80 하나로 통합 — 정확한 라우팅 방식은 미확인, VDI에서 대상 그룹 확인 필요) |
| ElastiCache(Redis) | `skmg-airbot-d-redis-cluster-*` | `skmg-benjamin-{stg,prd}-redis-*` |
| Aurora(RDS) | `skmg-benjamin-dev-aurora`(**dev인데 이미 "benjamin"**) | `skmg-benjamin-{stg,prd}-aurora` |
| ECS 서비스명 접두사 | security/medcare: `skmg-airbot-`, openapi: `skix-openapi-` | 동일(환경 무관하게 저장소별로 고정) |

dev의 Aurora 클러스터(`skmg-benjamin-dev-aurora`)는 `backend-api-main`(`airbotdev` 스키마)과 다른 서비스의 dev DB가 함께 올라간 **공유 클러스터**다. RDS 파라미터 그룹처럼 클러스터 전체에 영향을 주는 설정은 절대 손대지 말 것 — 다른 서비스와 운영자 조회 값까지 함께 움직인다.

### 5-6. 그 밖에 값을 잘못 옮기기 쉬운 지점

- **내부 URL에 컨텍스트 패스(`/api`)를 포함하는지가 저장소마다 다르다.** `backend-api-main`을 가리키는 내부 환경변수 중 `skix-medcare`의 `PLATFORM_API_URL`은 `/api`를 포함하고, `skix-security`의 `PLATFORM_API_URL`과 `skix-openapi`의 `NAMUHX_INTERNAL_URL`은 포함하지 않는다(각 저장소 코드가 호출 시 직접 붙이거나 붙이지 않는 방식이 다르기 때문). 한 저장소의 설정 패턴을 다른 저장소에 그대로 복사하면 `/api`가 중복되거나 빠진다.
- **DB 접속 문자열의 `useSSL=true`는 TLS를 강제하지 않는다.** MySQL Connector/J에서 `useSSL=true`는 드라이버 기본값(`sslMode=PREFERRED`)과 동작이 같아서, 서버가 TLS를 못 주면 **오류 없이 평문으로 연결된다.** TLS를 실제로 강제하려면 `sslMode=REQUIRED`(또는 `useSSL=true&requireSSL=true`)가 필요하다. `hikari.data-source-properties.sslMode`가 URL의 `useSSL`보다 우선한다는 점도 확인돼 있다 — 이 조직은 sslMode를 코드(yml)에 박아 URL(SSM)의 값보다 이기게 하는 방식을 쓴다.
- **DB는 KST, JVM은 UTC로 갈리는 저장소가 있었다.** RDS의 `time_zone`은 `Asia/Seoul`인데 배포 이미지의 베이스(`distroless`)는 시간대가 없어 JVM 기본값이 UTC였다 — `NOW()`(DB, KST)와 `LocalDateTime.now()`(앱, UTC)가 같은 순간을 9시간 다르게 찍는 문제였다. 지금은 저장은 UTC로 통일하고 업무 경계(일자·마감·파기 기준일)만 KST를 명시적으로 인자로 넘기는 규약으로 정리돼 있다. 새 시각 관련 코드를 짤 때 `LocalDateTime.now()`를 무심코 쓰면 이 규약을 깨뜨린다 — 반드시 UTC로 저장하고, KST가 필요한 지점에서만 명시적으로 변환한다.

---

## 6. VDI 빌드 도구 — `backend-api-main`

`backend-api-main`은 `Dockerfile`이 없는 EC2 배포 저장소라, VDI에서 빌드할 때 사내망(인터넷 차단) 제약을 우회하기 위한 전용 도구가 따로 있다. 두 파일이 세트다.

- `build-backend-api.ps1` — 빌드를 실행하는 PowerShell 스크립트
- `backend-api-nexus.init.gradle` — Gradle이 플러그인·의존성을 어디서 받아올지 강제로 바꾸는 init 스크립트

### 6-1. 무엇을 하는가

```powershell
.\build-backend-api.ps1 -ProjectPath <backend-api-main 체크아웃 경로> -TargetEnvironment {dev|stg|prd}
```

동작 순서.
1. 대상 프로젝트의 `gradle/wrapper/gradle-wrapper.properties`를 **임시로** 고쳐서 `distributionUrl`이 사내 Nexus(`http://10.239.68.173:8081/repository/gradle-dist/...`)를 가리키게 만든다(원래 값은 메모리에 백업).
2. `gradlew.bat --no-daemon --init-script backend-api-nexus.init.gradle clean bootJar`를 실행한다.
3. `backend-api-nexus.init.gradle`이 플러그인 저장소·의존성 저장소를 전부 Nexus 미러(`skix-gradle-repo`, `gradle-public`, `skix-maven-repo`, `maven-public`)로 바꿔치기한다 — VDI는 공인 인터넷(Maven Central, Gradle Plugin Portal)에 직접 나가지 못하기 때문에 이 초기화 스크립트 없이는 빌드가 아예 안 된다.
4. 빌드가 끝나면 `build/libs`에서 `-plain.jar`가 아닌 최신 jar를 찾아 `air-bot-api-<env>-<타임스탬프>.jar`로 이름을 바꾸고 SHA256을 출력한다.
5. **`finally` 블록에서 1번에서 백업해 둔 `gradle-wrapper.properties`를 원래대로 복원한다.** 즉 이 스크립트를 실행해도 저장소에는 흔적이 남지 않는다(커밋해서는 안 되는 임시 변경이라는 뜻).

### 6-2. 사용 시 주의점

- **`-TargetEnvironment`는 산출물 파일명에만 반영된다.** 이 파라미터가 빌드 자체의 프로필을 바꾸지는 않는다 — `bootJar`는 그냥 클래스패스에 모든 프로필 yml을 다 담아 만든다. 실제로 어떤 프로필이 뜨는지는 §4-2에서 설명한 대로 **EC2에서 jar를 실행할 때** `./air-bot-api.sh start {dev|stg|prd}`로 결정된다. 즉 이 스크립트로 만든 jar 하나를 dev/stg/prd 어디에나 올릴 수 있고, 파일명의 `-dev-`/`-stg-`/`-prd-`는 사람이 헷갈리지 않기 위한 라벨일 뿐이다.
- 스크립트가 요구하는 필수 파일 4개(`gradlew.bat`, `gradle-wrapper.properties`, `build.gradle`, `settings.gradle`) 중 하나라도 없으면 즉시 실패한다 — 체크아웃이 완전한지(서브모듈 누락 등) 먼저 확인한다.
- `--no-daemon`으로 돈다. VDI에 여러 명이 같은 빌드 박스를 쓴다면 데몬을 안 쓰는 이유가 있을 수 있으니 임의로 데몬 옵션을 넣지 않는다.
- 이 zip(`backend-api-vdi-build-tools.zip`)에는 압축 해제된 폴더와 동일한 파일이 중복으로 들어 있을 뿐 추가 내용은 없다.
- `gradle-wrapper.properties`의 원래 `distributionUrl`(공인 Gradle 배포 서버 주소)이 이 스크립트가 백업·복원하는 대상이라는 것은, **로컬 맥이나 사내망 밖에서 같은 저장소를 빌드할 때는 이 스크립트를 쓰면 안 된다**는 뜻이기도 하다 — Nexus(`10.239.68.173`)는 사내망에서만 열린다.

---

## 7. 환경별 공개 호스트

모든 호스트는 `*.namuhx.com`이다. Nginx·API Gateway 설정 자체는 이 9개 애플리케이션 저장소 어디에도 없다(별도 인프라 관리 영역) — 아래 표는 각 서비스의 `application-*.yml`/태스크 정의에 박혀 있는 값과 기존 인수인계 자료의 curl 예시로 교차 확인한 것이다.

| 서비스 | dev | stg | prd | 경유 경로 |
|---|---|---|---|---|
| 앱 전용 API(`backend-api-main` 등) | `app-api-dev.namuhx.com` | `app-api-stg.namuhx.com`(패턴상 추정, 미확인) | `app-api.namuhx.com`(패턴상 추정, 미확인) | **API Gateway**, Cognito 인증(IdToken) 검사 후 내부로 라우팅 |
| WebView 문서 + 웹 전용 API(`backend-api-main`) | `report-dev.namuhx.com` | `report-stg.namuhx.com` | `report.namuhx.com` | **Nginx가 API Gateway 없이 직접 프록시**. `/api/**`를 통째로 `backend-api-main`에 넘긴다(§7-1) |
| PASS 콜백 전용(`backend-api-main`) | (dev는 별도 host 없이 report-dev로 통합) | `clientstg.namuhx.com` | `client.namuhx.com` | Nginx 직접 프록시(추정 — report와 같은 구성) |
| 파트너 오픈 API(`skix-openapi`) | `openapi-dev.namuhx.com` | `openapi-stg.namuhx.com` | `openapi.namuhx.com` | API Gateway(자체 JWT 발급/검증, Cognito와 별개) |
| 건강상담 API(`skix-medcare`) | `medcare-dev.namuhx.com` | `medcare-stg.namuhx.com` | `medcare.namuhx.com` | API Gateway(추정, 확인 필요) |
| 영상 스트리밍(`skix-streaming`) | `streaming-dev.namuhx.com` | `streaming-stg.namuhx.com` | `streaming-prd.namuhx.com`(**유일하게 prd에도 접미사가 붙는다**) | API Gateway 또는 ALB(미확인) |
| FOTA 펌웨어(CloudFront) | `fota-dev.namuhx.com` | `fota-stg.namuhx.com` | `fota.namuhx.com` | CloudFront(서명 URL) |
| AWS IoT Core 커스텀 도메인 | `app-iot-dev.namuhx.com` | `app-iot-stg.namuhx.com` | `app-iot.namuhx.com` | IoT Core MQTT/HTTPS 게이트웨이 |

**호스트 이름 규칙이 서비스마다 다르다는 점을 주의한다.**
- prd 호스트는 보통 접미사가 없다(`report.namuhx.com`, `openapi.namuhx.com`, `medcare.namuhx.com`, `fota.namuhx.com`, `app-iot.namuhx.com`). **`skix-streaming`만 예외로 `streaming-prd.namuhx.com`처럼 prd에도 `-prd`가 붙는다.**
- stg 접미사도 통일돼 있지 않다 — 대부분 `-stg`인데 PASS 콜백 호스트만 하이픈 없이 `clientstg.namuhx.com`이다.
- `skix-security`는 이 표의 다른 서비스들과 달리 **자체 공개 도메인이 확인되지 않는다.** 앱이 부르는 `/security/v1/**` 계열 API는 기존 자료에서 호스트를 명시하지 않고 상대경로로만 등장한다 — `app-api-*.namuhx.com` 밑에 경로로 물려 있을 가능성이 높지만 **미확인**이며, API Gateway 라우팅 설정을 VDI 콘솔에서 직접 봐야 확정할 수 있다.

**내부(서비스 간) 통신은 위 공개 호스트를 쓰지 않는다.** `backend-api-main` ↔ `skix-security`/`skix-medcare`/`skix-openapi` 간 호출은 전부 내부 NLB(§5-5 표)의 포트로 나뉜 사설 주소를 쓴다 — dev는 `skmg-airbot-nlb-dev-*`의 8080(security)/8081(medcare)/8082(backend-api-main)/8084(openapi)/4000(streaming-kms), PHR 연동은 8030(별도 서비스, 이 9개 저장소 밖). stg/prd는 `skmg-benjamin-{env}-was-nlb-*`의 포트 80 하나로 합쳐져 있다(내부에 ALB가 있는지, 리스너 규칙이 어떻게 나뉘는지는 **미확인** — VDI에서 대상 그룹을 봐야 한다).

**§7-1. `report-*`/`client*` 호스트가 API Gateway를 우회한다는 것의 실질적 의미**

`report-dev`/`report-stg`/`report(prd)`.namuhx.com은 `/api/**`를 API Gateway 없이 `backend-api-main`으로 통째 프록시한다(실측: `GET report-{dev,stg,prd}/api/internal/security/check-member`가 세 환경 모두 **무인증 200**). `/app/**` 경로는 앱 쪽 토큰 검증(JWKS 서명 확인)이 컨트롤러 자체에 있어 이 우회로도 사칭이 불가능하지만, `InternalSecurityController`처럼 애초에 인증 게이트가 없는 내부 API는 이 우회 경로로 그대로 열려 있다. 이 구성은 이번에 새로 만든 것이 아니라 **기존 인프라 구성**이며 3환경 모두 해당한다 — 새 API를 `backend-api-main`에 추가할 때 "API Gateway가 앞에 있으니 인증은 거기서 됐겠지"라고 가정하면 안 되는 이유다.

---

## 8. 그 밖에 인수자가 확인해야 할 것 (미확인 목록)

이 문서에서 "미확인"으로 남긴 항목과 확인 방법을 모았다.

| # | 항목 | 확인 방법 |
|---|---|---|
| 1 | 콘솔 역할 전환의 정확한 역할 ARN(라벨 "벤자민 개발계" 등이 실제로 가리키는 역할명) | VDI 콘솔에서 역할 전환 메뉴 열어 직접 확인 |
| 2 | 각 ECS 서비스가 실제로 저장소의 최신 커밋을 반영한 리비전을 running 상태로 물고 있는지 | VDI에서 `aws ecs describe-services` / `describe-task-definition`으로 이미지 태그·환경변수 대조 |
| 3 | `backend-scheduler-main`·`backend-map-converter`의 정확한 배포 스크립트·EC2 위치 | VDI에서 해당 EC2 접속, 홈 디렉터리·crontab·systemd unit 확인 |
| 4 | `app-api-{stg,prd}.namuhx.com`, `medcare-{env}.namuhx.com`, `streaming-{env}.namuhx.com`이 API Gateway/ALB 중 무엇을 경유하는지, 그리고 정확한 라우팅 규칙 | VDI 콘솔에서 API Gateway 스테이지/ALB 리스너 규칙 확인 |
| 5 | `skix-security`의 공개 호스트(있다면) | VDI 콘솔의 API Gateway 라우팅 표 확인, 또는 앱 소스의 API 베이스 URL 설정 확인 |
| 6 | stg/prd 내부 NLB(`skmg-benjamin-<env>-was-nlb`)가 포트 80 하나 뒤에서 서비스를 어떻게 나누는지(ALB 존재 여부, 리스너 규칙) | VDI 콘솔에서 해당 NLB의 대상 그룹·리스너 확인 |
| 7 | 로컬 CLI 프로필(`source_profile`+`role_arn`) 구성이 실제로 동작하는지(3-2의 IP 제한과 별개로) | VDI 밖에서 시도 시 예상대로 거부되는지 재현, 또는 보안팀에 SCP 조건 문의 |

---

## 부록 A. ECS 태스크 정의 CPU/메모리 스펙 (참고용)

| 저장소 | CPU | 메모리 |
|---|---|---|
| `skix-security` | 512 | 1024 (stg 동일, **prd는 1024/2048**) |
| `skix-medcare` | 512 | 1024 (전 환경 동일) |
| `skix-openapi` | 1024 | 2048 (전 환경 동일) |

`skix-security`만 prd에서 CPU/메모리를 두 배로 올려 두었다는 점에 유의한다 — dev/stg 스펙을 그대로 prd 태스크 정의에 복사하면 스펙이 줄어든다.
