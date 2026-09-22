# [프로젝트] skix-openapi와 개발자 문서 포털

| 항목 | 내용 |
|---|---|
| **프로젝트 한 줄 정의** | 외부 파트너가 나무엑스 기기·건물·명령·웹훅을 사용하는 공개 API와 개발자 포털 |
| **저장소** | `skix-openapi`, `skix-openapi-docs` |
| **기술 스택** | Java 21, Spring Boot 3.3, MyBatis, MySQL, Redis, JWT, WebSocket, ShedLock / Docusaurus·TypeScript |
| **배포 형태** | API는 ECS Fargate, 문서 포털은 정적 산출물 압축 후 객체 스토리지·CDN 동기화 |
| **작성자 담당 범위** | 공개 API·토큰·플랜·사용량·웹훅·감사 로그, 포털 API 문서, 복수 로그인 identity, 파트너 API 3종 |
| **기준일** | 2026-09-22 (원격 추적 브랜치를 당일 fetch한 뒤 실측) |
| **인수자가 첫날 할 일** | 운영 명령 조회 오판을 파트너에게 알리고, 운영 공개 플래그·내부키·태스크 정의 상태를 확인한다 |

---

## 0. 세 줄 요약

1. `skix-openapi`는 파트너용 OAuth형 토큰, 건물·기기 조회와 제어, 명령 추적, 웹훅, 플랜·쿼터를 제공한다. 문서 포털은 계약의 공개 정본이다.
2. 최근 6개월에 초기 API·WebSocket·웹훅·포털, 감사·보안 하드닝, 복수 로그인 identity, 기기 설정·사용량·errorCode를 구현했다. API 운영 브랜치는 stg보다 32커밋, 문서 포털은 11커밋 뒤다.
3. 운영에서는 명령 추적 플래그가 이미 켜졌는데 판정·UTC 수정이 빠져 있다. 파트너 통보, 두 저장소 승격, 새 태스크 정의 등록, 내부키·공개 플래그 정책 확정이 최우선이다.

### 브랜치 상태

| 저장소 | `main..stg` | `stg..dev` | `main` 최신 |
|---|---:|---:|---|
| `skix-openapi` | **32** | 0 | `85b8b76`, 2026-08-20 |
| `skix-openapi-docs` | **11** | 0 | `685ff2f`, 2026-08-06 |

---

## 1. 이 프로젝트가 하는 일

### 1-1. 목적과 사용자

- 파트너 서버에 건물·기기·상태·배터리·공기질·위치·지도 조회를 제공한다.
- 청정·이동·복귀·설정 변경 명령과 상태 조회를 제공한다.
- OAuth client credential과 자체 JWT, 권한·플랜·월간 쿼터를 관리한다.
- 상태 변화를 파트너 callback URL로 웹훅 전송한다.
- 개발자 포털에서 로그인, 자격증명·사용량 화면, OpenAPI 문서를 제공한다.

기기 상태의 원장과 실제 명령 발행은 플랫폼 내부 API가 맡는다. 이 서비스는 파트너 인증·계약·사용량·웹훅·외부 표현을 담당한다.

### 1-2. 용어

| 용어 | 뜻 |
|---|---|
| **client** | 파트너 시스템에 발급하는 API 자격증명 주체다. secret은 발급·회전 때 한 번만 보인다. |
| **plan** | 호출량·웹훅·권한 한도를 묶는 상품 정책이다. |
| **scope** | 토큰이 호출할 수 있는 API 권한이다. |
| **commandId** | 비동기 기기 명령을 추적하는 식별자다. |
| **웹훅 delivery** | callback 전송 시도 한 건과 성공·실패 이력이다. |
| **identity 정규화** | 로그인 sub를 한 사람의 canonical sub으로 바꿔 권한·자격증명을 찾는 절차다. |
| **다크 배포** | 코드는 배포하되 기능 플래그를 false로 둬 외부 동작을 바꾸지 않는 배포다. |

---

## 2. 전체 구성

