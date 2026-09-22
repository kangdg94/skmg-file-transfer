# [작업] KVS 실시간 영상(Live View) 자사 AWS 전환

| 항목 | 내용 |
|---|---|
| **상태** | 백엔드 완료 · **dev·stg·main 3브랜치 전부 반영** · 개발계 활성 · **검증계·운영계 실활성화 여부 미확인**(로컬에서 AWS 확인 불가) |
| **작업 기간** | 2026-07-20 ~ 2026-08-19 (핵심 구현 7/20~7/31, 에스원 종료 통보 8/4~8/11, 설정 검증 강화 8/13~8/19) |
| **직접 수정한 저장소** | `skix-security` (단일). 부수로 `skix-streaming` 1커밋(관제 종료 경로 회귀 수정) |
| **요청서를 전달한 대상** | 기기(A1 펌웨어·LiveView) 담당, iOS 담당, Android 담당, 관제 웹 프론트 담당 |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §8-1의 1번(3환경 ECS·Redis 실태 확인). 이것을 모르면 아래 어떤 판단도 서지 않는다 |

---

## 0. 세 줄 요약

1. Safe Care(로봇 기반 보안 서비스)의 **실시간 영상을 에스원이 발급하던 KVS에서 자사 AWS Kinesis Video Streams WebRTC로 바꿨다.** 세션 생성 REST, 임시 VIEWER 자격증명 발급, 기기 MQTT 오케스트레이션, 출동 연계까지 `skix-security` 한 저장소에 들어 있다.
2. 코드는 **dev·stg·main 세 브랜치에 전부 들어가 있고**, 세 환경 태스크 정의 JSON도 모두 `KVS_PROVIDER=aws`로 준비되어 있다. 다만 **검증계·운영계에서 실제로 ECS에 적용되고 Redis epoch가 설정됐는지는 로컬에서 확인할 수 없다.** 정리해 둔 배포 런북은 부록 B에 전문으로 옮겼다.
3. 남은 것은 **3환경 실태 확인 → 검증계 활성화·관찰 → 운영계 단계적 cohort 오픈**, 그리고 **보안성검토 제출 자료가 에스원 KVS 전제로 쓰여 있어 자사 AWS 형상과 달라진 부분의 재검토 필요 여부 확인**이다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **Safe Care** | 자사 로봇(A1)을 이용한 가정용 보안 서비스. 백엔드는 `skix-security`다 |
| **Live View / 실시간 영상** | 앱·관제 웹에서 A1 기기의 카메라 영상을 실시간으로 보는 기능 |
| **KVS** | AWS Kinesis Video Streams. 이 작업에서 쓰는 것은 **영상 저장용 스트림이 아니라 WebRTC 시그널링 채널**(Signaling Channel)이다 |
| **WebRTC MASTER / VIEWER** | 시그널링 채널에서 영상을 내보내는 쪽(MASTER=A1 기기)과 받는 쪽(VIEWER=앱·관제 웹) |
| **시그널링(signaling)** | SDP·ICE 후보를 교환해 P2P 연결을 맺는 협상 단계. 영상 자체는 여기로 흐르지 않는다 |
| **P2P / Relay(TURN)** | 앱과 기기가 직접 연결되면 P2P, 중계 서버를 경유하면 Relay다. Relay는 비용·정책 제약이 있다 |
| **S1 / 에스원** | 보안 서비스 제휴사. 출동 접수와 기존 스트리밍 계약의 상대다. 전환 전에는 이쪽이 KVS 세션을 발급했다 |
| **provider** | KVS 세션(채널+자격증명)을 누가 발급하는지. `s1`(에스원 발급) / `aws`(자사 AWS 직접 발급) / `disabled` |
| **legacy 스트리밍** | KVS 이전의 자체 시그널링 경로(`skix-streaming`, `skix-streaming-signal`, `skix-streaming-kms`). 지금도 살아 있다 |
| **session lease** | 기기에 발급하는 "이 시각까지 세션을 유지해도 된다"는 만료시각. 기기가 스스로 집행한다 |
| **device lease** | Redis `lock:serial:<serial>` 키. 한 기기의 카메라를 누가 점유했는지 나타내며 legacy와 공유한다 |
| **fence** | 같은 기기의 세션 세대 토큰(단조 증가 정수). 지연·역순 도착한 과거 명령을 기기가 거부하는 근거다 |
| **epoch** | Redis 유실·복구 세대 문자열. 배포 설정값과 Redis 값이 정확히 같아야 KVS가 동작한다 |
| **cohort** | KVS 경로를 쓸 대상 기기·사용자 집합. **서버가 아니라 앱·관제 쪽 라우팅으로 정한다** |
| **출동(dispatch)** | 이상 상황 시 에스원 경비원 출동을 요청하는 기능. Live View 중 현재 영상을 15초 VOD로 올린 뒤 접수한다 |
| **MQTT / AWS IoT Core** | 서버가 기기에 명령을 보내고 상태를 받는 경로. X.509 mTLS다 |

### 1-2. 문제

전환 전 실시간 영상은 두 갈래였다.

- **legacy 스트리밍**: 자체 시그널링 서버(`skix-streaming-signal`)를 두고 Socket.IO로 협상했다. 서버를 직접 운영해야 하고 확장·안정성 부담이 우리 몫이다.
- **에스원 발급 KVS**: `POST /skix/livevideo/play`로 에스원에 세션을 요청하면 에스원 계정의 시그널링 채널 ARN과 camera/viewer 임시 자격증명을 돌려준다.

에스원 발급 경로에는 우리가 통제할 수 없는 제약이 있었다.

- **Relay 연결 90초 하드 한도.** 초과하면 해당 deviceId가 에스원 쪽에서 차단 대상이 된다. 관제 원격 맵핑처럼 수십 분짜리 작업은 애초에 불가능하다.
- 채널·자격증명이 **에스원 계정 리소스**라 권한·쿼터·장애를 우리가 볼 수 없다. 전문 오타(`deviceid`)·포맷 위반(`expiration`의 잉여 `Z`) 같은 문제도 상대 수정 일정에 묶인다.
- 세션 발급 API가 죽으면 실시간 영상이 통째로 멈춘다.

### 1-3. 목표

- **자사 AWS 계정의 KVS WebRTC 시그널링 채널**로 앱(VIEWER)과 A1 기기(MASTER)를 연결한다. 채널은 기기 시리얼당 하나 고정이며 없으면 자동 생성한다.
- 임시 자격증명은 **역할별로 분리**해 발급한다. 앱에는 VIEWER 자격증명만, 기기에는 MASTER(camera) 자격증명만 간다.
- 한 기기에서 **legacy 스트리밍과 KVS가 동시에 카메라를 점유하지 못하게** 한다.
- **기기가 응답하지 않아도 세션이 영구히 남지 않게** 한다(무한 점유 방지).
- Redis·DB 유실·롤백 같은 상태저장소 사고에서 **조용히 잘못 동작하지 말고 전면 차단**한다.
- 서버 로그·DB·관측 인프라에 **자격증명 원문을 남기지 않는다**.
- Live View 중 **출동 요청**(15초 영상 업로드 후 에스원 접수)을 붙인다.
- **legacy 경로는 한 줄도 바꾸지 않는다.** 두 경로가 공존하고 라우팅은 앱·관제가 정한다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 서버 코드

`skix-security` 한 저장소다. 브랜치 흐름은 dev → stg → main 단방향 승격이다.

| 커밋 | 내용 | dev | stg | main(운영) |
|---|---|---|---|---|
| `3e9a6cb` (07-20) | KVS 실시간 영상·출동 오케스트레이션 최초 구현 | 반영 | 반영 | 반영 |
| `6522113` (07-22) | renewable session lease | 반영 | 반영 | 반영 |
| `ad9a118` (07-27) | 출동 공개 API 단순화(availability) | 반영 | 반영 | 반영 |
| `f391246` (07-31) | **세션 권위를 Redis로 단일화 + 3환경 task def를 `aws`로 전환** | 반영 | 반영 | 반영 |
| `e62a482` (08-04) | KVS 종료 시 에스원 `livevideo/stop` 통보 | 반영 | 반영 | 반영 |
| `a8d9d03` (08-11) | 에스원 stop `NO_ACTIVE_SESSION` 멱등 처리 | 반영 | 반영 | 반영 |
| `71caaa9` (08-18) | **라이브뷰 relay 값 범위 검증 추가**(범위 밖이면 부팅 실패) | 반영 | 반영 | 반영 |
| `493e068` (08-19) | AWS issuer를 provider와 무관하게 항상 로드 | 반영 | 반영 | 반영 |

전체 커밋 목록은 부록 A에 있다. **부록 A의 커밋은 전부 세 브랜치 모두에 들어가 있다**(`git merge-base --is-ancestor`로 맵핑 커밋 포함 44건 전수 확인).

`main`은 dev 전체가 아니라 **선별 반영** 상태다. KVS 관련 커밋은 전부 올라가 있지만, KVS와 무관한 백엔드 변경 2건(`b1b148b` 토큰 클레임 제거, `cf5f56b` 아웃바운드 로그)이 일부러 빠져 있다. 다음 승격에서 `git merge dev` into main을 그냥 하면 그 2건이 조상으로 딸려온다. 지금까지는 필요한 머지커밋만 골라 머지하거나 cherry-pick으로 피했다. KVS 관련 작업만 올릴 때는 이 점을 신경 쓸 필요가 없다(이미 전부 올라가 있다).

| 저장소 | 커밋 | 상태 |
|---|---|---|
| `skix-streaming` | `280b6da` (08-22) | 관제 웹 Live View 종료 경로가 `X-Target-Login-Id` 없이 호출되는데 admin 분기가 헤더를 무조건 요구하게 바뀌어 전건 실패하던 회귀를 고쳤다. 인가만 필요한 호출(`assertMemberOrAdmin`)과 값을 실제로 쓰는 호출(`assertAndResolveLoginId`)을 분리했다. KVS 경로가 아니라 **legacy 경로 수정**이지만 같은 화면에서 만나므로 여기 적는다 |

### 2-2. 환경별 설정 (저장소의 task definition JSON 실측)

세 브랜치의 `deploy/task-definition-{dev,stg,prd}.json`은 내용이 **완전히 동일**하다(md5 대조).

| 환경 | AWS 계정 | provider | required | epoch | fence gen | 채널 prefix | 환경 태그 |
|---|---|---|---|---|---|---|---|
| dev | `010928196421` | `aws` | `true` | `dev-2026-07-31-01` | `1` | `skix-kvs-dev-` | `dev` |
| stg | `087432099373` | `aws` | `true` | `stg-2026-07-31-01` | `1` | `skix-kvs-stg-` | `stg` |
| prd | `779846811758` | `aws` | `true` | `prd-2026-07-31-01` | `1` | `skix-kvs-prd-` | `prd` |

Relay 정책 환경변수(`KVS_RELAY_FORCE_QUIT_SECONDS`, `KVS_RELAY_MAX_DURATION_MS`, `KVS_MAPPING_RELAY_MAX_DURATION_MS`)와 `KVS_CREDENTIAL_DURATION_SECONDS`는 **세 태스크 정의 어디에도 없다.** 따라서 코드 기본값(서버 70초 / 기기 80초 / 맵핑 600초 / STS 900초)이 적용된다. 자사 AWS에는 에스원의 90초 하드 한도가 없으므로 이 값은 **보수적 초기값**이며, 정책을 2분·3분으로 올리려면 `서버 relay-force-quit < 기기 relay-max-duration` 관계를 유지한 채 태스크 정의에서 바꾸면 된다(재빌드 불필요).

### 2-3. 배포·검증 상태

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| 코드(3브랜치) | **반영 완료** | Git 실측 |
| DDL `kvs_session_history`, `kvs_dispatch_job` | 개발계는 적용된 것으로 본다(개발계에서 에스원 전문 실측·기기 연동이 진행됐다 — 추정, DB 직접 확인은 안 함). stg·prd는 **미확인** | 부록 B-3의 `SHOW CREATE TABLE`로 확인 |
| `purpose` 컬럼 ALTER(원격 맵핑용) | **미확인**. 이미지보다 **먼저** 적용해야 한다 — 역순이면 맵핑뿐 아니라 **일반 라이브뷰 포함 모든 KVS 세션 생성이 실패**한다(§5의 11번) | 부록 B-3 |
| SSM `KVS_LEASE_SECRET` (환경별) | dev는 존재로 본다(태스크 정의가 참조하고 개발계가 기동 중 — 추정). stg·prd는 **미확인** | 부록 B-2-1 |
| IAM 역할·정책 (task / master / viewer) | dev는 존재로 본다(태스크 정의가 ARN 참조 — 추정). stg·prd는 **미확인** | 부록 B-2-2 |
| Redis `security:{kvs}:epoch` 설정 | dev는 설정됐을 것(개발계 동작). **stg·prd 미확인** — 설정 전이면 KVS API가 전부 `503 RECOVERING`이다 | 부록 B-4 |
| 개발계 KVS 실사용 | **동작 확인됨**(앱·기기·관제 연동 작업이 개발계에서 진행됐다) | — |
| 검증계 활성화 | **미확인.** 정리해 둔 계획 기준으로는 미실행 | 부록 B-5 |
| 운영계 활성화 | **미확인.** 미실행이 전제 | 부록 B-6 |
| 로컬 맥에서 AWS 확인 | **불가.** ECS·SSM·IAM·Redis 확인은 전부 회사 VDI에서 해야 한다 | — |

### 2-4. 보안성검토

에스원 KVS 실시간 영상 연동에 대한 보안성검토 요청 자료를 작성했다(전문은 부록 C). **제출 여부와 검토 결과는 확인하지 못했다.**

주의할 점이 하나 있다. **이 자료는 `provider=s1`(에스원이 시그널링 채널과 자격증명을 발급) 전제로 쓰여 있는데, 세 환경 태스크 정의는 모두 `provider=aws`(자사 AWS 발급)다.** 두 형상이 다른 지점은 부록 C 마지막 표에 정리했다. 재검토·정정 제출이 필요한지는 확인이 필요하다(§8-2).

### 2-5. 타 팀 상태 (Git 실측, 별도 담당자)

| 대상 | 상태 | 확인한 것 |
|---|---|---|
| A1 기기 — `packages-mr7`(IotAgent/DeviceAgent), `liveliew`(뷰어·미디어) | **구현 완료** | 7/23 최초 연동(`890d19e0`)부터 8/31 AAR 재빌드 동기화(`2b8d07a4`)까지. fence 영속값 단일 권위(`9a4a48d`), `relayMaxDurationMs` 연결(`bb67a7d`/`a1f9708b`), peer 상실 시 즉시 안전 정지(`4435116`)까지 서버 계약 반영 확인 |
| 기기 relay 수락 상한 | **10분(600,000ms)에 머물러 있다** | `liveliew` `KvsSessionManager.kt`의 `RELAY_TIME_LIMIT_MAX_MS = 600_000L` 실측. 서버 코드 천장(`DEVICE_RELAY_MAX_MS`)은 90분으로 열려 있지만 **기기가 아직 90분을 안 받는다.** 범위 밖 값을 보내면 기기가 기본값 80초로 되돌려 **늘리려던 게 7.5배 짧아진다** |
| iOS (`benjaminios`) | **구현 완료** | 7/28 KVS 스트리밍 적용부터 9/2 TURN 90초 타임아웃 처리까지 `develop` 머지 확인 |
| Android (`benjaminandroid`) | **구현 완료** | 8/7~9/2. KVS 스트리밍 중 앱 kill 시 연결 해제, TURN 90초 이상 시 종료, R8 빌드 대응 |
| 관제 웹 (`frontend-web-backoffice`) | **구현 완료** | 8/10 연동 테스트(`e1989c93`), 8/14 "Security Care Live View 에스원 KVS으로 전환"(`97e2ecd5`) |
| cohort 스위치 | **서버에 없다** | `skix-security` 전체에 cohort 설정·토글이 없다. KVS 경로 선택은 앱·관제 라우팅이 한다. 롤백 1차 수단이 **우리 손에 없다**는 뜻이므로 단계적 오픈·중단은 앱·관제 담당과 사전 합의가 필요하다 |

앱이 구현한 "TURN 90초 타임아웃"은 에스원 하드 한도에 맞춘 클라이언트 안전망이다. 자사 AWS에는 그 한도가 없고, 정상 경로에서는 **서버가 70초에 먼저 끊으므로** 앱 타이머는 발화하지 않는다.

---

## 3. 무엇을 만들었나

### 3-1. 전체 흐름 (일반 라이브뷰, provider=aws)

