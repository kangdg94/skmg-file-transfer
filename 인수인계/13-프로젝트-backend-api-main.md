# [프로젝트] backend-api-main에서 작성자가 맡은 영역

| 항목 | 내용 |
|---|---|
| **프로젝트 한 줄 정의** | 나무엑스 앱·관제·내부 서비스의 공용 플랫폼 백엔드 가운데 인증·사용자·OASYS·기기·날씨·내부 연동 영역 |
| **저장소** | `backend-api-main` |
| **기술 스택** | Java 17, Spring Boot 3.2, MyBatis, MySQL, Redis, SQS, AWS SDK, Cognito, IoT Data Plane |
| **배포 형태** | EC2 단일 boot jar, 서버의 `air-bot-api.sh`로 프로필별 수동 기동 |
| **작성자 담당 범위** | Cognito·회원·PASS, OASYS, security·medcare·openapi 내부 API, IoT·SQS, 맵·날씨, 탈퇴·AfterCommit 안정화 |
| **기준일** | 2026-09-22 (원격 추적 브랜치를 당일 fetch한 뒤 실측) |
| **인수자가 첫날 할 일** | AXDO-2102 Android 가입 회귀를 리뷰하고, 운영 이력 오염·SSM·날씨 DB·릴리스 범위를 VDI에서 확인한다 |

---

## 0. 세 줄 요약

1. 이 저장소는 여러 팀이 함께 쓰는 대형 모놀리스다. 이 문서는 전체 기능이 아니라 작성자가 최근 6개월 맡은 인증·사용자·OASYS·서비스 연동·IoT·맵·날씨 영역을 설명한다.
2. 최근 변경은 token PII 제거, medcare·openapi identity, 원격 맵핑, 직접 구독, 탈퇴·AfterCommit, SQS/HTTPS, 날씨 좌표화에 집중돼 있다. `stg`는 `main`보다 371커밋 앞이고 `dev`와는 같다.
3. 운영 승격은 단순 병합이 아니다. AXDO-2102 가입 회귀, 과거 B015 revert, 앱 릴리스 게이트, DDL·SSM을 먼저 정리한 뒤 전체 트랙을 한 순서로 배포해야 한다.

### 브랜치 상태

| 측정 | 값 |
|---|---:|
| `origin/main..origin/stg` | **371커밋** |
| `origin/stg..origin/dev` | **0커밋** |
| `origin/main` 최신 | `7d9ac170`, 2026-09-01 |

`main`의 최신 커밋은 B015 관련 잘못된 직접 반영을 되돌린 것이다. 트리는 2026-08-06 형상과 같고, `stg`에는 이후 가드가 보강된 B015 코드가 정상 승격 경로로 다시 포함돼 있다.

---

## 1. 이 프로젝트가 하는 일

### 1-1. 이 문서가 다루는 범위

- Cognito 인증, PASS 본인인증, 회원가입·탈퇴, `user_accounts` identity
- OASYS 고객·SafeKey·계약 동기화, 직접 구독 WebView
- `skix-security`, `skix-medcare`, `skix-openapi`, 스트리밍용 내부 API
- 기기 상태 Redis, IoT SQS 소비·HTTPS 발행, 발행 이력
- 관제 원격 맵핑 원장·중계
- 맵 변환·히트맵 연동
- 좌표 기반 날씨·외부 API 호출량
- 트랜잭션 커밋 후 발행과 탈퇴 후처리

바이탈사인·FOTA·콜센터·리포트 등 저장소의 나머지 영역은 해당 담당자에게 확인한다.

### 1-2. 용어

| 용어 | 뜻 |
|---|---|
| **platform** | 다른 서비스가 회원·계약·기기·FCM을 조회할 때 부르는 이 백엔드의 역할이다. |
| **sub** | Cognito 사용자의 불변 식별자다. 로그인 수단마다 다를 수 있다. |
| **user_accounts** | 여러 sub을 한 `user_id`에 매핑하는 권위 테이블이다. |
| **PASS CI** | 본인확인기관이 돌려주는 연계정보. 브라우저·앱 왕복을 피하고 서버 세션으로 결합해야 한다. |
| **B015** | OASYS가 주문·계약 정보를 푸시하는 연동이다. |
| **AfterCommit** | DB 커밋이 성공한 뒤 MQTT·외부 부수효과를 실행하는 유틸이다. |
| **원격 맵핑** | 관제가 KVS 영상과 조그 명령으로 SLAM 지도를 만드는 세션이다. |

