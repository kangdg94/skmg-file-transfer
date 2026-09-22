# [작업] skix-security — S1 연동·DLQ·유령 2002·R047 PIN·감사 backport

| 항목 | 내용 |
|---|---|
| **상태** | 6건 중 5건 코드 완료(dev·stg 전량 반영) · **ECS 배포 여부 미확인** · 유령 2002만 **미착수** |
| **작업 기간** | 2026-08-04 ~ 2026-08-31 |
| **직접 수정한 저장소** | `skix-security` (전부) |
| **요청서를 전달한 대상** | 펌웨어 담당(재전송 루프·유령 2002 2건), 에스원(S1) 측(전문 정정 stg/prd 반영 확인) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §7-1의 1번(세 환경 ECS 배포 실태 확인)과 2번(검증계 DLQ 적체 확인·purge) |

---

## 0. 세 줄 요약

1. `skix-security`(Safe Care — 로봇 기반 가정용 보안 서비스 백엔드)에서 8월 한 달간 처리한 **에스원(S1) 연동·SQS 안정화·보안 감사 반영** 묶음이다. 여섯 갈래이고 서로 코드가 겹친다.
2. **코드는 다섯 갈래 모두 `dev`·`stg`에 반영돼 있고 네 갈래는 `main`(운영 브랜치)에도 있다.** 유령 2002 이슈만 패치 자체가 없다. 실제 ECS 배포 여부는 로컬에서 확인할 수 없어 전부 미확인이다.
3. 남은 일의 핵심은 세 가지다 — **검증계 DLQ purge**(코드 수정 후에도 적체분은 자동 소멸하지 않는다), **유령 2002 패치 착수**(에스원에 잘못된 출동 해제 전문이 나가고 있다), **`main` 승격 시 의도적으로 제외한 2커밋을 다시 끌어들이지 않기**.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **skix-security** | Safe Care 백엔드. 로봇(A1 기기)이 집을 지키는 보안 서비스의 서버. 보안모드 시작/종료, 침입 이벤트, 출동 접수, 실시간 영상(라이브뷰), 구독 상태를 담당한다. Spring Boot, ECS 배포. |
| **에스원 / S1** | 물리 보안 출동 업체. 우리 서버가 HTTP로 이벤트·출동·영상 세션을 전달한다. base URL은 환경변수 `S1_API_URL`. |
| **A1** | 보안 기능을 탑재한 로봇 기기. S1 전문의 `device` 필드 고정값이 `"A1"`이다. |
| **OASYS** | SK매직 계약·고객 시스템. 구독(멤버십) 계약의 권위 시스템. |
| **R047** | OASYS 인터페이스 `IF_NMX_R047`. **구독 상태 변경 통보**(가입·요금제 변경·갱신·해지)를 OASYS가 우리 쪽으로 밀어 넣는 전문. 수신 엔드포인트는 `POST /internal/v1/oasys/subscription/status`. |
| **보안모드** | 기기가 집을 감시하는 상태. 시작=이벤트 코드 2001, 정상 종료=2002, 시작 실패=2003, 에러 종료=2004. |
| **triggerType** | 2001/2002 이벤트에 붙는 "누가 시작/종료시켰나" 값. `APP`/`ADMIN`/`SCHEDULE`/`PIN`/`FACE_RECOGNITION`/`LLM`/`PUI`. 서버가 MQTT 커맨드에 실어 보낸 `initiatedBy`를 **기기가 그대로 에코백**한다. |
| **KVS / 라이브뷰** | AWS Kinesis Video Streams WebRTC 기반 실시간 영상. 앱이 VIEWER, 기기가 MASTER로 붙는다. 세션 발급처(issuer)는 자사 AWS 또는 에스원 둘 중 하나이며 `KVS_PROVIDER` 환경변수로 고른다. |
| **livevideo play / stop** | 에스원이 KVS 세션을 발급(`POST /skix/livevideo/play`)·종료 통보(`POST /skix/livevideo/stop`)받는 전문. `KVS_PROVIDER=s1`일 때만 쓰인다. |
| **DLQ** | Dead Letter Queue. SQS에서 재시도를 다 소진한 메시지가 쌓이는 보관 큐. |
| **PIN 이력** | `pin_history` 테이블. 기기에 PIN을 설정한 적이 있는지 판단하는 근거. 앱이 `GET /security/v1/devices/{serial}/pin/check`로 조회해 PIN 재설정 유도 여부를 정한다. |
| **감사 브랜치** | 사내 보안점검 제출용으로 소스를 정리한 브랜치(`submission/security-audit-20260811`). 제출용 스쿼시 커밋이라 그대로 머지할 수 없어, 실제 적용할 변경만 골라 `dev`로 옮겼다(backport). |
| **승격** | `dev` → `stg` → `main` 단방향 머지. `main`이 운영계다. |

### 1-2. 이 문서가 다루는 여섯 갈래

| # | 갈래 | 한 줄 |
|---|---|---|
| 1 | S1 livevideo play/stop 연동 | 라이브뷰 세션이 끝날 때 에스원에 종료를 통보하는 기능 + 에스원 전문 오류 3건 정정 반영 |
| 2 | S1 이벤트 4xx 비재시도 · DLQ | 에스원이 영구 거절하는 이벤트를 무한 재시도해 검증계 DLQ가 쌓이던 문제 |
| 3 | 유령 2002 → 에스원 204 오전송 | 구독 해지 자동 정지가 **무장한 적 없는 기기의 "보안 해제"를 에스원에 보내고 있다.** 미해결 |
| 4 | R047 해지 시 PIN 이력 무효화 | 해지→재구독 시 이전 계약자의 PIN이 '설정됨'으로 남던 결함 |
| 5 | 감사 브랜치 backport | 보안점검 제출본에서 실제로 적용할 변경만 골라 반영 + 적용하면 안 되는 3건의 근거 |
| 6 | `main` 선별 반영 규칙 | 운영 브랜치가 `dev` 전체가 아니라 **2커밋을 의도적으로 뺀** 상태다 |

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 브랜치 지형

```
origin/dev   0fa25b5  2026-09-14
origin/stg   04b28dc  2026-09-14
origin/main  f19e9d7  2026-08-31
```

- **`stg`는 `dev`를 전부 포함한다.** `git log origin/stg..origin/dev`가 비어 있다. 두 브랜치의 코드 내용이 같고 `stg`에는 승격 머지 커밋만 더 있다.
- **`main`은 `dev`보다 비머지 커밋 12개 뒤처져 있다.** 그중 2건은 **의도적 제외**(§3-6), 9건은 OASYS 직접 구독 신청 기능(9월 작업, 이 문서 범위 밖), 1건은 cherry-pick으로 이미 내용이 들어가 있다.

`main`에 없는 12커밋 (`git cherry -v origin/main origin/dev` 실측):

| 커밋 | 날짜 | 내용 | 성격 |
|---|---|---|---|
| `b1b148b` | 08-22 | 토큰에서 loginId 클레임 의존 제거 | **의도적 제외** (§3-6) |
| `cf5f56b` | 08-23 | check-member 응답을 아웃바운드 로그에 미저장 | **의도적 제외** (§3-6) |
| `955ba0a` | 08-31 | R047 해지 시 PIN 이력 무효화 | `main`에 `f19e9d7`로 cherry-pick됨 — **내용은 반영 상태** |
| `8a288f4` `a71bf95` `36e3e91` `b5b373a` `88f14d5` `29043f5` `e4a2f8c` `4c7bdd2` `28a668f` | 09-01 ~ 09-14 | OASYS 직접 구독 신청(R045) 외 | 이 문서 범위 밖 |

### 2-2. 갈래별 반영 (`git merge-base --is-ancestor` 실측)

| # | 갈래 | 주요 커밋 | dev | stg | main |
|---|---|---|---|---|---|
| 1 | S1 livevideo stop 본체 | `e62a482` `c0d23b0` `7d57122` `ee150b1` (머지 `05e5edc`) | 반영 | 반영 | 반영 |
| 1 | S1 전문 정정 3건 | `36161a5` `f4f5d43` `0b67ac6` (머지 `073b22f`) | 반영 | 반영 | 반영 |
| 1 | stop `NO_ACTIVE_SESSION` 멱등 | `a8d9d03` (머지 `ab483f8`) | 반영 | 반영 | 반영 |
| 2 | S1 4xx 비재시도 · 보관기간 가드 | `cfed973` `7443f56` | 반영 | 반영 | 반영 |
| 2 | 보안모드 이력 멱등 저장 | `4a684bd` | 반영 | 반영 | 반영 |
| 3 | 유령 2002 패치 | **없음** | 미착수 | 미착수 | 미착수 |
| 4 | R047 해지 PIN 이력 무효화 | `955ba0a` / `main`은 `f19e9d7` | 반영 | 반영 | 반영(해시 다름) |
| 5 | 감사 backport 4커밋 | `e6af9c4` `6f51143` `bdc2102` `18e69dd` | 반영 | 반영 | 반영 |
| 5 | 날짜 가드 철회 + 정산 인덱스 | `7e91af9` | 반영 | 반영 | 반영 |
| 6 | 제외 대상 2커밋 | `b1b148b` `cf5f56b` | 반영 | 반영 | **미반영(의도)** |

**브랜치는 전부 정리되어 남아 있지 않다.** 메모에 `fix/s1-event-4xx-non-retryable`로 적혀 있는 브랜치는 원격·로컬 어디에도 없다(실측). 해당 커밋들은 `fix/mqtt-publish-log-success-default` 브랜치에 합쳐져 2026-08-05 머지 `b27b8e8`로 `dev`에 들어갔다. 커밋 해시로 추적하라.

### 2-3. 배포·인프라 (로컬에서 확인 불가 — 전부 미확인)

로컬 맥에서는 AWS 콘솔·CLI에 도달할 수 없다. 아래는 전부 회사 VDI에서 확인해야 한다.

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| dev·stg·prd ECS 애플리케이션 배포 | **미확인** | ECS 서비스의 태스크 정의 리비전과 이미지 태그를 보고, 실행 중 이미지가 8월 이후 빌드인지 확인 |
| `security_mode_history`에 `uk_serial_event_code_time` UNIQUE | **prd는 2026-08-05 적용 확인. dev·stg 미확인** | §부록 D의 확인 SQL |
| `mqtt_publish_log.success` DEFAULT 1 | **prd 조치 완료(운영에서 오류 발생해 즉시 ALTER). dev·stg 미확인** | §부록 D |
| `dispatch_billing_history` 인덱스 2종 | **3환경 전부 미적용** (코드의 참조 DDL만 갱신됨) | §부록 D |
| 검증계 SQS DLQ 적체 | **미확인.** 2026-08-04 관측 시 실패 639건 | §부록 B |
| 에스원 stg/prd 환경의 전문 정정 반영 | **미확인** | 에스원 담당 창구로 문의 (§3-1) |

### 2-4. 환경별 KVS provider — **세 환경 모두 `aws`다**

저장소의 태스크 정의 스냅샷(`deploy/task-definition-{dev,stg,prd}.json`, 마지막 갱신 2026-07-31) 실측:

| 환경 | `KVS_PROVIDER` | `KVS_REQUIRED` | SQS 큐 |
|---|---|---|---|
| dev | `aws` | `true` | `security-iot-event-dev.fifo` |
| stg | `aws` | `true` | `security-iot-event-stg.fifo` |
| prd | `aws` | `true` | `security-iot-event-prd.fifo` |

