# [작업] 나무엑스 OPEN API — 파트너 API 3종 · 웹훅 · 리뷰 반영

| 항목 | 내용 |
|---|---|
| **상태** | 파트너 API 3종 **개발계·검증계 반영 완료 · 운영계 미반영** · 문서 사이트 동일 · 웹훅 사고 복구 완료(재발 방지 미착수) |
| **작업 기간** | 2026-08-03 ~ 2026-09-15 |
| **직접 수정한 저장소** | `skix-openapi`, `skix-openapi-docs`, `backend-api-main` |
| **요청서를 전달한 대상** | 없음. **미전달 2건** — ABT 펌웨어 담당(`response/setConfig` 발행), 파트너사(명령 응답 시각 UTC 전환 통보). §6 |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 · 공개 HTTP 응답 실측 기준) |
| **인수자가 첫날 할 일** | §8-1의 1번(**운영계 명령 조회가 지금 잘못된 값을 파트너에게 내보내는 중**)과 2번(개발계·검증계 이미지 배포 여부 확인) |

---

## 0. 세 줄 요약

1. 고객사(파트너 관제서버)가 요청한 **API 3종**(디바이스 상태의 `errorCode`, 기기 설정 조회·변경, 일별 사용량)을 만들어 **개발계·검증계에 기능 플래그 ON으로 반영**했다. 운영계에는 코드도 플래그도 없다.
2. **기기 설정 변경(`SETTINGS`)의 명령 조회 결과는 영원히 `TIMED_OUT`이다.** ABT 펌웨어가 `response/setConfig`를 발행하지 않기 때문이며 **서버 코드로 고칠 수 없다.** 대외 문서에는 "명령 조회로 결과를 확인하라"고 적혀 있고, 이 한 줄만 사실과 다른 채로 두기로 결정했다(§5의 4번).
3. 그 외에 **운영계가 지금 잘못 동작하는 것이 하나 있다** — 명령 상태 조회가 응답 대기 중인 명령을 `DEVICE_REJECTED`로 내보내고 시각을 오프셋 없는 KST로 내보낸다. 수정은 개발계·검증계까지만 올라가 있다(§8-1의 1번).

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **나무엑스 OPEN API** (`skix-openapi`) | 외부 파트너사에 공기청정 로봇·에어센서를 노출하는 대외 공개 REST/WebSocket API. Spring Boot 3.3.6 / Java 21 / MyBatis / Redis. ECS Fargate, 3개 AWS 계정(dev `010928196421` / stg `087432099373` / prd `779846811758`), 리전 `ap-northeast-2`. **운영도 단일 태스크(`desiredCount = 1`)** |
| **개발자 포털** (`skix-openapi-docs`) | 위 API의 문서 사이트. Docusaurus 정적 사이트를 S3 + CloudFront로 호스팅. `developers{,-stg,-dev}.namuhx.com` |
| **상류(upstream)** | `backend-api-main`. 실제 기기 데이터·제어는 전부 여기 있고 `skix-openapi`는 인증·한도·번역·문서화만 한다. 내부 NLB로 호출한다 |
| **클라이언트 / 파트너** | `open_api_clients` 한 행. `clientId` + `secretKey`(BCrypt 해시 저장)로 `POST /v1/auth/token`에서 자체 발급 JWT(RS256, 3600초)를 받는다 |
| **스코프** | `read` / `control`. 조회가 아닌 모든 요청에 `control`이 필요하다(§5의 3번) |
| **플랜** (`open_api_plans`) | 분당·월간 호출 한도, WebSocket 동시 연결 수, 웹훅 등록·월간 발송 한도 묶음. `BASIC` / `ENTERPRISE` 등 |
| **다크 배포 / 기능 플래그** | 코드는 배포하되 환경변수로 엔드포인트를 감추는 방식. 값이 `false`면 Swagger 스펙·라우팅에서 함께 빠진다. 이 저장소는 `COMMAND_TRACKING_PUBLIC_ENABLED`·`WEATHER_PUBLIC_ENABLED`·`DEVICE_SETTINGS_PUBLIC_ENABLED`·`USAGE_PUBLIC_ENABLED` 4개를 쓴다 |
| **웹훅 / 딜리버리** | 파트너가 등록한 콜백 URL(`open_api_webhooks`)과 그 전송 이력 1행(`open_api_webhook_deliveries`). 스케줄러가 상류 스냅샷을 폴링해 변화가 있으면 발송한다 |
| **공유키 (`X-Internal-Api-Key`)** | 내부 시스템 간 호출에 싣는 대칭 시크릿. SSM에서 태스크 정의 `secrets`로 주입한다. 두 방향이 있고 이름이 다르다 — §3-4 |
| **C1** | 2026-08-03 전수 코드 리뷰에서 나온 최상위 항목. `skix-openapi`의 `/internal/v1/**`(백오피스용 클라이언트 발급·시크릿 로테이션·권한 부여)가 **공개 인터넷에서 인증 없이 200**이었던 건 |
| **제출본 브랜치** | `submission/security-audit-20260811`. 사내 보안점검에 **소스로 제출하기 위해서만** 만든 브랜치이며 어느 환경에도 배포되지 않았다. 그대로 머지하면 기동이 실패한다(§5의 10번) |
| **ABT / AQM / AQM\_WLS** | 각각 공기청정 로봇, 유선 에어센서, 무선 에어센서. 기기 타입에 따라 노출 설정 항목이 다르다 |

### 1-2. 문제 — 네 갈래

**(가) 파트너 관제서버가 할 수 없던 것 3가지**

- 로봇 상태가 `ERROR` 하나뿐이라 **필터 커버 열림과 구동 모터 고장에 같은 알림**이 나갔다. 정작 에러 코드는 상류까지 이미 와 있었고 매핑 두 줄에서 버려지고 있었다.
- 고객사가 **'스테이션 자동 고정청정'을 끌 수 없어** 충전 중 이동 제어가 막히는데 관제서버에서 풀 방법이 없었다. 읽기·쓰기 메커니즘은 전부 있었고 외부에 열려 있지 않았을 뿐이다.
- **사용량(청정 시간·AI 대화·바이탈 사인) 집계**를 API로 받을 방법이 없었다.

**(나) 명령 상태 조회가 거짓을 말하고 있었다**

`GET /v1/commands/{commandId}`는 응답 대기 중인 명령을 **`DEVICE_REJECTED`로 표시**했고, 어떤 명령도 `TIMED_OUT`이 되지 않았으며, `submittedAt`·`updatedAt`이 **오프셋 없는 KST 문자열**로 나가 같은 이름의 필드가 명령 접수 응답(UTC)과 9시간 어긋났다.

**(다) 내부 API 무인증**

`skix-openapi → backend-api-main` 방향(`/internal/v1/openapi/**`) 18개 중 공유키를 검증하던 것은 4개뿐이었다. 반대 방향(`backend-api-main 백오피스 → skix-openapi /internal/v1/**`)은 C1 그 자체다.

**(라) 감사 로그에 평문 자격증명**

배포돼 있던 `AuditLogFilter`의 본문 제외 목록이 **요청 본문에만** 적용돼 `POST /v1/auth/token`의 **응답 본문(=발급된 액세스 토큰 평문)** 이 `open_api_inbound_log`에 90일 저장되고 있었다.

**(마) 운영 웹훅이 6일간 조용히 죽어 있었다**

2026-09-09, 한 파트너사의 웹훅 16건이 전부 `is_active=0`이 됐다. **알람도 로그도 이력 조회 수단도 없어 6일 뒤 고객 문의로 알았다.**

### 1-3. 목표

- 파트너 API 3종을 **다크 배포**(플래그 OFF)로 먼저 올리고, 상류가 준비된 뒤 환경별로 켠다.
- 기존 파트너 계약(응답 필드·오류 코드)을 깨지 않는다. 새 필드는 값이 없으면 **키 자체를 내보내지 않는다**.
- 대외 값 어휘를 통일한다. DB `Y/N`, MQTT `ON/OFF`, 풍량 `WIND000x` 같은 내부 표기는 우리 사정이다.
- 공유키는 **경로 단위**로 강제하고 호출 측은 **필터 한 곳**에서 붙인다. "새 엔드포인트를 만드는 사람이 규칙을 알아야 하는" 구조는 언젠가 새는 쪽으로 끝난다.

---

## 2. 현재 상태 (2026-09-22 실측)

### 2-1. 저장소별 브랜치 반영

`git merge-base --is-ancestor <커밋> origin/<브랜치>` 로 실측했다.

| 저장소 | dev | stg | main(운영) |
|---|---|---|---|
| `skix-openapi` | `d6e5fd2` (09-04) | `8c7f3ca` (09-04) | **`85b8b76` (08-20)** |
| `skix-openapi-docs` | `fa2301a` (09-04) | `9f560d7` (09-04) | **`685ff2f` (08-06)** |
| `backend-api-main` | `3fdd8447` (09-18) | `ed6dc871` (09-18) | `7d9ac170` (09-01) |

주제별 반영 여부:

| 주제 | 저장소 | dev | stg | main |
|---|---|---|---|---|
| 보안점검 제출본 선별 반영 4건 (08-13) | skix-openapi | 반영 | 반영 | **반영** |
| 리뷰 백로그 문서 제거 · 내부 인증 방향 정정 (08-19·08-20) | skix-openapi | 반영 | 반영 | **반영** |
| 포털 sub 정규화 · `/internal/v1/**` 공유키 검증 · 탈퇴 파기 (08-27) | skix-openapi | 반영 | 반영 | **미반영** |
| 날씨 API (09-03) | skix-openapi / docs | 반영 | 반영 | **미반영** |
| `errorCode` · 기기 설정 · 사용량 (09-04) | skix-openapi / docs | 반영 | 반영 | **미반영** |
| control 스코프 허용목록 전환 (09-04) | skix-openapi | 반영 | 반영 | **미반영** |
| 명령 상태 UTC·판정 수정 (09-04) | skix-openapi + backend-api-main | 반영 | 반영 | **미반영** |
| 상류 공유키 전면 부착 / 상류 경로 인터셉터 (09-04) | skix-openapi + backend-api-main | 반영 | 반영 | **미반영** |

> **"미push"로 알려진 커밋은 없다.** `fix/audit-log-no-token-in-body`(`73963d4`)를 포함해 위 표의 모든 커밋이 원격에 올라가 있다. 다른 기록에 미push로 적혀 있더라도 인용 전에 위 명령으로 다시 실측하라.

### 2-2. 기능 플래그 — 태스크 정의 **파일** 기준

`deploy/task-definition-{dev,stg,prd}.json` (origin/dev 기준). **ECS에 실제로 등록된 리비전 값은 로컬에서 확인할 수 없다(미확인).** 확인 방법은 §7-4.

| 환경변수 | dev | stg | prd |
|---|---|---|---|
| `COMMAND_TRACKING_PUBLIC_ENABLED` | true | true | **true** (2026-08-06 `b608aeb`에서 false→true) |
| `WEATHER_PUBLIC_ENABLED` | true | true | false |
| `DEVICE_SETTINGS_PUBLIC_ENABLED` | true | true | false |
| `USAGE_PUBLIC_ENABLED` | true | true | false |
| `APP_INTERNAL_ENFORCE_API_KEY` | true | true | false |
| `IDENTITY_NORMALIZE_ENABLED` | true | true | false |
| secrets `APP_INTERNAL_API_KEY`·`NAMUHX_INTERNAL_API_KEY` | 있음 | 있음 | 있음(파일 기준) |

`origin/main`의 `task-definition-prd.json`에는 **`COMMAND_TRACKING_PUBLIC_ENABLED` 한 개만** 있고 아래 5개와 두 secrets가 **아직 없다.** 운영 승격 시 파일이 통째로 교체되므로 §7-2의 순서를 지켜야 한다.

### 2-3. 배포·검증