---

## 2. 전체 구성

```text
[앱·관제·WebView]
      │ API Gateway 또는 Nginx 직접 프록시
      ▼
[backend-api-main EC2 :8082]
      ├─ MySQL benjamin: 사용자·계약·기기·맵·이력·날씨
      ├─ Redis: 기기 최신 상태·세션·날씨 캐시
      ├─ SQS ← Router Lambda: IoT 메시지
      ├─ IoT Data Plane → 기기 명령
      ├─ 내부 NLB → security·medcare·openapi·streaming
      ├─ HTTP → OASYS·PASS·OpenWeather·Geocoding
      └─ FCM·S3·Cognito 등 AWS/외부 서비스
```

### 인증과 진입 경로

- 앱 `/app/**`는 Cognito ID Token 서명을 검증한다.
- 관제 경로는 관리자 JWT와 권한 인터셉터를 쓴다.
- `/internal/**`는 사설 네트워크와 선택적 공유키에 의존한다.
- `report-*.namuhx.com`은 `/api/**`를 API Gateway 없이 직접 프록시하므로 내부 경로 차단을 인프라에만 가정하면 안 된다.
- 직접 구독 WebView는 Cognito 토큰을 브라우저에 넘기지 않고 2분 티켓 → HttpOnly 쿠키 → CSRF 세션으로 바꾼다.

---

## 3. 코드 구조

저장소는 기능별 패키지와 MyBatis mapper가 병존한다. 작성자 영역의 주요 진입점은 다음과 같다.

| 경로 | 책임 |
|---|---|
| `src/main/java/com/skmagic/api/user` | 가입·탈퇴·설정, identity 내부 API |
| `.../auth/pass` | NICE PASS 시작·결과·purpose 분기 |
| `.../oasys` | OASYS inbound와 직접 구독 WebView |
| `.../internal/controller` | security·medcare·openapi·streaming 내부 API |
| `.../security` | 보안 서비스와 앱 사이 조정 |
| `.../map`·`.../mapping` | 앱 맵·관제 원격 맵핑·변환 연동 |
| `.../weather` | 좌표 해석, 캐시, OpenWeather, 외부 호출량 |
| `src/main/resources/mapper/user` | 사용자·계정·조건부 OASYS 필드 갱신 |
| `.../mapper/oasys` | 계약·직접 구독 원장 |
| `.../mapper/map`·`mapping` | 맵·원격 맵핑 SQL |
| `core` | 인증·설정·트랜잭션 유틸·공통 예외·IoT 배관 |

중요한 설계 원칙:

- 사용자 identity는 `user_accounts` → 활성 `user.sub` → 제한된 fallback 순으로 해석한다.
- 외부 계약 POST는 자동 재시도하지 않는다.
- DB 상태와 기기 명령이 묶이면 부수효과는 커밋 후 실행한다.
- 날씨는 주소가 아니라 좌표 격자를 캐시 키로 쓴다.
- 내부 API를 추가할 때 모든 공개 host의 라우팅을 확인한다.

---

## 4. 데이터

### 4-1. 주요 테이블

| 테이블 | 용도 |
|---|---|
| `user`, `user_accounts` | 사용자 원장과 복수 로그인 sub 매핑 |
| `contract`, `device` | 계약·기기 관계. 중복·더미 계약 기술 부채가 크다 |
| `history_control`, `mqtt_publish_log` | 기기 명령 이력·발행 결과 |
| `remote_mapping_session` | 원격 맵핑 세션 원장 |
| `oasys_membership_apply_attempt` | 직접 구독 신청 감사 원장 |
| `oasys_order_device_day` | B015 수신·판정 근거 |
| `outdoor_weather_history` | 좌표 격자 기반 날씨·미세먼지 이력 |
| `external_api_call_count` | 외부 API 분 단위 호출량 |
| `user_map_convert` | 맵 변환 요청. SQS 전환 브랜치는 outbox 컬럼을 요구한다 |