**이것이 §3-1을 읽을 때의 전제다.** `S1KvsApiClient`·`S1KvsStopApiClient`는 `@ConditionalOnProperty(app.kvs.provider=s1)`이라 `provider=aws`인 환경에서는 **빈 자체가 로드되지 않는다.** 종료 통보 호출부(`KvsLiveViewService.notifyProviderStoppedBestEffort`)도 `stopNotifierProvider.getIfAvailable()`이 null이면 즉시 반환한다. 즉 **S1 livevideo play/stop 경로는 현재 어느 환경에서도 실행되지 않는다.** 코드는 완성돼 있고, 에스원 발급으로 전환하는 날 켜진다.

단 `deploy/*.json`은 저장소에 커밋된 스냅샷이지 실 ECS 상태가 아니다. 실제 값은 VDI에서 태스크 정의를 확인하라.

---

## 3. 갈래별 상세

### 3-1. S1 livevideo play/stop 연동

**무엇을 만들었나.** 라이브뷰 세션이 끝날 때 에스원에 `POST /skix/livevideo/stop`으로 종료를 알린다. 에스원 쪽에 세션이 남아 있으면 같은 기기의 다음 `play`가 `ALREADY_IN_SESSION`으로 거절되므로, 종료를 알려 다음 시청을 막지 않는 것이 목적이다.

**왜 이렇게 만들었나.** 통보는 **at-most-once best-effort**다. 재시도도 outbox도 두지 않는다. stop 전문에는 세션 식별자가 없고 `device`+`deviceId`만 있어서, 재시도가 **새로 시작된 세션을 끊어버릴 수 있다.** 놓친 stop은 에스원 쪽 자체 만료로 수렴하고, 사용자가 한 번 더 시청을 시도하면 그 `ALREADY_IN_SESSION` 종결의 finalize가 다시 stop을 보내 잔존 세션을 정리한다(자기 치유). 즉 **놓치는 비용 < 잘못 보내는 비용**이다.

핵심 구조:

```
KvsLiveViewService.completeFinalize()
   └ DB TERMINAL 최초 확정(casTerminal = UPDATED) 직후
     └ notifyProviderStoppedBestEffort(serial, sessionId, fence)   ← 동기
        └ statePort.findProviderStopTarget(...)   ← primary EVAL. arm 시점 기록만 본다
           └ S1KvsStopApiClient.notifySessionStopped(serial, deviceId)
     └ leasePort.release(...)                                       ← stop 이후
```

- **대상 판단은 발급 직전 `armProviderIssue`로 기록한 `providerType`/`providerIssueTarget`만 본다.** finalize 시점에 계약을 재조회하지 않는다 — 세션 도중 계약 매핑이 바뀔 수 있고, 그러면 엉뚱한 기기의 세션을 끊는다. 기록이 없으면(play를 시도조차 못 한 경우) 통보하지 않고, `providerType != S1`(자사 AWS 발급)도 통보하지 않는다.
- **lease 해제 *전에* 동기 호출**한다. 응답 완료 경로에서 "이전 stop → 새 play" 순서를 보장하기 위해서다.
- **전용 서킷브레이커 `s1-kvs-stop`**과 전용 `RestClient`·커넥션 풀을 쓴다(`S1KvsClientConfig`). play와 공유하지 않는다 — stop 엔드포인트 단독 장애가 신규 발급을 막으면 best-effort 원칙이 깨진다. window=5·min-calls=5로 좁게 잡았는데, 넓으면 warm 호출에 희석돼 회로가 안 열린다.
- **`NO_ACTIVE_SESSION`은 멱등 성공**으로 처리한다(로그만 info). 통보의 목적 상태("에스원에 활성 세션 없음")가 이미 달성돼 있기 때문이다.

**에스원 전문 실측 정정 3건** (개발계에서 실제 호출해 확인):

| # | 발견 | 정정 후 | 우리 코드 |
|---|---|---|---|
| 1 | `result`가 문자열이 아니라 **boolean** | `{"result":true,"error":null,"errorDesc":null}` | `Boolean.TRUE.equals(...)` 비교로 전환. 문자열 denylist 휴리스틱 제거 |
| 2 | 요청 필드명이 소문자 `deviceid`로 문서화돼 있었음(오타) | camelCase `deviceId` 단독 수용 (2026-08-11 정정 확인) | **이중 전송 제거 완료.** `StopRequest(device, deviceId)` / `PlayRequest(device, deviceId, ownerId, userId)` |
| 3 | `expiration`에 잉여 `Z`(`2026-08-10T01:45:11+00:00Z` — RFC 3339 위반) | `2026-08-11T01:10:40Z` (UTC) | 확정 포맷으로 파싱. **잉여 Z 정규화 로직은 미반영 환경 방어로 남겨 뒀다** |

**현재 상태.** 코드는 `dev`·`stg`·`main` 세 브랜치 모두 반영. 릴리스 게이트 7항목 중 6항목이 닫혔고 1항목이 열려 있다.

**남은 것.**

1. **에스원 stg/prd 환경에 정정이 반영됐는지 확인(미결).** 우리가 `deviceid` 이중 전송을 **제거**했기 때문에, 정정이 안 된 에스원 환경에서는 **play가 전면 거절된다.** `KVS_PROVIDER=s1`로 전환하기 전에 반드시 확인해야 한다. 지금은 세 환경 다 `aws`라 당장의 장애는 없다(§2-4).
2. 지연 stop 의미론(지연된 stop이 새 세션을 끊거나, 지연된 play가 고아로 남는 경우)은 **위험 수용**으로 종결했다. 재평가 트리거는 운영에서 "재시청 직후 원인 불명 종료"가 관측되는 것이다. 그때의 대응안은 모호 실패 한정 lease 안전 격리(`providerIssueArmedAt` 기반)다.
3. watchdog 직렬 지연도 부하 테스트 없이 운영 관측으로 대응하기로 했다. 최악 직렬 지연 상한은 cold 5건·warm 3건 × 약 3.9초 ≈ 20초로, 기기 자체 종료 안전망(80초) 이내다. 대시보드 지표는 `kvs.watchdog.oldest.delay`.

주요 파일 (`src/main/java/com/skix/security/` 하위):

| 파일 | 역할 |
|---|---|
| `infra/kvs/S1KvsApiClient.java` | play 발급. `expiration` 파싱·잉여 Z 정규화가 여기 있다 |
| `infra/kvs/S1KvsStopApiClient.java` | stop 통보. `NO_ACTIVE_SESSION` 멱등·4xx 분류 |
| `infra/kvs/S1KvsClientConfig.java` | play/stop RestClient 2개 분리 생성 |
| `infra/kvs/KvsProperties.java` | `stop*` 타임아웃·풀 크기 4종 |
| `domain/service/KvsLiveViewService.java` | `notifyProviderStoppedBestEffort()` (예외를 삼키는 유일한 지점) |
| `domain/port/KvsSessionStopNotifierPort.java` | 포트 |

**provider 전환 운영 규칙.** `app.kvs.provider`(환경변수 `KVS_PROVIDER`)를 바꾸는 배포는 **활성 KVS 세션을 전부 종결시킨 뒤**(drain) 진행한다. 발급 시점 provider와 finalize 시점 provider가 다르면 통보가 누락된다 — `providerType` 게이트가 오발송은 막지만 누락은 막지 못하고, 에스원 자체 만료로만 수렴한다.

**원격 맵핑 세션은 예외다.** `purpose=REMOTE_MAPPING` 세션은 전역 `provider` 값과 무관하게 **항상 자사 AWS로 발급**된다(2026-08-19 정책). 맵핑은 수십 분 단위 작업이라 에스원의 90초 하드 한도와 양립하지 않기 때문이다. 그래서 AWS issuer 빈은 `provider` 값과 무관하게 항상 로드되고, AWS 설정(role ARN 2종·채널 prefix·환경 태그·STS 지속시간)은 **KVS를 쓰는 모든 환경에서 부팅 검증 대상**이다. "AWS 설정 없이 에스원만으로 도는 환경"은 만들 수 없으며, 그런 구성은 기동에서 막힌다 — AWS 설정이 비면 잘못된 이름의 채널이 만들어지는데 **삭제 권한이 없어 되돌릴 수 없다.** 기동 실패는 안전하다: ECS 롤링 배포에서 신규 태스크가 뜨지 못하면 구 리비전이 계속 서비스한다. 과거에 있던 `KVS_MAPPING_PROVIDER` 환경변수는 제거됐고, 설정돼 있어도 무시된다.

---

### 3-2. S1 이벤트 4xx 비재시도 처리 · DLQ

**무엇이 문제였나.** 2026-08-03부터 검증계 SQS DLQ가 쌓였다(8/4 기준 실패 639건). 원인 연쇄:

```
기기 WRBA1M10KRNGJ0325H00143 가 2026-06-24 발생한 1001(침입) 이벤트를 보관
  → 장기 오프라인
  → 2026-08-03 부터 6분 주기로 재전송 (재전송 횟수·기간 상한 없음)
  → 서버가 에스원에 전달 → 에스원이 400 EVENT_RETENTION_PERIOD_EXCEEDED 로 거절
     (에스원 이벤트 보관 기간 30일 초과)
  → S1ClientRequestException 이 SQS 소비 경로로 전파
  → SQS 재시도 소진 → DLQ 적재
  → 기기는 업로드 커맨드를 못 받아 다시 6분 뒤 재전송 (무한 루프)
```

**무엇을 고쳤나.** 세 커밋이다.

1. **`cfed973`** — 에스원 4xx 거절을 비재시도 종결로 바꾸고, 30일 지난 이벤트는 에스원 호출 전에 드랍하는 가드를 넣었다.
2. **`7443f56`** (리뷰 반영) — 4xx **전체**를 비재시도로 삼키면 인증 장애·408·429 같은 일시 오류까지 조용히 버려져 **보안 이벤트가 유실된다.** 그래서 **확정 오류 코드 allowlist**로 좁혔다. 같은 커밋에서 2001/2002 로컬 이력 저장을 보관기간 가드·에스원 전송보다 **앞으로** 옮겼다 — 에스원이 거절하든 30일이 지났든 우리 이력은 남아야 한다.
3. **`4a684bd`** — 이력 선저장 구조가 만든 회귀 보완. 에스원 5xx로 예외가 전파되면 SQS가 재시도하고, 그때마다 `security_mode_history`에 중복 행이 생긴다. `UNIQUE (serial, event_code, event_time)` + `ON DUPLICATE KEY UPDATE id = id` 멱등 INSERT로 막았다.

현재 코드 (`domain/service/DeviceEventService.java`):

```java
/** S1 이벤트 보관 기간 — 초과 이벤트는 S1이 400(EVENT_RETENTION_PERIOD_EXCEEDED)으로 거절한다 */
private static final Duration S1_EVENT_RETENTION = Duration.ofDays(30);

private static final Set<String> NON_RETRYABLE_S1_ERROR_CODES =
    Set.of("EVENT_RETENTION_PERIOD_EXCEEDED");
```

처리 순서 (`handleDeviceEvent`, 131~152행):

```
1) 2003/2004 등 S1 비전송 이벤트  → 이력 저장 + FCM 후 종료
2) 2001/2002 이면                → 이력 먼저 저장 (S1 성패·보관기간과 무관)
3) eventTime 이 30일 초과         → warn 로그 후 종료 (S1 호출 자체를 하지 않음)
4) S1 전송
   ├ S1ClientRequestException(4xx)
   │   ├ errorCode 가 allowlist 에 있으면 → warn 후 종결 (SQS ack)
   │   └ 그 외(408/429/401/코드 없음)   → 예외 전파 (SQS 재시도 유지)
   └ S1ApiException(5xx·네트워크)        → 예외 전파 (SQS 재시도 유지)
```