| 항목 | 상태 |
|---|---|
| 개발계·검증계 애플리케이션 배포 | **미확인.** 브랜치는 09-04자로 반영돼 있고 같은 날 배포한 것으로 남아 있으나, CI가 없어 이미지 빌드·배포가 전부 수동이라 브랜치만으로는 판정되지 않는다. 로컬 맥에서는 ECS를 볼 수 없다. 확인 방법 §7-4 |
| 운영계 배포 | **미배포.** `main`이 08-20에 멈춰 있다 |
| 개발계 실측 검증 | 설정 변경의 명령 결과가 `TIMED_OUT`으로 떨어지는 것을 개발계에서 확인하고 그 원인을 Athena로 규명했다(2026-09-04, §5의 4번). 플래그 미반영으로 404가 나던 것도 개발계에서 잡았다(§7-3). **3종 전체에 대한 체계적인 회귀 검증 기록은 남기지 않았다** — §7-6의 명령으로 다시 훑을 것 |
| `/internal` 공개 차단 (C1) | **3환경 전부 차단 확인됨.** 2026-09-22 실측: `openapi{,-dev,-stg}.namuhx.com/internal/v1/clients` → **404**, `/internal` → 404, `/actuator/health` → 200. ALB 리스너 고정 응답 규칙이 살아 있다 |
| `skix-openapi` `/internal/v1/**` 앱 계층 인증 | **log-only.** 검증 컴포넌트는 들어갔으나(`2c444ba`) `APP_INTERNAL_ENFORCE_API_KEY`가 운영계 파일에 아예 없어 강제되지 않는다. 그 브랜치 자체가 운영계 미반영 |
| 테스트 | 각 커밋 시점에 전량 통과. 규모 변화: **0**(08-03 이전) → 368(08-12) → 387(08-13) → 이후 커밋마다 증가. 현재 수치는 `./gradlew test`로 직접 확인한다. 이미지 빌드가 `clean test bootJar`라 **테스트가 곧 배포 게이트**다 |

### 2-4. 웹훅 사고 (운영계)

| 항목 | 상태 |
|---|---|
| 사고 | 2026-09-09 KST 09:00:11에 고객 콜백이 500 1회, 이후 전부 **502 Bad Gateway**(고객사 서버 측). KST 09:47까지 웹훅 16건이 순차 자동 비활성화 |
| 발견 | 2026-09-15, 고객 문의로. **6일간 알람·로그·이력 어디에도 신호가 없었다** |
| 복구 | **완료 (2026-09-15 KST 11:52).** 중복 등록 3건 정리(최신 등록본만 남김), 나머지 13건 `is_active=1` 복원. 같은 날 02:56 UTC부터 `http_code=200`·`success=1` 수신 확인. 유지한 웹훅 중 2건의 id 앞자리가 `00cdc4e1`·`9e589c88` |
| 남은 것 | 재발 방지 3건 **미착수** — `http_code` 기록, 비활성화 알람, 4xx 스킵 로그. §8-3 |
| 부작용 1건 | 복구 DML을 세션 시간대 설정 없이 실행해 그 13행의 `updated_at`에 KST가 들어갔다. 해가 되지는 않지만 그 열로 시각을 대사하면 9시간 어긋난다 |

### 2-5. 파트너 API 3종의 상대편 (상류)

`backend-api-main`의 `/internal/v1/openapi/**` 신규 엔드포인트는 dev·stg에 반영돼 있고 **운영계(main) 미반영**이다. `backend-api-main`의 `main`은 2026-09-01에 핫픽스 revert가 있었던 상태라 승격 시 별도 확인이 필요하다.

---

## 3. 무엇을 만들었나

### 3-1. 전체 흐름

```
[파트너 관제서버]
   ① POST /v1/auth/token           clientId + secretKey → JWT(RS256, 3600s)
   ② Authorization: Bearer <JWT>   로 아래 호출

[skix-openapi]  인증 · 스코프 · 한도 · 번역 · 문서화만 한다
   JwtAuthFilter      서명·iss·aud·폐기목록 확인 → 조회가 아닌 메서드면 control 스코프 요구
   RateLimit          분당 · 월간 · WS동시연결 (전부 fail-closed)
   AuditLogFilter     /v1/** 인바운드 기록 (자격증명 4경로는 [REDACTED])
   BuildingService    상류 호출 · 오류 번역 · 아웃바운드 기록
   NamuhxClientConfig 필터가 모든 상류 요청에 X-Internal-Api-Key 를 붙인다
        │
        │  내부 NLB (L4, ALB 경로 규칙 영향 없음)
        ▼
[backend-api-main]  /api/internal/v1/openapi/**
   InternalOpenApiKeyInterceptor   경로 단위로 공유키 검증
   OpenApiSettingsService          기기 설정 조회·변경 (값 어휘 변환)
   OpenApiUsageService             일별 사용량 (소스별로 시간대가 달라 구간을 따로 계산)
   OpenApiBuildingService          상태·명령 상태 (respondedAt 유무로 판정)
        │
        ▼  MQTT command/… → 기기
   [ABT / AQM]  → response/… 를 올리면 history_control.response_date 가 채워진다
                  단, setConfig 만은 response 를 올리지 않는다 (§5의 4번)
```

### 3-2. 트랙 1 — 파트너 API 3종

경로는 `skix-openapi`가 `src/main/java/com/skix/openapi/` 하위, `backend-api-main`이 `src/main/java/com/skmagic/` 하위 기준이다.

| 기능 | skix-openapi 주요 파일 | backend-api-main 주요 파일 |
|---|---|---|
| 상태 응답의 `errorCode` | `building/model/DeviceStatusResponse.java`, `building/service/BuildingService.java`(REST·웹훅), `building/service/RedisDeviceService.java`(WebSocket) | **변경 없음** — 값이 이미 상류까지 와 있었다 |
| 기기 설정 조회·변경 | `settings/controller/DeviceSettingsController.java`, `settings/model/DeviceSettingsResponse.java`, `settings/model/DeviceSettingsUpdateRequest.java` | `api/internal/controller/InternalOpenApiSettingsController.java`, `api/internal/service/OpenApiSettingsService.java`, `api/internal/model/OpenApiSettingKey.java`, `api/internal/model/ValueCodec.java`, `api/internal/model/OpenApiDeviceSettings.java` |
| 일별 사용량 | `usage/controller/UsageController.java`, `usage/model/DailyUsageResponse.java`, `common/config/ClockConfig.java` | `api/internal/controller/InternalOpenApiUsageController.java`, `api/internal/service/OpenApiUsageService.java`, `api/internal/mapper/OpenApiUsageMapper.java`, 매퍼 XML은 `src/main/resources/mapper/internal/OpenApiUsage.xml` |
| 오류 코드 번역 | `common/exception/ErrorCode.java`, `BuildingService.mapReasonToErrorCode` | – |

**설정 항목은 8개다.**

| 대상 | 항목 |
|---|---|
| ABT | `autoCleanMode`, `maxCleanTime`, `cleanCompleteCriteria`, `airflowMode`, `nightMode` |
| AQM · AQM\_WLS | `scanningCleanMode`, `autoCall`, `autoCallCriteria` |

값 어휘 변환표는 `OpenApiSettingKey` **한 곳**에만 둔다. 읽기 표와 쓰기 표를 따로 두면 "조회는 되는데 변경이 400"인 상태가 조용히 생긴다.

`autoCallCriteria`의 값 집합(`1`=보통, `2`=나쁨, `3`=매우나쁨)은 백오피스 에어센서 설정 화면의 자동 호출 기준 선택지(`AUTO_CALL_CRITERIA_OPTIONS`)를 근거로 했다. 임계 판정은 기기가 하므로 서버 코드에 의미가 남아 있지 않고, 유일한 흔적인 "`1`이면 연동 ABT의 청정완료기준을 `GOOD`으로" 분기가 `1`=보통이라야 말이 되는 동작이라 일치한다.

### 3-3. 트랙 2 — 명령 상태 판정·시각

두 결함이 서로를 가리고 있었다.

1. `history_control.result`가 **`tinyint NOT NULL DEFAULT 0`** 이라 기기 응답 전에도 `false`다. `result`로 먼저 가르면 **응답 대기 중인 명령이 전부 `DEVICE_REJECTED`** 가 되고 `PENDING`·`TIMED_OUT` 분기는 도달 불가능한 죽은 코드가 된다. → `response_date`(= `respondedAt`) 유무로 먼저 가르도록 뒤집었다.
2. 그 죽은 분기 안의 타임아웃 판정이 DB 시각(KST)과 `LocalDateTime.now()`(JVM=UTC)를 비교하고 있었다. KST가 9시간 앞서 **어떤 명령도 타임아웃되지 않는다.** 1번을 고치면 이 분기가 살아나므로 함께 맞췄다.

그리고 `submittedAt`·`updatedAt`을 `Instant`(UTC)로 통일했다. 양쪽 저장소를 다 고쳐야 한다 — 상류가 `Instant`를 내보내고(`OpenApiCommandStatus`), `skix-openapi` DTO도 `Instant`로 받는다(`CommandStatusResponse`). 명령 접수 응답(`CommandAcceptedResponse`)에도 `@JsonFormat`을 명시했다. 애너테이션이 없으면 전역 `write-dates-as-timestamps` 설정에 따라 `1788502692.000000000` 같은 숫자로 나갈 수 있다.

### 3-4. 트랙 3 — 내부 공유키 (방향이 둘이고 키 이름이 다르다)

| 방향 | 검증 주체 | 환경변수 |
|---|---|---|
| `skix-openapi` → `backend-api-main` `/internal/v1/openapi/**` | `backend-api-main`의 `core/security/InternalOpenApiKeyInterceptor` (경로 인터셉터) | `skix-openapi` 쪽 secret `NAMUHX_INTERNAL_API_KEY` |
| `backend-api-main` 백오피스 → `skix-openapi` `/internal/v1/**` | `skix-openapi`의 `common/security/InternalApiKeyVerifier` (컨트롤러 호출, log-only 시작) | `skix-openapi` 쪽 secret `APP_INTERNAL_API_KEY` + 플래그 `APP_INTERNAL_ENFORCE_API_KEY` |

호출 측은 `skix-openapi`의 `common/config/NamuhxClientConfig` **필터 한 곳**에서 붙인다. 실측하면 키를 보내지 않는 호출 지점이 16곳이었다 — `BuildingService` 12, `WebhookScheduler` 3, `WsPushScheduler` 1. 스케줄러 4곳이 특히 위험한데 사용자 요청이 없는 백그라운드라 빠뜨려도 즉시 드러나지 않고 **웹훅 전송이 조용히 멈춘 뒤에야** 알게 된다.

수신 측도 컨트롤러 메서드마다가 아니라 **경로 인터셉터**로 건다. `backend-api-main`의 `/internal/**` 전체를 걸면 키를 보내지 않는 호출자 20여 개(`skix-security`·medcare·streaming)가 전부 죽으므로, **호출자가 `skix-openapi` 하나뿐인 `/internal/v1/openapi/**` 하위만** 좁혀 건다.

### 3-5. 트랙 4 — 보안점검 제출본 선별 반영과 리뷰 백로그

제출본 `submission/security-audit-20260811`(`81f0d6a`)의 변경을 전수 검토해 **실제 운영에 반영할 것만** 4건으로 재작성했다. 제출본 자체는 머지하지 않는다(§5의 10번).

| 커밋 | 내용 |
|---|---|
| `145a838` | 감사 로그 본문을 **전면 미저장에서 경로별 마스킹으로 전환** + `[REDACTED]` 마커 |
| `60f4ac9` | 개발자 포털 요청의 감사 로그에 주체(`audit.user_id`) 기록 |
| `855b215` | 토큰 발급에 IP 기준 호출 제한 (기본 30회/분) |
| `8163537` | 로컬 실행용 RSA 개인키·DB 비밀번호를 레포 밖 `config/local-secrets.yml`로 분리(예시 파일 `config/local-secrets.yml.example`만 커밋, 실파일은 `.gitignore`) |

마스킹 대상은 **평문 자격증명이 실리는 4경로**로 특정했다.

```
POST /v1/auth/token                      요청 secretKey · 응답 액세스 토큰
POST /v1/developer/credentials           응답 Secret Key 평문 (재조회 불가)
POST /v1/developer/credentials/rotate    위와 동일
POST /v1/webhooks                        응답 웹훅 서명 secret (1회 반환)
```

같은 경로라도 **조회 응답과 실패 응답은 그대로 저장한다** — 조회 응답에는 시크릿이 없고(`ClientDetail`에 secret 없음, 웹훅 목록은 `includeSecret=false`) 실패 응답에도 자격증명이 없다. 장애 조사 능력을 남기기 위해서다.