```text
[앱/관제]  ① POST /security/v1/devices/{serial}/kvs/sessions
              헤더: Idempotency-Key (필수)
                           │
[skix-security]            ▼
   ② epoch 대조 (Redis security:{kvs}:epoch == KVS_EPOCH)   불일치면 503 RECOVERING
   ③ 같은 Idempotency-Key 이력 있으면 Redis 현재 상태로 결과 재현
   ④ MySQL kvs_session_history 에 CLAIMED INSERT → id 획득
      같은 트랜잭션에서 fence = fence_generation × 10^12 + id 확정 후 커밋
   ⑤ Redis lock:serial:<serial> 을 짧은 TTL로 SET NX  (device lease 예약)
   ⑥ 단일 Lua: epoch 재확인 + 세션 hash 생성 + fence HWM 갱신 + PREPARING deadline 등록
   ⑦ DB CLAIMED → OPEN
   ⑦' 일반 라이브뷰는 계약번호(에스원 deviceId)·icstNo 를 조회한다   실패하면 502 ISSUE_FAILED
       (provider=aws 여도 조회한다. 자사 AWS 발급에서는 STS 세션 이름 장식에만 쓰인다)
   ⑧ 자사 AWS 발급 ── AwsKvsSessionAdapter
        · DescribeSignalingChannel("skix-kvs-{env}-{serial}")
          없으면 CreateSignalingChannel(SINGLE_MASTER, TTL 60s) + 태그 2종 → ACTIVE 대기(최대 10초)
        · STS AssumeRole ×2 → MASTER(camera) 자격증명 / VIEWER 자격증명 (각 900초)
   ⑨ 자격증명 잔여 유효시간 < 10초면 start 미발행하고 종료 (fail-closed)
   ⑩ device lease PERSIST (TTL 제거)
   ⑪ 단일 Lua: epoch 재확인 + Redis TIME 기준 session lease 만료(60초) 계산 + PREPARING → ACTIVE CAS
   ⑫ MQTT kvsSessionStart 발행 (sessionId, fence, channelArn, camera 자격증명,
                                 commandExpiresAt, sessionLeaseExpiresAt, relayMaxDurationMs)
                           │
        발행 성공 확인 ────┴──▶ 201 CREATED + VIEWER 자격증명 (Cache-Control: no-store)
        발행 결과 불명확 ─────▶ 202 TERMINATING, 자격증명 미반환, 세션 종료 진행

[A1 기기]  ⑬ MASTER 로 채널 접속, clientId=sessionId 인 첫 offer 에만 answer
[앱]       ⑭ VIEWER 로 채널 접속 (clientId = sessionId)
           ⑮ 영상: 앱 ↔ 기기 직접(WebRTC DTLS-SRTP). Relay 경유해도 중계는 암호문만 전달

[유지]     기기 → status/kvsConnected, 이후 15초 주기 kvsHeartbeat
           서버 → 유효 heartbeat 마다 command/kvsSessionLeaseRenew 발행 (lease 60초 연장)

[종료]     앱 close(202 접수) 또는 relay cutoff 또는 heartbeat 상실
           → TERMINATING → 기기 kvsSessionClosed(proof) 또는 lease 만료+45초(연역)
           → FINALIZING → DB TERMINAL → device lease 해제 → Redis 세션 삭제
```

### 3-2. 상태머신

```text
PREPARING ── start 발행 가능 확정 ──▶ ACTIVE
    │                                  │
    ├─ 발급 전 실패·취소 ────────────▶ FINALIZING
    └─ prepare timeout(30초) ─────────▶ FINALIZING

ACTIVE ── close / timeout / NACK / session lease 만료 ──▶ TERMINATING
                                   │
                                   ├─ kvsSessionClosed 수신 ───────────▶ FINALIZING (proof)
                                   └─ lease 만료 + 45초 경과 ──────────▶ FINALIZING (LEASE_EXPIRED, 연역)

FINALIZING ── DB TERMINAL + device lease 해제 성공 ──▶ Redis 세션 삭제
```

- `PREPARING`: 기기에 아무것도 나가지 않은 상태.
- `ACTIVE`: start가 발행됐거나 발행됐을 수 있는 상태.
- `TERMINATING`: terminate를 발행·재발행하는 중. **device lease는 유지한다**(카메라를 아직 기기가 쥐고 있을 수 있으므로).
- `FINALIZING`: 종료 근거를 얻었고 DB·lease 정리를 재시도하는 상태.
- `TERMINAL`: Redis 상태가 아니라 DB 이력의 최종 상태.

`NOT_STARTED` NACK은 `{sessionId, fence, state=ACTIVE}`가 일치하면 "아무것도 시작하지 않았다"는 증거로 보고 즉시 종결한다. `BUSY`와 그 밖의 NACK은 같은 세 값이 맞을 때만 TERMINATING을 거쳐 closed를 기다린다. 과거 fence의 지연 보고는 상태를 바꾸지 않는다.

### 3-3. 두 종류 lease — 이 작업에서 가장 헷갈리는 부분

이름이 비슷한 lease가 둘이고, 역할이 완전히 다르다.

| | **device lease** | **session lease** |
|---|---|---|
| 저장 위치 | Redis `lock:serial:<serial>` (문자열 키) | Redis 세션 hash의 `maxIssuedLeaseExpiresAt` 필드 |
| 의미 | **이 기기의 카메라를 누가 점유했는가** | **기기가 이 세션을 언제까지 유지해도 되는가** |
| 공유 범위 | **legacy 스트리밍과 공유**한다(키 존재 여부로 상호배타) | KVS 전용 |
| 값 | `kvs\|v1\|base64url(HMAC-SHA256(leaseSecret, "kvs-lease\0"+serial+"\0"+sessionId))` | epoch ms 정수 |
| TTL | 예약 단계에만 짧은 TTL, 발급 성공 후 `PERSIST`(무기한) | TTL 아님. 만료시각 자체 |
| 누가 집행 | 서버만 해제한다 | **기기가 집행한다.** 만료되면 서버 명령 없이 스스로 끊는다 |
| 갱신 | 없음 | 유효 heartbeat마다 서버가 `kvsSessionLeaseRenew`를 발행해 60초 연장 |

핵심 원칙은 **"서버는 기기 생존을 추측하지 않는다"**이다. 서버는 유효기간이 명시된 세션 권한을 기기에 발급하고, 기기가 만료를 집행하며, 서버는 **마지막으로 발급한 만료시각 + 안전 여유(45초)**가 지난 뒤에만 device lease를 해제한다. 그래서 기기가 완전히 죽어도 세션이 무한히 남지 않고(약 105초 안에 `LEASE_EXPIRED`로 정리), 반대로 살아 있는 기기의 카메라를 서버가 먼저 빼앗지도 않는다.

device lease 값이 공개 sessionId가 아니라 **서버 비밀키 기반 HMAC**인 이유는 legacy와 키를 공유하기 때문이다. legacy 클라이언트가 어딘가에서 얻은 sessionId를 correlationId로 보내 KVS lease를 갱신하거나 삭제할 수 없게 막는다.

해제 결과는 `RELEASED` / `ABSENT` / `MISMATCH` 셋으로 구분한다. `MISMATCH`(내 것이 아닌 값이 들어 있음)이면 **삭제하지 않고** 경보를 한 번 남긴 뒤 backoff하여 재확인한다.

### 3-4. fence · epoch — 상태저장소 사고 방어

| 장치 | 동작 | 막는 사고 |
|---|---|---|
| **fence** | `fence = KVS_FENCE_GENERATION × 10^12 + kvs_session_history.id`. 기기가 처리한 최대 fence를 영속 저장해 그보다 작은 start를 거부한다 | MQTT 지연·역순 도착, 기기 재부팅 후 되살아난 stale 명령 |
| **fence generation** | DB를 초기화·스냅샷 복원하면 반드시 증가시킨다. 기본값이 없어 미설정이면 부팅 실패 | DB만 과거로 돌아가 `id`가 재사용될 때 기기가 모든 start를 stale로 거부하는 사고 |
| **fence HWM** | Redis `security:{kvs}:fence-hwm`에 발급된 최고 fence를 단조 기록. 부팅 시 `toFence(MAX(id)+1) <= HWM`이면 **기동 실패** | DB만 롤백하고 generation 증가를 잊은 배포 |
| **epoch** | Redis `security:{kvs}:epoch` 값과 `KVS_EPOCH` 설정값을 **문자열 정확 비교**. 키 부재·불일치면 create·멱등 재현·GET·출동 접수·availability를 전부 `503 RECOVERING` | Redis flush 후 키가 재생성돼 과거와 구분되지 않는 상황. 존재 검사만 하면 못 잡는다 |

epoch가 불일치일 때 **자동 force-release는 수행하지 않는다.** DB 값만 보고 lease를 푸는 것은 실제 세션 생존을 모르는 상태에서의 추측이기 때문이다. 종료 증거가 있는 경로(기기 `kvsSessionClosed`, 내부 force-close)는 허용한다.

### 3-5. P2P·Relay 정책

연결 유형 분류는 세션 생존과 **별개 축**이다. `kvsConnected.connectionType`은 `P2P`/`RELAY`만 인정하고, heartbeat의 `candidateType`은 `relay`/`host`/`srflx`/`prflx`만 인정한다. 그 밖은 UNKNOWN이다. UNKNOWN heartbeat도 session lease는 연장하지만 P2P로 간주하지 않으며, 최초 UNKNOWN 보고에서 분류 deadline(`classifyBy`, 40초)을 **한 번만** 설정한다. 그때까지 확정되지 않으면 잠정 RELAY로 보고 종료한다.

Relay 방어는 3중이다.

1. **기기**: selected pair가 relay로 확인된 시점부터 모노토닉 클록으로 약 80초 후 자체 종료.
2. **서버**: Relay 관측 시 약 70초 cutoff로 terminate 발행.
3. **sticky**: 한 번 Relay로 판정되면 이후 P2P 보고가 와도 완화하지 않는다.

`서버 cutoff(70s) < 기기 한도(80s)` 관계는 부팅 검증으로 강제한다. 기기 한도가 더 짧으면 안전망이 정책보다 먼저 발화해 서버가 상황을 모르게 된다.

P2P에는 Relay 시간 제한을 적용하지 않는다. 다만 앱 close, heartbeat 상실, MQTT 단절, 기기 오류 같은 일반 종료 조건은 그대로 적용된다.

> 관제 원격 맵핑 전용 세션(`purpose=REMOTE_MAPPING`)은 이 Relay cutoff의 예외이고, provider와 무관하게 항상 자사 AWS로 발급된다. 맵핑 기능 자체는 별도 문서의 주제이므로 여기서는 **일반 라이브뷰에 영향을 주는 부분만** 다룬다(§5의 10번·11번, §8).

### 3-6. 에스원 종료 통보 (provider=s1 일 때만 동작)

`provider=s1`로 운영하는 경우를 위해, 그때는 세션이 끝날 때 에스원에 `POST /skix/livevideo/stop`을 보낸다.

- **at-most-once best-effort**다. 재시도도 outbox도 없다.
- 통보 대상 판단은 **발급 직전에 Redis 세션 hash에 원자적으로 기록한** `providerType`/`providerIssueTarget`만 본다. 결과 코드로 역산하지 않는다.
- DB `TERMINAL` 최초 확정 직후 · device lease 해제 **전에** 동기로 호출한다. 이 순서가 "이전 stop → 새 play"를 보장한다.
- 전용 서킷브레이커 `s1-kvs-stop`과 별도 커넥션 풀을 쓴다. stop 단독 장애가 play를 막지 않는다.
- 미활성·이미 종료된 세션에 대한 stop은 에스원이 `result:false + NO_ACTIVE_SESSION`으로 답하는데, 이것은 **멱등 성공**으로 처리한다(목적 상태가 이미 달성됐으므로).

**지금 세 환경은 모두 `provider=aws`이므로 이 경로는 통째로 잠들어 있다.** `S1KvsApiClient`·`S1KvsStopApiClient`·`S1KvsClientConfig`가 전부 `@ConditionalOnProperty(app.kvs.provider=s1)`이라 **빈이 아예 로드되지 않는다.** provider를 되돌리기 전에는 이 코드의 동작을 확인할 수 없다.

### 3-7. 저장소 내 역할과 주요 파일

경로는 `skix-security` 기준이다.

| 책임 | 파일 |
|---|---|
| 앱·관제용 세션 REST (create/close/get, 맵핑 네임스페이스 포함) | `src/main/java/com/skix/security/api/controller/KvsLiveViewController.java` |
| 출동 REST (접수·availability) | `src/main/java/com/skix/security/api/controller/KvsLiveViewDispatchController.java` |
| 내부 전용 강제 종료 | `src/main/java/com/skix/security/api/controller/InternalDeviceController.java` (`POST /internal/v1/devices/{serial}/kvs/force-close`) |
| 세션 수명주기 오케스트레이션(핵심) | `src/main/java/com/skix/security/domain/service/KvsLiveViewService.java` |
| 출동 오케스트레이션 | `src/main/java/com/skix/security/domain/service/KvsLiveViewDispatchService.java` |
| provider별 issuer 선택 | `src/main/java/com/skix/security/domain/service/KvsSessionIssuerRegistry.java` |
| **자사 AWS 발급**(STS AssumeRole ×2, 채널 조회·생성·태깅) | `src/main/java/com/skix/security/infra/kvs/AwsKvsSessionAdapter.java` |
| STS·KinesisVideo 클라이언트 빈 | `src/main/java/com/skix/security/infra/kvs/KvsClientConfig.java` |
| 에스원 발급·종료 통보 (provider=s1 전용) | `src/main/java/com/skix/security/infra/kvs/S1KvsApiClient.java`, `S1KvsStopApiClient.java`, `S1KvsClientConfig.java` |
| 설정·부팅 검증(관계 검증 전부 여기) | `src/main/java/com/skix/security/infra/kvs/KvsProperties.java` |
| fence 롤백 부팅 가드 | `src/main/java/com/skix/security/infra/kvs/KvsFenceBootGuard.java` |
| Redis 세션 상태·lease·deadline (Lua) | `src/main/java/com/skix/security/infra/redis/KvsSessionRedisService.java` |
| 출동 serial guard | `src/main/java/com/skix/security/infra/redis/KvsDispatchGuardRedisService.java` |
| watchdog (5초 주기 sweep, metric 2종) | `src/main/java/com/skix/security/infra/scheduler/KvsSessionWatchdogScheduler.java` |
| DB 이력·출동 원장 | `src/main/java/com/skix/security/infra/db/KvsSessionHistoryRepository.java`, `KvsDispatchJobRepository.java`, `src/main/resources/mapper/Kvs*.xml` |
| DDL | `src/main/resources/db/kvs_session_history.sql`, `src/main/resources/db/kvs_dispatch_job.sql` |
| ECS 설정 바인딩 | `src/main/resources/application-ecs.yml` |
| 태스크 정의 | `deploy/task-definition-{dev,stg,prd}.json` |
| 배포 스크립트 | `deploy/build-export.sh`, `deploy/cloudshell-push.sh` |
| 저장소 규범 문서 | `docs/kvs-livevideo-design.md`, `docs/api-spec.md`, `docs/mqtt-spec.md`, `docs/kvs-app-guide.md`, `docs/kvs-device-guide.md`, `docs/kvs-web-backoffice-guide.md` |
| 테스트 | `src/test/java/com/skix/security/kvs/` (36개 파일), `src/test/java/com/skix/security/infra/kvs/` (5개 파일) |

### 3-8. 데이터

**MySQL** (`security` 스키마)

`kvs_session_history` — 멱등 claim, fence 발급, 종료 결과, Redis 유실 시 복구 후보를 보관한다.

| 컬럼 | 비고 |
|---|---|
| `id` bigint AI | 내부 PK. 기기·Redis·MQTT에 노출하지 않는다 |
| `fence_generation` bigint NOT NULL | INSERT 시점의 `KVS_FENCE_GENERATION` |
| `fence` bigint **NULL** | `generation × 10^12 + id`. auto_increment id를 받은 뒤 같은 트랜잭션에서 채우므로 nullable이다. **호환 목적이 아니다.** 애플리케이션 불변식은 "커밋된 `fence IS NULL` 행이 없다" |
| `idempotency_key` / `session_id` / `serial` / `login_id` | UNIQUE는 `fence`, `idempotency_key`, `session_id` 셋 |
| `initiated_by`, `admin_login_id` | USER / ADMIN, 관리자 대리 시 행위자 |
| `purpose` varchar(32) NULL | NULL=일반 라이브뷰, `REMOTE_MAPPING`=관제 원격 맵핑. **이 컬럼 ALTER가 배포 순서 함정이다**(§5의 11번) |
| `connection_type`, `connected_at`, `relay_duration_ms`, `last_heartbeat_at` | 종료 직전 Redis 스냅샷을 best-effort로 1회 기록. heartbeat마다 쓰지 않는다 |
| `status` | CLAIMED / OPEN / TERMINAL |
| `result_code` | 종료 사유 (아래 표) |

`kvs_dispatch_job` — 출동 영상 업로드부터 에스원 접수까지의 durable 작업 원장. `active_serial`이 generated column(진행 중 상태일 때만 serial 값)이고 여기에 UNIQUE가 걸려 있어 **같은 기기의 진행 중 출동 작업이 1건으로 제한**되며, terminal 전이 시 NULL이 되어 제약이 자동 해제된다.