### 4-2. Redis 키

| 키 | 용도 |
|---|---|
| `operation:{serial}`, `battery:{serial}`, `power:{serial}` | 기기 최신 상태 |
| `lastSeen:{serial}`·구역별 변형 | 텔레메트리 신선도·write-back |
| `weather:cur:{locationKey}` | 현재 날씨 캐시, fresh TTL 70분 |
| `weather:active` | 조회된 격자 ZSET. 정리 로직이 없다 |
| `oasys:membership:*` | WebView 티켓·세션·PASS state·identity lock |
| PASS 인증 코드·세션 키 | 회원가입·PIN·직접 구독 목적별로 분기 |

### 4-3. 설정과 환경 차이

- 런타임 프로필은 `application-{dev,stg,prd}.yml`과 SSM `/backend-api-main/<profile>/`을 조합한다.
- 이 프로젝트의 `spring.config.import`에서는 yml 값이 SSM보다 우선할 수 있다. 같은 키가 양쪽에 있으면 yml부터 확인한다.
- 직접 구독 WebView origin은 프로필별 yml 리터럴이다.
- stg 날씨에는 시험용 `weather.override.conditions` 시리얼 5개가 남아 있다.
- SQS·MQTT와 HTTPS 발행은 `aws.sqs.enabled`, `aws.iot.subscribe-enabled`, `aws.iot.publish-mode`로 전환한다.

---

## 5. 빌드·테스트·로컬 실행·배포

```bash
./gradlew clean test
./gradlew clean bootJar
```

배포 스크립트는 저장소에 없다. VDI에서 빌드한 jar를 환경별 EC2로 전달하고 서버에 있는 `air-bot-api.sh start <dev|stg|prd>`로 기동한다. 스크립트와 정확한 서버 위치는 형상관리 밖이므로 첫 접속 때 내용을 확인한다.

운영 전에는 다음을 함께 본다.

1. 선택한 브랜치 HEAD와 jar 빌드 시각·checksum.
2. 프로필 인자와 SSM 경로.
3. `/checks` 또는 헬스 엔드포인트와 기동 로그.
4. 내부 NLB 소비자별 smoke test.
5. DDL과 rollback 대상. 신규 테이블·컬럼은 롤백 때 삭제하지 않는다.

공개 host는 앱 API, report WebView, PASS callback으로 나뉜다. report host에서 `/api/internal/**`가 차단됐는지 별도 확인한다.

---

## 6. 최근 6개월 작업 이력