**반영하지 않기로 한 것** (제출본에 있었으나 뺐다):

| 항목 | 이유 |
|---|---|
| CORS/WS 오리진 화이트리스트 | `allowCredentials=false`라 실질 위험이 낮고, `/v1/**`는 외부 파트너의 브라우저 클라이언트가 대상이라 오리진 고정은 **계약 축소**다 |
| 상류에 `X-Internal-Api-Key` 전송 | 당시 `backend-api-main`의 `/internal/**`이 `SystemConstants.getExcludeUrls()`에 있어 permitAll이었다. **검증 주체가 없어 효과 0**. (이후 09-04에 경로를 좁혀 실제 검증을 붙이면서 해소됐다 — §3-4) |
| 감사 본문 전면 제거 · 웹훅 로그 `e.getMessage()` 삭제 | 진단 능력만 손실 |
| `InternalApiKeyFilter` | 그 필터의 판이 `/vital/v2/**`와 API Gateway WebSocket 통합 경로까지 삼킨다. 08-27에 **컨트롤러 검증 컴포넌트** 형태로 다시 만들었다 |

**리뷰 백로그(2026-08-03~08-04)** 는 이미 전부 처리·종료됐다. Critical 2 · High 5 · Medium 12 · 구조 2 · docs 4 · 신규 2를 처리했고, 종료(작업 안 함)는 3건(`D5-B` 커밋된 RSA 키 / `E3` `.env*` 커밋 / `C2` Tier 3 통합 테스트)이다. 그 과정에서 찾은 **테스트 인프라 결함 2건**이 인수자에게 중요하다.

- Gradle 9는 `junit-platform-launcher`를 자동 주입하지 않아 `test` 태스크가 기동조차 못 했다.
- `bootJar`는 `test`에 의존하지 않으므로 Dockerfile의 `bootJar -x test`는 **no-op**이었고 배포 경로에 테스트가 아예 없었다. → `clean test bootJar`로 바꿨다. **이것이 현재 배포 게이트다.**

### 3-6. 데이터 — 시간대 지도 (이 작업에서 가장 헷갈리는 부분)

| 대상 | 시간대 | 근거 |
|---|---|---|
| `skix-openapi`의 모든 DB 열 | **UTC** | `application-{ecs,localhost}.yml`이 세션 시간대를 UTC로 강제하고 기동 시 검증(`f1adacb`). 컨테이너도 `-Duser.timezone=UTC` (빌드 스크립트가 Dockerfile에서 이 플래그를 검사한다) |
| `benjamin` 스키마(상류 DB)의 `NOW()` | **KST** | UTC 통일 대상이 아니다 |
| `history_clean_result.start_time` | **KST 벽시계** (`Z`가 붙어 있어도 UTC가 아니다) | 같은 행 `time_stamp`(진짜 UTC)·`create_date`와 정확히 9시간 차이. **KST 날짜 문자열과 그대로 비교해야 맞고, "Z니까 UTC"로 변환하면 9시간 틀어진다** |
| `cloud_llm_usages.create_date` | **UTC** (세 소스 중 유일) | 백오피스도 이 테이블만 KST 달력일을 UTC로 바꿔 조회한다(`OperatingModeService.getUsageStatsByOperationMode`) |
| `vital_sign.create_date` · `device.create_date` | **KST** | – |
| Workbench 수동 조회 세션 | **KST** | Aurora global tz가 `Asia/Seoul`. 앱은 Hikari가 `SET time_zone='+00:00'`을 걸어 UTC로 쓴다. 수동 조회는 `UTC_TIMESTAMP()`, 수동 DML 전에는 `SET time_zone='+00:00'` |

사용량 API가 소스별로 구간을 따로 계산하는 이유가 이것이다. 구간을 한 벌로 계산해 넘기면 **AI 대화 횟수만 9시간 어긋난 채 값이 있으니 맞는 것처럼 보인다.**

**재연동 기기**: 기기 해제는 `device` 행을 지우지 않고 `linked_yn='N'`으로 UPDATE만 하고 재등록은 **새 행을 INSERT** 한다. `serial`에 UNIQUE가 없고 `(serial, linked_yn)` 인덱스가 있다 — 같은 serial의 행이 여럿인 것을 전제한 설계이며, **같은 serial에 `device` 행이 20개 넘는 기기가 실재한다.** 그래서 이력 조회를 테이블마다 다른 키로 좁힌다.

| 테이블 | 좁히는 키 |
|---|---|
| `history_clean_result` | `device_id` (적재 시점의 device 행을 가리킨다) |
| `cloud_llm_usages` | `device_id` |
| `vital_sign` | `deviceSerialNo` + `buildingId` (이 표엔 `device_id`가 없다) |

`vital_sign`에서 **등록 시각으로 더 자르지 않는다**. 그 조건이 추가로 거르는 것은 "같은 건물에서 재등록한 경우"뿐인데 그건 남의 데이터가 아니라 같은 고객의 자기 이력이고, 오히려 재등록만으로 과거 측정이 0으로 보이게 된다(`eb7b5bb2`).

**웹훅 테이블**

| 테이블 | 핵심 열 |
|---|---|
| `open_api_webhooks` | `webhook_id`(PK, UUID), `client_id`, `endpoint_url`, `secret`(HMAC용 **평문 저장**), `events`(공백 구분), `serial`(NULL이면 클라이언트 전체), `is_active`, `created_at`/`updated_at`(UTC) |
| `open_api_webhook_deliveries` | `webhook_id`, `event`, `serial`, `payload`, `http_code`(**NULL이 곧 네트워크 오류를 뜻하지 않는다 — §5의 6번**), `attempt`(최대 3), `success`, `duration_ms`, `delivered_at`(UTC, `DATETIME(3)`) |

발송 정책: 최대 3회 시도, 재시도 지연 60초 → 300초, **최근 이력 10행이 전부 실패면 자동 비활성화**(`AUTO_DEACTIVATE_THRESHOLD = 10`). 시도마다 1행이 쌓이므로 10행은 명령 10건이 아니라 **시도 10회**다.

---

## 4. API 계약

전문 명세는 저장소의 `docs/api-external.md`(파트너 공개본)와 `docs/api.md`(내부본)에 있고 두 파일은 같은 내용을 유지한다. 아래는 이번에 추가·변경된 부분이다.

### 4-1. 디바이스 상태 응답의 `errorCode`

`GET /v1/buildings/{buildingId}/devices/{serial}/status` 응답에 필드 1개 추가. **`robotStatus`가 `ERROR`일 때만 나타나고 그 외에는 키 자체가 없다**(`null`이 아니다). 빈 문자열도 싣지 않는다 — 상류는 `ERROR`인데 기기가 코드를 안 보내면 `""`를 남기는데 `NON_NULL`만으로는 걸러지지 않아 파트너가 "코드 없음"과 "빈 코드"를 구분할 수 없다.

REST·웹훅(`BuildingService`)과 WebSocket(`RedisDeviceService`) **양쪽에 같은 규칙**을 넣어 채널마다 판정이 갈리지 않게 했다.

문서에는 기기가 실제로 올리는 **31개 코드표**를 실었다(`F01` 배터리 과전류(충전) … `E01` 필터 커버 열림). IotAgent의 `ErrorCode` enum에서 `isTerminal=true`인 항목만 골라 생성했고 분류는 런처 `ErrorType`의 그룹을 따랐다. 기기는 동시 발생 에러 중 하나만 올리므로 **한 시점의 `errorCode`는 항상 한 개**다. 문서에 "표에 없는 코드가 올 수 있으니 모르는 코드는 문자열 그대로 표시하라"는 주의를 함께 적었다.

**푸시·웹훅은 직전 `data`의 직렬화 JSON과 비교해 변경을 감지**하므로(`WsPushScheduler`·`WebhookScheduler`) `ERROR`가 유지된 채 코드만 바뀌어도 한 번 더 나간다. 의도된 동작이고 양쪽 문서에 적었다. 파트너가 에러 알림을 `→ ERROR` 상태 전이로만 판단하면 이 변화를 놓친다.

### 4-2. 기기 설정 조회 — `GET .../devices/{serial}/settings`

`read` 스코프. 플래그 `openapi.device-settings.public-enabled`.

```json
{
  "serial": "WRBA1M10KRNGJ0125L00000",
  "deviceType": "ABT",
  "autoCleanMode": "OFF",
  "maxCleanTime": 30,
  "cleanCompleteCriteria": "GOOD",
  "airflowMode": "AUTO",
  "nightMode": "OFF"
}
```

| 필드 | 대상 | 허용값 |
|---|---|---|
| `autoCleanMode` | ABT | `ON` / `OFF` (스테이션 자동 고정청정) |
| `maxCleanTime` | ABT | `15` / `20` / `25` / `30` (분, **숫자**) |
| `cleanCompleteCriteria` | ABT | `GOOD` / `NORMAL` |
| `airflowMode` | ABT | `AUTO` / `LOW` / `MEDIUM` / `HIGH` / `TURBO` |
| `nightMode` | ABT | `ON` / `OFF` |
| `scanningCleanMode` | AQM · AQM\_WLS | `ON` / `OFF` |
| `autoCall` | AQM · AQM\_WLS | `ON` / `OFF` |
| `autoCallCriteria` | AQM · AQM\_WLS | `NORMAL` / `BAD` / `VERY_BAD` |

- **기기 타입에 없는 항목은 키 자체가 없다.** 설정 이력이 없는 기기는 값 항목이 전부 빠진 응답이 된다. **서버가 기본값을 대신 채우지 않는다** — 기기가 실제로 어떤 값을 쓰는지 모르는 상태에서 채우면 그 값이 사실처럼 보인다.
- **이 응답으로 변경 성공을 판단하면 안 된다.** ABT는 요청을 발행하는 즉시(기기가 수락하기 **전**에도) 저장하고, AQM·AQM\_WLS는 기기가 수락한 **뒤** 저장한다. 그래서 `setting_yn`·`update_date`는 외부로 내보내지 않았다 — 기기 타입마다 뜻이 달라 "내 변경이 반영됐나"의 답이 될 수 없다.

### 4-3. 기기 설정 변경 — `PATCH .../devices/{serial}/settings`

**`control` 스코프 필요.** 바꿀 항목만 담고, 담지 않은 항목은 유지된다.

```json
{ "autoCleanMode": "OFF", "maxCleanTime": 20 }
```

`202 Accepted`:

```json
{ "commandId": "3f2a1b4c-…", "status": "PENDING", "submittedAt": "2026-09-04T02:31:00Z" }
```

- **문서에 없는 키를 보내면 `400`이다.** 여러 항목을 한 번에 바꿔도 `commandId`는 하나이고 개별 항목의 성공 여부는 나뉘지 않는다. **한 항목이라도 거부되면 어떤 항목도 발행되지 않는다.**
- `autoCallCriteria`를 `NORMAL`로 바꾸면 연동된 ABT의 `cleanCompleteCriteria`가 `GOOD`으로 **함께 변경**된다. 이 변경은 별도 명령이라 위 `commandId`로 추적되지 않는다.
- 모바일 앱에서 동시에 변경하면 **마지막 요청이 이긴다.**

| HTTP | `code` | 상황 |
|---|---|---|
| 400 | `INVALID_REQUEST` | 문서에 없는 키, 허용값 이탈, 빈 본문 |
| 403 | `AUTH_FORBIDDEN` | `control` 스코프 없음 |
| 404 | `DEVICE_NOT_FOUND` | 기기 없음 또는 접근 권한 없음 |
| 409 | `DEVICE_CONDITION_NOT_MET` | 전원 꺼짐, AI 대화·바이탈 사인 동작 중 |
| 422 | `SETTING_NOT_SUPPORTED` | 요청 항목이 이 기기 타입에 없음 |

`400`과 `422`는 다르다. `400`은 요청을 고치면 되지만 `422`는 **요청을 고쳐도 이 기기에서는 설정할 수 없다**는 뜻이다.

### 4-4. 명령 상태 조회 — `GET /v1/commands/{commandId}`

플래그 `COMMAND_TRACKING_PUBLIC_ENABLED`. **3환경 전부 `true`**(파일 기준).