**세션 종료 코드** (`kvs_session_history.result_code`, 멱등 재현 시의 HTTP 상태를 겸한다)

| 코드 | HTTP | 의미 |
|---|---|---|
| `CLOSED_BY_APP` / `CLOSED_BY_DEVICE` | 200 | 정상 종료 |
| `RELAY_TIME_LIMIT` | 200 | Relay 제한 종료 |
| `HEARTBEAT_LOST` | 200 | heartbeat 상실 |
| `LEASE_EXPIRED` | 200 | session lease 만료 계열. 기기 자가 종료 보고와 서버의 연역적 정리 **둘 다** 이 값이다 |
| `PREPARE_TIMEOUT` / `NOT_STARTED` / `CANCELLED` / `RECOVERY` | 200 | — |
| `FORCE_CLOSED_INTERNAL` | 200 | 내부 force-close 전용(`LEASE_EXPIRED`와 구분) |
| `UNKNOWN_SESSION_CUTOFF` | 200 | **폐기된 65초 컷오프 시절의 legacy 값.** 신규 세션에서는 발생하지 않는다. 과거 이력 조회용으로만 남겨 뒀다 — 삭제 금지 |
| `DEVICE_IN_USE` | 409 | 다른 세션·legacy가 점유 중 |
| `RECOVERING` | 503 | 상태저장소 복구 중 |
| `ISSUE_FAILED` / `START_DISPATCH_FAILED` | 502 | 자격증명 발급 실패 / 기기 전달 준비 실패 |
| `ABANDONED` | 500 | 원 요청이 서버 장애로 유실 |

**Redis** — KVS 세션의 **유일한 live authority**다. MySQL의 OPEN/CLAIMED만으로 ACTIVE를 재구성하지 않는다.

| 키 | 역할 |
|---|---|
| `lock:serial:<serial>` | device lease. **legacy와 공유** |
| `security:{kvs}:epoch` | 복구 세대. 배포 설정값과 정확 비교 |
| `security:{kvs}:session:<serial>` | live session hash |
| `security:{kvs}:deadline` | watchdog `nextActionAt` ZSet |
| `security:{kvs}:fence-hwm` | 관측된 최고 발급 fence |
| `security:{kvsdispatch}:serial:<serial>` | serial당 진행 중 출동 guard |

`{kvs}` hash tag로 세션·deadline·epoch·fence HWM이 같은 Redis Cluster 슬롯에 들어간다(Lua 다중 키 접근 때문에 필수). 제어 read는 replica가 아니라 **Lua/EVAL로 primary에서** 한다. 상태를 읽고 따로 바꾸는 대신 Lua 안에서 검증과 전이를 원자적으로 끝낸다.

### 3-9. 설정 키 (전부 `app.kvs.*`)

ECS에서는 `src/main/resources/application-ecs.yml`이 환경변수를 바인딩한다. 값은 태스크 정의에 있고 비밀값만 SSM SecureString이다.

| 환경변수 | yml 키 | 태스크 정의에 존재 | 기본값 | 의미·제약 |
|---|---|---|---|---|
| `KVS_PROVIDER` | `provider` | **O** (`aws` ×3) | `disabled` | `disabled`/`aws`/`s1`. 다른 값이면 부팅 실패 |
| `KVS_REQUIRED` | `required` | **O** (`true` ×3) | `false` | true인데 provider가 disabled면 부팅 실패 |
| `KVS_EPOCH` | `epoch` | **O** (환경별) | 없음 | provider 활성 시 필수. Redis 값과 정확 비교 |
| `KVS_FENCE_GENERATION` | `fence-generation` | **O** (`1` ×3) | 없음 | provider 활성 시 필수. DB 초기화·복원 때 증가 |
| `KVS_LEASE_SECRET` | `lease-secret` | **O — SSM SecureString** | 없음 | provider 활성 시 필수. Base64 디코드 후 32바이트 이상. 값은 여기 적지 않는다 |
| `KVS_CHANNEL_NAME_PREFIX` | `channel-name-prefix` | **O** (환경별) | `skix-kvs-dev-` | `prefix + serial`로 채널 조회·생성. 영숫자·`_.-`만 허용 |
| `KVS_ENVIRONMENT_TAG` | `environment-tag` | **O** (환경별) | `dev` | 채널 태그 `Environment` 값 |
| `KVS_MASTER_ROLE_ARN` | `master-role-arn` | **O** (환경별) | 없음 | **provider가 무엇이든 필수**(§5의 10번) |
| `KVS_VIEWER_ROLE_ARN` | `viewer-role-arn` | **O** (환경별) | 없음 | 동상 |
| `KVS_CREDENTIAL_DURATION_SECONDS` | `credential-duration-seconds` | X | `900` | `[900, 3600]`. ECS task role 경유는 role chaining이라 AWS가 1시간을 강제한다 |
| `KVS_RELAY_FORCE_QUIT_SECONDS` | `relay-force-quit-seconds` | X | `70` | 서버 Relay cutoff |
| `KVS_RELAY_MAX_DURATION_MS` | `relay-max-duration-ms` | X | `80000` | 기기 자체 종료 안전망. **서버 cutoff보다 커야** 하고 기기 수락 범위 `[10s, 5400s]` 안이어야 한다. provider=s1이면 90초 미만이어야 한다 |
| `KVS_MAPPING_RELAY_MAX_DURATION_MS` | `mapping-relay-max-duration-ms` | X | `600000` | 맵핑 세션 전용. 600초 초과 시 부팅 WARN(현장 펌웨어 상한이 600초라서) |
| — | `region` | X | `ap-northeast-2` | |
| — | `min-credential-validity-seconds` | X | `10` | 잔여 유효시간이 이보다 짧으면 start 미발행 |
| — | `candidate-classification-timeout-seconds` | X | `40` | **`relay-force-quit-seconds`보다 작아야** 한다 |
| — | `start-command-validity-seconds` | X | `15` | **`session-lease-ttl-seconds`보다 작아야** 한다 |
| — | `session-lease-ttl-seconds` | X | `60` | 매 발급(start/renew)의 lease 유효기간 |
| — | `session-lease-renew-command-validity-seconds` | X | `15` | 역시 lease TTL보다 작아야 한다 |
| — | `lease-release-margin-seconds` | X | `45` | 시계오차+지연+기기 종료 여유. force-release 판단에 한 번만 더한다 |
| — | `prepare-timeout-seconds` | X | `30` | |
| — | `watchdog-sweep-interval-ms` | X | `5000` | |
| — | `terminate-retry-interval-seconds` / `terminate-max-attempts` | X | `10` / `6` | 이후 저빈도 재시도와 경보 |
| — | `lease-mismatch-backoff-seconds` | X | `300` | |
| — | `lease-reserving-ttl-seconds` | X | `120` | PERSIST 이전 예약 단계 TTL |
| — | `stop-*-timeout-ms`, `stop-pool-size` | X | `400`/`1000`/`2500`/`10` | 에스원 stop 전용 클라이언트(provider=s1일 때만) |

이 관계 검증은 전부 `KvsProperties.validate()`에서 `@PostConstruct`로 돈다. **위반하면 조용히 넘어가지 않고 기동이 실패한다.** ECS 롤링 배포에서 신규 태스크가 기동에 실패하면 구 리비전이 계속 서비스하므로, 잘못된 설정으로 배포하는 것보다 안전하다.

### 3-10. 확정값 요약

| 항목 | 값 | 근거 |
|---|---|---|
| 시그널링 채널 유형 | `SINGLE_MASTER` | `AwsKvsSessionAdapter` |
| 시그널링 메시지 TTL | 60초 | 상동 |
| 채널 ACTIVE 대기 상한 | 10초 (200ms 폴링) | 상동 |
| 채널 이름 | `{prefix}{serial}` (최대 256자) | 상동 |
| 채널 태그 | `Environment={env}`, `ManagedBy=skix-security` | 상동 + IAM 조건 |
| STS 세션 지속시간 | 900초 | `credential-duration-seconds` |
| session lease | 60초, heartbeat 15초마다 갱신 | `session-lease-ttl-seconds` |
| 강제 해제 여유 | lease 만료 + 45초 (≈ 105초 내 자동 정리) | `lease-release-margin-seconds` |
| 서버 Relay cutoff / 기기 자체 종료 | 70초 / 80초 | 태스크 정의 미지정 → 기본값 |
| ICE 분류 deadline | 40초 | `candidate-classification-timeout-seconds` |
| start 명령 유효시간 | 15초 | `start-command-validity-seconds` |
| 출동 VOD | 명령 수신 시각 t0 기준 `[t0-5초, t0+10초]` 총 15초 | 기기 계약 |

---

## 4. API 계약

전부 `skix-security` 앱 API 호스트(`/security/v1/...`)다. 인증은 앱이 Cognito JWT, 관제가 Admin JWT다.

### 4-1. 세션 생성

```http
POST /security/v1/devices/{serial}/kvs/sessions
Idempotency-Key: <UUIDv4>          # 필수
X-Target-Login-Id: <대상 loginId>   # 관제(Admin)만
```

권한: 구독 중인 홈 멤버, 또는 `CONTROL_ROBOT` 권한을 가진 관리자.

응답은 **HTTP 상태만으로 분기하면 안 되고** `data.resultType`을 함께 본다.

| HTTP | resultType | 의미 |
|---|---|---|
| `201` | `CREATED` | 신규 발급. **자격증명은 이 응답 한 번뿐**이다. `Cache-Control: no-store` |
| `202` | `INITIALIZING` | 같은 키의 원 요청이 아직 처리 중. `retryAfterMs` 후 같은 키로 재호출. sessionId 미노출 |
| `202` | `TERMINATING` | 최초 요청의 기기 전달 결과가 불명확해 종료 중. **자격증명 없음** |
| `200` | `EXISTING` | 같은 키의 세션이 진행 중. `credentialsReplayable:false` |
| `200` | `TERMINAL` | 같은 키의 세션이 이미 종료됨. `resultCode` 포함 |

`201 CREATED` 본문:

```json
{ "result": 200, "success": true,
  "data": {
    "resultType": "CREATED",
    "sessionId": "0f2a7c1e-...",
    "channelArn": "arn:aws:kinesisvideo:ap-northeast-2:...:channel/...",
    "region": "ap-northeast-2",
    "viewer": { "accessKeyId": "ASIA...", "secretAccessKey": "...", "sessionToken": "..." },
    "expiration": 1770000090000
  } }
```

에러:

| 상황 | HTTP | error.code |
|---|---|---|
| 기기 사용 중(KVS 또는 legacy — 상세 비공개) | 409 | `DEVICE_IN_USE` |
| 멱등키를 다른 주체가 사용 | 409 | `IDEMPOTENCY_KEY_CONFLICT` |
| 자격증명 발급 실패 | 502 | `ISSUE_FAILED` |
| 기기 전달 준비 실패 | 502 | `START_DISPATCH_FAILED` |
| 원 요청이 서버 장애로 유실 | 500 | `ABANDONED` |
| 상태저장소 복구 중 | 503 | `RECOVERING` |
| KVS 미활성 환경 | 503 | `KVS_DISABLED` |

실패를 받은 키는 재사용해도 같은 실패가 재현된다. 재시도는 새 키로 한다.

### 4-2. 종료·조회

```http
POST /security/v1/devices/{serial}/kvs/sessions/{sessionId}/close   → 202 (접수, 멱등)
GET  /security/v1/devices/{serial}/kvs/sessions/{sessionId}
```

권한: 세션 생성자 본인 또는 관리자.

```json
{ "data": { "sessionId": "...", "state": "ACTIVE", "connectionType": "P2P" } }
```

`state`는 `PREPARING`/`ACTIVE`/`TERMINATING`/`FINALIZING`/`TERMINAL`이고, `TERMINAL`이면 `resultCode`가 붙는다(§3-8 표). 활성 세션을 확인할 수 없는 경우는 `TERMINAL` 또는 `503 RECOVERING`으로만 응답하며, 내부 저장소 상태를 그대로 노출하지 않는다.

에러: `404 ENTITY_NOT_FOUND`(세션 없음·serial 불일치), `403 ENTITY_ACCESS_DENIED`(생성자 아님), `503 RECOVERING`.

**`503 RECOVERING`을 종료로 해석해 즉시 새 세션을 만들면 안 된다.** 재시도 가능한 일시 상태다.

### 4-3. 출동

```http
GET  /security/v1/devices/{serial}/kvs/sessions/{sessionId}/dispatches/availability
POST /security/v1/devices/{serial}/kvs/sessions/{sessionId}/dispatches    (Idempotency-Key 필수)
GET  /security/v1/devices/{serial}/s1/dispatch/status
```

내부 상태머신(`REQUESTED → UPLOAD_PENDING → UPLOADED → DISPATCHING → DISPATCHED`, 실패 계열 `FAILED`/`DISPATCH_UNKNOWN`/`EXPIRED`)과 `dispatchRequestId`는 **공개하지 않는다.** 앱은 availability + POST + 필요 시 상태 단건 조회만 쓴다.

- `availability`는 **락을 잡지 않는 advisory API**다. 호출이 Redis guard·DB 작업 생성·MQTT 발행을 일으키지 않는다. 버튼 활성화 판단용이며 **polling 용도가 아니다.**
- 동시성·중복 최종 판정자는 **POST**다. ACTIVE 재검증, 멱등키, Redis guard, DB CAS, 에스원 deviceId 중복 방어를 availability 결과와 무관하게 항상 전부 수행한다.
- **접수 후 KVS 세션이 종료돼도 업로드·출동은 계속 진행된다.**
- 에스원이 같은 deviceId에 이미 출동이 있으면 HTTP 201 + `result=false`, `error=ALREADY_EXISTS_DISPATCH`를 준다. 이때 출동을 재호출하지 않고 상태를 한 번만 조회한다. 기존 dispatchNo가 나오면 그 값으로 `DISPATCHED`에 수렴하고, 확인이 안 되면 `DISPATCH_UNKNOWN`으로 남겨 운영 확인 대상으로 둔다.

### 4-4. 기기 MQTT 계약

토픽은 `skmg/security/{serial}/v1/{command|response|status}/{name}`이다.

| 방향 | 이름 | 비고 |
|---|---|---|
| 서버→기기 | `command/kvsSessionStart` | sessionId, fence, channelArn, region, camera 자격증명, `commandExpiresAt`, `sessionLeaseExpiresAt`, `relayMaxDurationMs` |
| 서버→기기 | `command/kvsSessionLeaseRenew` | **하향 전용, 기기 응답 없음.** 유효 heartbeat 수신이 트리거 |
| 서버→기기 | `command/kvsSessionTerminate` | 멱등. 이미 idle이어도 `kvsSessionClosed`를 재보고해야 한다 |
| 서버→기기 | `command/dispatchVideoUpload` | dispatchRequestId로 멱등 |
| 서버→기기 | `command/undock` | 조그 컨트롤용 도킹 해제. 세션과 무관한 단발 명령 |
| 기기→서버 | `response/kvsSessionStart` | ACK는 "명령 수신"이지 접속 시작이 아니다. 거부 사유 `NOT_STARTED`/`BUSY`/기타 |
| 기기→서버 | `response/kvsSessionTerminate` | **ACK는 종료 증명이 아니다.** 종결은 반드시 `status/kvsSessionClosed`로 확정 |
| 기기→서버 | `status/kvsConnected` | `connectionType` = `P2P`/`RELAY` |
| 기기→서버 | `status/kvsHeartbeat` | 연결 직후 즉시 1회, 이후 15초 주기. `candidateType`, `relayElapsedMs` |
| 기기→서버 | `status/kvsSessionClosed` | 종료 proof |
| 기기→서버 | `status/dispatchVideoUploadCompleted` | — |

식별자 규칙이 중요하다. **`correlationId`는 command↔response를 잇는 전송 계층 ID일 뿐**이고 세션 상태 전이·lease·DB에 쓰지 않는다. 서버는 command마다 새 UUID를 만들고 기기는 그대로 에코백한다. `kvsConnected`/`kvsHeartbeat`/`kvsSessionClosed`/`dispatchVideoUploadCompleted`는 command response가 아니라 **독립 status라 correlationId가 없고**, payload에 `sessionId`와 `fence`를 직접 싣는다.

### 4-5. 내부 전용

```http
POST /internal/v1/devices/{serial}/kvs/force-close     # 바디 없음, 외부 미공개
```