```text
[파트너 서버] ── client_id/secret → POST /v1/auth/token
      │ Bearer JWT
      ▼
[skix-openapi ECS]
      ├─ 내부키 → backend-api-main /internal/v1/openapi/**
      ├─ MySQL → client·plan·permission·usage·audit·webhook
      ├─ Redis → rate limit·ShedLock·WebSocket 상태
      └─ HTTP callback → 파트너 웹훅

[개발자 포털 정적 사이트]
      ├─ Cognito 로그인
      ├─ 자격증명·사용량 화면
      └─ OpenAPI/AsyncAPI 문서
```

파트너 API는 자체 JWT를 검증한다. 포털 사용자는 Cognito로 로그인하고 canonical identity를 통해 개발자 권한을 찾는다. `/internal/v1/**`는 `APP_INTERNAL_API_KEY`로 수신 검증하고 플랫폼 호출에는 `NAMUHX_INTERNAL_API_KEY`를 싣는다. 운영에서 검증을 강제하기 전 모든 소비자가 키를 보내는지 확인한다.

---

## 3. 코드 구조

| 경로 | 책임 |
|---|---|
| `src/main/java/com/skix/openapi/auth` | 토큰 발급·폐기, JWT 계약 |
| `.../building` | 건물·기기 조회와 청정·이동·복귀 명령 |
| `.../command` | 명령 상태 판정 |
| `.../client`·`.../plan`·`.../permission` | 파트너 자격증명·플랜·권한 내부 관리 |
| `.../developer` | 포털 자격증명·사용량 API |
| `.../webhook` | 등록·테스트·delivery·자동 비활성화 |
| `.../common/audit` | 외부 요청·응답 감사 로그 |
| `.../common/ratelimit` | 토큰·플랜 한도 |
| `.../common/websocket`·`.../websocket` | 실시간 상태 채널 |
| `src/main/resources/mapper` | MyBatis SQL |
| 문서 `src/pages`, `docs`, `openapi.json` | 개발자 포털 화면과 API 계약 |

배포 경로는 테스트를 반드시 실행한다. `Dockerfile`은 `clean test bootJar`를 사용하며 Gradle 9의 JUnit launcher도 명시돼 있다. 웹훅 스케줄러는 Redis ShedLock으로 단일 실행을 보장한다.

---

## 4. 데이터

| 종류 | 주요 데이터 |
|---|---|
| MySQL | client·secret hash·token·plan·permission·usage·webhook·delivery·inbound audit |
| Redis | 월간·분당 rate limit, 스케줄러 분산 락, 실시간 상태 보조 |
| 플랫폼 DB/Redis | 기기 상태·위치·지도·공기질·명령·날씨. 내부 API로만 읽는다 |

### 주요 설정

| 키 | 의미 |
|---|---|
| `APP_INTERNAL_API_KEY` | 이 서비스의 `/internal/v1/**` 수신 검증 키 |
| `APP_INTERNAL_ENFORCE_API_KEY` | 내부키 강제 플래그 |
| `NAMUHX_INTERNAL_API_KEY` | 플랫폼 내부 API 호출 키 |
| `IDENTITY_NORMALIZE_ENABLED` | 포털 로그인 sub 정규화 |
| `DEVICE_SETTINGS_PUBLIC_ENABLED` | 기기 설정 API 공개 |
| `USAGE_PUBLIC_ENABLED` | 사용량 API 공개 |
| `WEATHER_PUBLIC_ENABLED` | 날씨 API 공개 |
| `COMMAND_TRACKING_PUBLIC_ENABLED` | 명령 상태 조회 공개 |
| `COGNITO_USER_POOL_ID`·`COGNITO_CLIENT_ID` | 포털 Cognito 검증 값 |

dev·prd는 기본 Dockerfile, stg는 Datadog 변형 이미지를 쓴다. 태스크 정의를 통째 교체할 때 기존 환경변수·secret을 잃지 않도록 현재 실행 리비전과 비교한다.

---

## 5. 빌드·테스트·로컬 실행·배포

### API

```bash
./gradlew clean test
./gradlew clean bootJar
./deploy/build-export.sh          # dev·prd
./deploy/build-export.sh dd       # stg Datadog 이미지
```

로컬 시크릿은 `config/local-secrets.yml`에 두며 예시는 `config/local-secrets.yml.example`이다. 배포는 태스크 정의 등록 후 `cloudshell-push.sh <env>`로 진행한다. 새 키·플래그가 있으면 등록을 생략할 수 없다.