같은 allowlist 분기가 업로드 URL 재발급 경로(`handleUploadResponse`, 610행 부근)에도 적용돼 있다.

**현재 상태.** 코드 3커밋 모두 `dev`·`stg`·`main` 반영. 인덱스는 prd만 적용 확인(2026-08-05), dev·stg 미확인.

**인덱스가 코드보다 먼저 올라간 것은 무해하다** — 이 테이블의 유일한 INSERT 경로가 `SafeRunner`로 감싸여 있고 이 저장소에는 `@Transactional`이 0건이라 rollback-only 오염이 생기지 않는다. 중복 시 행만 스킵되어 결과가 신규 코드와 같고 ERROR 로그 노이즈만 남는다. 그리고 인덱스 생성이 성공했다는 사실 자체가 기존 중복 행이 없었다는 증거다.

**남은 것.**

1. **검증계 DLQ purge.** 적체된 메시지는 코드를 고쳐도 사라지지 않고, 재구동해도 항상 400이라 되살릴 수 없다. 절차는 §부록 B.
2. **dev·stg의 UNIQUE 인덱스 적용 확인**(§부록 D).
3. **펌웨어 담당에게 재전송 정책 이슈 전달.** 기기가 보관 기간·횟수 상한 없이 무기한 재전송한다. 서버는 30일 가드로 방어만 하고 있고 근본 해결이 아니다. 같은 채널로 §3-3의 펌웨어 건도 함께 제기하기로 했다.

---

### 3-3. 유령 2002 → 에스원 204 오전송 (**미해결·패치 미착수**)

**이 문서에서 가장 먼저 붙어야 할 항목이다.** 발견 2026-08-05, 이후 코드 변경 없음(2026-09-14 코드 재확인 시에도 원인 코드 그대로).

**증상.** 보안모드를 **시작한 적이 없는 기기**에 `security_mode_history` 2002(종료) · `trigger_type='ADMIN'` 행이 남는다.

**연쇄.**

```
OASYS R047 을 subscribed=false(해지) 로 수신
  → OasysService.subscriptSecurityMembershipInfo 의 해지 분기가
    조건 없이 securityMode stop 커맨드를 발행한다        ← OasysService.java:264
       new SecurityModeCommand(null, false, null, null, true)
                                              ↑ isAdmin 하드코딩 true
  → SecurityModeCommand 생성자가 initiatedBy = "ADMIN" 을 부여한다
  → 기기는 {"message":"Already Stopped","result":true} 로 응답하면서도
    동일 correlationId 로 2002 이벤트를 함께 발행한다 (응답과 6ms 차)
  → 기기 페이로드의 triggerType(=ADMIN 에코백)을 서버가 그대로 이력에 저장
  → 2002 는 S1 전송 대상이고
    EventCodes.toS1SecurityModeEventCode(2002, ADMIN) == 204
  → 무장한 적 없는 기기의 "관리자에 의한 보안모드 종료"가 에스원에 전송된다
```

**실제 피해는 이력 한 줄이 아니라 에스원 오전송이다.** 발견 당시(2026-08-05)의 메모에는 "S1 전송이 이력 저장보다 먼저 실행된다"고 적혀 있으나, **현재 코드는 그렇지 않다** — §3-2의 `7443f56`이 2001/2002 이력 저장을 에스원 전송보다 앞으로 옮겼다(`DeviceEventService` 131~134행). 조사에 중요한 차이다: **이력에 남은 유령 2002 건수 = 에스원 전송을 시도한 건수의 상한**이며, 30일 보관기간 가드에 걸려 전송되지 않은 건도 이력에는 남는다.

**부수 효과 — `trigger_type='ADMIN'`은 "관제 웹 관리자"를 뜻하지 않는다.** ADMIN을 만드는 경로가 둘이다: `DeviceController`의 `caller.isAdmin()`(진짜 관리자)과 `OasysService`의 하드코딩 `true`(해지 자동 정지). 기기 페이로드에서 둘이 구분되지 않아 **이력만으로는 추적이 불가능하다.** 운영 문의 대응 때 이 점을 기억해야 한다.

**배제한 방안 — `robotStatus`로 stop 커맨드를 게이팅하지 않는다.** 조회 수단은 있다(`RobotStatusPort` → 플랫폼 공유 Redis `operation:{serial}`의 `robotStatus`, 값 `FIXED_SECURITY`/`PATROL_SECURITY`). 배제한 이유 셋:

1. `RedisRobotStatusAdapter`가 Redis 장애·키 부재·파싱 실패를 **전부 `"UNKNOWN"`으로 뭉갠다.** 이걸로 게이팅하면 해지된 기기가 무장 상태로 남는 false negative가 생긴다. 현 구조의 false negative는 0이다.
2. 우리가 소유하지 않은 **타 팀 캐시에 안전 동작을 매다는 셈**이다. 기존 사용처는 FCM 발송 여부 판단이라 등급이 다르다.
3. **층이 틀렸다.** stop 커맨드 자체는 잘못이 없다 — 관제 웹·앱에서 이미 정지된 기기에 정지를 눌러도 같은 유령 이벤트가 난다. 진입점을 막아봐야 다른 진입점이 남는다.

**합의한 방향 (미착수).**

| 순서 | 조치 | 내용 |
|---|---|---|
| ① 서버 (본 조치) | 중계 지점 차단 | 자동 정지 시 발행한 `correlationId`를 짧은 TTL로 남기고, 같은 `correlationId`로 들어온 2001/2002는 **S1 전송만 스킵**한다. **이력은 저장한다.** 진입점이 아니라 중계 지점을 막으므로 관제·앱 경로까지 한 번에 커버된다 |
| ② 펌웨어 (근본) | 모순 해소 | `Already Stopped` 응답과 2002 이벤트를 동시에 올리는 동작을 고친다. §3-2의 무한 재전송 이슈와 같은 채널로 제기 |
| ③ 무위험 선행 | 정량화 | `robotStatus`를 **판단이 아니라 로깅용으로만** 넣어 유령 이벤트 빈도를 측정한다 |

**피해 규모 미집계.** 2001 없이 2002만 있는 기기 전수 조사 쿼리를 전달했으나 결과를 받지 못했다. 쿼리는 §부록 D에 있다. 건수에 따라 **에스원과 정정 협의가 필요할 수 있다** — 에스원 쪽에는 있지도 않던 보안 세션이 해제된 기록이 남아 있다.

**조사할 때 주의.** 2026-08-13 감사 backport(`e6af9c4`) 이후 **S1 아웃바운드 호출의 요청·응답 본문은 `outbound_api_log`에 저장되지 않는다**(`[REDACTED:PII]`). 따라서 로그 테이블에서 "204를 보냈다"는 본문 증거를 찾을 수 없다. 남는 것은 `endpoint='/skix/event/occur'` 호출 행과 `security_mode_history`의 2002/ADMIN 행이다. 둘을 시각으로 대사해야 한다.

---

### 3-4. R047 구독 해지 시 PIN 이력 무효화

**무엇이 문제였나.** PIN 이력 초기화가 **기기 등록·연동해제 경로에만** 걸려 있었다(`backend-api-main`의 `ProductDeviceService` → `DELETE /internal/v1/devices/{serial}/pin/history`). 기기 연동은 그대로 두고 **구독만 해지**하면 이전 계약자의 PIN이 '설정됨'으로 남는다. 해지→재구독(계약자가 바뀔 수 있다) 시 앱이 PIN 재설정을 유도하지 않는다.

**무엇을 고쳤나.** `OasysService.subscriptSecurityMembershipInfo`의 해지 분기(`if (!subscribed)`)에 PIN 이력 무효화를 추가했다. 위치는 보안모드 정지·스케줄 삭제 바로 다음이다(`OasysService.java` 270~290행).

```java
try {
    int invalidated = pinHistoryPort.invalidatePinHis(serial);
    log.info("PIN history invalidated after unsubscribe: serial={}, rows={}", serial, invalidated);
} catch (Exception e) {
    log.error("[PIN_HISTORY_INVALIDATE_FAILED] serial={}", serial, e);
}
```

**설계 판단 두 가지 — 되돌리려 할 때 반드시 읽을 것.**

1. **해지(`subscribed=false`)에만 건다.** R047은 최초 가입뿐 아니라 **요금제 변경·갱신**으로도 도착한다. `subscribed=true`에도 걸면 서비스 중인 기기의 정상 PIN이 반복 초기화된다. 미구독 구간에 `/pin/check`가 false인 것은 무해하다 — 그 구간의 보안 기능은 `@SubscriptionRequired`가 이미 막는다.
2. **`DeviceCommandService.invalidatePinHis`를 쓰면 안 되고 `PinHistoryPort`를 직접 쓴다.** 그 메서드는 내부에서 `OasysContractResolveService.evict(serial)`를 먼저 부르는데, evict는 Redis 캐시뿐 아니라 **`device_current_contract` 행까지 DELETE** 한다. 같은 메서드 위쪽에서 `upsertFromR047`로 막 확정한 R047 계약번호가 되돌아 지워진다. 이 불변식을 고정하는 테스트가 `OasysServiceMembershipStatusTest`에 있다(호출 순서까지 검증).

**멱등성은 SQL이 보장한다.** `PinHistoryMapper.xml`의 `invalidatePinHistory`가 `UPDATE ... SET invalidated_at = CURRENT_TIMESTAMP(3) WHERE serial = ? AND invalidated_at IS NULL`인 soft-invalidate다. R047이 10분 배치로 중복 수신돼도 안전하고 감사 추적도 남는다.

**현재 상태.** `dev` `955ba0a` / `stg` / `main` `f19e9d7` 반영 (main은 cherry-pick이라 해시가 다르다 — §3-6의 2번 주의). **ECS 배포 미확인.**

**남은 것 — 배포 후 확인 3건.**

| # | 확인할 것 | 왜 |
|---|---|---|
| 1 | dev 배포 후 실제 해지 통보에서 `PIN history invalidated after unsubscribe: serial=..., rows=N` 로그와 `/pin/check` false 전환 대사 | `rows=0`은 결함이 아니라 "무효화는 돌았고 지울 이력이 없었다"는 뜻이다 |
| 2 | **앱이 `/pin/check` 응답만으로 재설정을 유도하는지** | 앱이 로컬에 '설정함' 플래그를 캐시하면 서버만 고쳐서는 화면이 안 바뀐다. iOS·Android 담당 확인 필요 |
| 3 | **기기 펌웨어에 남은 PIN 실물** | 이번 변경은 DB 이력만 무효화한다(기존 연동·해제 경로와 동일 범위). 재구독 후 새 PIN을 설정하기 전까지 이전 계약자 PIN이 기기에서 여전히 유효하다면 **별건 티켓 사안**이다 |

---

### 3-5. 감사 브랜치 backport

**무엇이었나.** 사내 보안점검 제출용으로 소스를 정리한 브랜치 `submission/security-audit-20260811`(제출용 스쿼시 커밋 `717f136`)이 있었다. 제출본은 스쿼시라 그대로 머지할 수 없고, 내용 중에는 적용하면 **오히려 나빠지는 것**도 섞여 있었다. 변경된 24개 파일을 전수 검토해 실제 적용할 것만 `dev`로 옮겼다.