현재 점유 세션을 원자적으로 조회해 기존 정리 경로(`completeFinalize`)로 종결한다. legacy lease MISMATCH이면 성공으로 응답하지 않고 Redis를 격리 상태로 둔다. 이것이 **raw Redis 키 수동 삭제의 대체 수단**이다(§5의 12번).

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다. 나머지 세부는 코드 주석과 테스트가 지킨다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **Redis가 live session의 유일한 권위다.** MySQL의 OPEN/CLAIMED만으로 ACTIVE를 재구성하지 않는다. 제어 read는 replica가 아니라 Lua/EVAL로 primary에서 한다 | DB에 OPEN이 남았다고 세션을 살아 있는 것으로 보면, 실제로는 끝난 기기의 카메라를 계속 잠가 그 기기가 **영구히 `DEVICE_IN_USE`**가 된다. replica stale read로 상태를 읽으면 두 서버가 같은 세션을 동시에 처리한다 |
| 2 | **epoch는 존재 검사가 아니라 값 정확 비교다.** 불일치·부재면 create·멱등 재현·GET·출동·availability를 전부 `503 RECOVERING`으로 fail-closed | 존재만 검사하면 Redis flush 후 누군가 키를 다시 만든 순간 "복구됐다"고 오인한다. 실제로는 모든 세션 상태가 날아간 상태에서 신규 발급이 재개되고, 살아 있는 기기의 lease를 모르는 채 다른 세션이 붙는다 |
| 3 | **`RECOVERING` 중에는 자동 force-release를 하지 않는다.** 종료 증거가 있는 경로(기기 closed 보고, 내부 force-close)만 허용 | DB 값만 보고 lease를 자동 해제하는 것은 기기 생존을 모르는 상태의 추측이다. 실제로 영상을 내보내는 중인 기기의 점유를 풀어 두 세션이 붙는다 |
| 4 | **fence generation은 기본값이 없다.** 미설정이면 부팅 실패. DB 초기화·스냅샷 복원 때 반드시 증가 | 조용히 `1`로 발급되면, 기기가 영속 보관한 `highestProcessedFence`보다 작은 fence가 나가 **그 기기의 모든 start가 stale로 거부**된다. 서버는 정상으로 보이는데 영상만 안 나온다 |
| 5 | **device lease 값은 공개 sessionId가 아니라 HMAC-SHA256이다.** `lock:serial:*`을 legacy와 공유하기 때문 | 공개값이면 legacy 클라이언트가 어디선가 얻은 sessionId를 correlationId로 보내 KVS lease를 갱신하거나 삭제할 수 있다. 값 위조는 카메라 강탈과 같다 |
| 6 | **device lease는 TTL로 풀지 않는다**(발급 성공 후 `PERSIST`). 해제는 `maxIssuedLeaseExpiresAt + 45초` 경과 또는 기기 종료 proof뿐 | TTL 만료로 풀면, 네트워크가 잠깐 끊겨 heartbeat 몇 번을 놓친 멀쩡한 기기의 카메라를 서버가 빼앗는다. 그 상태에서 새 세션이 붙으면 기기 안에서 두 세션이 카메라를 다툰다 |
| 7 | **lease `MISMATCH`이면 삭제하지 않는다.** 경보 1회 후 backoff 재확인 | 남의 lease를 지우는 것은 legacy 스트리밍이나 다른 KVS 세션을 강제 종료시키는 일이다. MISMATCH는 "정리 대상"이 아니라 "조사 대상"이다 |
| 8 | **VIEWER 자격증명은 `201 CREATED` 한 번만 반환한다.** 재조회·멱등 재현으로 복구되지 않고(`credentialsReplayable:false`), 응답에 `Cache-Control: no-store`를 붙이며, 로그·DB·관측 인프라에 원문을 남기지 않는다 | 자격증명을 재현 가능하게 만들면 멱등키 하나로 임의 시점에 AWS 자격증명을 다시 꺼낼 수 있다. 로그에 남으면 로그 열람 권한이 곧 영상 열람 권한이 된다. 정상·외부오류·MQTT오류 전 경로를 실제 비밀값으로 검증하는 테스트가 이것을 고정한다 |
| 9 | **기기 전달(start 발행) 결과가 불명확하면 자격증명을 반환하지 않고 세션을 종료한다**(`202 TERMINATING`) | 기기가 MASTER로 붙지 않은 채 앱에만 자격증명을 주면 앱은 영원히 연결되지 않는 채널에 붙어 재시도를 반복한다. "일단 주고 보자"가 복구 불가 상태를 만든다 |
| 10 | **AWS 설정(master/viewer role ARN, 채널 prefix, 환경 태그, STS 지속시간)은 provider가 `aws`든 `s1`이든 필수**다. AWS issuer 빈도 조건 없이 항상 로드된다 | 원래는 "S1으로 서비스하면서 맵핑만 AWS" 스위치가 있었는데, 그 스위치가 꺼진 구성이 **부팅에 성공하면서 맵핑만 조용히 503**이 됐다. 진단이 가장 어려운 실패다. 조건을 없애 그 상태를 도달 불가로 만들었다. 대가로 "AWS 설정 없이 S1만으로 도는 환경"을 만들 수 없는데, 그건 **부팅에서 막히는 편이 맞다** — 설정이 비면 런타임에 발급이 실패하거나 **잘못된 이름의 채널이 만들어지고, 우리에겐 채널 삭제 권한이 없어 되돌릴 수 없다** |
| 11 | **`kvs_session_history.purpose` 컬럼 ALTER는 애플리케이션 이미지보다 먼저 적용한다** | `insertClaimed`가 이 컬럼을 무조건 나열한다(맵핑 전용 경로가 아니라 **세션 생성 공통 경로**다). 컬럼 없이 새 이미지가 뜨면 `Unknown column 'purpose'`가 전파되어 **일반 라이브뷰를 포함한 모든 KVS 세션 생성이 실패**한다. 역순(ALTER 먼저, 구 이미지)은 안전하다 — 조회가 `SELECT *` 오토매핑이고 `autoMappingUnknownColumnBehavior` 기본값이 NONE이라 미지 컬럼을 무시한다 |
| 12 | **raw Redis 키를 손으로 지우지 않는다.** 즉시 정리가 필요하면 `POST /internal/v1/devices/{serial}/kvs/force-close`를 쓴다 | lease(`lock:serial:`)와 세션 hash(`security:{kvs}:session:`)를 따로 지우면 반쪽만 정리돼 다시 `DEVICE_IN_USE`가 난다. 이 엔드포인트가 그 실수를 원자적으로 대체한다 |
| 13 | **relay 값의 관계 검증은 부팅에서 막는다.** `분류 deadline(40s) < 서버 cutoff(70s) < 기기 한도(80s)`, 그리고 기기 한도는 수락 범위 `[10s, 5400s]` 안 | 범위 밖 값을 실으면 기기가 조용히 **기본값 80초로 되돌린다.** 늘리려던 값이 7.5배 짧아지는데 로그에는 성공으로 보인다. 서버 cutoff가 기기 한도보다 크면 안전망이 정책보다 먼저 발화해 서버가 상황을 모른다 |
| 14 | **출동 원장의 중복 방어는 `active_serial` generated column + UNIQUE다.** terminal 전이 시 자동으로 NULL이 되어 제약이 풀린다 | 일반 컬럼에 UNIQUE를 걸면 진행 중이 아닌 과거 행까지 제약에 걸려 그 기기가 **영구 재접수 불가**가 된다. Redis guard가 유실돼도 이 제약이 최종 방어선이다 |
| 15 | **`DISPATCH_UNKNOWN`은 자동으로도 수동으로도 출동 API를 재호출하지 않는다.** 에스원 timeout·5xx는 `FAILED`가 아니라 `UNKNOWN`이다 | 요청이 처리되지 않았다는 보장이 없다. 실패로 확정해 재호출하면 경비원이 두 번 출동한다. "실패"와 "모름"을 뭉개면 안 된다 |
| 16 | **`KVS_LEASE_SECRET`을 활성 세션이 있는 상태에서 롤링 교체하지 않는다** | 이 값이 `lock:serial:*`의 HMAC 소유권 값을 만들고 검증한다. 구·신 태스크가 서로 다른 secret으로 동시에 돌면 lease를 정상 해제하지 못하고 `MISMATCH`로 격리된다. 교체는 신규 create 차단 → drain → 구 태스크 0개 확인 → 새 값 → 신규 태스크만 기동 순서로만 한다 |
| 17 | **`UNKNOWN_SESSION_CUTOFF` enum 값을 삭제하지 않는다** | 폐기된 65초 컷오프 시절의 legacy 값이라 신규 세션에서는 생기지 않지만, **과거 DB 이력에 남아 있다.** 지우면 이력 조회가 깨진다 |

---

## 6. 앱·기기·관제에 요청한 내용

앱·기기·관제 코드는 직접 수정하지 않았다. 각 담당에게 전달한 계약의 핵심이 아래다. 세 팀 모두 구현을 마쳤으므로(§2-5), 인수자는 **이 계약이 실제 구현과 맞는지 확인하는 역할**이다.

### 6-1. 앱 (iOS·Android) — 필수 항목

1. **create 요청마다 `Idempotency-Key`.** HTTP timeout·`INITIALIZING` 같은 **같은 논리 시도는 같은 키**, terminal 이후 새 시도는 새 키.
2. **HTTP 상태만으로 분기하지 않는다.** `resultType`과 함께 본다(§4-1 표).
3. **`clientId = sessionId`로 VIEWER 연결**하고 한 세션에 PeerConnection 하나만 소유한다.
4. **`201 CREATED` 외에는 자격증명을 복구할 수 없다.** 자격증명을 잃은 `EXISTING` 세션은 close 후 새 세션으로 복구한다.
5. **close `202`는 접수다.** GET으로 `TERMINAL`을 확인한 뒤 새 세션을 만든다.
6. **Relay 종료 후에는 기존 세션 재접속이 아니라 새 세션 생성**이다.
7. `DEVICE_IN_USE` 응답에는 점유자 정보가 없다. **추정해 표시하지 않는다.**
8. 출동 버튼 활성화는 `availability`로 확인하되 **polling 하지 않는다.** 실제 출동 상태가 필요하면 화면 진입·복귀·사용자 새로고침 같은 **명시적 시점에만** `/s1/dispatch/status`를 단건 조회한다.
9. `dispatchRequestId`로 내부 작업을 조회하지 않고, `DISPATCH_UNKNOWN`에서 **자동 재출동하지 않는다.**
10. KVS 대상 cohort는 KVS API만, legacy cohort는 기존 API만 쓴다. **한 번의 시청 동작에서 두 경로를 동시에 호출하거나 실패를 보고 앱이 스스로 legacy fallback을 시작하면 안 된다.**
11. 조그 컨트롤 전 도킹 해제가 필요하면 `POST /security/v1/devices/{serial}/undock`. 응답은 접수이며 완료는 기기 도킹 상태 조회로 확인한다.

### 6-2. A1 기기 — 필수 항목

1. **legacy/KVS 카메라 상호배타**를 보장한다. 판정과 점유 사이의 경합으로 두 세션이 시작되면 안 된다. 사용 중이면 두 번째 start는 `BUSY`로 거부한다.
2. **가장 큰 처리 fence를 영속 저장**해 재부팅 후에도 stale start를 수락하지 않는다. 동일 활성 `(sessionId, fence)` start만 멱등 성공한다. terminate 선도착·만료·BUSY로 시작 불가가 확정된 세션은 **나중에 카메라가 비어도 시작하지 않는다.**
3. `NOT_STARTED`는 **영속 proof가 있을 때만** 보낸다(서버가 이 값을 "아무것도 시작 안 됐다"는 즉시 종결 근거로 쓴다).
4. start 수락 조건은 `now < commandExpiresAt` **AND** `now < sessionLeaseExpiresAt` **둘 다**다.
5. **session lease 만료 판단은 모노토닉 클록으로** 한다. 유효한 `kvsSessionLeaseRenew`(sessionId·fence 일치, 미만료, 새 값이 기존보다 큼)만 적용한다.
6. **갱신을 못 받아 `sessionLeaseExpiresAt`이 지나면 서버 terminate 없이도 스스로 PeerConnection과 카메라를 종료**한다. 이것이 lease 모델의 핵심이다.
7. `clientId = sessionId`인 **첫 offer만** 수락한다.
8. 연결 직후 heartbeat 1회, 이후 15초 주기. Relay 타이머와 `relayElapsedMs`는 모노토닉·sticky로 관리한다.
9. `relayMaxDurationMs`가 없거나 허용 범위 밖이면 보수적 기본값 80초를 쓴다.
10. terminate는 멱등 처리하고, **실제 자원 종료 후** `kvsSessionClosed`를 보고한다. 재부팅 후 이전 세션의 terminate가 와도 ACK와 closed를 보고한다.
11. `dispatchVideoUpload`는 dispatchRequestId로 멱등 처리하고, 명령 수신 시각 t0 기준 `[t0-5초, t0+10초]` **15초 VOD를 항상 생성**한다. KVS 송출과 로컬 VOD 생성은 병행한다. **KVS 세션이 종료돼도 이미 시작한 업로드는 계속**한다.
12. `command/undock`(빈 request)은 기존 legacy 도킹 해제 루틴으로 처리하고 correlationId를 에코백한다(멱등).

### 6-3. 관제 웹 — 필수 항목

1. 모든 KVS 호출에 **Admin JWT + `X-Target-Login-Id`**(대상 기기 소유자 loginId). 세션 생성·출동 접수에는 `Idempotency-Key`도.
2. 관리자에게 **`CONTROL_ROBOT` 권한**이 필요하다.
3. **기존 `useWebRTC`·`/signal`·`/streaming` 재사용 불가.** 기존 구현은 Socket.IO + 기기 offer/웹 answer이고, KVS는 AWS signaling + 웹 offer/기기 answer다. Safe Care 안에 KVS 전용 API adapter와 WebRTC composable을 새로 두고 **기존 legacy Live View 컴포넌트는 건드리지 않는** 구성이 안전하다.
4. **자격증명·응답 body·signed WSS URL을 console·APM·analytics·오류 리포트에 남기지 않는다.** 공통 Axios 모듈이 응답 객체를 console에 출력하므로 KVS create 응답은 별도 redaction 또는 로깅 제외가 필요하다.
5. 자격증명을 **Pinia persisted state·localStorage·sessionStorage·IndexedDB에 저장하지 않는다.** 현재 탭의 연결 객체 메모리에서만 갖고 종료 시 참조를 제거한다.
6. 관제 단말 네트워크에서 다음이 열려 있어야 한다 — 백엔드 HTTPS, `*.kinesisvideo.<region>.amazonaws.com`의 HTTPS/WSS 443, KVS STUN/TURN의 UDP/TCP 443. **사내 VDI·방화벽이 UDP를 막아도 TURN TCP 443이 되는지 실제 관제망에서 확인**해야 한다. 백엔드 배포 성공만으로 영상 연결이 보장되지 않는다.
7. 영상 autoplay가 브라우저 정책에 막힐 수 있으므로 사용자 클릭 흐름 안에서 재생을 시작한다.
8. cohort·capability가 있는 기기에만 기능을 노출하고, **세션 생성 실패를 보고 임의로 legacy fallback을 시작하지 않는다.**
9. 오류 화면에 자격증명·signed URL·타 세션 ID·소유자 정보를 포함하지 않는다.

---

## 7. 배포 방법

실행 가능한 전문은 **부록 B**에 있다. 여기서는 순서와 이유만 적는다.

### 7-1. 순서 (역순 금지)

```
1. SSM KVS_LEASE_SECRET 생성            ← 없으면 태스크가 기동 못 한다
2. IAM (task 정책 / MASTER·VIEWER 역할) ← 없으면 발급이 런타임에 AccessDenied
3. RDS 스냅샷
4. DDL (테이블 생성 또는 purpose ALTER) ← 반드시 이미지보다 먼저 (§5의 11번)
5. Redis 사전 조사 (stray key 확인)      ← 지우기 전에 보는 단계
6. 태스크 정의 수동 등록 → 이미지 push → 서비스 적용
   이 시점에 KVS API는 전부 503 RECOVERING 이어야 정상
7. Redis epoch SET                      ← 마지막. 여기서 KVS가 켜진다
8. 내부 시험 serial 로 smoke
9. 관찰 기간 → 앱·관제 cohort 단계적 오픈
```

**7번을 마지막에 두는 이유**: 새 태스크가 정상 기동하는지, 비KVS 기능이 멀쩡한지를 **KVS가 꺼진 상태에서 먼저** 확인하기 위해서다. epoch를 먼저 넣으면 기동 직후부터 세션 발급이 열려, 문제가 생겼을 때 이미 기기에 자격증명이 나가 있는 상태에서 롤백해야 한다.

**4번을 6번보다 먼저 두는 이유**: §5의 11번. 역순이면 일반 라이브뷰까지 전멸한다.

### 7-2. 배포 후 최소 확인

```bash
# epoch 미설정 상태 — KVS API는 503 이어야 한다
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "Idempotency-Key: $(uuidgen)" -H "Authorization: <Admin JWT>" \
  "https://<host>/security/v1/devices/<serial>/kvs/sessions"     # 기대 503

# epoch 설정 후 — 내부 시험 serial 로 생성
# 기대 201, data.resultType=CREATED, channelArn 이 이 환경 계정·prefix
```