```json
{
  "commandId": "3f2a1b4c-…", "serial": "WRBA1M10KRNGJ0125L00000",
  "command": "SETTINGS", "status": "DEVICE_ACCEPTED", "message": "OK",
  "submittedAt": "2026-09-04T02:31:00Z", "updatedAt": "2026-09-04T02:31:02Z"
}
```

`command`: `CLEAN` / `MOVE` / `RETURN` / `SETTINGS` / `UNKNOWN`.
`status`: `PENDING`(응답 대기) / `DEVICE_ACCEPTED` / `DEVICE_REJECTED` / `TIMED_OUT`(30초 무응답).

- `PENDING`과 `DEVICE_REJECTED`는 다르다. 응답이 도착해야 `updatedAt`이 채워지므로 **그 필드의 유무로도 구분**할 수 있다.
- `TIMED_OUT` 이후 늦은 응답이 도착하면 상태가 바뀔 수 있으므로 최종 실패로 단정하면 안 된다.
- 시각은 두 응답 모두 **UTC**이고 접수 응답의 `submittedAt`과 같은 기준이다. **운영계는 아직 이 계약을 지키지 않는다** — §8-1의 1번.
- **`command`가 `SETTINGS`인 건은 항상 `PENDING` → `TIMED_OUT`이다.** §5의 4번.

### 4-5. 일별 사용량 — `GET .../devices/{serial}/usage/daily?from&to`

`read` 스코프. 플래그 `openapi.usage.public-enabled`.

날짜 경계는 **KST**, `from`·`to`는 **양끝 포함**, 한 번에 **최대 31일**, 둘 다 생략하면 **오늘까지 7일**.

```json
{
  "serial": "WRBA1M10KRNGJ0125L00000", "timezone": "Asia/Seoul",
  "from": "2026-08-28", "to": "2026-08-29",
  "days": [
    { "date": "2026-08-28",
      "cleanMinutes": { "FIXED": 0, "ALL": 42, "ZONE": 15 },
      "aiChatCount": 3, "vitalSignCount": 1 },
    { "date": "2026-08-29",
      "cleanMinutes": { "FIXED": 0, "ALL": 0, "ZONE": 0 },
      "aiChatCount": 0, "vitalSignCount": 0 }
  ]
}
```

- **사용이 없는 날도 0으로 포함**되므로 `days` 길이는 항상 구간의 일수와 같다.
- **이동청정은 `ALL` + `ZONE`이고 합산은 호출하는 쪽이 한다.** 서버가 미리 합치면 "무엇을 이동청정으로 볼 것인가"가 응답 모양에 고정되어 나중에 바꿀 수 없다.
- 당일 데이터의 완성도가 항목마다 다르다. **청정 시간은 완료된 청정만** 반영되므로 진행 중인 청정은 끝난 뒤에 더해진다(당일 값이 조회할 때마다 늘어난다). AI 대화·바이탈은 원장 직접 조회라 조회 시점까지 포함된다. 뭉뚱그려 "당일은 부정확"이라 쓰면 파트너가 쓸 수 있는 값까지 버린다.
- 청정 시간은 **기기가 보고한 소요 시간**이고 서버가 시작·종료 시각으로 재계산하지 않는다. 자정을 넘긴 청정은 **시작한 날짜**에 전체 시간이 기록된다.
- **전원이 켜져 있던 시간은 제공하지 않는다.** 원시 데이터가 없다 — 전원 이력 테이블은 하루 한 번 스냅샷 1행이고 A1 펌웨어가 누적 가동시간을 보내지 않는다. 별도 적재 트랙이 필요하다. **없는 이유를 문서에 적지 않으면 고객사가 계속 물어본다.**

오류: `400 INVALID_REQUEST`(`from > to`, 31일 초과, 형식 오류) / `404 DEVICE_NOT_FOUND`.

### 4-6. 상류 내부 API

```
GET   /api/internal/v1/openapi/buildings/{buildingId}/devices/{serial}/settings?userId=
PATCH /api/internal/v1/openapi/buildings/{buildingId}/devices/{serial}/settings?userId=
GET   /api/internal/v1/openapi/buildings/{buildingId}/devices/{serial}/usage/daily?from&to
```

전 구간에 `X-Internal-Api-Key` 필요(경로 인터셉터). 기본 구간(오늘까지 7일)은 **대외 계약이라 `skix-openapi`가 정하고 상류에는 항상 명시적으로 넘긴다** — 두 곳에서 각자 기본값을 두면 어긋나도 드러나지 않는다.

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **명령 상태는 `response_date`(응답 여부)로 먼저 가른다. `result`로 가르면 안 된다** | `history_control.result`가 `tinyint NOT NULL DEFAULT 0`이라 기기 응답 전에도 `false`다. `result` 우선으로 되돌리면 **응답 대기 중인 명령이 전부 `DEVICE_REJECTED`** 가 되고 `PENDING`·`TIMED_OUT`은 죽은 코드가 된다. 실제로 설정 변경을 조회하면 기기가 정상 수행했는데도 "거부됨"이 나왔다. `OpenApiCommandStatusTest`가 고정 — 되돌리면 2건 실패 |
| 2 | **타임아웃 판정의 기준 시각을 DB와 같은 KST로 가져온다** | 1번 뒤에 살아난 분기다. DB 시각(KST)과 `LocalDateTime.now()`(JVM=UTC)를 비교하면 KST가 9시간 앞서 **어떤 명령도 타임아웃되지 않는다.** "JVM이 UTC니까 UTC로 통일"은 이 표에서만은 틀리다 — `benjamin` 스키마는 UTC 통일 대상이 아니다 |
| 3 | **스코프 판정은 "GET·HEAD 허용목록"이고 "POST 차단목록"이 아니다** | 종전 `resolveRequiredScope`는 POST만 검사했다. 설정 변경을 **PATCH로 추가하는 순간 `read`만 가진 파트너가 남의 기기 설정을 바꿀 수 있게 된다.** HEAD를 조회로 분류한 것은 실측 근거가 있다 — 스프링 `RequestMethodsRequestCondition`은 `@GetMapping`에 GET·HEAD만 매칭하므로 HEAD를 쓰기로 분류하면 `read` 전용 파트너의 조회가 403이 된다. 되돌리면 `JwtAuthFilterTest` 7건 실패 |
| 4 | **`SETTINGS` 명령이 영원히 `TIMED_OUT`인 것을 "서버 버그"로 보고 고치려 들지 않는다** | **ABT 펌웨어가 `response/setConfig`를 보내지 않는다.** Athena 실측(2026-09-04 dev): `command_setConfig` 9건 → `response_setConfig` **0건**, 대조군 `getConfig`는 125 → 124. 대신 기기가 `event`(CONFIG / `eventStatus=update`)를 보내고 서버가 `getConfig`를 되던져 실값을 받는다(`ResponseDeviceService`의 CONFIG 케이스). **서버는 이미 수신 준비가 돼 있다.** 해결하려면 펌웨어에 `response/setConfig` 발행을 요청해야 한다(§6-1). 사용자 결정으로 현 상태를 유지하고, 문서의 "명령 조회로 결과 확인" 안내는 설정에 한해 사실과 다른 채로 두었다 |
| 5 | **`history_clean_result.start_time`을 UTC로 변환하지 않는다** | `Z`가 붙어 있지만 **KST 벽시계**다. 기기가 로컬 시각을 UTC 표기로 보낸다(같은 행 `time_stamp`가 진짜 UTC이고 `create_date`와 정확히 9시간 차이 나는 것으로 확인). KST 달력일 문자열과 **그대로** 비교해야 맞고, "Z니까 변환"하는 순간 9시간 틀어진다. ISO-8601 고정폭이라 사전순 비교가 곧 시간순이고 인덱스도 탄다 |
| 6 | **웹훅 실패 원인을 `deliveries.http_code`로 판정하지 않는다** | `WebhookDispatcher`가 `.retrieve()`를 쓰므로 **4xx/5xx에서 예외가 던져지고 `http_code`는 NULL·`duration_ms`는 0으로 기록된다.** DDL 주석의 "네트워크 오류 시 NULL"은 사실과 다르다. non-2xx를 기록하는 분기는 사실상 죽은 코드이고 테스트도 `httpCode`를 검증하지 않는다. **원인 문자열은 WARN 로그에만 있다.** 이 사고의 1차 답변에서 실제로 "네트워크 오류"로 오판했다. 판정은 CloudWatch 로그로 한다(부록 B) |
| 7 | **`errorCode`는 빈 문자열을 싣지 않고, `ERROR`가 아닌 상태에서는 키 자체를 내보내지 않는다** | 상류는 `ERROR`인데 기기가 코드를 안 보내면 `""`를 남기고 `NON_NULL`만으로는 걸러지지 않는다. 파트너가 "코드 없음"과 "빈 코드"를 구분할 수 없게 된다. 또 `ERROR`가 아닌 상태에 남은 코드를 실으면 **해소된 에러로 알림이 나간다**. REST·웹훅·WebSocket 세 채널에 같은 규칙이 들어가 있으니 한쪽만 고치지 말 것 |
| 8 | **모르는 요청 키는 `@JsonAnySetter`로 거부한다. `@JsonIgnoreProperties(ignoreUnknown=false)`가 아니다** | 전역 설정이 `fail-on-unknown-properties=false`라 그 상태에서는 애너테이션을 붙여도 모르는 키가 그대로 통과한다(두 방식을 나란히 돌려 확인). 이게 없으면 **오타 난 키가 상류에 닿기도 전에 사라진 채 `202`가 나가고 파트너는 바뀌지 않은 설정을 바뀐 줄 안다** |
| 9 | **새 오류 코드는 `ErrorCode` enum과 `mapReasonToErrorCode` 두 곳을 같이 고친다** | enum에만 넣으면 상류의 422가 폴백(`return ErrorCode.INVALID_REQUEST`)에 걸려 뭉개진다. **상태코드는 422 그대로라 겉보기에는 멀쩡하고** 파트너만 원인을 구분할 수 없게 된다. 분기를 주석 처리하면 테스트 2건이 실패한다 |
| 10 | **제출본 브랜치(`81f0d6a`)를 그대로 머지하지 않는다** | `application-ecs.yml`이 기본값 없는 환경변수 3종(`CORS_ALLOWED_ORIGINS`·`OPENAPI_INTERNAL_API_KEY`·`NAMUHX_INTERNAL_API_KEY`)을 요구하는데 태스크 정의 3개 어디에도 없어 **기동 실패**한다. 그 브랜치의 `InternalApiKeyFilter`는 키가 비면 `/internal/v1/**` 전부 401이라 **백오피스 클라이언트 발급이 즉시 죽는다.** 실제 반영분은 §3-5의 4건이다 |
| 11 | **`/internal/v1/**`에 prefix 단위 인증 *필터*를 걸지 않는다 (컨트롤러 검증 컴포넌트를 쓴다)** | `backend-api-main`의 `/internal/**` 아래에는 키를 보내지 않는 호출자가 20여 개 남아 있어 필터로 걸면 전부 죽는다. 경로를 **`/internal/v1/openapi/**` 하위로 좁혀** 걸었기 때문에 안전한 것이다(호출자가 `skix-openapi` 하나). 범위를 넓히면 `skix-security`·medcare·streaming이 401로 죽는다 |
| 12 | **한도 검사는 fail-closed, 웹훅 발송 쿼타만 fail-open** | 종전 fail-open의 전제는 "Redis가 죽으면 앞단 블랙리스트 확인에서 이미 503"이었는데, 블랙리스트는 `EXISTS`(읽기)라 maxmemory 초과 + noeviction에서 **성공**하고 호출량 집계는 `INCR`(쓰기)라 **실패**한다. 즉 죽은 코드가 아니라 **Redis가 가득 차는 순간 한도가 통째로 풀리는 경로**였다(Docker Redis 7로 실측). 웹훅만 fail-open인 이유는 재시도 큐가 없어 막으면 **이벤트가 영구 유실**되기 때문이다 |
| 13 | **자격증명 폐기·로테이션은 "게이트를 먼저 닫고 토큰을 나중에 지운다"** | 발급 경로는 `status='ACTIVE'`와 `secret_key_hash` **두 개의 독립 게이트**를 본다. 토큰을 먼저 지우면 그 사이에 받아 간 토큰이 폐기 대상에서 빠져 **만료(1시간)까지 살아남는다.** 로테이션은 대개 시크릿 유출 대응이라 그 창이 정확히 위험 구간이다. `ClientServiceTest`가 `inOrder`로 고정 |
| 14 | **감사 로그 마스킹은 경로별이고, 가린 자리에는 `null`이 아니라 `[REDACTED]`를 남긴다** | `null`이면 "본문이 없었다"와 "일부러 버렸다"가 구분되지 않아 감사 기록이 비었을 때 정상인지 저장 로직 고장인지 판별할 수 없다. 전면 미저장(`73963d4`)으로 되돌리면 장애 조사에 필요한 응답 본문까지 사라진다. **트레이드오프는 감수한 것이다** — 위치·공기질 등 개인 데이터를 담은 본문이 감사 DB에 90일 남는다 |
| 15 | **배포 순서: 공유키를 *붙이는* 쪽(`skix-openapi`)이 *검증하는* 쪽(`backend-api-main`)보다 먼저** | 반대로 하면 **dev·stg가 즉시 죽는다.** 그 두 환경은 `enforce-api-key`가 이미 `true`다. 운영계는 `false`라 붙여도 로그만 남는다. 반대로 플래그(`DEVICE_SETTINGS_PUBLIC_ENABLED` 등)는 **상류가 먼저 배포돼 있어야** 한다 — 먼저 켜면 두 API가 전부 404가 된다 |