**적용한 것 — 4커밋 + 철회 1커밋.**

| 커밋 | 내용 |
|---|---|
| `e6af9c4` | **로그 평문 제거.** ① MQTT PIN 커맨드가 평문 PIN을 `mqtt_publish_log`에 저장하던 것 ② `/pin` 계열 인바운드 요청 본문 ③ 아웃바운드 S1·OASYS 페이로드(`S1DispatchRequest`의 `userName`·`userPhone`·`userAddr`) ④ MQTT 파싱 실패 시 payload 전문, PIN PASS 불일치 시 loginId 로그. 아울러 본문 컬럼 어휘를 `LogBodyPolicy`로 통일 |
| `6f51143` | **`@AdminPermission` 6곳 추가.** `/{serial}/status`, `/{serial}/events/detail`, `/{serial}/app-url`, `/{serial}/subscription/payment/history`, `/{serial}/s1/dispatch/status`, `/{serial}/s1/dispatch/cancel`. 특히 **출동 취소**가 권한 확인 없이 관제에서 호출 가능했다 |
| `bdc2102` | 조회 API 날짜 범위 가드(92일) + `limit` clamp(100) + `cursor` 정규식 |
| `7e91af9` | **위의 날짜 가드를 전량 철회** + `dispatch_billing_history` 인덱스 2종 추가 (아래 설명) |
| `18e69dd` | **로컬 프로필 시크릿 분리.** `application-localhost.yml`에 실 자격증명 6종이 커밋돼 있었다 |

**`LogBodyPolicy` — 본문 컬럼 어휘 (신설).** 전부 `null`로 저장하면 "응답이 없었던 것"과 "민감정보라 지운 것"을 구별할 수 없어 장애 분석이 막힌다. `common/utils/LogBodyPolicy.java`:

| 값 | 뜻 |
|---|---|
| `null` | 본문 자체가 없음 (GET 요청, 204 응답 등) |
| `[REDACTED:PIN]` / `[REDACTED:PII]` / `[REDACTED:CREDENTIAL]` | 정책상 미저장 |
| `[NOT_CAPTURED]` | 본문은 있었으나 필터가 캐시하지 못함 (핸들러 미도달 등) |
| `...(truncated)` 접미사 | 길이 초과로 잘림 |

`[REDACTED` 접두사를 유지해 사유 없이 저장된 과거 데이터도 같은 `LIKE '[REDACTED%'`에 걸린다.

**대상별 적용 현황** (현재 코드 실측):

| 대상 | 요청/응답 본문 | 근거 |
|---|---|---|
| S1 일반 API (`S1ApiConfig`) | `[REDACTED:PII]` | 출동 전문에 이름·전화번호·주소 |
| S1 KVS play (`S1KvsClientConfig`) | `[REDACTED:CREDENTIAL]` | 응답에 AWS 임시 자격증명 |
| **S1 KVS stop** | **그대로 저장** | 본문이 `device`+`deviceId`뿐이라 민감정보가 없다. 의도적이다 |
| OASYS | `[REDACTED:PII]` | |
| PLATFORM | 원칙적으로 저장 | 스케줄·계약·영역명은 장애 분석에 본문이 필요하다 |
| PLATFORM `/api/internal/security/check-member` 응답 | `[REDACTED:PII]` | 경로 단위 예외. `cf5f56b` — `main` 미반영(§3-6) |

**날짜 범위 가드를 철회한 이유.** 협의 없이 외부 호출자를 제약하는 것은 순서가 뒤바뀐 조치였다. 엔드포인트별로 실익을 다시 확인한 결과:

- `GET /{serial}/events` — 에스원 이벤트 보관 기간이 30일이라(`S1_EVENT_RETENTION`) 범위를 넓게 줘도 반환량이 그 이상 늘지 않는다. **가드가 막는 것이 없다.**
- `/internal/v1/oasys/**` — 외부 API가 아니라 **로컬 MySQL 조회**다. 얻는 것에 비해 조율 불가능한 외부 시스템(OASYS)을 깨뜨릴 위험이 크다.
- `security-mode/history` — 날짜가 선택 파라미터라 한쪽만 와도 가드가 적용되지 않는다. 실효가 거의 없었다.
- `92`라는 값 자체가 측정이 아니라 안전마진 추측이었다.

**대신 실제 병목을 고쳤다.** OASYS 정산 조회가 느렸던 진짜 원인은 날짜 범위가 아니라 `dispatch_billing_history`의 `ctrt_no`·`icst_no` 인덱스 부재였다. 등가 조건 컬럼을 선두에 둔 복합 인덱스 2종을 참조 DDL에 추가했다. **ALTER는 세 환경 모두 미적용**(§부록 D).

**유지한 것:** `limit` clamp(100, `DeviceEventService.MAX_PAGE_LIMIT`)와 `cursor` 정규식(`^[0-9]{1,19}$`, `DeviceController`). 호출 계약을 좁히지 않고 자원 사용만 묶는 변경이라 성격이 다르다. `limit`에 **`defaultValue=50`은 적용하지 않았다** — `DeviceEventService`는 `limit`이 null이면 페이징을 건너뛰고 전체를 반환하므로, 기본값 주입은 기존 클라이언트의 응답 건수를 바꾼다.

**로컬 시크릿 분리 방식.** `config/local-secrets.yml`(gitignore) + `config/local-secrets.yml.example`(커밋). `application-localhost.yml`의 `spring.config.import: file:./config/local-secrets.yml`이 읽으며 **`optional:` 접두사를 쓰지 않았다** — 파일이 없으면 `ConfigDataResourceNotFound`로 경로를 명시하며 기동 즉시 실패한다(조용히 빈 값으로 뜨는 것보다 낫다). 분리 대상 6종: OASYS dev 계정, KVS lease secret, 서비스 JWT 키쌍, AWS IoT 디바이스 인증서·키, 관리자 JWT 시크릿. `datasource.password`(로컬 docker 전용)·KVS role ARN·Cognito 풀 ID는 비밀이 아니라 커밋에 남겼다. **인수자는 `cp config/local-secrets.yml.example config/local-secrets.yml` 후 값을 채워야 로컬 기동이 된다.** 각 값의 발급 경로는 `.example` 파일의 주석에 적혀 있다.

**적용 금지 판정 3건 — 재검토 방지용 근거.** 감사 제출본에 있지만 **적용하면 안 되는** 것들이다. 나중에 "왜 이건 안 고쳤지?" 하고 다시 손대지 않도록 근거를 남긴다.

| # | 제출본의 제안 | 적용 금지 이유 |
|---|---|---|
| 1 | `AdminJwtDecoder`의 로그아웃 블랙리스트 Redis 키를 SHA-256 지문으로 | **보안 후퇴다.** `skix-security`는 이 Redis 키를 **읽기만** 하고, 쓰는 쪽은 `backend-api-main`의 `ManagerService.java:725`가 **원문 accessToken**으로 저장한다. 읽는 쪽만 해시로 바꾸면 `hasKey()`가 영구 false가 되어 **관리자 로그아웃 블랙리스트가 무력화된다.** 바꾸려면 양쪽을 같은 배포에서 함께 바꿔야 한다 |
| 2 | `RequestLoggingFilter`의 `/files/download` 스킵 제거 | **메모리 회귀.** 다운로드 응답 전체가 `ContentCachingResponseWrapper`로 힙에 버퍼링된다 |
| 3 | `jwks.json` permitAll 설정 제거 | **무의미.** 해당 경로를 서빙하는 컨트롤러가 없다(죽은 설정). Cognito 원격 JWKS만 사용한다. 참고로 이 설정 자체는 9월에 별도 커밋 `88f14d5`로 정리됐다 |

그 외 CORS 오리진 목록과 `/internal` 공유키 도입은 **이번 범위에서 제외**하기로 협의했다(2026-08-13). CORS는 9월에 별건 커밋 `b5b373a`로 와일드카드 제거·비활성화 처리됐다.

**`@AdminPermission` 영향 범위.** `AdminPermissionInterceptor`는 `isAdminUser()`가 아니면 즉시 통과하므로 **앱 사용자 경로에는 영향이 0이다.** 관제 쪽 `/control/robot` 권한 부여는 2026-08-13 확인 완료. 현재 `dev` 기준 컨트롤러의 `@AdminPermission`은 29곳이다(backport 시점 26곳 + 이후 KVS 작업분).

**현재 상태.** 5커밋 모두 `dev`·`stg`·`main` 반영. 승격 경로는 `security/log-redaction-and-request-guards` → dev(`2eb9a28`) → stg(`dcc4596`) → main(`2089d82`). **ECS 배포 미확인.**

---

### 3-6. `main` 선별 반영 규칙 — **다음 승격 때 통째 머지 금지**

**무엇인가.** `skix-security`의 `main`(운영 브랜치)은 `dev` 전체가 아니라 **선별 반영** 상태다. 운영 미배포 의존성이 있는 백엔드 변경 2건을 일부러 뺐다.

| 제외 커밋 | 내용 | 제외 이유 |
|---|---|---|
| `b1b148b` (08-22) | 토큰에서 `loginId` 클레임 의존 제거 — Cognito 토큰에서는 `sub`만 읽고, 로그인 아이디는 플랫폼 `check-member` **인가 응답**으로 받는다 | **코드 결함이 아니라 플랫폼(`backend-api-main`) 운영 배포 미완료 때문이다.** 이 변경은 `backend-api-main`의 check-member 응답 확장이 **먼저** 나가 있어야 동작한다 |
| `cf5f56b` (08-23) | `check-member` 응답을 아웃바운드 로그에 저장하지 않는다 | 위 변경의 짝이다. 응답에 실려 오는 `loginId`가 전화번호라 매 인가 호출마다 로그 DB에 쌓인다. 앞 커밋 없이 이것만 올릴 이유가 없다 |

**선행 조건 실측 (2026-09-22).** `backend-api-main`의 대응 커밋 `59b359a4` "[feat] check-member 응답에 해석된 loginId 를 담는다"는 **`dev`·`stg`에는 반영, `main`에는 미반영**이다. 즉 **제외 해제 조건은 `backend-api-main`이 운영계에 나가는 것**이고, 아직 충족되지 않았다.

> 주의: 같은 제목의 커밋이 `ba2092bc`·`0ecd8ade`·`59b359a4` 세 개 존재한다(리베이스 흔적). `dev`·`stg`에 실제로 들어간 것은 **`59b359a4`**다. `b1b148b`의 커밋 메시지에 적힌 `ba2092bc`는 리베이스 전 해시라 브랜치 추적에 쓰면 "미반영"으로 잘못 나온다.

**`b1b148b` 자체의 배포 순서 제약** (승격할 때 반드시 지킬 것): `backend-api-main`의 check-member 응답 확장이 **먼저** 배포돼야 한다. 구버전 플랫폼과 섞이면 응답의 `loginId`가 비고, 그 경우 이 코드는 **즉시 실패시킨다** — 빈 전화번호가 S1 전문에 나가기 전에 배포 순서 오류를 잡기 위한 의도적 fail-fast다.

**다음 승격 때 주의 3가지.**