- DB: `SELECT COUNT(*) FROM security.kvs_session_history WHERE fence IS NULL;` → **0**
- DB: 새 행의 `fence`가 `10^12` 이상
- Redis: `security:{kvs}:session:<serial>` 존재, `lock:serial:<serial>` 값이 `kvs|v1|`로 시작
- 로그·CloudWatch에 자격증명 원문 0건
- legacy 스트리밍과 legacy 출동 회귀 없음

### 7-3. 롤백

**1차 수단은 스키마 되돌리기가 아니라 (a) 앱·관제 cohort 차단, (b) 이전 ECS 태스크 정의 복귀다.**

- **KVS 기능 문제 · 정상 drain 가능**: cohort OFF → **epoch는 즉시 지우지 않는다**(지우면 renew와 자동 force-release까지 막혀 active lease가 남는다) → 기존 세션 close·lease 만료 대기 → session/deadline과 `kvs|v1|` lease가 0인지 확인 → 이전 태스크 정의로 복귀 → **DB 스키마는 그대로 둔다** → 다음 배포에서 epoch를 새 값으로 바꾼다(DB를 초기화·복원했다면 fence generation도 증가).
- **플랫폼 전체 장애**: cohort 즉시 OFF → 이전 태스크 정의로 우선 복귀 → **최소 `session lease TTL + margin`(약 105초)이 지나기 전에 KVS lease를 임의 삭제하지 않는다**(이미 발급된 기기는 그 시각에 자체 종료한다) → 이후 모든 Redis shard에서 KVS 키와 `kvs|v1|` lease만 **선별** 정리 → legacy `lock:serial:*`과 `streaming-upload:*`은 보존 → DB는 감사 원장으로 유지.

**금지**: active session이 있는 상태의 lease secret 롤링 교체 / epoch 삭제 후 즉시 재생성 / `lock:serial:*` 일괄 삭제 / Redis `FLUSHALL`·`FLUSHDB` / 에스원 출동 결과가 불명확한 작업의 자동 재호출 / 장애 중 DB 스키마 즉흥 롤백 / 이전 리비전과 그때의 ECR digest를 모르는 채 하는 롤백.

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **3환경 실태 확인.** 각 환경에서 (a) ECS 서비스가 실제로 쓰는 태스크 정의 리비전과 그 안의 `KVS_*` 값, (b) SSM `KVS_LEASE_SECRET` 존재, (c) IAM 역할 3종 존재와 신뢰 정책, (d) DB에 두 테이블과 `purpose` 컬럼 존재, (e) Redis `security:{kvs}:epoch` 값. **이걸 모르면 아래 어떤 판단도 못 한다** | 백엔드 | VDI에서. 명령은 부록 B-1 |
| 2 | 확인 결과 **stg가 미활성**이면 부록 B-2~B-5 순서로 검증계 활성화 후 최소 24시간 관찰 | 백엔드 | 순서 역전 금지 |
| 3 | **보안성검토 제출 여부·결과 확인**, 그리고 제출 자료가 에스원 KVS 전제인데 형상이 자사 AWS인 점의 정정·재검토 필요 여부 확인 | 백엔드 + 보안 담당 | 차이는 부록 C 마지막 표 |
| 4 | **cohort 오픈 주체 합의.** 서버에 cohort 스위치가 없으므로 단계적 오픈·즉시 중단을 앱·관제 담당이 실행한다. 누가 무엇을 눌러 끄는지 사전에 정해 둔다 | 백엔드 + 앱·관제 | §2-5 |

### 8-2. 운영계 릴리스 게이트 (전부 통과 전 prd 활성화 금지)

| # | 확인할 것 | 상대 |
|---|---|---|
| 5 | 검증계에서 전체 시나리오 통과 — 내부 serial create `201`, 채널 자동 생성·태그·ACTIVE, DB fence 정상, Redis 세션·lease, MQTT start 필드, **P2P 연결**, heartbeat→renew→close→TERMINAL 수렴, **기기 무응답 후 `LEASE_EXPIRED` 자동 정리**, **forced-TURN과 Relay cutoff**, 출동 접수·업로드·에스원 접수, legacy 회귀 없음 | 백엔드 + 기기 + 앱 |
| 6 | 검증계 최소 24시간 관찰 Go 조건 — 5xx/`RECOVERING` 비정상 증가 없음, watchdog backlog 누적 없음, Redis eviction 0, 설명되지 않는 lease MISMATCH 0, KVS/STS `AccessDenied` 0 | 백엔드 |
| 7 | **관제망에서 TURN TCP 443 실제 연결 확인.** 사내 VDI·방화벽이 UDP를 막는 환경이라 백엔드만 정상이어도 영상이 안 붙을 수 있다 | 관제 + 인프라 |
| 8 | signaling channel 쿼터, Create/Describe API 쿼터, STS AssumeRole 쿼터가 운영 기기 수를 감당하는지. **채널은 기기 시리얼당 1개씩 자동 생성되므로 cohort를 넓히는 만큼 늘어난다** | 인프라 |
| 9 | 운영 변경 창 확보, 롤백 결정권자와 중단 기준 사전 합의, 이전 태스크 정의 ARN과 ECR digest 확보, RDS 스냅샷 | 백엔드 + 운영 |
| 10 | 단계적 cohort 오픈(사내 → 1% → 5% → 25% → 100%), 각 단계 최소 30~60분 관찰 후 **담당자 승인**. 자동 확대 금지 | 백엔드 + 앱·관제 |

### 8-3. 후속 (이 기능을 막지는 않음)

| # | 항목 |
|---|---|
| 11 | **Relay 정책값 확정.** 지금은 태스크 정의에 없어서 보수적 기본값(서버 70초 / 기기 80초)이다. 이 값은 에스원 90초 하드 한도에 맞춘 것이고 **자사 AWS에는 그 한도가 없다.** 2분·3분으로 올릴지 정하고, 올릴 때 `서버 relay-force-quit < 기기 relay-max-duration` 관계를 유지한다(예: 120s < 130s, 180s < 190s). 재빌드 없이 환경변수만 바꾸면 된다 |
| 12 | **기기 relay 수락 상한 90분 반영.** 서버 코드 천장은 이미 5,400,000ms로 열려 있는데 기기(`liveliew`)는 아직 600,000ms다. **반드시 기기 배포가 먼저**다 — 서버가 먼저 큰 값을 보내면 기기가 범위 밖으로 보고 기본값 80초로 되돌려 **오히려 짧아진다.** 기기 반영 확인 후 `KVS_MAPPING_RELAY_MAX_DURATION_MS`만 올리면 된다 |
| 13 | **에스원 종료 통보 경로의 처분 결정.** `provider=aws`에서는 `S1KvsApiClient`·`S1KvsStopApiClient`가 아예 로드되지 않아 잠들어 있다. 에스원 provider로 되돌릴 계획이 없다면 제거를 검토할 수 있다. 다만 제거 전에 §5의 10번(AWS 설정 필수화) 때문에 남아 있는 provider 분기 구조를 함께 봐야 한다 |
| 14 | **provider 전환 시 drain 규칙.** `app.kvs.provider`를 바꾸는 배포는 활성 KVS 세션이 전부 종결된 뒤에 한다. 발급 provider와 finalize 시점 provider가 다르면 에스원 stop 통보가 누락된다(`providerType` 게이트가 오발송은 막지만 누락은 에스원 자체 만료로만 수렴한다) |
| 15 | **관측 대시보드 구성.** `kvs.watchdog.backlog`와 `kvs.watchdog.oldest.delay`는 **인스턴스 합계가 아니라 max**로 집계해야 한다(모든 replica가 같은 Redis 스냅샷을 본다). 그 밖에 `RECOVERING`·`DEVICE_IN_USE`·`NOT_STARTED`·`BUSY`·`LEASE_EXPIRED`·`HEARTBEAT_LOST`·`RELAY_TIME_LIMIT` 추이, KVS/STS AccessDenied, 채널 수·TURN minutes·signaling messages·data transfer 비용 |
| 16 | **개발계에서 쓰는 `AmazonKinesisVideoStreamsFullAccess`를 검증·운영 MASTER/VIEWER 역할에 연결하지 않는다.** 부록 B-2-2의 최소 권한 정책을 쓴다. 배포 후 정리 단계에서 임시 FullAccess가 남아 있지 않은지 확인한다 |
| 17a | **일반 라이브뷰의 계약 조회 의존 정리 검토.** `provider=aws`인데도 `KvsLiveViewService`가 발급 전에 계약번호(`platformPort.resolveS1DeviceId`)와 icstNo(`contractResolveService.resolve(...).requireIcstNo`)를 조회하고, 실패하면 `ISSUE_FAILED`로 끝낸다. 이 값들은 자사 AWS 발급에서 **STS RoleSessionName 장식에만** 쓰인다(`kvs-master-{계약번호}`, `kvs-viewer-{loginId}`). 즉 (a) 계약 조회 장애가 자사 AWS 라이브뷰까지 막고, (b) **loginId가 STS 세션 이름에 들어가 자사 CloudTrail에 남는다.** 원격 맵핑은 이미 이 조회를 건너뛴다. 일반 세션도 건너뛸지는 구독·계약 검증을 어디서 보장할지(구독 인터셉터가 이미 막는지)와 함께 판단해야 하므로 설계 판단으로 남긴다 |
| 17 | **Redis 운영 설정 확인** — `maxmemory-policy=noeviction`, Multi-AZ/failover, 자동 백업과 보존 기간, TLS. Redis가 KVS의 유일한 권위이므로 eviction이 곧 세션 유실이다 |

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| **"영상이 안 나와요"** | 먼저 `GET .../kvs/sessions/{sessionId}`로 `state`를 본다. `PREPARING`에 머물면 기기에 start가 안 갔거나 기기가 응답이 없는 것, `ACTIVE`인데 화면이 검으면 WebRTC 연결 문제(ICE·방화벽)다 |
| **`503 RECOVERING`이 나온다** | Redis `security:{kvs}:epoch` 값과 태스크 정의 `KVS_EPOCH`이 정확히 같은지 본다. **공백·개행·대소문자가 달라도 차단된다.** 키가 없으면 아직 활성화 전이거나 Redis가 flush된 것이다. 임의로 `SET` 하기 전에 활성 세션·기기 상태를 먼저 확인한다 |
| **`502 ISSUE_FAILED`** | 두 갈래다. (a) 발급 전 준비 실패 — **provider=aws여도 일반 라이브뷰는 계약번호·icstNo를 먼저 조회**하므로 계약 정보가 없거나 플랫폼·계약 조회가 일시 실패하면 여기서 끝난다. 로그 `KVS issue preparation failed`. (b) 발급 자체 실패 — STS AssumeRole·채널 생성·ACTIVE 대기(10초) 실패. 로그 `KVS issue failed`, CloudTrail의 AccessDenied를 함께 본다 |
| **`409 DEVICE_IN_USE`가 안 풀린다** | `lock:serial:<serial>` 값을 본다. `kvs\|v1\|`로 시작하면 KVS가 점유 중, 아니면 legacy가 점유 중이다. **직접 지우지 말고** `POST /internal/v1/devices/{serial}/kvs/force-close`를 쓴다 |
| **세션이 `TERMINATING`에 머문다** | 정상적으로는 `maxIssuedLeaseExpiresAt + 45초`(기본 105초 내외) 안에 `LEASE_EXPIRED`로 자동 정리된다. 그 전의 저빈도 재발행과 lease 유지는 **정상 정책**이다. 시간을 넘겨도 남아 있으면 워치독 자체 장애(로그의 `watchdog sweep failed`)를 먼저 의심한다 |
| **lease `MISMATCH` 경보** | 현재 `lock:serial` 값을 확인해 legacy인지 다른 KVS 주체인지 조사한다. **다른 주체 값을 덮어쓰거나 삭제하지 않는다.** 경보는 300초 backoff로 중복 억제된다 |
| **세션 이력 조회** | `SELECT session_id, serial, login_id, purpose, status, result_code, connection_type, relay_duration_ms, requested_at, closed_at FROM security.kvs_session_history WHERE serial = '<serial>' ORDER BY id DESC LIMIT 10;` |
| **출동이 `DISPATCH_UNKNOWN`** | 에스원 실제 상태와 dispatchNo, 아웃바운드 호출 결과, `kvs_dispatch_job` 행을 확인한다. **출동 API를 자동으로도 수동으로도 무조건 재호출하지 않는다** |
| **출동 작업 조회** | `SELECT dispatch_request_id, serial, session_id, status, result_code, dispatch_no, requested_at, updated_at FROM security.kvs_dispatch_job WHERE serial = '<serial>' ORDER BY requested_at DESC LIMIT 5;` |
| **`fence IS NULL` 행이 보인다** | 불변식 위반이다. 커밋된 NULL fence는 없어야 한다. `SELECT COUNT(*) ... WHERE fence IS NULL;`이 0이 아니면 조사 대상이다 |
| **부팅 실패** | `KvsProperties.validate()`의 예외 메시지가 어떤 설정의 어떤 관계를 어겼는지 그대로 말해 준다. `KvsFenceBootGuard` 실패는 "DB만 롤백했는데 fence generation을 안 올렸다"는 뜻이다 |
| **`purpose` 관련 오류** | `Unknown column 'purpose'`가 보이면 ALTER를 안 하고 이미지를 먼저 올린 것이다. **일반 라이브뷰까지 전부 실패한다.** ALTER를 적용하면 재배포 없이 바로 복구된다 |
| **비용이 튄다** | 채널은 **기기 시리얼당 1개씩 자동 생성**되고 우리에겐 삭제 권한이 없다. cohort를 넓힌 만큼 늘어난다. signaling channel 수, TURN minutes, signaling messages, data transfer를 본다 |
| **로그 키워드** | `KVS aws session issued`(발급), `KVS issue preparation failed`(계약 조회 실패), `KVS issue failed`(발급 실패), `watchdog sweep failed`(워치독 장애), `KVS issuer not configured`(provider·설정 불일치), `mapping relay ... exceeds pre-90m firmware ceiling`(기기 미반영인데 값만 올림) |

---

## 부록 A. 핵심 커밋 (시간순)

저장소는 전부 `skix-security`다. **아래 전부 dev·stg·main 세 브랜치에 반영되어 있다**(2026-09-22 실측).

| 날짜 | 커밋 | 내용 |
|---|---|---|
| 07-20 | `3e9a6cb` | **AWS KVS 실시간 영상 및 출동 오케스트레이션 구현** (최초) |
| 07-20 | `d380ecf` | 만료 자격증명 차단, 출동 설정 분기 제거 |
| 07-20 | `2e2a5a7` | KVS 출동 외부 계약 확정 |
| 07-20 | `5bc1a42` | 설계·연동 문서 정리(`docs/kvs-*`) |
| 07-20 | `ac0c60c` | lease secret 연결 및 개발계 활성화(태스크 정의) |
| 07-20 | `9e4a116` / `9da2eb2` / `8779f61` | ECS 배포 시 태스크 정의 리비전 반영, 이미지 아카이브 추적 제외, 수동 배포 흐름 유지 |
| 07-20 | `99e9185` | 개발계 채널 자동 생성(자사 AWS 발급 경로의 시작) |
| 07-20 | `d46b998` | 조그 컨트롤을 위한 도킹 해제(undock) 명령 |
| 07-22 | `6522113` | **renewable session lease 도입** — 기기 무응답 시 서버 lease 영구 점유 해소 |
| 07-22 | `425c94f` | P2P heartbeat의 워치독 스케줄 무한 연기 결함 수정 + 출동 설정 fail-fast |
| 07-22 | `3919b76` | 폐기된 cutoff 잔재 정리 |
| 07-22 | `c6efec3` | 기기 가이드에 lease 만료 자체 종료의 종료 사유 명시 |
| 07-23 | `1ccdbcc` | 잔재 정리 — CONNECT_TIMEOUT 제거, lease 만료 reason 정정, VOD 확장자 검증 보완 |
| 07-23 | `3bbe579` | **`RECOVERING` 중 자동 force-release 차단** |
| 07-27 | `ad9a118` | 출동 공개 API 단순화 — `availability` 추가, 작업 조회 API 제거 |
| 07-28 | `4b7747d` | 출동 상태 조회 수정 |
| 07-29 | `7fadd79` | 관제 웹 연동 가이드 |
| 07-31 | `838f196` | Redis 권위 모델 전환 설계 변경서 |
| 07-31 | `f391246` | **세션 권위를 Redis로 단일화 + dev·stg·prd 태스크 정의를 `aws`로 전환** (epoch·fence generation·채널 prefix·환경 태그·역할 ARN 주입) |
| 08-04 | `e62a482` | KVS 종료 시 에스원 `livevideo/stop` 통보 — at-most-once best-effort |
| 08-04 | `c0d23b0` | stop 통보 리뷰 반영 — 누락 창 축소, 워치독 지연 상한 |
| 08-04 | `7d57122` | stop 서킷브레이커 warm 상한 정정 (window 5) |
| 08-05 | `ee150b1` | stop 릴리스 게이트 상태 갱신 |
| 08-10 | `36161a5` | 에스원 전문 실측 반영 — `expiration` 잉여 Z, `deviceid` 소문자, `result` boolean |
| 08-10 | `f4f5d43` | stop 릴리스 게이트 종료 — cross-account signaling 권한 실측 |
| 08-11 | `0b67ac6` | 에스원 정정 반영 — `deviceid` 이중 표기 제거, `expiration` Z 확정 포맷 |
| 08-11 | `a8d9d03` | stop `NO_ACTIVE_SESSION`을 멱등 성공으로 처리 |
| 08-18 | `71caaa9` | **라이브뷰 relay 값 범위 검증 추가**(범위 밖이면 부팅 실패) + 맵핑 천장 90분 선반영 |
| 08-19 | `f6d114b` | relay 기본값을 현장 펌웨어 상한으로 되돌림 |
| 08-19 | `493e068` | **AWS issuer를 provider와 무관하게 항상 로드**(§5의 10번) |

