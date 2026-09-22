# [프로젝트] skix-security

| 항목 | 내용 |
|---|---|
| **프로젝트 한 줄 정의** | 로봇 기반 Safe Care의 보안모드·에스원 출동·OASYS 구독·KVS 실시간 영상을 담당하는 백엔드 |
| **저장소** | `skix-security` |
| **기술 스택** | Java 21, Spring Boot 3.2, MyBatis, MySQL, Redis, SQS, IoT Data Plane, Kinesis Video Streams, Resilience4j |
| **배포 형태** | ECS Fargate, Docker 이미지 수동 반입·ECR push·ECS 롤링 배포 |
| **작성자 담당 범위** | 서비스 초기 안정화, S1·OASYS 연동, 보안모드·PIN·이벤트, KVS, SQS/HTTPS 전환, 인증·로그 하드닝 |
| **기준일** | 2026-09-22 (원격 추적 브랜치를 당일 fetch한 뒤 실측) |
| **인수자가 첫날 할 일** | VDI에서 실행 중 ECS 리비전과 `device_current_contract` 실재 여부를 확인하고, 검증계 DLQ 및 유령 2002 규모를 점검한다 |

---

## 0. 세 줄 요약

1. 이 서비스는 앱·관제의 보안 기능을 기기, 에스원, OASYS, 자사 AWS KVS에 연결하는 경계 서비스다. 실패를 잘못 분류하면 출동 오전송, 중복 계약, 영상 세션 누수가 생긴다.
2. 최근 6개월에는 초기 기능 안정화 뒤 SQS/HTTPS 전환, 자사 KVS, 원격 맵핑, 토큰 개인정보 제거, R011 캐시, 직접 구독을 차례로 반영했다. `dev`와 `stg`는 같고 운영 `main`은 26커밋 뒤다.
3. 가장 급한 일은 운영 DDL·실배포 확인, 검증계 DLQ 정리, 유령 2002 수정, 운영 릴리스에서 플랫폼을 먼저 올린 뒤 이 서비스를 곧바로 승격하는 것이다.

### 브랜치 상태

| 측정 | 값 |
|---|---:|
| `origin/main..origin/stg` | **26커밋** |
| `origin/stg..origin/dev` | **0커밋** |
| `origin/main` 최신 | `f19e9d7`, 2026-08-31 |

`main`에는 PIN 이력 무효화가 `f19e9d7`로 선반영돼 있고, `dev`·`stg`의 같은 내용은 `955ba0a`라 해시가 다르다. 승격 충돌은 내용 기준으로 해소한다.

---

## 1. 이 프로젝트가 하는 일

### 1-1. 목적과 사용자

- 앱 사용자에게 보안모드 시작·종료, PIN, 이벤트, VOD, 라이브뷰, 출동 요청을 제공한다.
- 관제 사용자에게 보안 이력, KVS 세션, 원격 맵핑, 출동 상태를 제공한다.
- 기기 MQTT 이벤트를 소비하고 제어 명령을 발행한다.
- OASYS 계약·구독 상태와 에스원 출동 시스템을 연결한다.
- 보안영상 콘텐츠 키의 저장·복호화는 별도 KMS 서비스에 위임한다.

사용자 계정의 원장, PASS 본인인증, 앱 전체 회원가입, 계약 테이블의 권위는 이 서비스가 맡지 않는다. 그 정보는 플랫폼 내부 API에서 조회한다.

### 1-2. 용어

| 용어 | 뜻 |
|---|---|
| **S1** | 외부 출동 서비스. 출동 접수·취소, 이벤트 통보, 라이브영상 연동을 받는다. |
| **OASYS** | 계약·고객 시스템. R011은 계약 조회, R045는 구독 신청, R046은 구독 확인, R047은 상태 통보다. |
| **KVS** | AWS Kinesis Video Streams WebRTC. 기기와 앱·관제 사이 실시간 영상 세션을 중계한다. |
| **lease** | KVS 세션의 생존권. Redis가 권위이며 heartbeat로 갱신한다. |
| **relay** | P2P가 안 될 때 TURN 경유로 영상을 중계하는 모드다. |
| **DLQ** | 처리 실패한 SQS 메시지가 쌓이는 큐다. 원인을 고친 뒤에도 적체분은 자동으로 사라지지 않는다. |
| **R011 캐시** | 현재 계약번호는 MySQL, 계약 상세는 Redis 1시간 캐시로 보유해 OASYS 조회를 줄이는 구조다. |