---

## 6. 타 팀·외부에 전달해야 할 것 (미전달)

### 6-1. ABT 펌웨어 담당 — `response/setConfig` 발행 요청

- **현상**: 기기 설정 변경 명령(`command/setConfig`)에 대해 기기가 `response/setConfig`를 발행하지 않는다. 그래서 파트너가 `GET /v1/commands/{commandId}`로 결과를 확인할 수 없고 항상 `PENDING` → `TIMED_OUT`이 된다.
- **근거**: Athena 실측(2026-09-04, 개발계). `command_setConfig` 9건 대비 `response_setConfig` 0건. 같은 기간 대조군 `getConfig`는 125건 요청에 124건 응답.
- **요청**: 다른 명령(`clean`·`move`·`return`·`getConfig`)과 같은 규약으로 `response/setConfig`를 발행해 달라. **서버는 이미 수신 준비가 돼 있어 펌웨어 변경만으로 해소된다.**
- **대안**: 기기가 보내는 `event`(CONFIG / `eventStatus=update`)를 ack로 간주하는 방법이 있으나, 그 이벤트는 설정 변경 주체가 누구든(앱·관제·파트너) 똑같이 올라와 **어느 명령에 대한 응답인지 특정할 수 없다.** 그래서 서버 쪽 우회로 처리하지 않았다.

### 6-2. 파트너사 — 명령 응답 시각 UTC 전환 통보

- 운영계 배포와 함께 `GET /v1/commands/{commandId}`의 `submittedAt`·`updatedAt` 표기가 **오프셋 없는 KST 문자열(`2026-09-04T15:18:12`)에서 UTC ISO-8601(`2026-09-04T06:18:12Z`)로 바뀐다.**
- 같이 알려야 할 것: `PENDING`과 `DEVICE_REJECTED`를 이제 구분해 다룰 수 있다(종전에는 응답 대기 중인 명령도 "거부됨"으로 보였다).
- **운영 배포 전에 보내야 한다.** 파트너가 지금 값을 KST로 파싱하고 있다면 배포 순간 9시간 틀어진다.

### 6-3. 파트너사 — 웹훅 자동 비활성화 정책 안내 (권장)

현재 파트너에게 "연속 실패 10회면 웹훅이 꺼지고, 다시 켜는 API는 없다"는 안내가 없다. 이번 사고가 6일간 방치된 이유의 절반이 이것이다. §8-3의 재발 방지와 함께 다루는 편이 낫다.

---

## 7. 배포 방법

### 7-1. 환경·형상

| 환경 | AWS 계정 | API 호스트 | 문서 호스트 | ECS 클러스터 / 서비스 |
|---|---|---|---|---|
| dev | `010928196421` | `openapi-dev.namuhx.com` | `developers-dev.namuhx.com` | `skmg-airbot-dev-ecs-cluster` / `skix-openapi-dev-svc` |
| stg | `087432099373` | `openapi-stg.namuhx.com` | `developers-stg.namuhx.com` | `skmg-airbot-stg-ecs-cluster` / `skix-openapi-stg-svc` |
| prd | `779846811758` | `openapi.namuhx.com` | `developers.namuhx.com` | `skmg-airbot-prd-ecs-cluster` / `skix-openapi-prd-svc` |

리전은 전부 `ap-northeast-2`. **CI가 없다.** 이미지 빌드·푸시·서비스 갱신이 전부 수동이고 로컬 맥에서는 AWS에 도달할 수 없으므로 **CloudShell(또는 VDI)에서 해야 한다.**

### 7-2. 순서 (역순 금지)

```
1. backend-api-main 배포            ← 상류가 먼저. 안 그러면 설정·사용량이 404
2. skix-openapi 태스크 정의 등록    ← 새 환경변수·secrets 가 여기서 들어간다
3. skix-openapi 이미지 배포         ← 공유키를 "붙이는" 쪽
4. backend-api-main 의 공유키 검증  ← 2·3 이 끝난 뒤. (같은 배포에 묶여 있으면 1과 동시)
5. 플래그 ON (태스크 정의 갱신 + 재배포)
6. skix-openapi-docs 스펙 재수집 · 사이트 배포   ← 플래그 ON 이후여야 스펙에 나타난다
```

| 순서 위반 | 결과 |
|---|---|
| `skix-openapi`보다 상류의 공유키 검증이 먼저 | **dev·stg 즉시 전면 실패.** 두 환경은 `enforce-api-key`가 이미 `true` |
| 상류 배포 전에 플래그 ON | 설정·사용량 API가 전부 404 → 파트너에게는 `INVALID_REQUEST`로 보인다(§8-3의 잔여 결함) |
| 문서 사이트를 플래그 ON 전에 재수집 | 플래그가 꺼진 엔드포인트는 스펙에서 빠지므로 **새 API가 문서에 나타나지 않는다** |

### 7-3. ⚠️ 가장 많이 걸리는 함정 — 태스크 정의 리비전

**`deploy/cloudshell-push.sh`는 태스크 정의를 등록하지 않는다.** `describe-task-definition`으로 **기존 최신 리비전을 찾아 재사용**할 뿐이다.

```bash
# cloudshell-push.sh 의 [6/7] 단계가 하는 일
TASK_DEFINITION_ARN="$(aws ecs describe-task-definition \
  --region ap-northeast-2 --task-definition skix-openapi-${ENV}-tdef \
  --query 'taskDefinition.taskDefinitionArn' --output text)"
```

따라서 **환경변수나 secrets를 추가·변경했다면 `register-task-definition`을 따로 해야 한다.** 이걸 놓치면 플래그가 안 먹고 → 엔드포인트가 404 → 파트너 응답에는 `INVALID_REQUEST`로 표시돼 **원인 진단을 두 번 헤맨다**(실제로 그렇게 됐다).

```bash
# 리비전 등록 (레포의 JSON 을 그대로 쓴다)
aws ecs register-task-definition --region ap-northeast-2 \
  --cli-input-json file://deploy/task-definition-dev.json
# 그 다음에 cloudshell-push.sh 를 돌리면 방금 등록한 리비전을 집어 간다
```

### 7-4. 애플리케이션 배포 (`skix-openapi`)

```bash
# 로컬 (맥)  — 테스트가 빌드 게이트다. 하나라도 실패하면 이미지가 만들어지지 않는다
./deploy/build-export.sh            # → openapi.tar.bz2
#   이 스크립트는 Dockerfile 에 -Duser.timezone=UTC 가 있는지 먼저 검사한다.
#   베이스 이미지가 바뀌어도 LocalDateTime.now() 가 조용히 다른 시간대로 움직이지 않게 하는 가드다.

# CloudShell (대상 계정으로 로그인, 리전 서울)
#   openapi.tar.bz2 와 deploy/cloudshell-push.sh 를 업로드한 뒤
./cloudshell-push.sh dev            # dev | stg | prd
```

**현재 배포된 이미지·리비전 확인** (로컬에서는 불가, CloudShell/VDI에서):

```bash
aws ecs describe-services --region ap-northeast-2 \
  --cluster skmg-airbot-dev-ecs-cluster --services skix-openapi-dev-svc \
  --query 'services[0].{TaskDef:taskDefinition,Running:runningCount,Desired:desiredCount}'

aws ecs describe-task-definition --region ap-northeast-2 \
  --task-definition skix-openapi-dev-tdef \
  --query 'taskDefinition.containerDefinitions[0].{Image:image,Env:environment}'
```

### 7-5. 문서 사이트 배포 (`skix-openapi-docs`)

```bash
# 로컬
#   .env.local 에 OPENAPI_CLIENT_ID / OPENAPI_SECRET_KEY 가 있어야 한다(스펙 수집용 자격증명)
./deploy/build-export.sh dev        # .env.dev 적용 → 스펙 수집 → npm run build → tar.bz2

# CloudShell
bash cloudshell-sync.sh dev         # S3 sync --delete + CloudFront invalidation /*
```

| 환경 | S3 버킷 | CloudFront 배포 ID |
|---|---|---|
| dev | `airbot-s3-openapi-dev` | `E2QBE903EMO088` |
| stg | `skmg-benjamin-stg-s3-openapi` | `E2RFBUL6KIDY59` |
| prd | `skmg-benjamin-prd-s3-openapi` | `E7J3I6XPEGFG7` |

**`npm run build`는 커밋된 `openapi.json`을 쓴다.** 새 API를 문서에 올리려면 **해당 환경에 플래그를 켠 뒤** `npm run fetch-spec -- <env>`로 스펙을 다시 받아야 한다. 빌드 로그의 `Source:` / `Synced:` 줄에 어느 환경 스펙인지 표시된다.

사이드바 라벨은 `sidebars.ts`의 `labelMap`이 **태그명과 정확히 일치할 때만** 치환하고, 어긋나면 오류 없이 한글 태그명을 그대로 내보낸다. 새 태그를 만들면 여기에 매핑을 넣어야 한다.

### 7-6. 배포 후 검증

```bash
ENV_HOST=openapi-dev.namuhx.com        # stg: openapi-stg / prd: openapi

# 0) 내부 경로가 외부에 열려 있지 않은지 — 기대 404. 200/401 이면 릴리스 중단
for P in /internal /internal/v1/clients /internal/v1/plans /internal/v1/permissions; do
  printf "%-28s %s\n" "$P" "$(curl -s -o /dev/null -w '%{http_code}' "https://$ENV_HOST$P")"
done

# 1) 토큰 발급
TOKEN=$(curl -s -X POST "https://$ENV_HOST/v1/auth/token" \
  -H 'Content-Type: application/json' \
  -d '{"clientId":"<CLIENT_ID>","secretKey":"<SECRET_KEY>"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["accessToken"])')

# 2) 상태 응답에 errorCode 필드 규약 확인 (ERROR 가 아니면 키 자체가 없어야 한다)
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://$ENV_HOST/v1/buildings/<BID>/devices/<SERIAL>/status" | python3 -m json.tool

# 3) 설정 조회 — 기기 타입에 없는 항목은 키가 없어야 한다
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://$ENV_HOST/v1/buildings/<BID>/devices/<SERIAL>/settings"

# 4) 모르는 키 거부 — 기대 400
curl -s -o /dev/null -w '%{http_code}\n' -X PATCH -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"autoCleanModo":"OFF"}' \
  "https://$ENV_HOST/v1/buildings/<BID>/devices/<SERIAL>/settings"

# 5) read 전용 토큰으로 PATCH — 기대 403 (control 스코프 회귀)
# 6) 설정 변경 → commandId 로 상태 조회. SETTINGS 는 TIMED_OUT 이 정상이다(§5의 4번)
curl -s -H "Authorization: Bearer $TOKEN" "https://$ENV_HOST/v1/commands/<COMMAND_ID>"
#    → submittedAt/updatedAt 이 'Z' 로 끝나는지, PENDING 이 DEVICE_REJECTED 로 나오지 않는지

# 7) 사용량 — days 길이가 구간 일수와 같은지, 31일 초과가 400 인지
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://$ENV_HOST/v1/buildings/<BID>/devices/<SERIAL>/usage/daily?from=2026-09-01&to=2026-09-07"
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  "https://$ENV_HOST/v1/buildings/<BID>/devices/<SERIAL>/usage/daily?from=2026-01-01&to=2026-09-01"

# 8) 스펙에 새 엔드포인트가 나타나는지 (플래그 ON 확인의 가장 빠른 방법)
#    /v3/api-docs 는 Form 로그인(파트너 clientId/secretKey)이 필요하다
```