1. **`git merge dev` into `main`을 그냥 하면 제외한 2건이 조상으로 딸려온다.** 지금까지는 원격 맵핑 머지커밋(`f26d8f1`)만 골라 머지하고(`a9a35fd`), PIN 커밋은 `cherry-pick -x`로 옮겼다(`f19e9d7`). **`stg`도 `dev`와 내용이 같으므로 `stg` → `main` 통째 머지도 같은 문제가 있다.**
2. **PIN 이력 커밋은 `dev`(`955ba0a`)와 `main`(`f19e9d7`)의 해시가 다르다**(cherry-pick). 나중에 `dev`를 통째 머지하면 git이 같은 내용을 양쪽에서 보게 되는데, 그 사이 같은 줄이 더 바뀌면 **충돌한다.** 충돌이 나도 "중복 적용 사고"가 아니라 정상 상황이니 내용 기준으로 해소하면 된다.
3. **검증 방법** (승격 직후 실행):

```bash
cd ~/benjamin/skix-security

# (a) 제외 2건이 main에 없어야 정상 — 두 명령 모두 "제외됨" 이 나와야 한다
for c in b1b148b cf5f56b; do
  git merge-base --is-ancestor $c origin/main \
    && echo "$c: 딸려 들어감 — 되돌려야 함" \
    || echo "$c: 제외됨 (정상)"
done

# (b) main 에 없는 커밋 목록 확인. '+' 가 내용까지 없는 것, '-' 는 동등 커밋이 있는 것
git cherry -v origin/main origin/dev

# (c) main 과 dev 의 코드 차이 확인
git diff --stat origin/main origin/dev | tail -1
```

> **`(c)`의 기대값은 시점에 따라 달라진다.** 2026-08-31 시점에는 "정확히 제외 2건의 파일 20개"(`cf5f56b` 2개 + `b1b148b` 18개)였으나, 2026-09-22 현재는 9월 OASYS 직접 구독 작업이 더해져 **42파일**이다. 숫자를 외우지 말고 `(b)`의 `git cherry` 출력으로 "제외 2건 + 의도한 승격 대상"만 있는지 확인하라.

4. **이 `main` 조합은 어디서도 빌드된 적이 없다.** cherry-pick과 선별 머지로 만들어진 트리이므로 push 전 `./gradlew clean test` 필수다(2026-08-31 시점 437건 통과, 2026-09-22 현재 `src/test` 61개 파일·`@Test` 454개).

---

## 4. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 실제 사고가 나는" 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **비재시도 종결은 `NON_RETRYABLE_S1_ERROR_CODES` allowlist 안에서만.** 4xx 전체를 삼키지 않는다 | 408·429·인증 장애도 함께 삼켜져 **보안 이벤트가 조용히 유실된다.** 침입 이벤트가 에스원에 안 가도 아무도 모른다. 반례 테스트(allowlist 밖 4xx·코드 없는 4xx 전파)가 `DeviceEventServiceTest`에 있다 |
| 2 | **2001/2002 로컬 이력은 보관기간 가드·S1 전송보다 먼저 저장한다** | 에스원이 거절하거나 30일이 지나면 우리 이력조차 남지 않는다. 사후 조사가 불가능해진다 |
| 3 | **그 선저장은 반드시 `UNIQUE (serial, event_code, event_time)` + `ON DUPLICATE KEY UPDATE id = id`와 함께다** | 인덱스 없이 선저장만 남기면 에스원 5xx → SQS 재시도마다 **중복 이력 행이 재시도 횟수만큼** 쌓인다 |
| 4 | **stop 통보 대상은 arm 시점(`armProviderIssue`)에 기록한 값만 본다. finalize 시점 재조회 금지** | 세션 도중 계약-기기 매핑이 바뀌면 **다른 기기의 세션을 끊는다.** 결과 코드로 역산하는 것도 금지 |
| 5 | **stop에 재시도·outbox를 두지 않는다 (at-most-once)** | stop 전문에는 세션 식별자가 없다. 재시도가 **이미 시작된 새 세션을 끊는다.** 놓친 stop은 에스원 자체 만료와 다음 시도의 자기 치유로 수렴한다 |
| 6 | **stop 전용 서킷브레이커(`s1-kvs-stop`)·RestClient·풀을 play와 분리한다. window=5·min-calls=5** | 공유하면 stop 엔드포인트 단독 장애가 **신규 라이브뷰 발급(play)을 차단한다.** window를 넓히면 warm 호출에 희석돼 회로가 안 열린다 |
| 7 | **`NO_ACTIVE_SESSION`은 멱등 성공.** 예외도 warn도 내지 않는다 | 통보의 목적 상태가 이미 달성된 경우다. 실패로 처리하면 정상 운영에서 서킷이 열리고 경보가 쌓인다 |
| 8 | **에스원 `deviceid` 이중 전송을 되살리지 않는다** | 되살리는 게 아니라, **상대 환경의 정정 반영을 먼저 확인**하는 것이 맞는 순서다. 미정정 환경에서는 camelCase 단독 전송이 전면 거절되므로 증상이 "라이브뷰가 아예 안 됨"으로 나타난다. §3-1의 미결 1건 |
| 9 | **`expiration` 파싱에서 오프셋이 없으면 KST로 해석한다** | 반대로 UTC로 가정하면 **만료된 자격증명을 유효로 오판한다.** KST 해석은 만료를 9시간 이르게 잡는 안전측 실패다. 잉여 `Z` 정규화도 미반영 환경 방어로 남겨 둔다(오프셋이 이미 있을 때의 잉여 Z만 떼고, 오프셋 없는 정상 `...Z`는 건드리지 않는다) |
| 10 | **`robotStatus`로 해지 stop 커맨드를 게이팅하지 않는다** | 어댑터가 Redis 장애·키 부재·파싱 실패를 전부 `"UNKNOWN"`으로 뭉갠다. 게이팅하면 **해지된 기기가 무장 상태로 남는** false negative가 생긴다. 현 구조의 false negative는 0이다 (§3-3) |
| 11 | **R047 PIN 무효화는 해지(`subscribed=false`)에만 건다** | R047은 요금제 변경·갱신으로도 도착한다. `true`에 걸면 **서비스 중인 기기의 정상 PIN이 반복 초기화된다** |
| 12 | **PIN 무효화는 `PinHistoryPort`를 직접 호출한다. `DeviceCommandService.invalidatePinHis` 금지** | 그 메서드의 `evict()`가 Redis 캐시뿐 아니라 **`device_current_contract` 행까지 DELETE** 한다. 같은 메서드 위쪽에서 `upsertFromR047`로 확정한 계약번호가 되돌아 지워진다 |
| 13 | **`LogBodyPolicy` 어휘를 "전부 null"로 되돌리지 않는다** | "응답이 없었던 것"과 "민감정보라 지운 것"이 구별되지 않아 장애 분석이 막힌다. `[REDACTED` 접두사도 유지해야 기존 데이터가 같은 `LIKE`에 걸린다 |
| 14 | **`DateRangeGuard`를 협의 없이 재도입하지 않는다.** `limit` clamp·`cursor` 검증은 유지 | 조율 불가능한 외부 시스템(OASYS)의 호출을 깨뜨린다. 상한이 필요하면 **호출 측과 합의한 값**으로 넣고, `validate(start, end, maxDays)` 오버로드 자리가 준비돼 있다 |
| 15 | **`limit`에 `defaultValue`를 넣지 않는다** | `limit`이 null이면 전체를 반환하는 기존 동작이 있다. 기본값 주입은 **기존 클라이언트의 응답 건수를 바꾼다** |
| 16 | **`AdminJwtDecoder`의 로그아웃 블랙리스트 키를 해시화하지 않는다** | 쓰는 쪽(`backend-api-main`)이 원문으로 저장한다. 읽는 쪽만 바꾸면 **관리자 로그아웃 블랙리스트가 영구 무력화된다** (§3-5) |
| 17 | **`main` 승격 시 `dev`/`stg`를 통째 머지하지 않는다** | 의도적으로 제외한 2커밋이 조상으로 딸려 들어간다. 그 2건은 `backend-api-main` 운영 배포 선행이 필요하다 (§3-6) |

---

## 5. 앱·펌웨어·타 팀에 요청한 내용

### 5-1. 펌웨어 담당 (2건, 같은 채널로 제기 합의)

1. **무기한 재전송 정책.** 기기가 보관하던 이벤트를 **보관 기간·횟수 상한 없이** 6분 주기로 무한 재전송한다. 서버는 30일 가드로 방어만 하고 있어 근본 해결이 아니다. 기기 쪽에 재전송 상한(기간 또는 횟수)을 두어 달라는 요청이다. (§3-2)
2. **`Already Stopped` 응답과 2002 이벤트 동시 발행.** 정지 커맨드에 `{"message":"Already Stopped","result":true}`로 답하면서 같은 correlationId로 2002(보안모드 정상 종료) 이벤트를 6ms 차로 함께 올린다. **이미 정지된 기기가 "정상 종료"를 보고하는 것은 모순이다.** 이것이 에스원 204 오전송의 근본 원인이다. (§3-3)

### 5-2. 에스원 측 (1건, 미결)

**stg·prd 환경에 전문 정정 2건이 반영됐는지 확인.** 개발계에서는 2026-08-11 반영을 실측 확인했다.
- `deviceId` camelCase 단독 수용 (소문자 `deviceid` 오타 정정)
- `expiration`의 RFC 3339 준수 (`2026-08-11T01:10:40Z` 형태)

우리는 이중 표기 전송을 제거했으므로, **미정정 환경에서는 `play`가 전면 거절된다.** `KVS_PROVIDER=s1` 전환 전 필수 확인 항목이다.

### 5-3. 앱 담당 (1건, 배포 후 확인)

**`/pin/check` 응답만으로 PIN 재설정을 유도하는지.** 앱이 로컬에 'PIN 설정함' 플래그를 캐시하고 있으면 서버가 이력을 무효화해도 화면이 바뀌지 않는다. (§3-4)

---

## 6. 배포 방법

### 6-1. 순서

```
1. DDL 선적용          ← §부록 D. 코드보다 먼저여도 무해하다(근거는 §3-2)
2. skix-security       ← 소비자(플랫폼·스트리밍)보다 먼저.
                         소비자가 fail-closed 라 순서를 뒤집으면 소비자가 먼저 깨진다
3. 검증 (§6-2)
4. DLQ purge           ← 검증계. 코드 배포 이후에 한다 (§부록 B)
```

`main` 승격이 포함되는 배포라면 **§3-6의 검증 스크립트를 push 전에 반드시 돌린다.** 그리고 `./gradlew clean test`도 필수다(`main` 트리는 선별 머지·cherry-pick으로 만들어져 CI에서 빌드된 적이 없다).

### 6-2. 배포 후 검증

**신규 환경변수·SSM 파라미터는 없다.** 이 묶음의 변경은 전부 코드와 DDL이다.