---

## 2. 전체 구성

```text
[앱·관제]
   │ Cognito ID Token 또는 관리자 인증
   ▼
[skix-security]
   ├─ 내부 NLB → backend-api-main: 회원·권한·계약·FCM
   ├─ HTTP → OASYS: R011/R021/R045/R046/R047 처리
   ├─ HTTP → S1: 출동·이벤트·영상 play/stop
   ├─ Redis: KVS 세션·lease·계약 상세 캐시
   ├─ MySQL: 보안 이력·출동 이력·KVS 세션·현재 계약
   ├─ SQS ← IoT Router: 기기 이벤트
   └─ IoT Data Plane → 기기: 제어 명령
```

### 인증 경계

- `/security/v1/**`는 Cognito 토큰 또는 관리자 경로의 인가를 거친다.
- `/internal/v1/**`는 사설 네트워크를 전제로 한다. prefix 전체에 인증 필터를 한꺼번에 걸면 스트리밍의 출동·업로드 호출이 끊긴다. 소비자 배선부터 확인한다.
- 서비스가 토큰에서 읽어야 하는 사용자 식별자는 `sub`다. 전화번호·이름은 플랫폼의 `check-member` 응답으로 얻는다.
- KVS 임시 자격증명은 MASTER·VIEWER 역할을 분리하고 세션별로 발급한다.

---

## 3. 코드 구조

| 경로 | 책임 |
|---|---|
| `src/main/java/com/skix/security/api/controller` | 앱·관제·내부 REST 진입점 |
| `.../api/security` | Cognito·관리자 요청 주체 해석 |
| `.../domain/model` | 보안·출동·KVS·명령 도메인 값 |
| `.../domain/service` | 보안모드, 직접 구독, KVS 세션의 업무 규칙 |
| `.../domain/port` | DB·Redis·외부 HTTP·MQTT 경계 인터페이스 |
| `.../infra/http/oasys` | OASYS 신방식 단일 게이트웨이와 전문 DTO |
| `.../infra/http/s1` | 출동·영상 연동과 오류 분류 |
| `.../infra/http/platform` | 플랫폼 내부 API 클라이언트 |
| `.../infra/kvs` | 채널 생성, STS 자격증명, 세션 fence |
| `.../infra/sqs`·`.../infra/mqtt` | IoT 수신·발행 전송 계층 |
| `src/main/resources/mapper` | MyBatis SQL |

설계 원칙은 세 가지다. Redis KVS 상태를 단일 권위로 두고 DB는 감사·복구 기록으로 쓴다. 외부 계약 요청은 자동 재시도하지 않는다. 네트워크 오류와 업무 거절을 분리해 중복 출동·중복 계약을 막는다.

주요 진입점은 `KvsLiveViewController`, `DeviceController`, `S1ServiceController`, `OasysController`, `InternalDeviceController`다.

---

## 4. 데이터

### 4-1. 주요 저장소

| 종류 | 이름 | 용도 |
|---|---|---|
| MySQL | `security_mode_history` | 보안모드 이벤트. `(serial,event_code,event_time)` 멱등 인덱스 필요 |
| MySQL | `dispatch_billing_history` | 출동·정산 조회 |
| MySQL | `mqtt_publish_log`, `mqtt_subscribe_log` | 명령 발행 결과와 수신 이력 |
| MySQL | `kvs_session_history`, `kvs_session_credential` | 영상 세션·자격증명 감사 기록 |
| MySQL | `remote_mapping_session` | 원격 맵핑 시작·종료·실패 원장 |
| MySQL | `device_current_contract` | 시리얼별 현재 계약번호. 없으면 기능은 돌아도 R011 감축 효과가 0이다 |
| Redis | `security:{kvs}:*` | KVS 세션·lease·epoch 권위 |
| Redis | OASYS 계약 상세 캐시 | R011 응답을 1시간 재사용 |
| SQS | security 입력 큐와 DLQ | Router가 보안·KVS 관련 IoT 메시지를 전달 |