### 문서 포털

```bash
npm ci
npm run build
node scripts/validate-specs.mjs
./deploy/build-export.sh <dev|stg|prd>
```

정적 파일은 CloudShell에서 `deploy/cloudshell-sync.sh`로 동기화한다. 꺼진 기능을 문서에 먼저 노출하지 않도록 API 공개 시점과 함께 배포한다.

환경별 API 호스트는 `openapi-dev.namuhx.com`, `openapi-stg.namuhx.com`, `openapi.namuhx.com`이다.

---

## 6. 최근 6개월 작업 이력

| 시기 | 무엇을 | 왜 | 결과 | 핵심 커밋 |
|---|---|---|---|---|
| 5월 | 공개 API 초기 구현, 감사 로그, TLS·오류 처리를 보강했다. | 파트너가 기기 상태와 청정 명령을 안전하게 써야 했다. | 외부 API의 기본 인증·응답·감사 틀이 생겼다. | `03193e1`, `92c9d52`, `f0ede8e` |
| 6월 초 | WebSocket·웹훅, 개발자 포털, 자격증명·사용량·월간 쿼터를 구현했다. | polling 외 실시간 전달과 셀프서비스 포털이 필요했다. | 웹훅 SSRF 방어, Redis fail-closed, 서킷브레이커, 문서 계약을 갖췄다. | `e509d80`, `4c9abd8`, `085c915`, `c92524c` |
| 6월 중순 | 내부 발급 권한 검증, 사용량 집계, 웹훅 월간 한도를 보강했다. | 권한 없는 발급과 당일 사용량 누락을 막아야 했다. | 플랜·쿼터 모델과 문서가 정합해졌다. | `ff492ca`, `6dd3c16`, `c9f12c9` |
| 7~8월 | 입력 오류·토큰 오류·JWKS·ShedLock, 감사 로그 마스킹, 토큰 rate limit, 로컬 시크릿 분리를 반영했다. | 테스트 0건과 내부·외부 보안 경계의 결함을 해소하기 위해서다. | 배포 경로 테스트와 3브랜치 하드닝이 완료됐다. | `145a838`, `855b215`, `8163537` |
| 8월 말 | 복수 로그인 identity 정규화, 탈퇴 파기, 내부키를 구현했다. | 로그인 수단을 바꾸면 권한이 있어도 포털 403이 났다. | dev·stg R5 완주, 운영은 token PII 백필을 기다린다. | `2c444ba`, `769707a` |
| 9월 | `errorCode`, 기기 설정, 일별 사용량, 명령 판정·UTC를 반영하고 문서를 갱신했다. | 파트너 운영 관제에 필요한 상태·설정·사용량이 부족했다. | dev·stg 공개 완료. 운영은 구판정 플래그만 켜진 불완전 상태다. | `50155df`, `40dc742`, `c20c33a` |
| 9월 | 웹훅 무수신 사고를 조사·복구했다. | 고객 callback 502가 10회 누적돼 16개 웹훅이 조용히 비활성화됐다. | 13개를 재활성화했으나 http_code 기록·알람은 미착수다. | 운영 DB 조치, 코드 후속 없음 |

---

## 7. 알려진 이슈와 기술 부채

| 우선순위 | 이슈 | 근거 |
|---|---|---|
| P0 | 운영 명령 조회 공개 플래그는 true인데 응답 대기 명령을 `DEVICE_REJECTED`로 판정하고 timeout이 나지 않는다. | 판정 수정은 `main`에 없고 플랫폼·API의 `stg`에만 있다. |
| P0 | 운영 시각은 오프셋 없는 KST이고 새 계약은 UTC다. | 파트너가 파싱·정렬을 바꿔야 하므로 배포 전 통보가 필요하다. |
| P1 | SETTINGS 명령은 ABT가 `response/setConfig`를 보내지 않아 항상 timeout이다. | 서버는 수신 준비가 돼 있어 펌웨어 변경 없이는 해결할 수 없다. |
| P1 | 웹훅 4xx·5xx가 delivery의 `http_code=NULL`로 남고 자동 비활성화 알림이 없다. | 원인은 CloudWatch 로그에서만 보이며 장애가 6일간 조용히 지속된 사례가 있다. |
| P1 | `/internal/v1/**`의 실제 운영 강제 여부와 ALB 노출 차단이 불명확하다. | 강제를 먼저 켜면 백오피스 발급 기능이 죽을 수 있다. |
| P2 | 사용량의 원천 테이블마다 UTC/KST가 다르고 재연동 기기 청정 이력이 분리된다. | 소스별 시간 구간과 device_id 집계 규칙을 유지해야 한다. |
| P2 | 정적 문서와 기능 플래그 공개 시점이 분리돼 있다. | 꺼진 기능을 먼저 문서화하거나 켠 기능의 계약이 뒤처질 수 있다. |