| 시기 | 무엇을 | 왜 | 결과 | 핵심 커밋 |
|---|---|---|---|---|
| 3~5월 | 보안모드 스케줄, PIN PASS, security·medcare·openapi 내부 API, FCM·알림, OASYS 계약을 정비했다. | 신규 서비스들이 공통 플랫폼 기능을 필요로 했다. | 서비스 간 내부 계약과 앱 인증 기반이 생겼다. | `b8911f69`, `fa4c127d`, `5a12c997` |
| 5~6월 | 계약 `user_id` 의존 제거, OpenAPI 모델·권한·공기질 Redis, R002/R008 고객·SafeKey를 구현했다. | 계약 공유와 외부 API 확장, 직접 구독 준비가 필요했다. | OASYS identity 수렴과 OpenAPI 내부 데이터 공급이 가능해졌다. | `d9def788`, `fde7460b`, `e5640bb3` |
| 6~7월 | 기기 최신 상태를 Redis로 전환하고 SQS 소비·HTTPS 발행, DLQ·발행 로그, 공기질 원장 축소를 반영했다. | RDS 쓰기 과부하와 MQTT 직접 연결 의존을 줄여야 했다. | 운영 SQS 전환 사고를 수정해 `main`까지 반영했다. | `4215f182`, `3ceba618`, `60f0643e` |
| 7~9월 | 주소 기반 날씨를 좌표 격자 캐시·이력으로 바꾸고 호출량 계측·파트너 날씨를 추가했다. | 중복 외부 호출과 3,600만 행 이상 레거시 이력을 줄이기 위해서다. | 핵심은 운영 반영, 후속 5건은 `stg`까지만 반영됐다. | 날씨 핵심 묶음, `0fd28b0b`, `bbdcef14` |
| 8월 | Cognito token PII 제거와 `user_accounts` 3층 해석을 구현했다. | 토큰 개인정보 의존과 복수 계정 사용자를 동시에 해결해야 했다. | dev·stg S0~S3 완료, 운영 미배포다. | `59b359a4`, `d028844f` |
| 8월 | medcare owner identity API·탈퇴 파기, openapi identity·내부키·탈퇴 파기를 구현했다. | 형제 sub 데이터 통합과 탈퇴 후 잔존 데이터 제거가 필요했다. | 두 소비자의 운영 rollout은 플랫폼 운영 백필에 종속된다. | `dea4fdd8`, `b2138c78`, `ee2b1fc5` |
| 8월 | 관제 원격 맵핑 세션·API·원장을 구현했다. | 현장 리모컨 없이 관제에서 SLAM 지도를 만들기 위해서다. | 플랫폼 `dev`·`stg` 반영, `main` 미반영이며 기기·관제 브랜치가 미머지다. | `34172405`~`d2abc9db` |
| 8~9월 | 직접 구독 WebView의 티켓·PASS·identity 수렴·원장을 구현했다. | 브라우저에 Cognito 토큰·CI를 노출하지 않고 R045 직접 계약을 해야 했다. | 개발계 E2E 통과, stg 브랜치 반영, 운영 미반영이다. | `fb1d635d`~`63286529`, `a678cb58` |
| 9월 | 탈퇴 실패 200→500, `contract_no` 회귀, 중첩 AfterCommit 유실을 수정했다. | 조용한 성공과 커밋 후 MQTT 유실을 없애기 위해서다. | dev·stg 반영, 운영은 앱 릴리스·실기기 게이트가 남았다. | `1b814394`, `2844154c`, `f5d82cf4`, `1eff45ce` |
| 9월 | AXDO-2102 PASS CI 서버 세션화를 검토했다. | CI가 앱을 왕복하는 경로를 없애기 위해서다. | `dev`·`stg`에 들어갔지만 Android 가입 OASYS 연동 누락과 테스트 0건이 남았다. | `c7b20f20` |

---

## 7. 알려진 이슈와 기술 부채

| 우선순위 | 이슈 | 근거 |
|---|---|---|
| P0 | AXDO-2102 이후 Android 가입이 `messageAuthCode`를 보내지 않아 OASYS 연동 없이 성공한다. | `stg`에 있어 통째 승격하면 운영에 함께 들어가며 자동 테스트가 없다. |
| P0 | 운영 명령 추적 공개 플래그는 켜졌지만 판정·UTC 수정이 `main`에 없다. | 파트너가 대기 명령을 거절로 보고 timeout도 받지 못한다. |
| P0 | PHR 5년 제한이 실검증 없이 운영 브랜치에 들어갔다. | 옵션을 무시하는 침묵 실패 가능성이 있다. |
| P1 | `history_control.contract_no`가 2026-06-01 이후 NULL로 오염됐을 수 있다. | 환경별 nullability drift 때문에 운영에서는 조용히 기록됐다. |
| P1 | report host가 `/api/internal/**`를 직접 프록시한다. | 내부 security endpoint가 무인증으로 노출된 실측이 있다. |
| P1 | 계약 테이블은 중복·공유 더미·무인덱스 구조다. | B015와 OASYS 송신이 형식 스니핑에 의존하며 리팩토링은 설계만 끝났다. |
| P1 | SSM 이관은 `dev`·`stg` 브랜치만이며 값 로테이션·이력 정리가 남았다. | 운영 jar·프로필 방식도 미확인이다. |
| P2 | 날씨 DB 백필·레거시 제거·행정구역 교체 상태가 미확인이다. | 저장소 DDL과 실 DB가 다를 수 있다. |
| P2 | 맵 변환 SQS 브랜치는 오래됐고 DDL보다 먼저 개발계에 배포하면 즉시 활성화된다. | `vdi` 플래그가 true다. |

---

## 8. 남은 일

### 8-1. 즉시