08-13~08-19의 원격 맵핑 전용 커밋 8건(`8d84aea`, `b59a19c`, `b284d23`, `02d91dd`, `1183e68`, `20be6b8`, `ba84854`, `146f474`)도 같은 기간에 들어갔으나 맵핑 기능 자체는 별도 문서의 주제다. 다만 그중 `20be6b8`(맵핑 전용 도킹 해제 제거)과 위 `71caaa9`·`493e068`은 **일반 라이브뷰의 부팅 검증·설정 필수화에 영향을 주므로** 이 문서의 §3-9·§5에 반영되어 있다.

관련 커밋(다른 저장소): `skix-streaming` `280b6da`(08-22) — 관제 Live View 종료 경로 회귀 수정. legacy 경로다.

테스트: `src/test/java/com/skix/security/kvs/` 36개 파일, `src/test/java/com/skix/security/infra/kvs/` 5개 파일(저장소 전체 테스트 파일 61개). Testcontainers로 실제 MySQL·Redis를 띄워 상태전이와 CAS를 검증하고, HMAC lease 위조(`LeaseHmacForgeryTest`), fence 부팅 가드(`KvsFenceBootGuardTest`), 자격증명 비노출(`MqttPublisherSecretZeroTest`, `RequestLoggingFilterSecretZeroTest`, `S1KvsApiClientSecretZeroTest`), relay 범위 검증(`KvsRelayRangeValidationTest`)을 각각 고정한다. 저장소 전체 테스트는 2026-08-31 시점 437건 통과가 마지막 기록이다.

---

## 부록 B. 검증계·운영계 활성화 런북 (실행 가능 전문)

이 부록만 보고 검증계·운영계에서 KVS를 처음 켤 수 있도록 썼다. **명령은 전부 회사 VDI 또는 AWS CloudShell에서 실행한다**(로컬 맥에서는 AWS에 도달하지 않는다).

### B-0. 기본 원칙

1. **검증계 선행.** 검증계 전체 시나리오와 관찰 기간을 통과하기 전 운영계에 적용하지 않는다.
2. **백엔드 배포와 고객 활성화를 분리한다.** 백엔드를 먼저 배포하고 내부 serial만 검증한다. 앱·관제 cohort는 마지막에 단계적으로 연다.
3. **운영계에서 전체 Redis flush와 legacy 키 일괄 삭제를 금지한다.** KVS와 legacy가 같은 Redis의 `lock:serial:*`을 공유한다.
4. **Redis가 live session의 유일한 권위다.** Redis epoch가 태스크 정의의 `KVS_EPOCH`과 다르면 KVS create·replay·GET·출동은 `503 RECOVERING`이어야 한다.
5. **DB/Redis 초기화 또는 과거 복원 시 `KVS_FENCE_GENERATION`을 증가**시키고 `KVS_EPOCH`을 새 값으로 바꾼다.
6. 적용할 태스크 정의 리비전과 이전 리비전을 기록한다. 수동 배포가 `latest` 태그를 쓰므로 **ECR digest도 따로 기록**해 실제 적용 이미지를 추적한다.
7. 스키마는 장애 중 즉흥적으로 되돌리지 않는다. 롤백 1차 수단은 cohort 차단과 이전 태스크 정의 복귀다.

**환경 기준값**

| 구분 | 검증계 | 운영계 |
|---|---|---|
| AWS 계정 | `087432099373` | `779846811758` |
| 리전 | `ap-northeast-2` | `ap-northeast-2` |
| provider / required | `aws` / `true` | `aws` / `true` |
| epoch | `stg-2026-07-31-01` | `prd-2026-07-31-01` |
| fence generation | `1` | `1` |
| 채널 prefix | `skix-kvs-stg-` | `skix-kvs-prd-` |
| 환경 태그 | `stg` | `prd` |
| lease secret | `/skix-security/stg/KVS_LEASE_SECRET` | `/skix-security/prd/KVS_LEASE_SECRET` |
| ECS 클러스터 | `skmg-airbot-stg-ecs-cluster` | `skmg-airbot-prd-ecs-cluster` |
| ECS 서비스 | `skmg-airbot-stg-security-svc` | `skmg-airbot-prd-security-svc` |
| 태스크 정의 family | `skix-security-stg-tdef` | `skix-security-prd-tdef` |
| Redis config endpoint | `clustercfg.skmg-benjamin-stg-redis.gkhojn.apn2.cache.amazonaws.com:33379` | `clustercfg.skmg-benjamin-prd-redis.xskvtx.apn2.cache.amazonaws.com:33379` |

> `fence generation=1`은 **그 환경에서 KVS fence가 외부 기기에 발급된 적이 전혀 없고 DB를 새로 시작할 때만** 유효하다. 시험 발급 이력이 조금이라도 있으면 기존 최댓값을 확인하고 더 큰 값으로 바꾼다.

**승인 전 산출물 체크리스트**

- [ ] 적용 Git commit과 전체 테스트 결과
- [ ] 실제 push된 ECR image digest
- [ ] 변경 전 ECS 태스크 정의 ARN
- [ ] 변경 후 태스크 정의 JSON/ARN
- [ ] RDS 스냅샷 식별자와 완료 시각
- [ ] DDL 실행 결과와 `SHOW CREATE TABLE` 증적
- [ ] Redis parameter group·Multi-AZ·backup·TLS 확인 결과
- [ ] 환경별 epoch/fence generation 결정 기록
- [ ] IAM role·policy·trust relationship 검증 결과
- [ ] SSM SecureString 검증 결과
- [ ] 내부 시험 serial 목록
- [ ] 배포 담당자·검증 담당자·롤백 결정권자
- [ ] 검증계 결과와 운영계 Go/No-Go 승인

---

### B-1. 먼저 현황부터 실측한다

무엇을 해야 하는지는 현재 상태에 달렸다. 아래를 먼저 돌린다(`ENV`만 `stg`/`prd`로 바꾼다).

```bash
export AWS_REGION=ap-northeast-2
export ENV=stg
export ACCOUNT=087432099373        # prd: 779846811758

# 1) ECS 서비스가 실제로 쓰는 태스크 정의
aws ecs describe-services --region "$AWS_REGION" \
  --cluster "skmg-airbot-${ENV}-ecs-cluster" \
  --services "skmg-airbot-${ENV}-security-svc" \
  --query 'services[0].{taskDef:taskDefinition,desired:desiredCount,running:runningCount}' \
  --output table

# 2) 그 태스크 정의의 KVS 설정 (실제 적용값)
TD=$(aws ecs describe-services --region "$AWS_REGION" \
      --cluster "skmg-airbot-${ENV}-ecs-cluster" \
      --services "skmg-airbot-${ENV}-security-svc" \
      --query 'services[0].taskDefinition' --output text)
aws ecs describe-task-definition --region "$AWS_REGION" --task-definition "$TD" \
  --query 'taskDefinition.containerDefinitions[0].{env:environment,secrets:secrets}' \
  | grep -i kvs

# 3) SSM lease secret 존재 여부 (값은 출력하지 않는다)
aws ssm get-parameter --region "$AWS_REGION" \
  --name "/skix-security/${ENV}/KVS_LEASE_SECRET" \
  --query 'Parameter.{Name:Name,Type:Type,Version:Version}' --output table

# 4) IAM 역할 3종 존재 여부
for R in "skmg-airbot-${ENV}-role-ecs-task" \
         "skmg-airbot-${ENV}-role-ecs-kvs-master-role" \
         "skmg-airbot-${ENV}-role-ecs-kvs-viewer-role"; do
  echo "--- $R"
  aws iam get-role --role-name "$R" --query 'Role.{Arn:Arn,MaxSession:MaxSessionDuration}' --output text 2>&1
  aws iam list-attached-role-policies --role-name "$R" --query 'AttachedPolicies[].PolicyName' --output text 2>&1
done

# 5) 이미 만들어진 signaling channel 이 있는지
aws kinesisvideo list-signaling-channels --region "$AWS_REGION" \
  --channel-name-condition "ComparisonOperator=BEGINS_WITH,ComparisonValue=skix-kvs-${ENV}-" \
  --query 'ChannelInfoList[].{Name:ChannelName,Status:ChannelStatus}' --output table
```

DB(해당 환경 RDS에 접속):

```sql
SELECT table_name FROM information_schema.tables
 WHERE table_schema = 'security'
   AND table_name IN ('kvs_session_history','kvs_dispatch_job');

SELECT column_name FROM information_schema.columns
 WHERE table_schema = 'security' AND table_name = 'kvs_session_history'
   AND column_name = 'purpose';

SELECT status, COUNT(*) FROM security.kvs_session_history GROUP BY status;
SELECT status, COUNT(*) FROM security.kvs_dispatch_job GROUP BY status;
```

Redis(해당 환경 Redis에 접근 가능한 Amazon Linux 2023 EC2에서):

```bash
redis6-cli --tls -h clustercfg.skmg-benjamin-stg-redis.gkhojn.apn2.cache.amazonaws.com -p 33379 -c \
  GET 'security:{kvs}:epoch'
```

**판정**: 5개(태스크 정의 `aws`·SSM·IAM·DDL·epoch)가 모두 갖춰져 있고 epoch가 위 기준값과 정확히 같으면 그 환경의 KVS는 **이미 켜져 있다.** 하나라도 비면 아래 순서로 진행한다.

---

### B-2. AWS 인프라 선행 작업

#### B-2-1. SSM SecureString (`KVS_LEASE_SECRET`)

환경별로 **서로 다른 값**을 쓴다. Base64 디코드 후 최소 32바이트, 같은 환경의 모든 태스크에는 동일 값을 주입한다.

CloudShell에서 최초 생성(원문을 출력하지 않고, `--no-overwrite`로 기존 값을 보호한다):

```bash
export AWS_REGION=ap-northeast-2
export TARGET_ENV=stg          # 운영계는 prd

(
  set -euo pipefail
  set +x

  : "${AWS_REGION:?AWS_REGION을 먼저 설정하세요.}"
  : "${TARGET_ENV:?TARGET_ENV를 먼저 설정하세요.}"

  PARAMETER_NAME="/skix-security/${TARGET_ENV}/KVS_LEASE_SECRET"
  KVS_LEASE_SECRET="$(openssl rand -base64 32 | tr -d '\n')"

  DECODED_BYTES="$(
    printf '%s' "$KVS_LEASE_SECRET" | openssl base64 -d -A | wc -c | tr -d ' '
  )"

  if [ "$DECODED_BYTES" -lt 32 ]; then
    echo "ERROR: 생성된 secret이 32바이트 미만입니다." >&2
    exit 1
  fi

  aws ssm put-parameter \
    --region "$AWS_REGION" \
    --name "$PARAMETER_NAME" \
    --description "skix-security KVS lease HMAC secret (${TARGET_ENV})" \
    --type SecureString \
    --value "$KVS_LEASE_SECRET" \
    --tier Standard \
    --no-overwrite \
    --tags "Key=Environment,Value=${TARGET_ENV}" "Key=ManagedBy,Value=skix-security" \
    --query 'Version' --output text

  unset KVS_LEASE_SECRET
  echo "Created: ${PARAMETER_NAME} (${AWS_REGION}), decoded-bytes=${DECODED_BYTES}"
)
```

- 고객 관리형 KMS 키를 써야 하는 환경이면 `--key-id <대칭 KMS key ARN 또는 alias>`를 추가한다. 없으면 Systems Manager 기본 관리형 키를 쓴다.
- `ParameterAlreadyExists`는 **정상적인 보호 동작**이다. 확인 없이 삭제하거나 `--overwrite`로 덮어쓰지 않는다.
- 태스크 실행 역할에 해당 파라미터의 `ssm:GetParameters` 권한이, 고객 관리형 키를 쓰면 `kms:Decrypt` 권한이 있어야 한다.
- Parameter Store 파라미터는 **생성한 리전에만 존재**한다. 태스크 정의의 SSM ARN 리전·계정·경로가 전부 일치해야 한다.

검증(원문 미출력):

```bash
AWS_REGION=ap-northeast-2
PARAMETER_NAME=/skix-security/stg/KVS_LEASE_SECRET

aws ssm get-parameter --region "$AWS_REGION" --name "$PARAMETER_NAME" \
  --with-decryption --query 'Parameter.Value' --output text \
  | openssl base64 -d -A | wc -c        # 32 이상이어야 한다

aws ssm get-parameter --region "$AWS_REGION" --name "$PARAMETER_NAME" \
  --query 'Parameter.{Name:Name,Type:Type,Version:Version,ARN:ARN}' --output table
```

**회전이 필요할 때**는 아래 순서로만 한다(§5의 16번). ① 신규 create 차단 → ② 활성 세션과 `kvs|v1|` device lease가 0이 될 때까지 drain → ③ 구 태스크 0개 확인 → ④ 새 32바이트 값을 `put-parameter --overwrite` → ⑤ 새 secret을 주입한 태스크만 기동 → ⑥ epoch·lease·create smoke 통과 후 cohort 재개.

#### B-2-2. IAM

**① ECS task role 정책** — `skmg-airbot-{env}-role-ecs-task`에 붙인다. 정책명 권장값 `SkixKvsStgTaskPolicy` / `SkixKvsPrdTaskPolicy`.

검증계:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AssumeKvsSessionRoles",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": [
        "arn:aws:iam::087432099373:role/skmg-airbot-stg-role-ecs-kvs-master-role",
        "arn:aws:iam::087432099373:role/skmg-airbot-stg-role-ecs-kvs-viewer-role"
      ]
    },
    {
      "Sid": "DescribeManagedSignalingChannels",
      "Effect": "Allow",
      "Action": "kinesisvideo:DescribeSignalingChannel",
      "Resource": "arn:aws:kinesisvideo:ap-northeast-2:087432099373:channel/skix-kvs-stg-*/*"
    },
    {
      "Sid": "CreateAndTagManagedSignalingChannels",
      "Effect": "Allow",
      "Action": [
        "kinesisvideo:CreateSignalingChannel",
        "kinesisvideo:TagResource"
      ],
      "Resource": "arn:aws:kinesisvideo:ap-northeast-2:087432099373:channel/skix-kvs-stg-*/*",
      "Condition": {
        "StringEquals": {
          "aws:RequestTag/Environment": "stg",
          "aws:RequestTag/ManagedBy": "skix-security"
        },
        "ForAllValues:StringEquals": {
          "aws:TagKeys": ["Environment", "ManagedBy"]
        }
      }
    }
  ]
}
```

운영계는 계정을 `779846811758`로, `stg`를 `prd`로 바꾼 동일 구조를 쓴다.

> `CreateSignalingChannel` 요청에 태그 두 개를 함께 보내므로 `TagResource` 권한이 같이 필요하다. **`DeleteSignalingChannel`·`UpdateSignalingChannel`은 주지 않는다.**

**② MASTER/VIEWER 역할** — 이 둘은 ECS가 직접 Assume하는 task role이 **아니다.** 역할 생성 화면에서 `AWS 서비스 → Elastic Container Service Task`를 고르지 말고 **`사용자 지정 신뢰 정책`**을 선택한다.

| 환경 | 역할 | 이름 |
|---|---|---|
| stg | MASTER | `skmg-airbot-stg-role-ecs-kvs-master-role` |
| stg | VIEWER | `skmg-airbot-stg-role-ecs-kvs-viewer-role` |
| prd | MASTER | `skmg-airbot-prd-role-ecs-kvs-master-role` |
| prd | VIEWER | `skmg-airbot-prd-role-ecs-kvs-viewer-role` |

신뢰 정책(검증계, MASTER·VIEWER 동일):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TrustSkixSecurityEcsTaskRole",
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::087432099373:role/skmg-airbot-stg-role-ecs-task" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

운영계는 `arn:aws:iam::779846811758:role/skmg-airbot-prd-role-ecs-task`.

**계정 root Principal(`arn:aws:iam::<account-id>:root`)을 쓰지 않는다.** root 사용자만 의미하는 것이 아니라 계정 전체에 신뢰를 위임하므로 범위가 불필요하게 넓다. 이미 그렇게 만들었다면 `신뢰 관계 → 신뢰 정책 편집`에서 위 정책으로 교체한다.

역할의 **최대 세션 지속 시간은 최소 1시간**으로 둔다(애플리케이션 발급값은 900초).

MASTER 권한 정책(검증계, 정책명 `SkixKvsStgMasterPolicy`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "UseManagedChannelAsMaster",
      "Effect": "Allow",
      "Action": [
        "kinesisvideo:DescribeSignalingChannel",
        "kinesisvideo:GetSignalingChannelEndpoint",
        "kinesisvideo:GetIceServerConfig",
        "kinesisvideo:ConnectAsMaster"
      ],
      "Resource": "arn:aws:kinesisvideo:ap-northeast-2:087432099373:channel/skix-kvs-stg-*/*"
    }
  ]
}
```