---

## 8. 남은 일

### 8-1. 즉시

1. 파트너에게 명령 판정·`submittedAt` UTC 전환을 통보하고 운영 현재 오동작을 공유한다.
2. dev·stg의 이미지와 내부키 2종, 공개 플래그가 실제 등록된 리비전에 있는지 확인한다.
3. 운영 공개 API 3종과 `APP_INTERNAL_ENFORCE_API_KEY`의 전환 시점을 결정한다.
4. 웹훅 delivery가 4xx·5xx status를 저장하도록 수정하고 자동 비활성화 알람을 추가한다.
5. 파트너 사용량 2건을 플랫폼 원장과 대사한다.

### 8-2. 운영 릴리스 게이트

- `skix-openapi`와 플랫폼 `main`을 함께 승격한다.
- 운영 SSM에 양방향 내부키를 등록하고 태스크 정의 리비전을 새로 등록한다.
- identity 다크 배포 뒤 `present=true, valid=true`를 확인하고 token PII 운영 백필 후 정규화를 켠다.
- 파기 실패 알람, legacy-only 해소, 오귀속 재검, 구버전 태스크 drain을 완료한다.
- API 플래그와 문서 포털 공개 시점을 일치시킨다.

### 8-3. 후속

- SETTINGS 응답 topic을 펌웨어에 추가할지 결정한다.
- 내부 API를 ALB 경로에서 차단하고 앱 계층 키를 2차 방어로 유지한다.
- 웹훅 재활성화 API와 실패 사유·알림 운영 화면을 검토한다.
- 감사 로그 본문 보관 범위와 90일 정책을 재검토한다.

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| 포털 로그인 후 403 | 로그인 sub와 canonical identity, permission의 user key를 대조한다. 운영 백필 전 플래그를 켜지 않는다. |
| 명령이 즉시 거절로 보임 | `history_control.response_date`를 먼저 본다. NULL이면 응답 대기 또는 timeout이지 `result=0` 거절이 아니다. |
| SETTINGS만 timeout | 펌웨어가 `response/setConfig`를 발행하지 않는 알려진 계약이다. event/config 후 `getConfig` 실값을 별도로 확인한다. |
| 웹훅이 안 옴 | DB의 NULL status만 보고 네트워크 오류로 단정하지 않는다. CloudWatch의 `[Webhook] <id> 전송 오류`에서 실제 HTTP status를 본다. |
| 웹훅이 자동 비활성 | 최근 10건 실패와 callback 상태를 확인한다. 재활성 전 중복 등록과 고객이 보관한 secret을 확인한다. |
| 사용량이 다름 | 청정은 `history_clean_result`, AI는 `cloud_llm_usages`, 바이탈은 `vital_sign`의 서로 다른 시간대를 적용한다. |

---

## 부록 A. 주요 API

- 인증: `POST /v1/auth/token`, `POST /v1/auth/token/revoke`
- 건물·기기: `/v1/buildings`, devices·status·battery·air-quality·location·map
- 제어: clean·move·return, `GET /v1/commands/{commandId}`
- 개발자: `/v1/developer/credentials`, `/v1/developer/usage`
- 웹훅: `/v1/webhooks`, test·deliveries
- 내부 관리: `/internal/v1/clients`, plans, permissions

## 부록 B. 핵심 커밋

`085c915` 포털·쿼터, `c92524c` Redis·SSRF, `145a838` 감사 마스킹, `2c444ba` identity 정규화, `50155df`·`40dc742` 파트너 API, `c20c33a` 명령 판정·시각을 우선 확인한다.