```bash
# 1) 기동 확인 — 로컬 프로필 시크릿 분리 때문에 기동 실패가 나는지 먼저 본다.
#    ECS 는 ecs 프로필이라 영향 없지만, 로컬에서 재현할 때는
#    config/local-secrets.yml 이 없으면 ConfigDataResourceNotFound 로 즉시 죽는다.
./gradlew clean test        # 전 항목 통과 확인

# 2) 관제 권한 회귀 (@AdminPermission 6곳 추가분).
#    관제 계정에 /control/robot 권한이 있는 상태에서 아래가 200 이어야 한다.
#    권한 없는 관제 계정으로는 403 이어야 한다.
#      GET  /security/v1/devices/{serial}/status
#      GET  /security/v1/devices/{serial}/events/detail
#      POST /security/v1/devices/{serial}/app-url
#      GET  /security/v1/devices/{serial}/subscription/payment/history
#      GET  /security/v1/devices/{serial}/s1/dispatch/status
#      POST /security/v1/devices/{serial}/s1/dispatch/cancel
#    ※ 앱 사용자 경로는 영향 0 (인터셉터가 isAdminUser() 아니면 즉시 통과)

# 3) 이벤트 페이징 회귀 — limit clamp(100)·cursor 정규식
curl -s "https://<host>/security/v1/devices/<serial>/events?startDate=2026-09-01&endDate=2026-09-22&limit=500"
#    → 200. 반환 건수가 100 이하. 400 이 아니다(거절이 아니라 낮춰 받는 것이 의도)
curl -s -o /dev/null -w '%{http_code}\n' \
  "https://<host>/security/v1/devices/<serial>/events?startDate=2026-09-01&endDate=2026-09-22&cursor=abc"
#    → 400 (cursor 는 숫자 1~19자리만)

# 4) 로그 마스킹 확인 — 아래 3개 쿼리가 모두 0건이어야 한다 (§부록 D 참조)
```

**로그 마스킹 확인 SQL** (배포 후 몇 시간 뒤 실행):

```sql
-- S1·OASYS 아웃바운드에 평문 본문이 남아 있으면 안 된다
SELECT COUNT(*) FROM security.outbound_api_log
 WHERE target IN ('S1','OASYS')
   AND called_at > NOW() - INTERVAL 1 DAY
   AND request_body IS NOT NULL
   AND request_body NOT LIKE '[REDACTED%'
   AND request_body NOT LIKE '[NOT_CAPTURED]';
-- 기대 0. 단 KVS stop(/skix/livevideo/stop)은 의도적으로 저장하므로
-- 0 이 아니면 endpoint 를 함께 확인할 것

-- MQTT PIN 커맨드 페이로드가 남아 있으면 안 된다
SELECT COUNT(*) FROM security.mqtt_publish_log
 WHERE topic LIKE '%command/pin%'
   AND payload IS NOT NULL
   AND payload NOT LIKE '[REDACTED%';
-- 기대 0
```

**R047 PIN 무효화 동작 확인** (실제 해지 통보가 온 뒤):

```sql
SELECT serial, invalidated_at FROM security.pin_history
 WHERE serial = '<serial>' ORDER BY id DESC LIMIT 5;
```
서버 로그에서 `PIN history invalidated after unsubscribe: serial=..., rows=N`을 찾는다. `rows=0`은 정상이다(지울 이력이 없었다는 뜻).

### 6-3. 롤백

| 대상 | 롤백 방법 | 주의 |
|---|---|---|
| 애플리케이션 | 이전 이미지 리비전으로 되돌린다 | **`security_mode_history`의 UNIQUE는 되돌리지 않는다.** 구버전 코드도 중복 INSERT 시 행이 스킵될 뿐이고, 유일한 INSERT 경로가 `SafeRunner`로 감싸여 있으며 이 저장소에 `@Transactional`이 0건이라 트랜잭션 오염이 없다. ERROR 로그 노이즈만 남는다 |
| `mqtt_publish_log.success` DEFAULT 1 | 되돌리지 않는다 | DEFAULT를 빼면 구버전 코드의 INSERT가 strict mode에서 `Field 'success' doesn't have a default value`로 거절된다. 운영계에서 실제로 발생한 사고다 |
| `dispatch_billing_history` 인덱스 | 되돌리지 않는다 | 조회 성능만 개선한다. 기능 영향 없음 |

---

## 7. 남은 일

### 7-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **세 환경 ECS 배포 실태 확인.** 이 문서의 코드는 전부 브랜치에 있으나 배포 여부를 로컬에서 볼 수 없다. dev·stg·prd 태스크 정의 리비전과 실행 이미지 태그 확인 | 백엔드 | VDI. 미배포면 §6-1 순서로 배포 |
| 2 | **검증계 DLQ 적체 확인·purge.** 적체분은 코드 수정으로 사라지지 않고 재구동해도 항상 400이다 | 백엔드 | 절차 §부록 B |
| 3 | **dev·stg의 `uk_serial_event_code_time` UNIQUE 적용 확인.** prd만 확인됨(2026-08-05) | 백엔드 | SQL §부록 D |
| 4 | **유령 2002 피해 규모 집계.** 2001 없이 2002/ADMIN만 있는 기기 전수 조사. 건수에 따라 에스원 정정 협의 필요 | 백엔드 | SQL §부록 D |
| 5 | R047 PIN 무효화 동작 대사 — dev에서 실제 해지 통보 시 `rows=N` 로그와 `/pin/check` false 전환 | 백엔드 | §6-2 |

### 7-2. 릴리스 게이트 (통과 전 진행 금지)

| # | 게이트 | 막는 것 | 상대 |
|---|---|---|---|
| 6 | **에스원 stg/prd 환경의 전문 정정 반영 확인** | `KVS_PROVIDER=s1` 전환. 미정정 환경에서는 라이브뷰 play가 전면 거절된다 | 에스원 측 |
| 7 | **`backend-api-main` 운영 배포 (check-member 응답 확장 `59b359a4`)** | `b1b148b`·`cf5f56b`의 `main` 승격. 순서를 뒤집으면 빈 전화번호로 fail-fast 한다 | 백엔드 |
| 8 | `main` 승격 시 §3-6 검증 스크립트 통과 + `clean test` 통과 | 운영 배포 | 백엔드 |

### 7-3. 후속

| # | 항목 | 우선도 근거 |
|---|---|---|
| 9 | **유령 2002 서버 패치 착수** (자동 정지 correlationId를 짧은 TTL로 기록 → 같은 correlationId의 2001/2002는 S1 전송만 스킵, 이력은 저장) | **에스원에 실제로 잘못된 전문이 나가고 있다.** 이 묶음에서 가장 등급이 높다. 설계는 §3-3에 확정돼 있고 구현만 남았다 |
| 10 | 무위험 선행 조치 — `robotStatus`를 **로깅용으로만** 넣어 유령 이벤트 빈도 정량화 | 9번의 효과 측정용. 판단에 쓰면 안 된다(§4의 10번) |
| 11 | 펌웨어 2건 제기 (§5-1) | 근본 해결. 서버 조치는 둘 다 방어책이다 |
| 12 | `dispatch_billing_history` 인덱스 2종 ALTER 적용 | OASYS 정산 조회 성능. 기능 영향 없음 |
| 13 | 앱의 `/pin/check` 캐시 여부 확인 (§5-3) | 서버만 고쳐서는 화면이 안 바뀔 수 있다 |
| 14 | **기기 펌웨어에 남은 PIN 실물** — 재구독 후 이전 계약자 PIN이 기기에서 유효한지 | 유효하다면 별건 보안 티켓 사안 |
| 15 | `trigger_type='ADMIN'`의 출처 2원화 해소 — 관제 관리자와 해지 자동 정지를 구분할 수 있게 | 지금은 이력만으로 추적 불가. 9번 패치 때 같이 처리 가능 |
| 16 | 조회 API 날짜 상한 재도입 여부 — 호출 측(OASYS·관제 UI)과 합의된 값이 나오면 | `validate(start, end, maxDays)` 오버로드 자리가 준비돼 있다 |
| 17 | 지연 stop 의미론 재평가 — "재시청 직후 원인 불명 종료" 관측 시 | 현재는 위험 수용. 대응안은 모호 실패 한정 lease 안전 격리 |
| 18 | 낡은 원격 브랜치 정리 (`origin/AXDO-1784`, `origin/billingType`, `origin/develop_wj`, `origin/develop_0624hk` 등은 이미 `dev`에 포함된 6월 이전 브랜치) | 위생 |

---

## 8. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| **"보안모드를 켠 적이 없는데 종료 이력이 있어요"** | 유령 2002다(§3-3). 해당 시각 근처에 구독 해지(R047 `subscribed=false`)가 있었는지 본다. `trigger_type='ADMIN'`이어도 **관제 관리자가 아니다** |
| **DLQ에 메시지가 쌓인다** | 먼저 이벤트의 `eventTime`이 30일을 지났는지 본다. 지났다면 30일 가드가 배포되지 않은 것이다. 아니라면 에스원 5xx·타임아웃·인증 장애 쪽이다. 로그 키워드는 `Stale device event dropped (S1 retention exceeded)`와 `S1 rejected device event (non-retryable)` |
| **같은 이벤트의 이력이 여러 줄** | `uk_serial_event_code_time` UNIQUE가 그 환경에 없다. §부록 D로 확인 |
| **라이브뷰가 되다가 안 된다 / 재시청이 안 된다** | 에스원 발급(`KVS_PROVIDER=s1`) 환경에서만 해당. `ALREADY_IN_SESSION`이면 이전 stop이 전달되지 않은 것이고, **한 번 더 시도하면 그 실패의 finalize가 stop을 보내 자기 치유된다.** `outbound_api_log`의 `endpoint='/skix/livevideo/stop'` 행을 본다 — stop은 본문이 저장되므로 요청·응답을 그대로 볼 수 있다 |
| **라이브뷰가 아예 전면 실패** | 에스원 환경에 `deviceId` 정정이 반영되지 않았을 가능성(§3-1). 우리는 이중 전송을 제거했다 |
| **`S1 livevideo stop endpoint not found` warn** | 에스원 쪽에 stop 엔드포인트가 아직 없다는 뜻. 운영 릴리스 전에는 무해하고, 릴리스 후에는 구성 오류 경보 대상이다 |
| **해지했는데 PIN이 그대로** | `pin_history`의 `invalidated_at`을 본다. NULL이면 무효화가 안 돌았고, 값이 있으면 서버는 정상이고 앱 캐시 또는 기기 펌웨어 쪽이다(§3-4 미확인 2건) |
| **로그·이력 테이블에서 본문이 비어 있다** | `null`=본문 없음 / `[REDACTED:PIN|PII|CREDENTIAL]`=정책상 미저장 / `[NOT_CAPTURED]`=필터 캐시 실패 / `...(truncated)`=길이 초과. 넷을 구별해서 읽는다 |
| **관제에서 갑자기 403이 난다** | `@AdminPermission` 6곳이 추가됐다(§3-5). 해당 관제 계정에 `/control/robot` 권한이 있는지 확인. 앱 사용자에게는 영향이 없다 |
| **로컬 기동 시 `ConfigDataResourceNotFound`** | `config/local-secrets.yml`이 없다. `cp config/local-secrets.yml.example config/local-secrets.yml` 후 값을 채운다. **의도된 실패다**(`optional:`을 일부러 안 붙였다) |
| **로그 키워드 모음** | `[R047_CONTRACT_SAVE_FAILED]` / `[PIN_HISTORY_INVALIDATE_FAILED]` / `[CRITICAL] Security mode stop failed after unsubscribe` / `[R011_FRESH_RETRY]` / `Subscription cancelled — sending security mode stop` / `KVS stop notify failed (best-effort...)` / `S1 KVS stop already satisfied (no active session)` |

---

## 부록 A. 핵심 커밋 (시간순)

전부 `skix-security`. `main` 열의 값이 다른 것은 cherry-pick이다.