로그 키워드: 웹훅 `[Webhook]`, WS 세션 재검증 `[WsRevalidate]`, 웹훅 폴링 예산 초과 `[WebhookScheduler] 예산 초과`(뜨면 상류 지연 또는 스레드풀 부족).

### 7-7. 롤백

- **애플리케이션**: 이전 태스크 정의 리비전으로 `update-service`. 신규 엔드포인트만 추가된 배포라 기존 파트너 계약에 영향이 없다. 단 **명령 상태 응답 시각이 UTC↔KST로 되돌아간다** — 파트너에게 통보한 뒤라면 다시 알려야 한다.
- **플래그만 되돌리기**: 태스크 정의에서 해당 `*_PUBLIC_ENABLED`를 `false`로 바꿔 등록·재배포. 이미지를 되돌릴 필요가 없다. **다크 배포를 쓴 이유가 이것이다.**
- **DB**: 이번 작업에 신규 테이블·컬럼이 없다. 되돌릴 것이 없다.
- **문서 사이트**: 이전 아카이브로 `s3 sync --delete` 후 CloudFront 무효화.

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **운영계 명령 상태 조회가 지금 거짓을 내보내고 있다.** `COMMAND_TRACKING_PUBLIC_ENABLED`가 운영계에서 `true`인데(2026-08-06 `b608aeb`) 판정·시각 수정(`41af4e53` / `c20c33a`)은 `main`에 없다. 즉 운영 파트너는 **응답 대기 중인 청정·이동·복귀 명령을 `DEVICE_REJECTED`로 보고 있고**, 타임아웃이 영원히 나지 않으며, 시각이 오프셋 없는 KST다. 운영 승격 우선순위를 여기에 둘지 판단할 것 | 백엔드 | 승격 전 §6-2 통보 선행 |
| 2 | 개발계·검증계에 **이미지가 실제로 배포됐는지** 확인. 브랜치는 09-04자이고 CI가 없어 브랜치만으로는 판정되지 않는다 | 백엔드 | §7-4 명령. VDI/CloudShell |
| 3 | 개발계·검증계 **태스크 정의 리비전**에 09-04 환경변수 6종과 secrets 2종이 실제로 들어갔는지 확인 | 백엔드 | §7-3의 함정. 파일에만 있고 ECS엔 없을 수 있다 |
| 4 | 문서 사이트(`developers-dev` / `developers-stg`)에 설정·사용량·`errorCode`가 보이는지 확인. 안 보이면 플래그 ON 후 스펙 재수집·재배포 | 백엔드 | §7-5 |
| 5 | §6-1 펌웨어 요청 전달 | 백엔드 → 펌웨어 담당 | 전달 안 하면 `SETTINGS` 명령 조회는 영구히 무의미 |

### 8-2. 운영 릴리스 게이트

| # | 확인할 것 |
|---|---|
| 6 | **§6-2 파트너 통보를 배포 전에** 보낸다. 시각 표기가 바뀐다 |
| 7 | `skix-openapi` `main` 승격 시 `task-definition-prd.json`이 통째로 교체된다. 현재 `main` 파일에는 환경변수 5종과 secrets 2종이 없으므로 **승격 후 `register-task-definition`을 반드시** 한다(§7-3) |
| 8 | 운영계 플래그 정책 확정 — 3종을 켤지, 언제 켤지. 코드가 올라가도 `*_PUBLIC_ENABLED=false`면 파트너에게 보이지 않는다 |
| 9 | `APP_INTERNAL_ENFORCE_API_KEY`를 운영계에서 `true`로 올릴지 결정. 지금은 값 자체가 없어 `skix-openapi` `/internal/v1/**`가 **앱 계층 무인증**이다. 올리기 전에 백오피스가 실제로 키를 보내는지 확인해야 한다 — 안 보내면 클라이언트 발급이 즉시 죽는다 |
| 10 | `backend-api-main` `main` 승격. 이 저장소 `main`은 2026-09-01에 핫픽스 revert가 있었으므로 승격 범위를 따로 확인할 것 |

### 8-3. 후속 (이 기능을 막지는 않음)

| # | 항목 |
|---|---|
| 11 | **웹훅 재발 방지 3건** — (a) `deliveries.http_code`에 4xx/5xx를 실제로 기록(`.retrieve()` → `exchangeToMono`), (b) 자동 비활성화 시 알람, (c) 폴링 중 상류 4xx(`USER_NOT_FOUND`/`DEVICE_NOT_FOUND`) 스킵 로그. 지금은 셋 다 없어 **같은 사고가 또 6일간 안 보인다** |
| 12 | **웹훅 재활성화 API 없음.** 복구가 수동 DB UPDATE거나 삭제·재등록(= 시크릿 변경, 고객 작업 유발)뿐이다. `PATCH /v1/webhooks/{id}` 또는 백오피스 경로를 검토 |
| 13 | **상류 404가 파트너에게 `INVALID_REQUEST`로 나간다.** `mapReasonToErrorCode`의 폴백이 `reason`을 모르는 모든 4xx를 `INVALID_REQUEST`로 떨어뜨린다. 플래그 순서를 틀렸을 때 진단을 두 번 헤맨 원인 |
| 14 | **사용량 실측 2건** — (a) 재연동 기기의 청정 이력이 `device_id` 단위로 갈리는 것이 고객 기대와 맞는지(같은 serial에 `device` 행 20개 넘는 기기 실재), (b) `CLMD0002`·`CLMD0003`(전체·구역 청정) 집계가 백오피스 수치와 일치하는지 대사 |
| 15 | **전원 켜진 시간** 제공. 원시 데이터가 없어 별도 적재 트랙이 필요하다(전원 이력은 하루 1행 스냅샷, A1 펌웨어가 누적 가동시간 미전송) |
| 16 | **`/internal` 노출 기간 조사.** ALB 차단(C1)은 앞으로를 막을 뿐이다. 노출 기간에 외부 출처 호출이 있었다면 이미 유출된 자격증명이 있다. `/internal`은 앱 감사 로그에 남지 않으므로(`AuditLogFilter`가 `/v1/` 외 skip) **ALB 액세스 로그가 유일한 단서**다. 절차는 부록 C-3 |
| 17 | **C1의 남는 위험** — ALB 규칙은 증상만 막는다. `/internal/**`이 `permitAll()`인 것은 그대로라 **같은 VPC의 어떤 워크로드든 태스크 IP로 직접 호출**하면 인증 없이 평문 시크릿을 발급받을 수 있다. 이건 ALB로 원천 차단이 불가능하다. 9번(`enforce-api-key` ON)이 이 항목을 닫는다 |
| 18 | `DeviceCodeMapper.toExternalChargeState`가 모르는 코드를 **그대로 통과**시킨다(다른 매퍼는 전부 `null`). 내부 코드 `CHST9999`가 고객에게 노출된다. 테스트로 현재 동작을 고정만 해 뒀다 |
| 19 | `RedisDeviceService`의 누락 값 표현이 정수는 `-1`, 실수는 `null`로 갈린다. 같은 "측정 안 됨"인데 `-1`을 등급으로 오해할 수 있다. `getStatus`는 전원 키가 없으면 `"OFF"`로 **단정**한다 |
| 20 | `WsAuditAspect`는 핸들러가 예외를 던지면 감사 로그를 남기지 않는다(`try/finally` 부재). "호출 없음"과 "호출 실패"를 구분할 수 없다 |
| 21 | **CI 없음.** 이미지 빌드가 `clean test bootJar`라 테스트가 게이트이긴 하나, 통합 테스트(Testcontainers)를 기본 `test`에 넣을 수 없어 `C2` Tier 3은 종료 처리했다. CI 도입이 선행 조건 |
| 22 | 커밋 히스토리에 로컬 실행용 RSA 개인키가 남아 있다(`8163537`에서 파일은 분리). 운영 SSM `JWT_PRIVATE_KEY`와 같은 값인지 **미확인**. 히스토리 재작성은 비용 대비 이득이 없다고 보고 종료했으나 값 비교는 남아 있다 |

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| "기기 설정을 바꿨는데 명령 조회가 계속 `TIMED_OUT`" | **정상이다.** ABT가 `response/setConfig`를 올리지 않는다(§5의 4번). 반영 여부는 잠시 뒤 `GET .../settings`로 확인하라고 안내한다. 단 ABT는 요청 즉시 저장하므로 그 조회도 "수락됨"의 증거는 아니다 |
| "명령이 계속 `DEVICE_REJECTED`로 나온다" | 운영계면 §8-1의 1번(수정 미배포). 개발계·검증계면 실제 거부일 수 있으니 `updatedAt` 유무를 본다 — 없으면 아직 응답이 안 온 것이다 |
| "시각이 9시간 틀리다" | 어느 응답인지 먼저 구분한다. 접수 응답(`202`)은 예전부터 UTC, 상태 조회는 운영계가 아직 KST다. `history_clean_result.start_time`은 `Z`가 붙어도 KST다(§5의 5번) |
| "웹훅이 안 온다" | ① `SELECT is_active FROM open_api_webhooks WHERE webhook_id=…` ② `0`이면 자동 비활성화. **DB의 `http_code` NULL을 네트워크 오류로 읽지 말 것** — 4xx/5xx도 NULL이다. 원인은 CloudWatch `/ecs/skix-openapi-{env}`에서 `[Webhook] <id> 전송 오류` 메시지로 판정한다. 절차 전문은 부록 B |
| "웹훅을 다시 켜 달라" | 재활성화 API가 없다. 부록 B-3의 DML. **`SET time_zone='+00:00'`을 먼저** 하지 않으면 `updated_at`에 KST가 섞인다 |
| "웹훅 시크릿을 잃어버렸다" | **복구 수단이 없다.** 시크릿은 등록 응답에서 1회만 반환되고 목록 조회에는 없다. 삭제·재등록만 가능하며 그러면 고객 쪽 검증 코드도 바꿔야 한다 |
| "특정 API만 404" | 기능 플래그 또는 상류 미배포다. 파트너 응답에는 `INVALID_REQUEST`로 보이므로 헷갈린다(§8-3의 13번). ① 태스크 정의 **리비전**의 `*_PUBLIC_ENABLED` 확인(파일 말고), ② 상류 배포 확인 |
| "파트너 전원이 401" | `COGNITO_CLIENT_ID`가 틀리면 개발자 포털이 전면 401이 된다. 파트너 API(Bearer JWT)는 별개 경로다. 상류 공유키 불일치는 **파트너에게 500으로 나간다**(`clientFacingStatus`) — 그대로 401을 내보내면 파트너가 자기 토큰이 만료된 줄 알고 재발급을 반복하기 때문이다 |
| "응답이 503" | 한도 검사가 fail-closed다(§5의 12번). Redis 부분 장애일 수 있다 |
| 인바운드 감사 로그 조회 | `open_api_inbound_log`(90일). 자격증명 4경로의 본문은 `[REDACTED]`로 남아 있다. `/internal/**`은 **기록되지 않는다**(`AuditLogFilter`가 `/v1/` 외 skip) |
| 아웃바운드(상류 호출) 조사 | `open_api_outbound_log`. 실제 상태코드와 **오류 응답 본문만** 담긴다(성공 본문은 담지 않는다 — 무제한 버퍼링 방지) |
| DB를 수동 조회할 때 | 세션 시간대가 **KST**다. 앱은 UTC로 쓴다. 조회는 `UTC_TIMESTAMP()`, DML 전에는 `SET time_zone='+00:00'` |

---

## 부록 A. 핵심 커밋 (시간순)

### skix-openapi