### 4-2. 주요 설정

| 키 | 의미 |
|---|---|
| `KVS_PROVIDER` | `aws`일 때 자사 KVS 사용 |
| `KVS_LEASE_SECRET` | lease 토큰 서명 시크릿. SSM에서 주입 |
| `KVS_RELAY_MAX_DURATION_MS` | 일반 라이브뷰 relay 상한 |
| `KVS_MAPPING_RELAY_MAX_DURATION_MS` | 원격 맵핑 relay 상한. 기기 상한 반영 뒤 올린다 |
| `KVS_CHANNEL_NAME_PREFIX`, `KVS_ENVIRONMENT_TAG` | 환경별 채널 이름·태그. 운영에서 기본값 사용 금지 |
| `OASYS_API_URL` | OASYS 신방식 엔드포인트 호스트 |
| `PLATFORM_API_URL` | 플랫폼 내부 API 주소 |
| `AWS_SQS_ENABLED`, `AWS_IOT_SUBSCRIBE_ENABLED` | SQS 소비와 MQTT 구독 전환 |
| `AWS_IOT_PUBLISH_MODE` | `https`면 IoT Data Plane 발행 |

시크릿은 태스크 정의 `secrets`의 SSM ARN으로 주입한다. 저장소의 `deploy/task-definition-*.json`을 고쳐도 등록하지 않으면 ECS에는 반영되지 않는다.

---

## 5. 빌드·테스트·로컬 실행·배포

```bash
./gradlew clean test
./gradlew clean bootJar
./deploy/build-export.sh
```

로컬 실행은 `config/local-secrets.yml.example`을 복사해 `config/local-secrets.yml`을 만들고 자리표시자에 개발용 값을 넣는다. 파일이 없으면 의도적으로 기동 실패한다.

배포 순서는 이미지 빌드·압축, CloudShell 업로드, 필요 시 `deploy/task-definition-{env}.json` 등록, `deploy/cloudshell-push.sh <env>` 실행, ECS 안정화 확인이다. 스크립트는 최신 태스크 정의를 조회해 재사용할 뿐 JSON을 등록하지 않는다.

환경별 공개 host의 실제 `/security/v1/**` 라우팅은 **미확인**이다. VDI에서 ALB listener와 target group을 조회한다. 내부 통신은 개발계 NLB 8080 또는 검증·운영계 통합 NLB를 쓴다.

---

## 6. 최근 6개월 작업 이력