| 날짜 | 커밋 | 내용 | dev | stg | main |
|---|---|---|---|---|---|
| 08-04 | `cfed973` | S1 4xx 이벤트 거절 비재시도 처리 + 30일 보관기간 사전 드랍 | Y | Y | Y |
| 08-04 | `7443f56` | 비재시도를 확정 오류 코드 allowlist로 축소, 2001/2002 이력 선저장 | Y | Y | Y |
| 08-04 | `4a684bd` | 보안모드 이력 멱등 저장 (UNIQUE + ON DUPLICATE KEY) | Y | Y | Y |
| 08-04 | `e62a482` | KVS 종료 시 S1 livevideo/stop 통보 — at-most-once best-effort | Y | Y | Y |
| 08-04 | `c0d23b0` | stop 리뷰 반영 — 누락 창 축소·watchdog 지연 상한 | Y | Y | Y |
| 08-04 | `7d57122` | stop CB warm circuit 상한 정정 (window 5) | Y | Y | Y |
| 08-05 | `2e0df23` | `mqtt_publish_log.success` DEFAULT 1 (스키마 선적용 대비) | Y | Y | Y |
| 08-05 | `b27b8e8` | ↑ 위 7건이 `dev`로 들어온 머지 | — | — | — |
| 08-10 | `36161a5` | S1 livevideo 전문 실측 반영 — `expiration` 잉여 Z·`deviceid`·`result` boolean | Y | Y | Y |
| 08-10 | `f4f5d43` | stop 릴리스 게이트 종료 — `result` 리터럴 확정, cross-account signaling 권한 실측 | Y | Y | Y |
| 08-11 | `0b67ac6` | **전문 정정 반영 — `deviceid` 이중 표기 제거·`expiration` Z 확정 포맷** | Y | Y | Y |
| 08-11 | `a8d9d03` | stop `NO_ACTIVE_SESSION` 멱등 처리 | Y | Y | Y |
| 08-13 | `e6af9c4` | 로그 평문 제거 + `LogBodyPolicy` 신설 | Y | Y | Y |
| 08-13 | `6f51143` | `@AdminPermission` 6곳 보완 | Y | Y | Y |
| 08-13 | `bdc2102` | 날짜 범위 가드 + limit clamp + cursor 검증 | Y | Y | Y |
| 08-13 | `7e91af9` | **날짜 가드 전량 철회** + `dispatch_billing_history` 인덱스 2종 | Y | Y | Y |
| 08-13 | `18e69dd` | 로컬 프로필 시크릿 분리 | Y | Y | Y |
| 08-22 | `b1b148b` | 토큰 loginId 클레임 의존 제거 | Y | Y | **N (의도적 제외)** |
| 08-23 | `cf5f56b` | check-member 응답 아웃바운드 로그 미저장 | Y | Y | **N (의도적 제외)** |
| 08-31 | `955ba0a` / `f19e9d7` | **R047 해지 시 PIN 이력 무효화** | Y | Y | Y (cherry-pick, 해시 다름) |

승격 머지 커밋: dev→stg `dcc4596`·`038d1e7` 외, stg→main `2089d82`·`f9485f9` 외. `main`의 마지막 승격은 `a9a35fd`(2026-08-31, 원격 맵핑 feature 브랜치 선별 머지) + `f19e9d7`(cherry-pick).

---

## 부록 B. SQS DLQ 조사·purge 절차

AWS 접근은 회사 VDI에서만 된다. 공유 계정 IAM으로 로그인해 대상 환경 계정으로 AssumeRole 한 뒤 실행한다.

메인 큐 이름 (태스크 정의 `AWS_SQS_IOT_EVENT_QUEUE` 실측):

| 환경 | 큐 |
|---|---|
| dev | `security-iot-event-dev.fifo` |
| stg | `security-iot-event-stg.fifo` |
| prd | `security-iot-event-prd.fifo` |

DLQ 이름은 태스크 정의에 없다. 메인 큐의 `RedrivePolicy`에서 얻는다.

```bash
REGION=ap-northeast-2
MAIN=security-iot-event-stg.fifo      # 대상 환경으로 바꾼다

# 1) 메인 큐 URL
MAIN_URL=$(aws sqs get-queue-url --region $REGION --queue-name "$MAIN" \
             --query QueueUrl --output text)

# 2) DLQ ARN 찾기 (RedrivePolicy 안의 deadLetterTargetArn)
aws sqs get-queue-attributes --region $REGION --queue-url "$MAIN_URL" \
  --attribute-names RedrivePolicy --query 'Attributes.RedrivePolicy' --output text

# 3) 위에서 얻은 ARN 의 마지막 토큰이 DLQ 이름이다
DLQ=<위 출력의 queue 이름>
DLQ_URL=$(aws sqs get-queue-url --region $REGION --queue-name "$DLQ" \
            --query QueueUrl --output text)

# 4) 적체량 확인
aws sqs get-queue-attributes --region $REGION --queue-url "$DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
                    ApproximateNumberOfMessagesDelayed
```

**purge 전에 반드시 표본을 뜬다.** 지우면 되돌릴 수 없다.

```bash
# 5) 표본 조회 — 삭제하지 않고 읽기만 한다(가시성 타임아웃 뒤 큐로 돌아온다)
aws sqs receive-message --region $REGION --queue-url "$DLQ_URL" \
  --max-number-of-messages 10 --visibility-timeout 5 \
  --message-attribute-names All --attribute-names All \
  > /tmp/dlq-sample.json
python3 -m json.tool /tmp/dlq-sample.json | head -80
```

표본에서 확인할 것 — **아래 3가지가 모두 맞아야 purge 대상이다.**

| 확인 | 기대 |
|---|---|
| `Body`의 `eventCode`·`eventTime` | `eventTime`(epoch ms)을 사람이 읽는 시각으로 바꿨을 때 **30일 이상 과거**여야 한다 |
| `topic` 메시지 속성 | 기기 이벤트 토픽이어야 한다 |
| 실패 원인 | 애플리케이션 로그에서 해당 `serial`·`eventTime`으로 `EVENT_RETENTION_PERIOD_EXCEEDED`를 확인한다 |

`eventTime` 변환:

```bash
python3 -c "import datetime,sys; print(datetime.datetime.utcfromtimestamp(int(sys.argv[1])/1000))" <eventTime>
```

**30일 이내 이벤트가 섞여 있다면 purge하지 말 것.** 그것은 이 이슈가 아니라 다른 실패(에스원 5xx 지속, 인증 장애 등)이고, 코드 배포 후 redrive로 되살릴 수 있다.

```bash
# 6a) 되살릴 수 있는 것들이면 — 메인 큐로 redrive (코드 배포 이후에)
aws sqs start-message-move-task --region $REGION \
  --source-arn <DLQ ARN> --destination-arn <메인 큐 ARN>

# 6b) 전부 보관기간 초과분이면 — purge (되돌릴 수 없다. 60초에 1회만 가능)
aws sqs purge-queue --region $REGION --queue-url "$DLQ_URL"

# 7) 확인
aws sqs get-queue-attributes --region $REGION --queue-url "$DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages
```

**purge가 필요한 이유.** 코드를 고쳐도 이미 DLQ에 들어간 메시지는 자동으로 사라지지 않는다. 그리고 이 메시지들은 redrive해도 **에스원이 항상 400을 돌려주므로** 되살릴 수 없다(신규 코드에서는 30일 가드에 걸려 에스원 호출 없이 warn 후 종결되지만, 어차피 처리 결과는 동일하다). 근본 원인인 기기 재전송 루프는 서버가 이벤트를 정상 ack 하면서 업로드 커맨드가 나가 끊긴다.

---

## 부록 C. 에스원(S1) 전문 형식

저장소에 있던 `docs/s1-api-spec.md`는 2026-04-19 커밋에서 삭제됐다. 아래는 현재 코드에서 그대로 옮긴 것이다. 공통 사항:

- base URL: 환경변수 `S1_API_URL`
- 인증: `Authorization: Bearer <JWT>` — `S1JwtService`가 subject `"skix-security"`로 발급
- `Content-Type: application/json`
- `device` 필드는 기기 종류 고정값 **`"A1"`**
- `deviceId`는 에스원이 아는 기기 식별자(9자리 계약번호). 우리는 `PlatformPort.resolveS1DeviceId(serial)`로 얻는다

### C-1. 이벤트 발생 — `POST /skix/event/occur`

요청 (`S1EventRequest`):

```json
{
  "device": "A1",
  "deviceId": "123456789",
  "eventCode": 204,
  "eventTime": 1754870400000,
  "eventInfo": "{\"eventCode\":2002,\"areaId\":\"...\"}"
}
```

| 필드 | 뜻 |
|---|---|
| `eventCode` | **에스원 코드다. 우리 내부 코드와 다르다.** 변환표는 아래 |
| `eventTime` | epoch ms. 에스원 콘텐츠 파일명·메타데이터용 시각이며 기기 캡처 시각이 아니다 |
| `eventInfo` | 부가 정보(JSON 문자열). 에스원이 저장 후 이벤트 조회 시 그대로 반환한다 |

응답 (`S1EventResponse`):

```json
{ "result": true, "error": null, "errorDesc": null,
  "thumbnailUrl": "...", "vodUrl": "...",
  "thumbnailFileName": "...", "vodFileName": "...", "expiryTime": "..." }
```

**내부 코드 → 에스원 코드 변환** (`EventCodes.toS1SecurityModeEventCode`):

| 내부 | `triggerType` | 에스원 |
|---|---|---|
| 1001 (침입) / 1002 (인증 실패) / 1003 (반복 인증 실패) | — | **`1` 고정** (`DeviceEventService.S1_FIXED_EVENT_CODE`). 내부 코드는 `eventInfo` JSON 안에만 들어간다 |
| 2001 (시작) | `APP` 또는 null | 101 |
| 2001 | `SCHEDULE` | 102 |
| 2001 | `PUI` | 103 |
| 2001 | `LLM` | 104 |
| 2001 | `ADMIN` | 105 |
| 2002 (종료) | `APP` 또는 null | 203 |
| 2002 | `FACE_RECOGNITION` | 201 |
| 2002 | `PIN` | 202 |
| 2002 | **`ADMIN`** | **204** ← §3-3의 오전송 코드 |

에스원 전송 대상은 1001·1002·1003·2001·2002뿐이다. 2003(시작 실패)·2004(에러 종료)는 내부 이력·FCM만 처리한다.

**오류 응답 처리.** 4xx는 `S1ClientRequestException(errorCode, message)`, 5xx·네트워크 오류는 `S1ApiException`으로 매핑된다(`S1ApiClient.executeCommand`). 4xx 본문은 `{"error": "...", "errorDesc": "..."}` 형태로 파싱해 `errorCode`를 꺼낸다. 확인된 코드:

| 코드 | HTTP | 의미 | 우리 처리 |
|---|---|---|---|
| `EVENT_RETENTION_PERIOD_EXCEEDED` | 400 | 이벤트 보관 기간(30일) 초과 | **비재시도 종결** (allowlist 유일 항목) |
| 그 외 4xx | 400대 | 미확인 | 예외 전파 → SQS 재시도 |
| — | 5xx / 네트워크 | | 예외 전파 → SQS 재시도 |

### C-2. 라이브뷰 세션 발급 — `POST /skix/livevideo/play`

**`KVS_PROVIDER=s1`일 때만 호출된다. 현재 세 환경 모두 `aws`라 미사용이다.**

요청 (`S1KvsApiClient.PlayRequest`):

```json
{ "device": "A1", "deviceId": "123456789", "ownerId": "<icstNo>", "userId": "<로그인 사용자>" }
```

`deviceId`=9자리 계약번호, `ownerId`=통합고객번호(icstNo), `userId`=로그인 사용자.

성공 응답 (`PlayResponse`):