VIEWER 권한 정책(`SkixKvsStgViewerPolicy`)은 `ConnectAsMaster`를 `ConnectAsViewer`로 바꾼 동일 구조다. 운영계는 계정과 prefix만 바꾼다.

연결 관계:

| 환경 | 역할 | 정책 |
|---|---|---|
| stg | `skmg-airbot-stg-role-ecs-task` | `SkixKvsStgTaskPolicy` |
| stg | `...-kvs-master-role` | `SkixKvsStgMasterPolicy` |
| stg | `...-kvs-viewer-role` | `SkixKvsStgViewerPolicy` |
| prd | `skmg-airbot-prd-role-ecs-task` | `SkixKvsPrdTaskPolicy` |
| prd | `...-kvs-master-role` | `SkixKvsPrdMasterPolicy` |
| prd | `...-kvs-viewer-role` | `SkixKvsPrdViewerPolicy` |

**개발계에서 쓴 `AmazonKinesisVideoStreamsFullAccess`를 검증·운영 역할에 연결하지 않는다.** 배포 전 IAM Policy Simulator 또는 내부 시험 태스크에서 `Describe → 없으면 Create → ACTIVE 조회 → Tag` 흐름을 검증하고, 실제 호출 권한은 CloudTrail·AccessDenied 로그로 재확인한다.

#### B-2-3. 네트워크·쿼터·관측

- ECS private subnet에서 **STS와 Kinesis Video control plane으로 HTTPS** 통신 가능(NAT 또는 VPC endpoint, DNS resolution).
- 기존 IoT Data Plane / SQS / KMS 경로도 같은 배포에서 회귀 확인.
- signaling channel 쿼터, Create/Describe API 쿼터, STS AssumeRole 쿼터 확인.
- 대시보드/조회 쿼리 준비: ECS desired·running·deployment failure / 애플리케이션 5xx·`RECOVERING`·`DEVICE_IN_USE` / KVS `AccessDenied`·STS 실패·channel ACTIVE timeout / `kvs.watchdog.backlog`(**max**) / `kvs.watchdog.oldest.delay`(**max**) / lease MISMATCH 경보 / `NOT_STARTED`·`BUSY`·`LEASE_EXPIRED`·`HEARTBEAT_LOST`·`RELAY_TIME_LIMIT` 추이 / Redis CPU·latency·eviction·failover / DB connection·error·slow query / signaling channel 수·TURN minutes·signaling messages·data transfer.

---

### B-3. DB 선행 작업

**RDS 스냅샷을 먼저 만든다.**

안전 조건 확인:

```sql
SELECT COUNT(*) FROM information_schema.tables
 WHERE table_schema = 'security'
   AND table_name IN ('kvs_session_history', 'kvs_dispatch_job');

SELECT status, COUNT(*) FROM security.kvs_session_history GROUP BY status;
SELECT status, COUNT(*) FROM security.kvs_dispatch_job GROUP BY status;
```

- 테이블이 없으면 저장소의 `src/main/resources/db/kvs_session_history.sql`, `src/main/resources/db/kvs_dispatch_job.sql`을 그대로 적용한다.
- 과거 시험 테이블이 **비어 있으면** 스냅샷을 만든 뒤 날짜가 포함된 이름으로 rename하거나 승인 후 재생성한다.
- **데이터가 있거나 활성 상태가 있으면 DROP하지 않는다.** 원인부터 확인한다.
- 테이블은 있는데 `purpose` 컬럼이 없으면 아래 additive ALTER를 **이미지 배포보다 먼저** 1회 적용한다(무중단).

```sql
ALTER TABLE `security`.`kvs_session_history`
  ADD COLUMN `purpose` varchar(32) NULL
  COMMENT '세션 용도 - NULL이면 일반 라이브뷰, REMOTE_MAPPING이면 관제 원격 맵핑'
  AFTER `admin_login_id`;
```

적용 후 확인:

```sql
SHOW CREATE TABLE security.kvs_session_history;
SHOW CREATE TABLE security.kvs_dispatch_job;

SELECT COUNT(*) AS invalid_fence FROM security.kvs_session_history WHERE fence IS NULL;
```

필수 확인 항목:

- `kvs_session_history`: `fence_generation BIGINT NOT NULL`, `fence BIGINT NULL`, `uk_kvs_session_fence`, `purpose` 컬럼 존재, `max_issued_lease_expires_at` 컬럼은 **없어야** 한다(Redis에만 있다)
- `kvs_dispatch_job`: generated column `active_serial`, `uk_dispatch_active_serial`
- 최초 생성 직후 `invalid_fence = 0`

---

### B-4. Redis 선행 작업

#### B-4-1. 운영 설정 확인

- 클러스터 모드와 TLS 정상
- Multi-AZ / failover 설정
- 자동 백업과 보존 기간
- **`maxmemory-policy=noeviction`**
- 모든 ECS 태스크가 같은 클러스터를 바라봄
- Redis 장애 시 KVS는 fail-closed하고 비KVS 기능은 계속 기동하는지

접속(각 환경 Redis에 접근 가능한 Amazon Linux 2023 EC2, `redis6-cli`):

```bash
# 검증계
redis6-cli --tls -h clustercfg.skmg-benjamin-stg-redis.gkhojn.apn2.cache.amazonaws.com -p 33379 -c
# 운영계
redis6-cli --tls -h clustercfg.skmg-benjamin-prd-redis.xskvtx.apn2.cache.amazonaws.com -p 33379 -c
```

#### B-4-2. 최초 활성화 전 키 조사

**`FLUSHALL`·`FLUSHDB`·`KEYS *`를 쓰지 않는다.** Redis Cluster에서 `SCAN`은 접속한 노드만 조회하므로, config endpoint 하나에서 대화형으로 `SCAN`한 결과를 전체 키로 판단하면 안 된다. 반드시 **모든 shard primary를 순회**한다.

```bash
export REDIS_HOST=clustercfg.skmg-benjamin-stg-redis.gkhojn.apn2.cache.amazonaws.com
export REDIS_PORT=33379
# 운영계: clustercfg.skmg-benjamin-prd-redis.xskvtx.apn2.cache.amazonaws.com / 33379

# 1) 모든 primary 수집
PRIMARIES=$(redis6-cli --tls -h "$REDIS_HOST" -p "$REDIS_PORT" CLUSTER NODES \
  | awk '$3 ~ /master/ { split($2, a, "@"); print a[1] }')
echo "primaries:"; echo "$PRIMARIES"

# 2) 각 primary 에서 대상 패턴 SCAN
for NODE in $PRIMARIES; do
  H="${NODE%:*}"; P="${NODE##*:}"
  echo "=== ${H}:${P}"
  for PATTERN in 'security:{kvs}:*' 'security:{kvsdispatch}:*' 'lock:serial:*'; do
    echo "--- $PATTERN"
    redis6-cli --tls -h "$H" -p "$P" --scan --pattern "$PATTERN"
  done
done
```

**보호 대상과 정리 대상을 값으로 구분한다.** `lock:serial:*`은 legacy와 공유하므로 패턴만 보고 지우면 안 된다.

```bash
for NODE in $PRIMARIES; do
  H="${NODE%:*}"; P="${NODE##*:}"
  for K in $(redis6-cli --tls -h "$H" -p "$P" --scan --pattern 'lock:serial:*'); do
    V=$(redis6-cli --tls -h "$H" -p "$P" GET "$K")
    case "$V" in
      'kvs|v1|'*) echo "KVS lease  : ${H}:${P} $K" ;;
      *)          echo "legacy(보존): ${H}:${P} $K" ;;
    esac
  done
done
```

| 확인 대상 | 처리 |
|---|---|
| `security:{kvs}:*` | KVS 비활성 환경인데 있으면 **바로 지우지 말고** 생성 주체와 마지막 사용 시각부터 확인. 승인 후 `UNLINK` |
| `security:{kvsdispatch}:*` | 동상 |
| `lock:serial:*` 중 값이 `kvs\|v1\|`로 시작 | 동상. 한 건씩 `UNLINK` |
| `lock:serial:*` 중 그 밖의 값 | **보존**(legacy 소유) |
| `streaming-upload:job:serial:*` | **보존**(legacy 소유) |

#### B-4-3. epoch는 마지막에 설정한다

**배포 전에는 epoch 키를 만들지 않는다.** 새 태스크가 정상 기동하고 KVS API가 `503 RECOVERING`을 반환하는 것을 먼저 확인한 다음, 마지막 단계에서 정확한 값을 넣는다.

```redis
# 검증계
SET security:{kvs}:epoch stg-2026-07-31-01
GET security:{kvs}:epoch

# 운영계
SET security:{kvs}:epoch prd-2026-07-31-01
GET security:{kvs}:epoch
```

설정 직후 `GET`으로 태스크 정의 값과 **정확히** 같은지 확인한다. 공백·개행·대소문자가 달라도 차단된다.

---

### B-5. 검증계 배포

#### B-5-1. 사전 차단

- 앱·관제의 검증계 KVS cohort를 **내부 시험 serial 외에는 비활성**
- 배포 시간 동안 실제 에스원 출동 호출 여부를 사전 합의
- 변경 전 태스크 정의 ARN과 desired count 기록
- RDS 스냅샷 완료 확인
- DDL과 Redis 조사 완료

#### B-5-2. 이미지·태스크 정의

태스크 정의 등록은 배포 담당자가 **수동으로 먼저** 한다. `deploy/cloudshell-push.sh`는 이미지를 `latest`로 push한 뒤 `skix-security-{env}-tdef` family의 **가장 최신 ACTIVE 리비전**을 조회해 서비스에 적용하기 때문이다.

```bash
# 로컬(또는 빌드 머신)
./deploy/build-export.sh          # security.tar.bz2 생성 (linux/amd64)

# JSON 문법 확인
jq empty deploy/task-definition-stg.json
jq empty deploy/task-definition-prd.json
```

태스크 정의 확인 항목: 계정·역할 ARN이 대상 환경과 일치 / `KVS_PROVIDER=aws` / `KVS_REQUIRED=true` / epoch·fence generation이 승인값 / 채널 prefix·환경 태그가 환경과 일치 / MASTER·VIEWER 역할이 실제 존재 / SSM ARN이 대상 계정·환경 / Relay 정책이 승인값(미지정이면 기본값 70·80초가 적용된다는 것을 인지).

배포 전 기록:

```bash
aws ecs describe-services --region ap-northeast-2 \
  --cluster skmg-airbot-stg-ecs-cluster --services skmg-airbot-stg-security-svc \
  --query 'services[0].taskDefinition' --output text        # 이전 리비전 ARN

aws ecr describe-images --region ap-northeast-2 \
  --repository-name skmg/security --image-ids imageTag=latest \
  --query 'imageDetails[0].imageDigest' --output text       # 적용 digest
```

**태스크 정의 등록자는 한 명으로 제한한다.** 스크립트 실행 직전에 다른 리비전이 추가되지 않았는지 확인하고, 스크립트 출력의 최종 ARN을 배포 기록에 남긴다.

#### B-5-3. epoch 없는 상태로 배포

`security.tar.bz2`를 CloudShell에 업로드한 뒤:

```bash
./deploy/cloudshell-push.sh stg

aws ecs wait services-stable --region ap-northeast-2 \
  --cluster skmg-airbot-stg-ecs-cluster \
  --services skmg-airbot-stg-security-svc
```

확인:

- 새 태스크 리비전만 실행 중(구 태스크 0개)
- `/actuator/health` 정상
- 비KVS API 정상
- **KVS create/GET/availability가 전부 `503 RECOVERING`** (epoch 부재)
- secret·property 원문이 로그에 없음
- fence boot guard 오류 없음

#### B-5-4. epoch 설정 후 내부 smoke

구 태스크가 0개인 것을 확인한 다음 B-4-3으로 epoch를 설정하고, 내부 시험 serial로 아래를 순서대로 확인한다.

1. create → `201 CREATED`, `data.resultType=CREATED`
2. 자동 생성된 채널의 이름(`skix-kvs-stg-{serial}`)·태그(`Environment=stg`, `ManagedBy=skix-security`)·`ACTIVE` 상태
3. DB `fence`가 `10^12` 이상이고 `fence IS NULL` 0건
4. Redis 세션 hash·deadline ZSet·device lease(`kvs|v1|` 시작) 확인
5. MQTT start payload에 sessionId·fence·`sessionLeaseExpiresAt`·`relayMaxDurationMs` 포함
6. **기기 MASTER와 앱 VIEWER의 P2P 연결**
7. heartbeat → lease renew → close → `TERMINAL` 수렴
8. **기기 무응답 시나리오** → lease TTL + margin(약 105초) 이후 `LEASE_EXPIRED`로 자동 정리
9. **forced-TURN 환경에서 Relay cutoff** 동작(서버 70초)
10. 출동 availability · VOD 업로드 · KMS · 에스원 접수 상태
11. **legacy 스트리밍과 legacy 출동 회귀 없음**
12. DB·API·MQTT·CloudWatch 어디에도 자격증명 원문 없음

#### B-5-5. 관찰 기간

최소 24시간 또는 합의된 충분한 세션 수. **Go 조건**: 5xx/`RECOVERING` 비정상 증가 없음 / watchdog backlog 지속 누적 없음 / oldest delay가 sweep 주기 이상으로 지속되지 않음 / Redis eviction 0 / 설명되지 않는 lease MISMATCH 0 / 채널 생성·STS·KVS `AccessDenied` 0 / P2P·Relay·close·무응답 종료·출동·legacy 회귀 통과 / 앱·기기 계약 QA 통과.

---

### B-6. 운영계 배포

#### B-6-1. 변경 창

검증계 Go 승인 이후 별도 운영 변경 창을 잡는다. 고객 KVS cohort는 **기본 OFF**, 내부 운영 serial만 allowlist. AWS·DB·Redis·앱·기기 담당자가 연락 가능해야 하고, 롤백 결정권자와 중단 기준을 사전에 합의한다. 이전 태스크 정의 ARN과 이미지 digest를 확보하고 RDS 스냅샷을 만든다.

#### B-6-2. 백엔드 선배포

검증계와 동일하게 **epoch 키가 없는 상태**에서 새 태스크 정의를 먼저 배포한다.

```bash
./deploy/cloudshell-push.sh prd

aws ecs wait services-stable --region ap-northeast-2 \
  --cluster skmg-airbot-prd-ecs-cluster \
  --services skmg-airbot-prd-security-svc
```

비KVS 기능과 `503 RECOVERING` 게이트를 확인하고 구 태스크가 0개인 것을 확인한 뒤 운영 epoch를 설정한다.

#### B-6-3. 단계적 cohort 활성화

1. 사내 운영 serial
2. 전체 대상의 약 1%
3. 약 5%
4. 약 25%
5. 100%

각 단계는 **최소 30~60분과 충분한 실제 세션 수**를 관찰한 뒤 다음으로 넘어간다. 트래픽이 적으면 시간 대신 최소 세션 수를 따로 정한다. **자동 확대하지 않고 단계마다 담당자가 승인한다.**

#### B-6-4. 즉시 확대 중단 조건

- 의도하지 않은 `RECOVERING`이 **1건이라도** 발생
- KVS/STS `AccessDenied` 또는 channel ACTIVE timeout 반복
- `DEVICE_IN_USE`·`NOT_STARTED`·`BUSY`·lease MISMATCH 급증
- watchdog backlog / oldest delay 지속 증가
- Redis eviction 또는 failover 후 세션 이상
- Relay 한도 초과 또는 기기 자동 차단
- 자격증명·URL·키가 로그·APM·DB에 노출
- legacy 스트리밍·출동 장애
- 에스원 출동 중복 또는 상태 불명확 증가
- 고객 영향 5xx 또는 앱 무한 재시도

---