| 날짜 | 커밋 | 내용 | main 반영 |
|---|---|---|---|
| 08-03 | `0a3fc58` | 테스트 실행 환경 복구 — Launcher 누락, 배포 경로에 테스트 미실행 | 반영 |
| 08-03 | `33eae57` | Tier 1 테스트 80건 (인증·한도·SSRF·웹훅 소유권) | 반영 |
| 08-04 | `dae43fc` `3ea6e4c` `b824101` | 기본 프로필 제거, JWKS 증폭 차단, Cognito `token_use`·`aud` 검증 | 반영 |
| 08-04 | `4a39b74` `be49859` | ShedLock 분산 락, 웹훅 폴링 병렬화(고정 8 + 30초 예산) | 반영 |
| 08-04 | `af1154f` | 한도 검사 fail-open → **fail-closed** | 반영 |
| 08-04 | `91222cf` | WS 세션 중 토큰·권한 재검증, 서버 측 종료 | 반영 |
| 08-04 | `0621ee9` | **자격증명 폐기·로테이션 순서 정정** | 반영 |
| 08-04 | `3e2065b` | 아웃바운드 감사 로그에 실제 상태코드·상류 오류 본문 | 반영 |
| 08-04 | `d46d5e9` | Swagger 공통 오류 응답 `$ref` 단일화(64건, 컨트롤러 2,217 → 1,571줄) | 반영 |
| 08-11 | `81f0d6a` | 보안점검 **소스 제출 전용** 브랜치. **머지 금지** | 미반영(의도) |
| 08-12 | `73963d4` | 감사 로그 본문 전면 미저장 — `145a838`이 대체 | 반영 |
| 08-13 | `145a838` | **감사 로그 경로별 마스킹 + `[REDACTED]`** | 반영 |
| 08-13 | `60f4ac9` `855b215` `8163537` | 포털 감사 주체 기록 / 토큰 발급 IP 제한 30회분 / 로컬 시크릿 분리(`config/local-secrets.yml.example`만 커밋) | 반영 |
| 08-14 | `f1adacb` `014ef2c` | DB 세션 UTC 강제·기동 검증 / 사용량·파기 기준 UTC 정정 | 반영 |
| 08-18 | `dcb1a5a` | DB TLS 강제 `sslMode=REQUIRED` | 반영 |
| 08-19 | `2f4c290` | 내부 API 2차 방어선 방향 정정 기록 | 반영 |
| 08-27 | `2c444ba` `769707a` | 포털 sub 정규화 · `/internal/v1/**` 공유키 검증(log-only) · 탈퇴 파기 | **미반영** |
| 08-27 | `e8bffe8` `e8db0f6` `0a08a5f` | task-definition 3환경에 내부키·정규화 env / 플래그 실상태 고정 / 작업 문서 2종 저장소에서 제거 | **미반영** |
| 09-03 | `d622f2f` `da37073` `758774a` `8df8271` | 날씨 API · 공기질 AQM 전용 문서 오류 수정 · 대외 용어 '건물' 환원 · 날씨 플래그 | **미반영** |
| 09-04 | `120d885` | **상태 응답에 `errorCode`** (+ 31개 코드표 문서) | **미반영** |
| 09-04 | `50155df` | **기기 설정 조회·변경** (다크 배포) | **미반영** |
| 09-04 | `40dc742` | **일별 사용량** (다크 배포) + `ClockConfig` | **미반영** |
| 09-04 | `9a2045f` | **control 스코프를 GET·HEAD 허용목록으로 전환** | **미반영** |
| 09-04 | `c20c33a` | 명령 상태 응답 시각 UTC 수신·표기 고정 | **미반영** |
| 09-04 | `9ba32fa` | 상류 호출 전체에 공유키 부착(필터 1곳, 호출 지점 16곳 대체) | **미반영** |
| 09-04 | `5d66a72` | 설정·사용량 플래그를 dev·stg에서 ON | **미반영** |

### skix-openapi-docs

| 날짜 | 커밋 | 내용 | main 반영 |
|---|---|---|---|
| 08-04 | `286143d` `7379cd2` `fc31157` `1bd90f3` | 목업 API 키 페이지 제거 / 인증 훅 이중화 제거 / 생성 문서 git 제외 / `.env.local` 파싱 교체 | 반영 |
| 09-03 | `89f2fd9` | 사이드바 라벨 2건 추가('명령'·'웹훅 이벤트'가 한글로 노출되고 있었다) | **미반영** |
| 09-03 | `290104e` `9b7df4f` | 날씨 스코프·라벨 / 가이드 용어 '건물' 환원 | **미반영** |
| 09-04 | `f2cb1dd` | `errorCode`·기기 설정 반영. `control` 스코프 설명을 "POST"에서 "조회가 아닌 모든 요청"으로 정정 | **미반영** |
| 09-04 | `2a1669c` | 사용량 API 소개 문서·사이드바 반영 | **미반영** |

### backend-api-main (이 작업 관련분)

| 날짜 | 커밋 | 내용 | main 반영 |
|---|---|---|---|
| 08-27 | `ee2b1fc5` | openapi 연동 키를 canonical sub로 정규화 + 공유키·탈퇴 파기 연동 | **미반영** |
| 09-03 | `bbdcef14` | 홈 날씨 조회 내부 엔드포인트 | **미반영** |
| 09-04 | `c0d95f15` | **기기 설정 조회·변경 내부 API** (`OpenApiSettingKey`·`ValueCodec`) | **미반영** |
| 09-04 | `c41236c7` | **일별 사용량 내부 API** (소스별 시간대 분리) | **미반영** |
| 09-04 | `e92c1b15` | 사용량 매퍼 XML ↔ 인터페이스 정합 테스트 | **미반영** |
| 09-04 | `eb7b5bb2` | 바이탈 사인의 등록시각 조건 제거 | **미반영** |
| 09-04 | `41af4e53` | **명령 상태를 응답 여부로 판정 + 시각 UTC 통일** | **미반영** |
| 09-04 | `00b10674` | `/internal/v1/openapi/**` 경로 인터셉터로 공유키 검증 | **미반영** |

---

## 부록 B. 웹훅 — 판정과 재활성화 절차

### B-0. 먼저 알아야 할 것

- **`deliveries.http_code`가 NULL이라고 네트워크 오류가 아니다.** `WebhookDispatcher`가 `.retrieve()`를 쓰므로 4xx·5xx에서도 예외가 나고 NULL·`duration_ms=0`으로 기록된다. 원인 판정은 **CloudWatch 로그**로 한다.
- **Workbench 수동 세션은 KST다** (Aurora global tz = `Asia/Seoul`). 앱은 Hikari가 `SET time_zone='+00:00'`을 걸어 **UTC로 쓴다.** 조회는 `UTC_TIMESTAMP()`, DML 전에는 세션 시간대를 UTC로 바꾼다.
- 자동 비활성화 조건: **최근 딜리버리 10행이 전부 `success=0`.** 시도마다 1행이 쌓이므로 이벤트 4건(각 3시도)이면 조건이 충족된다.
- 재활성화 API가 없다. 삭제·재등록은 **시크릿이 바뀌어 고객 작업을 유발**하므로 최후 수단이다.

### B-1. 상태 파악

```sql
-- 세션 시간대 확인 (KST 로 나오는 것이 정상이다)
SELECT @@global.time_zone, @@session.time_zone, NOW(), UTC_TIMESTAMP();

-- 1) 해당 클라이언트의 웹훅 전량
SELECT webhook_id, client_id, endpoint_url, events, serial, is_active,
       created_at, updated_at            -- 둘 다 UTC
  FROM open_api_webhooks
 WHERE client_id = '<CLIENT_ID>'
 ORDER BY created_at;

-- 2) 죽은 시점 — 마지막 성공과 첫 실패
SELECT webhook_id,
       MAX(CASE WHEN success = 1 THEN delivered_at END) AS last_success_utc,
       MIN(CASE WHEN success = 0 THEN delivered_at END) AS first_fail_utc,
       SUM(success = 0) AS fail_rows,
       COUNT(*)         AS total_rows
  FROM open_api_webhook_deliveries
 WHERE webhook_id IN (SELECT webhook_id FROM open_api_webhooks WHERE client_id = '<CLIENT_ID>')
   AND delivered_at >= UTC_TIMESTAMP() - INTERVAL 30 DAY
 GROUP BY webhook_id;

-- 3) 비활성화를 유발한 최근 10행 (이 10행이 전부 success=0 이면 조건 충족)
SELECT id, event, serial, http_code, attempt, success, duration_ms, delivered_at
  FROM open_api_webhook_deliveries
 WHERE webhook_id = '<WEBHOOK_ID>'
 ORDER BY delivered_at DESC
 LIMIT 10;

-- 4) 중복 등록 탐지 (같은 URL·이벤트·시리얼 조합이 여럿이면 최신만 남긴다)
SELECT endpoint_url, events, IFNULL(serial,'(ALL)') AS serial,
       COUNT(*) AS cnt, GROUP_CONCAT(webhook_id ORDER BY created_at) AS ids
  FROM open_api_webhooks
 WHERE client_id = '<CLIENT_ID>'
 GROUP BY endpoint_url, events, IFNULL(serial,'(ALL)')
HAVING cnt > 1;
```

### B-2. 원인 판정 — CloudWatch (DB로는 알 수 없다)

로그 그룹 `/ecs/skix-openapi-prd`(dev·stg는 접미사만 다름). **앱 로그 시각은 UTC**이고 `delivered_at`과 그대로 대사된다.

```
# Logs Insights
fields @timestamp, @message
| filter @message like /\[Webhook\]/
| filter @message like /<WEBHOOK_ID 앞 8자리>/
| sort @timestamp asc
| limit 200
```

| 로그 패턴 | 뜻 |
|---|---|
| `[Webhook] <id> 전송 실패 (http=NNN), attempt=N` | 응답은 왔고 2xx가 아니었다. `NNN`이 실제 상태코드 |
| `[Webhook] <id> 전송 오류, attempt=N: <메시지>` | 예외. **4xx·5xx도 여기로 온다.** 메시지 끝에 원인이 있다(`502 Bad Gateway`, `Connection refused`, `timeout` 등) |
| `[Webhook] <id> 월간 딜리버리 쿼타 초과` | 실패가 아니라 쿼타. `payload`에 `{"skipped":"MONTHLY_DELIVERY_QUOTA_EXCEEDED"}`가 남는다 |

**2026-09-09 사고의 판정 결과**: KST 09:00:11에 `500` 1회, 이후 전부 `502 Bad Gateway`. 직전 13시간은 전부 성공이었고 `skix-openapi` `main`은 08-20 이후 커밋이 없다(우리 쪽 배포가 없었다) → **고객사 서버 측 장애로 확정.** KST 09:47까지 16건이 순차 비활성화.

### B-3. 재활성화

```sql
-- ⚠️ 반드시 먼저. 안 하면 ON UPDATE 컬럼(updated_at)에 KST 가 박힌다.
SET time_zone = '+00:00';

START TRANSACTION;

-- (선택) 중복 정리 — B-1의 4번에서 나온 그룹마다 최신 1건만 남긴다
-- DELETE 는 deliveries 를 CASCADE 로 함께 지운다. 이력을 남기려면 삭제 대신
-- is_active=0 으로 두고 고객에게 어느 것을 쓸지 확인받는다.
-- DELETE FROM open_api_webhooks WHERE webhook_id IN ('<OLD_1>','<OLD_2>');

-- 재활성화
UPDATE open_api_webhooks
   SET is_active = 1
 WHERE client_id = '<CLIENT_ID>'
   AND is_active = 0;

-- 기대 건수와 맞는지 확인한 뒤에만 커밋한다
SELECT webhook_id, is_active, updated_at FROM open_api_webhooks WHERE client_id = '<CLIENT_ID>';

COMMIT;
```

**바로 다시 꺼지는 것을 막으려면 고객 서버가 복구된 뒤에 켠다.** 켜자마자 폴링이 돌고, 실패가 10행 쌓이면 다시 비활성화된다.

**시크릿 주의**: 시크릿은 등록 응답에서 1회만 반환되고 목록 조회에 없다. 중복 중 하나를 남긴다면 **고객이 보관 중인 시크릿은 대개 최신 등록본**이다. 어느 것을 남길지 고객에게 확인받는 편이 안전하다.

### B-4. 복구 확인