| 시기 | 무엇을 | 왜 | 결과 | 핵심 커밋 |
|---|---|---|---|---|
| 3~4월 | S1 출동·취소, 보안 이벤트, PIN·파일·KMS·내부 API 기반을 안정화했다. | 초기 기능의 응답·토픽·권한·복호화 오류가 연속 발생했다. | 출동·이벤트·영상 업로드의 기본 경로가 형성됐다. | `fa0122f`, `84b3651`, `986a24e` |
| 5월 | OASYS 구독·S1 오류를 세분화하고 출동 이력·구독 상태 캐시를 보강했다. | 업무 오류를 일반 장애로 취급하고 반복 호출하던 문제를 줄여야 했다. | 구독 해지 시 스케줄·보안모드 정리와 영속 캐시가 들어갔다. | `8418976`, `2166a0a`, `ea5b78b` |
| 6월 | 업로드 버스트 제한, 외부 연동 서킷브레이커, S1 주소·ownerId, 보유기간 파기를 정리했다. | S1·플랫폼 장애가 본 흐름으로 전파되고 업로드가 몰렸다. | 회복탄력성과 개인정보 보유기간 처리 기반을 갖췄다. | `7a1ab4c`, `1279543`, `21934fb` |
| 7월 | MQTT 직접 수신·발행을 SQS 소비·HTTPS 발행으로 바꾸고 자사 KVS를 구현했다. | RDS 과부하와 MQTT 재연결 의존을 줄이고 영상 인프라를 자사 계정으로 옮기기 위해서다. | 텔레메트리 경로와 KVS 세션·lease·watchdog가 `main`까지 반영됐다. | `5bf94ae`, `a6fca8a`, `3e9a6cb`, `6522113` |
| 8월 초 | S1 4xx 재시도·DLQ, 멱등 이력, 영상 stop, 감사 로그·권한을 보강했다. | 보관기간 초과 이벤트가 DLQ를 채우고 잘못된 재시도를 만들었다. | 확정 오류만 ack하고 나머지는 재시도하며, S1 stop은 at-most-once로 정리됐다. 유령 2002는 미착수다. | `7443f56`, `4a684bd`, `e62a482`, `e6af9c4` |
| 8월 중순 | 원격 맵핑 세션, relay 정책, DB UTC·TLS를 반영했다. | 관제 원격 주행과 장시간 영상, 시각 혼재, TLS 평문 폴백을 해결해야 했다. | 서버 코드는 반영됐지만 기기·관제 브랜치와 운영 게이트가 남았다. | `8d84aea`, `493e068`, `242196e`, `d7e953c` |
| 8월 말 | Cognito `sub` 기반 인증, 로그 개인정보 제거, R011 캐시, R047 PIN 이력 무효화를 반영했다. | 토큰 개인정보 의존과 OASYS 호출량, 재구독 PIN 오판을 줄였다. | `dev`·`stg` 완료, `main`은 플랫폼 운영 배포를 기다리며 일부를 제외했다. | `b1b148b`, `cf5f56b`, `4942d5f`, `f19e9d7` |
| 9월 | OASYS 직접 구독 내부 API, 요금제·SafeKey·계약분류번호, 재시도 차단을 구현했다. | 앱에서 상담 없이 계약을 접수하되 중복 접수와 민감정보 노출을 막아야 했다. | 개발계 E2E 통과, `dev`·`stg` 반영, 운영 미반영이다. | `a71bf95`, `e4a2f8c`, `4c7bdd2`, `28a668f` |

---

## 7. 알려진 이슈와 기술 부채

| 우선순위 | 이슈 | 근거 |
|---|---|---|
| P0 | R047 자동 stop이 이미 정지된 기기의 2002를 S1 204로 오전송한다. | 패치가 없고 피해 규모도 미집계다. 자동 stop correlationId로 S1 전송만 차단하는 설계가 합의됐다. |
| P0 | 원격 맵핑은 기기 relay 90분과 `purpose` 컬럼·환경변수 실값이 확인되지 않았다. | 서버만 올리면 10분 이후 기능이 깨지거나 운영에 dev 이름 채널이 생길 수 있다. |
| P1 | `device_current_contract` DDL·ECS 배포가 미확인이다. | 테이블이 없으면 오류 없이 캐시 miss로 강등돼 감축 효과가 사라진다. |
| P1 | `startsWith("T")` 계약번호 게이트가 M·F·G 실계약을 거절한다. | 운영 실측과 모순된다. 형식이 아니라 계약 유형 데이터로 판별해야 한다. |
| P1 | `/internal/**` 인증은 네트워크 격리에 의존한다. | 소비자 목록 없이 prefix를 잠그면 S1 긴급 호출이 401로 죽는다. 단계적 공유키 배선이 필요하다. |
| P2 | 보안영상 복호화 키가 내부 HTTP와 앱 응답에서 평문이다. | 저장소를 바꾸는 것만으로 해결되지 않으며 앱 임시 공개키 재래핑이 필요하다. |
| P2 | Redis KVS 운영 정책과 재부팅 후 스트리밍 프로세스 복구가 미확인이다. | KVS 상태의 단일 권위가 Redis이므로 eviction은 세션 유실이다. |

---

## 8. 남은 일

### 8-1. 즉시