### B-7. 롤백

#### B-7-1. KVS 기능 문제 — 정상 drain 가능

1. 앱·관제 KVS cohort를 OFF해 신규 create를 중단한다.
2. **Redis epoch를 즉시 삭제하지 않는다.** 삭제하면 renew와 자동 force-release까지 막혀 active lease가 남는다.
3. 기존 세션의 close·lease 만료를 기다린다.
4. Redis session/deadline과 `kvs|v1|` device lease가 0인지 확인한다.
5. 이전 태스크 정의로 ECS 서비스를 되돌린다.
6. **DB 스키마는 그대로 둔다.**
7. 원인 분석 후 다음 배포에서 epoch를 새 값으로 바꾼다. DB를 초기화·복원했다면 fence generation도 증가시킨다.

#### B-7-2. 플랫폼 전체 장애 — 즉시 ECS 롤백

1. 고객 KVS cohort 즉시 OFF.
2. 이전 태스크 정의로 서비스 우선 복귀.
3. 이미 발급된 기기는 마지막 lease 만료 후 자체 종료한다. **최소 `session lease TTL + margin`(약 105초)이 지나기 전에 KVS lease를 임의 삭제하지 않는다.**
4. 대기 후 모든 Redis shard에서 KVS session/dispatch key와 `kvs|v1|` lease만 **선별** 정리(B-4-2의 값 판별 사용).
5. legacy `lock:serial:*`과 `streaming-upload:*`은 보존.
6. DB는 감사 원장으로 유지하고 강제 DROP하지 않는다.

#### B-7-3. 금지 사항

- active session이 있는데 HMAC lease secret 롤링 교체
- active session이 있는데 epoch 삭제 후 즉시 재생성
- `lock:serial:*` 일괄 삭제
- Redis `FLUSHALL` / `FLUSHDB`
- 에스원 출동 결과가 불명확한 작업의 자동 재호출
- 운영 장애 중 DB 스키마 즉흥 롤백
- 이전 태스크 리비전과 그때의 ECR digest를 확인하지 않은 롤백

---

### B-8. 배포 후 정리

- [ ] 최종 ECS 태스크 정의 ARN 기록
- [ ] ECR digest 기록
- [ ] epoch / fence generation 기록
- [ ] 내부 serial 및 cohort 단계별 활성화 시각 기록
- [ ] smoke 결과와 로그·DB·Redis·AWS 증적
- [ ] watchdog·Redis·KVS 비용 대시보드 확인
- [ ] 생성된 signaling channel 수와 태그 확인
- [ ] 운영 역할·정책의 임시 FullAccess 제거
- [ ] 실패한 작업과 수동 조치 기록
- [ ] 다음 배포·복구 시 쓸 새 epoch 규칙 공유

### B-9. 환경별 최종 Go/No-Go 표

| 항목 | 검증계 | 운영계 |
|---|---|---|
| SSM lease secret | | |
| task / execution / master / viewer IAM | | |
| network / quota | | |
| RDS 스냅샷 | | |
| 최종 DDL(+`purpose` 컬럼) | | |
| Redis noeviction / backup / TLS | | |
| KVS stray key 없음 | | |
| epoch / fence generation 승인 | | |
| 적용 ECR image digest 기록 | | |
| `RECOVERING` 게이트 확인 | | |
| P2P / Relay / lease smoke | | |
| 출동 / legacy 회귀 | | |
| 자격증명 비노출 실면 확인 | | |
| 앱·기기 계약 QA | | |
| 관찰 기간 | | |
| 최종 승인자 | | |

---

## 부록 C. 보안성검토 제출 자료 (원문)

아래는 작성한 보안성검토 요청 자료의 내용이다. **제출 여부와 검토 결과는 확인하지 못했다.** 또한 이 자료는 **에스원이 KVS 시그널링 채널과 자격증명을 발급하는 형상(`provider=s1`)** 전제로 쓰였고, 현재 세 환경 태스크 정의는 `provider=aws`다. 차이는 이 부록 마지막 표에 있다.

### C-1. 검토 배경·목적·범위

- **배경**: 에스원이 발급하는 AWS Kinesis Video Streams(KVS) WebRTC 시그널링 채널을 이용한 실시간 영상 경로를 신규 구축했다.
- **목적**: 고객이 앱에서 A1 기기의 실시간 영상을 확인한다.
- **검토 요청 범위**: 에스원 KVS 시그널링 채널 및 임시 자격증명 연동 / 실시간 영상(WebRTC) 전송 구간 / 해당 기능의 인증·인가·자격증명·로그 통제.

| 범위 밖 | 사유 |
|---|---|
| 출동 요청 영상의 업로드·암호화·복호화 | 기 보안성검토 완료 항목(기존 에스원 보안서비스 연동 시 검토) |
| 앱·기기 단말 내부의 자체 저장·로그 정책 | 각 담당 조직 기존 기준 적용 |

### C-2. 시스템 구성

| 구성요소 | 소유 | 역할 |
|---|---|---|
| 앱 (iOS/AOS) | 자사 | WebRTC **VIEWER** — 영상 수신 |
| A1 기기 | 자사 | WebRTC **MASTER** — 영상 송출 |
| `skix-security` | 자사 (AWS ECS, ap-northeast-2) | 세션 생성·종료 오케스트레이션 |
| AWS KVS Signaling Channel | **에스원** | SDP/ICE 교환 (시그널링) |
| AWS IoT Core (MQTT5) | 자사 | 기기 명령·상태 수신 (X.509 mTLS) |
| Redis / MySQL | 자사 | 세션 상태·점유 lease / 세션 이력 |
| 에스원 API | 에스원 | KVS 세션 발급 |

```text
                      ┌──────────────────────────────┐
                      │  에스원                       │
                      │  KVS 세션발급 API · 시그널링 채널│
                      └───────────────┬──────────────┘
                                      │
                      ② 세션 발급 요청 → 채널ARN + camera·viewer 자격증명
                         (HTTPS + JWT)
                                      │
                      ┌───────────────┴──────────────┐
                      │  skix-security (자사 AWS ECS) │
                      └──┬────────────────────────┬──┘
                         │                        │
  ① 세션 생성 요청 (HTTPS) │                        │  ③ camera 자격증명 전달
  ④ viewer 자격증명 응답   │                        │     (AWS IoT MQTT, mTLS)
                         ▼                        ▼
              ┌────────────────────┐   ┌────────────────────┐
              │  앱 (WebRTC VIEWER) │   │  A1 기기 (MASTER)   │
              └──────────┬─────────┘   └─────────┬──────────┘
                         │  ⑤ 시그널링(SDP/ICE) — KVS 채널 경유
                         │◀─────────────────────▶│
                         │  ⑥ 영상 — 앱↔기기 직접 연결, DTLS-SRTP 종단간 암호화
                         │◀═════════════════════▶│
```

핵심:

- **KVS 세션 발급 API를 호출하는 주체는 `skix-security` 뿐이다.** 앱과 기기는 직접 자격증명을 받지 않고 자사 서버를 통해서만 전달받는다.
- **자격증명은 역할별로 분리 전달된다.** 앱에는 VIEWER만, 기기에는 CAMERA(MASTER)만 가며 서로의 것을 알 수 없다.
- 기기에 시작 명령(③) 발행이 확인된 경우에만 앱에 자격증명(④)을 반환한다. 불확실하면 반환하지 않고 세션을 종료한다(fail-closed).
- **KVS가 담당하는 구간은 ⑤ 시그널링(연결 협상)뿐이다.**
- **⑥ 실제 영상은 앱과 기기가 직접 WebRTC로 주고받으며 DTLS-SRTP로 종단간 암호화된다.** 중계(TURN)를 경유해도 중계 노드는 암호문만 전달한다.

**영상 저장 여부** — 시그널링 채널은 영상을 저장하지 않는다. 사용 API는 채널 조회·생성·태깅(`DescribeSignalingChannel`, `CreateSignalingChannel`, `TagResource`)에 한정되며, 영상을 저장하는 KVS Video Stream(저장형 스트림)은 생성·사용하지 않는다(코드상 `PutMedia`·`CreateStream` 등 저장 계열 호출이 없다). 시그널링 메시지 TTL은 60초다. **실시간 영상은 어느 서버에도 저장되지 않고 세션 종료와 함께 소멸한다.**

### C-3. 처리·제공 정보

세션 발급 요청 시 외부에 전달되는 항목은 4개뿐이다.

| 항목 | 값 | 성격 |
|---|---|---|
| `device` | 기기 유형 코드(A1) | 비식별 |
| `deviceId` | 기기 아이디 | 기존 계약 연동 항목 |
| `ownerId` | 기기 소유자 아이디 | 기존 계약 연동 항목 |
| `userId` | 사용자 아이디 | 세션 소유자 식별 |

모두 기존 보안서비스 연동에서 이미 쓰는 항목이며 **본 건으로 신규 추가되는 개인정보 항목은 없다. 영상 데이터는 외부 서버로 전송되지 않는다.**

| 자사 저장소 | 보관 항목 | 영상 포함 |
|---|---|---|
| MySQL `kvs_session_history` | 세션 ID, 기기 시리얼, 세션 상태·종료사유, 연결 요약 | 없음 |
| Redis | 세션 상태, 기기 점유 lease, 만료 시각 | 없음 |

### C-4. 인증·인가

| 호출 주체 | 인증 방식 |
|---|---|
| 앱 | AWS Cognito JWT |
| 관제 웹(내부 관리자) | Admin JWT (HMAC-SHA256, Redis 블랙리스트·중복로그인 검증) |
| `skix-security` → 에스원 | 요청별 JWT 발급 (`Authorization: Bearer`) |
| 에스원 → `skix-security` | RSA 서명 JWT, JWKS 엔드포인트로 검증 |
| `skix-security` → A1 기기 | AWS IoT Core X.509 mTLS |

| 기능 | 허용 대상 |
|---|---|
| 세션 생성 | 해당 기기를 구독 중인 **홈 멤버**, 또는 관제에서 권한을 가진 관리자 |
| 세션 조회·종료 | **세션 생성자 본인** 또는 관리자 |

홈 멤버 여부는 매 요청 검증한다. 타인이 점유 중인 기기에 대한 세션 생성은 `409`로 거절하되 **세션 ID·점유자 정보를 응답에 포함하지 않는다.**

앱에 전달되는 것은 **VIEWER 역할의 임시 자격증명**이며 CAMERA(MASTER)는 앱에 가지 않고 MQTT로 기기에만 간다. 자격증명 잔여 유효시간이 임계값(기본 10초) 미만이면 세션을 시작하지 않고 종료한다.

### C-5. 자격증명 보호 (secret-zero)

서버 로그·로그 테이블·관측 인프라에 다음을 저장하지 않는다 — VIEWER/CAMERA 임시 자격증명(access key, secret key, session token), 세션 토큰.

| 수단 | 내용 |
|---|---|
| 응답 헤더 | 자격증명 응답에 `Cache-Control: no-store` |
| 1회 발급 | VIEWER 자격증명은 `201 CREATED`에서만 반환. 재조회·재시도로 복구 불가 |
| 로그 마스킹 | 기기 명령 객체의 `toString()`에서 자격증명을 `****`로 치환 |
| 외부 응답 비로깅 | 세션 발급 응답은 자격증명을 포함하므로 오류 메시지에 원문 body를 넣지 않음 |
| 자동 재시도 금지 | 자격증명 응답 API는 자동 재시도를 적용하지 않음 |
| 검증 | 정상·외부오류·MQTT오류 전 경로를 실제 비밀값 기준 자동화 테스트로 검증 |

서버 비밀키(`KVS_LEASE_SECRET` 등)는 AWS SSM Parameter Store에서 주입하며 코드·이미지에 포함하지 않는다.

### C-6. 세션 수명·오남용 방지

| 통제 | 값 |
|---|---|
| 세션 권한 유효기간(lease) | 60초 (기기에 만료시각 명시 전달) |
| 기기 heartbeat 주기 | 15초 — 유효한 heartbeat에만 lease 연장 |
| 시작 명령 유효시간 | 15초 |
| 강제 해제 여유시간 | lease 만료 + 45초 |

설계 원칙은 **서버가 기기 생존을 추측하지 않는 것**이다. 유효기간이 명시된 세션 권한을 발급하고 기기가 만료를 집행하며, 서버는 마지막 발급 만료시각 + 안전 여유가 지난 뒤에만 점유를 해제한다. 통신 두절·기기 무응답에서도 세션이 무한 유지되지 않는다.

중계(Relay) 제한 — 에스원 정책상 중계 연결이 90초를 넘으면 기기 차단 대상이므로 3중 방어를 적용한다. ① 기기가 중계 확인 시점부터 약 80초 후 자체 종료 ② 서버가 중계 관측 시 약 70초에 종료 명령 발행 ③ 한 번 중계로 판정되면 완화하지 않음(sticky).

| 위협 | 통제 |
|---|---|
| 기존 스트리밍과 동시 카메라 점유 | Redis 공유 점유키 + 기기 카메라 상호배타. 두 번째 시작은 `BUSY` 거절 |
| 점유키 탈취·위조 | 점유값이 공개 세션 ID가 아니라 **서버 비밀키 기반 HMAC-SHA256**. 외부 클라이언트가 위조·삭제 불가 |
| 지연·역순 도착한 과거 명령 | 세대 토큰(fence) 단조 증가 검증. 기기가 최대 처리 fence를 영속 저장해 재부팅 후에도 stale 명령 거부 |
| 중복 요청 | 요청별 `Idempotency-Key` + DB UNIQUE + Redis Lua 원자 CAS |
| 상태 저장소 유실 후 오작동 | epoch 불일치 시 신규 발급·조회 전면 차단(`503 RECOVERING`) |
| DB 단독 롤백 | Redis 최고 fence 기록과 대조해 불일치 시 기동 실패 |

### C-7. 전송 암호화·가용성

| 구간 | 방식 |
|---|---|
| 앱 ↔ `skix-security` | HTTPS (TLS) |
| `skix-security` ↔ 세션 발급 API | HTTPS (TLS) + JWT |
| `skix-security` ↔ A1 기기 | AWS IoT Core, X.509 mTLS |
| **앱 ↔ A1 기기 (영상)** | **WebRTC DTLS-SRTP 종단간 암호화** |
| 서버 비밀정보 | AWS SSM Parameter Store |

| 상황 | 처리 |
|---|---|
| 세션 발급 실패 | 기기에 시작 명령 미발행 후 세션 실패 확정 (fail-closed) |
| 명령 발행 결과 불명확 | 자격증명 미반환, 세션 종료 처리 |
| 기기 무응답 | lease 만료 + 여유시간 경과 후 자동 정리 |
| 외부 API 장애 | Circuit Breaker 적용 |
| 상태 저장소 유실 | 전면 차단 후 승인된 운영 절차로만 복구 |

### C-8. 제출 자료와 현재 형상의 차이 (인수자 확인 필요)

| 항목 | 제출 자료 (에스원 KVS 전제) | 현재 태스크 정의 (`provider=aws`) |
|---|---|---|
| 시그널링 채널 소유 | **에스원 AWS 계정** | **자사 AWS 계정**(dev `010928196421` / stg `087432099373` / prd `779846811758`) |
| 채널 생성 주체 | 에스원이 세션 발급 시 제공 | `skix-security`가 `skix-kvs-{env}-{serial}`로 조회·자동 생성·태깅 |
| 임시 자격증명 발급 | 에스원 세션 발급 API가 camera/viewer를 내려줌 | 자사 STS `AssumeRole` ×2 (MASTER 역할 / VIEWER 역할, 각 900초) |
| 외부에 전달되는 정보 | `device`, `deviceId`, `ownerId`, `userId` 4개 | **세션 발급을 위해 외부로 나가는 정보 없음**(자사 계정 내부에서 발급). 다만 STS RoleSessionName에 계약번호(MASTER)와 **loginId(VIEWER)**가 들어가 **자사 CloudTrail에 기록된다**(§8-3의 17a) |
| Relay 90초 하드 한도 | 있음(에스원 정책, 초과 시 deviceId 차단) | **없음.** 지금 적용되는 서버 70초 / 기기 80초는 보수적 초기값 |
| 세션 종료 통보 | 에스원 `POST /skix/livevideo/stop` | 해당 코드가 로드되지 않음(빈이 provider=s1 조건부) |
| 영상 전송 구간 | 동일 (앱↔기기 직접, DTLS-SRTP) | 동일 |
| 자격증명 보호·세션 수명·오남용 방지 | 동일 | 동일 |

**확인이 필요한 것**: 이 보안성검토가 (a) 에스원 KVS 경로를 대상으로 제출·완료된 것인지, (b) 자사 AWS 전환 형상으로 정정 제출이 필요한지, (c) 자사 계정 발급이라 오히려 외부 제공 정보가 줄었으므로 별도 검토가 불필요한지. 위 표를 그대로 들고 보안 담당과 상의하면 된다.