```sql
-- 2xx 가 다시 들어오는지 (http_code 가 채워지고 success=1)
SELECT webhook_id, event, http_code, success, duration_ms, delivered_at
  FROM open_api_webhook_deliveries
 WHERE webhook_id IN (SELECT webhook_id FROM open_api_webhooks
                       WHERE client_id = '<CLIENT_ID>' AND is_active = 1)
   AND delivered_at >= UTC_TIMESTAMP() - INTERVAL 1 HOUR
 ORDER BY delivered_at DESC
 LIMIT 50;
```

`POST /v1/webhooks/{webhookId}/test`로 단건 즉시 발송도 가능하다(재시도 없음). **비활성 웹훅에도 동작**하므로 켜기 전에 고객 서버 상태를 확인하는 용도로 쓸 수 있다.

### B-5. 그 밖의 침묵 경로

- 폴링 중 상류가 4xx(`USER_NOT_FOUND`·`DEVICE_NOT_FOUND`)를 주면 **로그 없이 스킵**한다. 웹훅이 등록돼 있고 활성인데도 아무것도 안 오면 이 경우를 의심한다.
- `serial`이 NULL인 웹훅은 폴링 대상에서 제외된다.
- 웹훅 발송 쿼타는 **fail-open**이다(Redis 장애 시 막지 않는다). 다른 한도와 방향이 반대인 것은 의도다 — 재시도 큐가 없어 막으면 이벤트가 영구 유실된다.

---

## 부록 C. `/internal` 공개 차단 (C1) — 적용 상태 확인 · 재적용 · 조사

**현재 3환경 모두 적용돼 있다** (2026-09-22 실측: `/internal/v1/clients` → 404). 아래는 규칙이 사라졌을 때 다시 세우는 절차이고, 인프라 변경 뒤 확인용으로도 쓴다.

| 환경 | 계정 | 도메인 |
|---|---|---|
| dev | `010928196421` | `openapi-dev.namuhx.com` |
| stg | `087432099373` | `openapi-stg.namuhx.com` |
| prd | `779846811758` | `openapi.namuhx.com` |

리전은 전부 `ap-northeast-2`.

### C-0. 왜 이 조치가 백오피스를 깨지 않는가

`backend-api-main`은 `skix-openapi`를 **내부 NLB의 8084/80 포트**로 호출한다(환경별 `app.openapi.url`). NLB는 L4라 **경로 기반 규칙의 영향을 받지 않고**, 차단은 공개 **ALB의 443 리스너**에만 건다. 따라서 클라이언트 발급·시크릿 로테이션 등 백오피스 기능은 그대로 동작한다.

> **이 전제가 깨지는 경우**: `backend-api-main`의 `app.openapi.url`이 공개 도메인으로 바뀌면 즉시 백오피스가 깨진다. 그 값을 바꿀 때 이 절을 함께 본다.

### C-1. 적용 (CloudShell, 대상 계정 로그인 · 리전 서울)

```bash
REGION=ap-northeast-2
DOMAIN=openapi-dev.namuhx.com        # stg: openapi-stg / prd: openapi

# 1) 도메인이 가리키는 ALB 특정
dig +short "$DOMAIN"
aws elbv2 describe-load-balancers --region "$REGION" \
  --query 'LoadBalancers[?Scheme==`internet-facing`].{Name:LoadBalancerName,DNS:DNSName,ARN:LoadBalancerArn}' \
  --output table
LB_ARN=<위 결과에서 DNS 가 일치하는 ALB 의 ARN>

# 2) HTTPS(443) 리스너
aws elbv2 describe-listeners --region "$REGION" --load-balancer-arn "$LB_ARN" \
  --query 'Listeners[].{Port:Port,Protocol:Protocol,ARN:ListenerArn}' --output table
LISTENER_ARN=<Protocol=HTTPS, Port=443 인 리스너의 ARN>
#   80 리스너가 443 리다이렉트만 하면 규칙은 443 에만. 80 이 타깃 그룹으로 포워딩하면 80 에도 건다.

# 3) 기존 규칙 확인 (우선순위 충돌 방지 — 숫자가 작을수록 먼저 평가된다)
aws elbv2 describe-rules --region "$REGION" --listener-arn "$LISTENER_ARN" \
  --query 'Rules[].{Priority:Priority,Type:Actions[0].Type,Paths:Conditions[?Field==`path-pattern`].Values|[0]}' \
  --output table

# 4) 차단 규칙 생성
aws elbv2 create-rule --region "$REGION" \
  --listener-arn "$LISTENER_ARN" \
  --priority 1 \
  --conditions '[{"Field":"path-pattern","Values":["/internal","/internal/*"]}]' \
  --actions '[{
    "Type":"fixed-response",
    "FixedResponseConfig":{
      "StatusCode":"404",
      "ContentType":"application/json",
      "MessageBody":"{\"code\":\"NOT_FOUND\",\"message\":\"Not found.\"}"
    }
  }]'

# 5) 롤백용 규칙 ARN 기록
aws elbv2 describe-rules --region "$REGION" --listener-arn "$LISTENER_ARN" \
  --query 'Rules[?Priority==`1`].RuleArn' --output text
```

- **403이 아니라 404인 이유**: 403은 "여기에 뭔가 있는데 막혔다"를 알려준다. 404는 경로의 존재 자체를 드러내지 않는다.
- **`/internal`과 `/internal/*` 둘 다 넣는 이유**: ALB 경로 패턴에서 `/internal/*`는 `/internal` 자체와 매칭되지 않는다.

콘솔로 하려면: EC2 → 로드 밸런싱 → 로드 밸런서 → 대상 ALB → **리스너 및 규칙** → `HTTPS:443`의 규칙 → **규칙 추가** → 조건 **경로**에 `/internal`과 `/internal/*` **둘 다** → 작업 **고정 응답 반환**(404 / `application/json` / 위 본문) → 우선순위 `1`.

### C-2. 검증

```bash
for P in /internal/v1/clients /internal/v1/plans /internal/v1/permissions /internal; do
  printf "%-32s %s\n" "$P" "$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN$P")"
done
# 다른 경로에 영향이 없는지
printf "%-32s %s\n" "/actuator/health" "$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/actuator/health")"
printf "%-32s %s\n" "/v1/auth/token"   "$(curl -s -o /dev/null -w '%{http_code}' -X POST "https://$DOMAIN/v1/auth/token")"
```

| 경로 | 차단 전 | 차단 후 |
|---|---|---|
| `/internal/v1/clients` · `/plans` · `/permissions` | 200 | **404** |
| `/actuator/health` | 200 | 200 (변화 없음) |
| `POST /v1/auth/token` | 400/401 | 동일 |

**마지막으로 백오피스에서 클라이언트 목록 조회 또는 발급을 실제로 해 본다.** 이게 통과해야 NLB 경로가 영향받지 않았음이 실증된다.

롤백: `aws elbv2 delete-rule --region "$REGION" --rule-arn <C-1의 5에서 기록한 ARN>`

### C-3. 노출 기간 조사 (차단과 별개로 남아 있는 숙제 — §8-3의 16번)

`/internal`은 **앱 감사 로그에 남지 않는다**(`AuditLogFilter`가 `/v1/` 외 경로를 전부 건너뛴다). **ALB 액세스 로그가 유일한 단서**다.

```bash
# 액세스 로그가 켜져 있는지
aws elbv2 describe-load-balancer-attributes --region "$REGION" --load-balancer-arn "$LB_ARN" \
  --query "Attributes[?starts_with(Key,'access_logs')]" --output table
```

- `access_logs.s3.enabled = false` → **로그가 없다.** 조사가 불가능하므로 **예방적 로테이션**(전 클라이언트 시크릿 재발급 + 토큰 일괄 폐기)을 검토해야 한다. 그리고 이번 기회에 액세스 로그를 켠다.
- `true` → 아래로 진행한다.

```bash
BUCKET=<access_logs.s3.bucket>
PREFIX=<access_logs.s3.prefix>       # 비어 있을 수 있다
ACCOUNT=<계정 ID>

mkdir -p /tmp/alb && cd /tmp/alb
aws s3 cp "s3://$BUCKET/${PREFIX:+$PREFIX/}AWSLogs/$ACCOUNT/elasticloadbalancing/$REGION/" . \
  --recursive --exclude "*" --include "*.log.gz"

# 호출한 클라이언트 IP 별 집계 ($4 는 ALB 액세스 로그의 client:port 필드)
zgrep -h ' /internal' *.log.gz | awk '{print $4}' | cut -d: -f1 | sort | uniq -c | sort -rn
```

**판정 기준**: 나온 IP가 전부 사내 VPN·사무실 대역이면 외부 유출 정황이 없는 것으로 본다. **하나라도 모르는 출처가 있으면** 해당 시각의 원본 라인을 확인하고, `/internal/v1/clients`(목록·발급) 호출이 포함돼 있으면 **평문 시크릿이 유출된 것으로 간주**하고 전 클라이언트 시크릿 로테이션 + 토큰 일괄 폐기를 진행한다.

로그량이 많으면 Athena가 빠르다. AWS 표준 ALB 액세스 로그 DDL로 테이블을 만들고 `WHERE request_url LIKE '%/internal/%'`로 좁힌다.

### C-4. 이 조치로도 남는 위험

ALB 규칙은 **원인이 아니라 증상**을 막는다. 근본 원인은 `/internal/**`이 `SecurityConfig`에서 `permitAll()`이고 유일한 방어선이 "내부망 전용"이라는 **네트워크 가정 하나**라는 점이다. 아래에서 다시 뚫린다 — **인프라를 바꿀 때 이 목록을 확인한다.**

- ALB 리스너 규칙이 삭제·수정되거나 우선순위가 뒤로 밀림
- 다른 리스너·로드밸런서가 같은 타깃 그룹을 가리킴
- 타깃 그룹에 새 진입 경로가 생김 (예: 신규 CloudFront 오리진)
- **같은 VPC 안에서 태스크 IP로 직접 호출** — ALB를 우회하므로 규칙으로 막을 수 없다

마지막 항목은 ALB로 **원천 차단이 불가능**하다. 이걸 닫는 것이 `APP_INTERNAL_ENFORCE_API_KEY=true`이고, 검증 컴포넌트는 `2c444ba`에 이미 들어가 있으나 **운영계 미반영 + 플래그 없음** 상태다(§8-2의 9번).

적용 형태는 **전용 필터 체인이 아니라 컨트롤러에서 호출하는 검증 컴포넌트**다. `/internal/**` 아래에 키를 보내지 않는 호출자가 20여 개 남아 있어 필터로 걸면 그들이 전부 죽는다. 제출본의 `InternalApiKeyFilter`가 라이브에 못 올라간 이유도 그 판이 `/vital/v2/**`와 API Gateway WebSocket 통합 경로까지 삼키는 형태여서다.

---

## 부록 D. 기능 플래그 ↔ 환경변수 ↔ 엔드포인트 대조표

| 환경변수 | yml 키 | 감추는 대상 | dev | stg | prd(파일) |
|---|---|---|---|---|---|
| `COMMAND_TRACKING_PUBLIC_ENABLED` | `openapi.command-tracking.public-enabled` | `GET /v1/commands/{commandId}` | true | true | **true** |
| `WEATHER_PUBLIC_ENABLED` | `openapi.weather.public-enabled` | `GET /v1/buildings/{id}/weather` | true | true | false |
| `DEVICE_SETTINGS_PUBLIC_ENABLED` | `openapi.device-settings.public-enabled` | `GET`·`PATCH /v1/buildings/{id}/devices/{serial}/settings` | true | true | false |
| `USAGE_PUBLIC_ENABLED` | `openapi.usage.public-enabled` | `GET /v1/buildings/{id}/devices/{serial}/usage/daily` | true | true | false |
| `APP_INTERNAL_ENFORCE_API_KEY` | – | `skix-openapi` `/internal/v1/**`의 공유키 **강제**(false면 log-only) | true | true | false |
| `IDENTITY_NORMALIZE_ENABLED` | – | 개발자 포털 sub 정규화 | true | true | false |

플래그가 `false`면 라우팅뿐 아니라 **Swagger 스펙에서도 함께 빠진다.** 그래서 문서 사이트 스펙 재수집은 반드시 플래그 ON 이후에 한다.

`errorCode`(상태 응답 필드)에는 **플래그가 없다.** 응답에 필드가 하나 늘어나는 변경이라 기존 파트너 계약을 깨지 않기 때문이다. 코드가 배포되면 즉시 나간다.