1. VDI에서 세 환경 ECS 이미지·태스크 정의·SSM·KVS IAM·Redis epoch를 대조한다.
2. `SHOW TABLES LIKE 'device_current_contract'`와 `SHOW COLUMNS FROM security.mqtt_publish_log`를 세 환경에서 실행한다.
3. 검증계 DLQ 적체 원인을 확인하고 확정 4xx 재전송분을 purge한다.
4. 유령 2002 건수를 집계하고 자동 stop correlationId 기반 S1 전송 억제를 구현한다.
5. 원격 맵핑의 기기·관제 브랜치, relay 90분, 맵 업로드 설정을 담당자와 회수한다.

### 8-2. 운영 릴리스 게이트

- `backend-api-main` 신버전을 먼저 배포하고 이 서비스를 바로 뒤에 배포한다. 반대 순서는 `check-member` 계약을 깨뜨린다.
- 토큰 `sub` 전환의 운영 사전 점검과 `user_accounts` 백필 계획을 확정한다.
- 직접 구독은 화이트리스트 미등록 시리얼 실호출, 중복 신청·timeout 정책, 성공·오류 코드를 확정한다.
- 원격 맵핑은 기기 3종과 펌웨어, 데드맨 6시나리오, 맵 업로드, relay·KVS 운영 설정을 모두 통과한다.
- KVS 일반 라이브뷰는 검증계 24시간 관찰과 TURN TCP 443, 채널·STS 쿼터를 확인한다.

### 8-3. 후속

- push-authoritative 구독 스냅샷 도입 여부를 결정한다.
- MQTT 코드와 인증서 제거는 SQS·HTTPS 3환경 안정화 뒤 진행한다.
- 보안영상 키는 점검 원문을 확보한 뒤 AWS KMS 비대칭 키와 앱 재래핑을 단계적으로 설계한다.
- KVS provider 전환 시 활성 세션 drain 규칙과 운영 대시보드를 만든다.

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 순서 |
|---|---|
| SQS DLQ 증가 | 메시지의 topic·serial → 같은 시각 `IoT handler failed` → 확정 오류인지 일시 장애인지 판단 → 수정 뒤 redrive 또는 폐기 |
| S1 출동·이벤트 오류 | `S1ClientRequestException`의 업무 코드와 서킷브레이커 상태를 분리해 본다. 보관기간 초과만 비재시도다. |
| 라이브뷰가 안 열림 | Redis 세션·lease → STS/KVS `AccessDenied` → 채널 상태 → MQTT start 응답 → TURN 연결 순서로 본다. |
| `RECOVERING`이 오래 감 | watchdog backlog와 oldest delay, Redis epoch·lease MISMATCH를 확인한다. 강제 종결은 세션 fence를 지킨다. |
| OASYS 호출 급증 | `device_current_contract` 존재·행 수, Redis TTL, R011 상위 시리얼을 확인한다. |
| 구독 해지 뒤 PIN이 설정됨으로 보임 | R047 처리 로그의 `PIN history invalidated`와 `/pin/check`를 대조한다. 기기 실물 PIN은 별도 확인이다. |
| 명령을 보냈는데 기기가 반응하지 않음 | `mqtt_publish_log.success/error_message`를 보고, 성공이면 Athena의 응답 topic으로 넘어간다. |

---

## 부록 A. 주요 API

| 영역 | API |
|---|---|
| 보안 | `POST /security/v1/devices/{serial}/security-mode`, PIN·상태·이벤트·VOD·파일 API |
| 출동 | `POST /security/v1/devices/{serial}/s1/dispatch`, 취소·상태·이력 API |
| KVS | `POST /security/v1/devices/{serial}/kvs/sessions`, close·상태·출동 API |
| 원격 맵핑 | `POST /security/v1/devices/{serial}/kvs/mapping-sessions`, close·조회 API |
| OASYS | 구독 조회·납입이력·상담 신청, 내부 직접 구독 API |
| 내부 | PIN 이력 무효화, 업로드 통보, 구독 조회, force-close |

## 부록 B. 핵심 커밋

`1279543` 외부 연동 회복탄력성, `5bf94ae` SQS·HTTPS 전환, `3e9a6cb` 자사 KVS, `6522113` renewable lease, `4942d5f` R011 감축, `8d84aea` 원격 맵핑, `b1b148b` sub 전환, `a71bf95` 직접 구독, `4c7bdd2` 자동 재시도 차단을 먼저 본다.