```json
{
  "channelArn": "arn:aws:kinesisvideo:ap-northeast-2:...:channel/...",
  "camera": { "accessKeyID": "...", "secretAccessKey": "...", "sessionToken": "..." },
  "viewer": { "accessKeyID": "...", "secretAccessKey": "...", "sessionToken": "..." },
  "expiration": "2026-08-11T01:10:40Z",
  "error": null, "errorDesc": null
}
```

주의점:

- `accessKeyID`의 **`ID`가 대문자**다(`@JsonProperty("accessKeyID")`). 나머지는 camelCase.
- `channelArn`의 4번째 토큰이 region이다. 파싱 실패 시 설정값(`app.kvs.region`)으로 폴백한다.
- `expiration` 확정 포맷은 RFC 3339 UTC `Z`. 파서는 방어적으로 epoch(초/밀리)·명시 오프셋·오프셋 없는 로컬시각도 받는다. **오프셋이 없으면 KST로 해석한다**(§4의 9번).
- 응답의 `webrtcUrl`은 **쓰지 않는다.** WSS endpoint가 role별로 다르므로(`m-*`=MASTER, `v-*`=VIEWER) 단일 URL을 앱에 전달하면 안 된다. 앱·기기가 `channelArn`+`region`으로 직접 조회한다.
- `channelArn`이 비고 `error`가 있으면 업무 거절이다. 확인된 코드: **`ALREADY_IN_SESSION`** (활성 세션 존재 중 재-play, HTTP 200 + `result:false` + 발급 필드 전부 null). 이 경우 `ISSUE_FAILED`로 종결하고, **그 종결의 finalize가 stop을 보내 잔존 세션을 정리한다(자기 치유)**.
- 에스원 측 "사용시간 90초 초과 밴"은 최초 요청 후 5분에 해제된다(임의 설정이라 변동 가능). 우리 Relay 종료(서버 70초·기기 80초)가 90초 하드 한도보다 앞서므로 정상 운영에서 밴은 발생하지 않는다.

### C-3. 라이브뷰 세션 종료 통보 — `POST /skix/livevideo/stop`

요청 (`S1KvsStopApiClient.StopRequest`):

```json
{ "device": "A1", "deviceId": "123456789" }
```

**세션 식별자가 없다.** 기기 단위로만 식별되며, 이것이 재시도를 금지하는 이유다(§4의 5번).

응답 (`StopResponse`):

```json
{ "result": true, "error": null, "errorDesc": null }
```

**`result`는 boolean이다**(문자열 아님, 2026-08-10 실측 확정).

판정 규칙 (`S1KvsStopApiClient.validate`):

| 응답 | 판정 |
|---|---|
| `result: true` | 전달 확인 |
| `error: "NO_ACTIVE_SESSION"` | **멱등 성공** (info 로그만, 예외·warn 없음) |
| 그 외 `error` 존재 | 업무 거절 → `S1ClientRequestException` |
| `result`가 true가 아님 | 전달 미확인 → `S1ClientRequestException` |
| 200이지만 본문 없음·비JSON | 전달 미확인 → `S1ClientRequestException` |

4xx 분류 (`toStopException`) — **play와 다르다**:

| HTTP | 처리 | 이유 |
|---|---|---|
| 408 / 429 | `S1ApiException` (CB 기록) | 과부하 신호. 회로를 빨리 열어 fail-fast |
| 401 / 403 | `S1ApiException` + ERROR 로그 | 인증·설정 장애 |
| 404 | `S1ClientRequestException` + warn | stop 미배포 기간에는 무해. 운영 릴리스 후에는 구성 오류 경보 대상 |
| 그 외 4xx | `S1ClientRequestException` (CB ignore) | 업무 거절 |

타임아웃(`KvsProperties`, play와 분리):

| 설정 | 기본값 |
|---|---|
| `app.kvs.stop-connection-request-timeout-ms` | 400 (풀 대기 상한 — 포화 시 fail-fast) |
| `app.kvs.stop-connect-timeout-ms` | 1000 |
| `app.kvs.stop-read-timeout-ms` | 2500 |
| `app.kvs.stop-pool-size` | 10 |

서킷브레이커 `s1-kvs-stop` (`application.yml`): `sliding-window-size: 5`, `minimum-number-of-calls: 5`, `failure-rate-threshold: 50`, `slow-call-duration-threshold: 2s`, `wait-duration-in-open-state: 30s`. `S1ApiException`만 기록하고 `S1ClientRequestException`은 무시한다(업무 거절로 회로가 열리면 안 된다).

### C-4. MQTT — 보안모드 커맨드와 이벤트

서버 → 기기 (`command/securityMode`):

```json
{ "type": "FIXED", "enabled": true, "patrolIntervalHours": null,
  "areaId": "...", "initiatedBy": "APP" }
```

`enabled=false`(해제)면 `type`·`areaId`는 미포함이고 `enabled`·`initiatedBy`만 나간다. `initiatedBy`는 `APP` 또는 `ADMIN`이며 **`SecurityModeCommand` 생성자가 `isAdmin` boolean으로 결정한다**(`this.initiatedBy = isAdmin ? "ADMIN" : "APP"`).

기기 → 서버 (이벤트):

```json
{ "eventCode": 2002, "eventTime": 1754870400000,
  "areaId": "...", "securityModeType": "FIXED",
  "triggerType": "ADMIN", "errorCode": null, "correlationId": "..." }
```

**`triggerType`은 커맨드의 `initiatedBy`를 기기가 에코백한 값이다.** 그래서 OASYS 해지 자동 정지(`isAdmin` 하드코딩 `true`)가 만든 2002도 `ADMIN`으로 돌아오고, 이것이 에스원 204가 된다(§3-3).

---

## 부록 D. 조사·적용 SQL

DB는 MySQL, 스키마 이름은 `security`. **저장 시각 컬럼은 UTC다**(`created_at`, `called_at`). `event_time`은 epoch ms(기기 기준).

### D-1. 인덱스·DDL 적용 확인 (환경별로 실행)

```sql
-- security_mode_history 의 UNIQUE 존재 확인. 1행이 나와야 정상
SHOW INDEX FROM security.security_mode_history WHERE Key_name = 'uk_serial_event_code_time';

-- mqtt_publish_log.success DEFAULT 확인. Default 가 1 이어야 한다
SHOW COLUMNS FROM security.mqtt_publish_log LIKE 'success';

-- dispatch_billing_history 인덱스 2종 확인 (현재 세 환경 모두 미적용 예상)
SHOW INDEX FROM security.dispatch_billing_history
 WHERE Key_name IN ('idx_ctrt_no_dispatch_req_time', 'idx_icst_no_dispatch_req_time');
```

### D-2. 미적용 시 ALTER

```sql
-- (1) security_mode_history UNIQUE — 반드시 중복 확인을 먼저 한다
SELECT serial, event_code, event_time, COUNT(*) c
  FROM security.security_mode_history
 GROUP BY serial, event_code, event_time
HAVING c > 1;
-- 0행이면 아래 실행. 행이 나오면 먼저 정리해야 한다(가장 작은 id 만 남기고 삭제).

ALTER TABLE security.security_mode_history
  ADD UNIQUE KEY uk_serial_event_code_time (serial, event_code, event_time);

-- (2) mqtt_publish_log.success DEFAULT — 메타데이터 변경, 테이블 리빌드 없음
ALTER TABLE security.mqtt_publish_log
  ALTER COLUMN success SET DEFAULT 1;

-- (3) dispatch_billing_history 정산 조회 인덱스
ALTER TABLE security.dispatch_billing_history
  ADD INDEX idx_ctrt_no_dispatch_req_time (ctrt_no, dispatch_req_time),
  ADD INDEX idx_icst_no_dispatch_req_time (icst_no, dispatch_req_time);
```

### D-3. 유령 2002 피해 규모 조사 (§3-3, §7-1의 4번)

```sql
-- (a) 전체 규모 — 2002/ADMIN 인데 앞선 24시간 안에 2001 이 없는 행
SELECT COUNT(*) AS phantom_rows, COUNT(DISTINCT h.serial) AS devices
  FROM security.security_mode_history h
 WHERE h.event_code = 2002
   AND h.trigger_type = 'ADMIN'
   AND NOT EXISTS (
        SELECT 1 FROM security.security_mode_history s
         WHERE s.serial     = h.serial
           AND s.event_code = 2001
           AND s.event_time <= h.event_time
           AND s.event_time >= h.event_time - 86400000   -- 24h
   );

-- (b) 기기별 건수 — 정정 협의 대상 목록
SELECT h.serial,
       COUNT(*) AS phantom_2002,
       FROM_UNIXTIME(MIN(h.event_time)/1000) AS first_utc,
       FROM_UNIXTIME(MAX(h.event_time)/1000) AS last_utc
  FROM security.security_mode_history h
 WHERE h.event_code = 2002
   AND h.trigger_type = 'ADMIN'
   AND NOT EXISTS (
        SELECT 1 FROM security.security_mode_history s
         WHERE s.serial     = h.serial
           AND s.event_code = 2001
           AND s.event_time <= h.event_time
           AND s.event_time >= h.event_time - 86400000
   )
 GROUP BY h.serial
 ORDER BY phantom_2002 DESC;

-- (c) 한 번도 2001 이 없는 기기만 (가장 확실한 유령) — 협의 시 먼저 제시할 목록
SELECT h.serial, COUNT(*) AS rows_2002
  FROM security.security_mode_history h
 WHERE h.event_code = 2002
   AND h.trigger_type = 'ADMIN'
   AND h.serial NOT IN (
        SELECT DISTINCT serial FROM security.security_mode_history WHERE event_code = 2001
   )
 GROUP BY h.serial
 ORDER BY rows_2002 DESC;

-- (d) 해지 통보와의 시각 대사 — 유령 행 근처에 에스원 전송이 있었는지.
--     2026-08-13 이후로는 본문이 [REDACTED:PII] 라 eventCode 를 볼 수 없다.
--     호출 자체의 존재와 시각으로만 대사한다.
SELECT o.serial, o.endpoint, o.http_code, o.called_at
  FROM security.outbound_api_log o
 WHERE o.target   = 'S1'
   AND o.endpoint LIKE '%/skix/event/occur%'
   AND o.serial   = '<serial>'
 ORDER BY o.called_at DESC
 LIMIT 50;
```

`(a)`의 24시간 창은 임의값이다. 보안모드를 24시간 넘게 켜 두는 사용 패턴이 확인되면 넓혀야 한다. 넓힐수록 유령 판정이 보수적(과소 집계)이 된다.

### D-4. DLQ 원인 대사 (§부록 B와 함께)

```sql
-- 30일 넘은 이벤트가 이력에 들어온 흔적. eventTime 과 수신시각의 간극이 크면 재전송분이다
SELECT serial,
       event_code,
       FROM_UNIXTIME(event_time/1000) AS event_utc,
       created_at                     AS received_utc,
       TIMESTAMPDIFF(DAY, FROM_UNIXTIME(event_time/1000), created_at) AS lag_days
  FROM security.security_mode_history
 WHERE created_at > NOW() - INTERVAL 30 DAY
   AND TIMESTAMPDIFF(DAY, FROM_UNIXTIME(event_time/1000), created_at) > 30
 ORDER BY lag_days DESC
 LIMIT 50;
```

애플리케이션 로그에서 함께 볼 키워드: `Stale device event dropped (S1 retention exceeded)`, `S1 rejected device event (non-retryable)`.