1. AXDO-2102의 가입 두 경로를 리뷰하고 `latestAuthCode`를 무효화 전에 재사용하도록 보완하거나 승격에서 제외한다.
2. 운영 `history_control.contract_no` NULL 규모와 세 환경 nullability를 실측한다.
3. EC2 실행 프로필, SSM 파라미터 31개, 인스턴스 프로파일 경로 권한을 확인한다.
4. 날씨 실제 스키마·백필·stg override·JVM 시간대를 확인한다.
5. report host에 `/api/internal/` 차단 규칙을 적용하고 외부에서 404인지 검증한다.
6. 원격 맵핑의 타 팀 브랜치와 P0 설정을 회수한다.

### 8-2. 운영 릴리스 게이트

- AXDO-2102 보완 또는 제외, 코드 리뷰와 가입 실기기 검증.
- iOS 탈퇴 선로그아웃 버전이 스토어에 배포될 때까지 500 전환을 운영에 올리지 않는다.
- token PII S0 점검·RDS snapshot·플랫폼 배포·백필을 순서대로 수행한다.
- 플랫폼을 먼저 배포하고 `skix-security`를 바로 뒤에 배포한다. 직접 구독은 아직 호출자가 없어 구 security가 `plan`을 버리는 창을 만들지 않도록 기능을 차단한 상태에서 진행한다.
- medcare·openapi identity·PHR의 별도 플래그와 DDL 게이트를 지킨다.
- 날씨 `external_api_call_count`, 직접 구독 원장, 원격 맵핑 원장의 실 스키마를 확인한다.

### 8-3. 후속

- 계약번호 리팩토링 Phase 0을 구현한다. 형식 판별 대신 `contract_type`·`usage_type`, B015 shadow·판정 원장을 사용한다.
- 탈퇴 행 부활을 익명화·retention 테이블·만료 파기와 함께 해결한다.
- 맵 변환 SQS 브랜치를 최신 dev에 재작성하고 DDL→다크 배포→활성화 순으로 검증한다.
- 날씨 레거시 테이블·함수와 사용하지 않는 `weather:active`를 정리한다.

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| 가입은 성공했는데 OASYS 정보가 없음 | 가입 본문의 `messageAuthCode`, Redis PASS 세션, `icst_no`·`skm_sfky` 조건부 UPDATE 순서 확인 |
| 탈퇴가 500 | ERROR의 실패 stage와 `history_control.contract_no`를 본다. 후처리 API는 멱등이라 실패 sub를 재호출할 수 있다. |
| MQTT 성공인데 기기가 반응 없음 | `mqtt_publish_log` 성공 여부 뒤 Athena 응답 topic을 확인한다. |
| 내부 서비스가 401 | 키 강제 플래그와 양쪽 SSM 값, 배포 순서를 확인한다. 같은 이름의 키가 서비스마다 역할이 다르다. |
| 날씨 외부 호출 급증 | `weather:cur:*` TTL과 `external_api_call_count`의 vendor·app·endpoint를 본다. 운영 fresh TTL은 70분이어야 한다. |
| 원격 맵핑이 진행 중에 멈춤 | `remote_mapping_session` 상태, KVS 세션, 맵 업로드 설정, 조그 heartbeat를 함께 본다. |
| 커밋 후 기기 통보가 없음 | `AfterCommit` 중첩, `REQUIRES_NEW` 복귀, 실제 DB commit과 발행 로그 순서를 확인한다. |

---

## 부록 A. 핵심 내부 API

- 사용자: `GET /internal/user`, `/internal/user/identity`, `/internal/user/identities`
- security: `/internal/security/check-member`, 계약·권한·FCM·스케줄 정리
- medcare: `POST /internal/medcare/send-fcm`
- openapi: `/internal/v1/openapi/**` 건물·기기·명령·설정·사용량·날씨
- streaming: `POST /internal/streaming/send-fcm`
- OASYS inbound: `POST /oasys/inbound/interface`

## 부록 B. 핵심 커밋

`59b359a4` token identity, `dea4fdd8` medcare identity, `b2138c78` 탈퇴 파기, `34172405` 원격 맵핑, `fb1d635d` 직접 구독, `1b814394` 탈퇴 오류, `2844154c` 계약번호 회귀, `f5d82cf4`·`1eff45ce` AfterCommit, `c7b20f20` PASS CI를 우선 확인한다.
