# [작업] OASYS 직접 구독 신청 WebView

| 항목 | 내용 |
|---|---|
| **상태** | 백엔드 완료 · 개발계 검증 완료 · **검증계/운영계 미배포** · 앱/프론트 미완 |
| **작업 기간** | 2026-08-31 ~ 2026-09-14 |
| **직접 수정한 저장소** | `backend-api-main`, `skix-security`, `db-schema` |
| **요청서를 전달한 대상** | WebView 프론트(`frontend-report-web`), iOS(`benjaminios`), Android(`benjaminandroid`) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §8-1의 1번(프론트 계약 불일치 정리)과 2번(개발계 배포 상태 확인) |

---

## 0. 세 줄 요약

1. 앱에서 **상담 없이 바로 구독 계약을 접수**하는 경로를 새로 만들었다. 기존 상담 신청은 한 줄도 바뀌지 않았다.
2. 서버 3개 저장소는 개발계·검증계 브랜치에 전부 반영됐고, 개발계에서 실제 PASS 인증까지 포함한 전체 흐름 검증을 통과했다. 운영계 브랜치(`main`)에는 아직 없다.
3. 남은 것은 **검증계·운영계 배포**, **WebView 프론트가 서버 계약과 다르게 만들어지고 있는 문제 정리**, **앱 작업 착수**, 그리고 운영계 릴리스 전 OASYS에 확인할 항목 5가지다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **OASYS** | SK매직 계약·고객 시스템. 우리 백엔드가 HTTP 전문(인터페이스 ID별)으로 호출한다. 단일 엔드포인트 `/api/nmx/interface`에 인터페이스 ID를 바꿔 보낸다. |
| **R045** | OASYS 인터페이스 `IF_NMX_R045`. 구독(멤버십) 신청 접수 전문. `appType=2`가 상담 접수, `appType=1`이 직접 계약 접수. |
| **R011 / R002 / R008** | 각각 계약 조회, SafeKey 발급, 통합고객 연동 전문. |
| **icstNo (통합고객번호)** | OASYS가 고객 한 명에게 부여하는 번호. `backend-api-main`의 `user.icst_no`에 저장. |
| **SafeKey (skm_sfky)** | OASYS 고객 식별 키. R002로 발급받아 `user.skm_sfky`에 저장. |
| **ctrtNo / ctrtClsNo** | 계약번호 / 계약분류번호. |
| **gdsCd** | 멤버십 가격 일련번호(요금제 코드). BASIC과 PLUS가 다르다. |
| **PASS** | NICE 본인인증. `backend-api-main`이 이미 회원가입·PIN 설정에 쓰고 있다. |
| **WebView** | 앱 안에 띄우는 웹 화면. `report-*.namuhx.com`에서 서비스된다. |
| **IdToken** | 앱이 모든 API 호출에 싣는 Cognito ID Token. 헤더 이름이 `IdToken`이다. |
| **화이트리스트 시리얼** | `oasys_test_serial` 테이블에 등록된 기기 시리얼. 이 시리얼로 호출하면 실제 OASYS 대신 스텁 응답을 받는다. |

### 1-2. 문제

기존 구독 신청은 **상담 접수**였다. 앱이 신청하면 서버가 OASYS에 "상담 요청"(R045 `appType=2`)을 넣고, 이후 사람이 전화해서 계약을 진행했다. 즉시 계약이 되지 않는다.

### 1-3. 요구사항

- 상담을 거치지 않고 **앱에서 바로 계약 접수**(R045 `appType=1`)까지 끝낸다.
- 결제 유도 화면이 들어가야 해서 앱 심사 문제를 피하려고 **화면을 WebView로** 둔다. 이 제약이 이후 티켓·쿠키·CSRF 같은 복잡도의 원인이다. 제약이 없었다면 네이티브 화면 + IdToken으로 훨씬 단순했을 것이다.
- 사용자 DB에 통합고객번호·SafeKey가 있든 없든 **항상 PASS 본인인증을 먼저** 하고, 인증된 사람의 정보로만 OASYS를 호출한다.
- 통합고객번호·SafeKey가 둘 다 있으면 재사용하고, 하나라도 없으면 R002·R008로 채운 뒤 `user` 테이블을 갱신한다.
- 브라우저(WebView)가 통합고객번호·SafeKey·계약번호·상품코드를 **임의 지정할 수 없어야** 한다. 요금제는 사용자가 고르지만 브라우저가 보내는 것은 `BASIC`/`PLUS` 이름뿐이다.
- OASYS는 전달된 본인정보가 기기 계약자 정보와 정확히 일치할 때만 접수 성공을 반환한다. 불일치는 실패다.
- 기존 상담 접수 API와 OASYS 전문은 그대로 유지한다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 서버 코드

| 저장소 | dev | stg | main(운영) | 비고 |
|---|---|---|---|---|
| `skix-security` | `0fa25b5` 반영 | `04b28dc` 반영 | **미반영** | 마지막 변경은 9/14 ctrtClsNo 추가 |
| `backend-api-main` | `7e9b5109` 반영 | `bbba78ba` 반영 | **미반영** | 마지막 변경은 9/8 PASS 실패 사유 |
| `db-schema` | – | – | `692eecf` 반영 | 신규 테이블 1개 |

### 2-2. 배포·검증

| 항목 | 상태 |
|---|---|
| DDL(`oasys_membership_apply_attempt`) | **dev·stg·prd 3환경 적용 완료** (2026-09-01) |
| 개발계 애플리케이션 배포 | 완료. **2026-09-08 전체 흐름 검증 통과** (실제 PASS 인증 포함, BASIC 요금제 접수 SUCCEEDED). 검증 스크립트는 부록 B |
| 개발계에 9/14 ctrtClsNo 변경이 배포됐는지 | **미확인.** 로컬에서는 ECS를 볼 수 없어 VDI에서 태스크 정의 이미지 태그를 확인해야 한다 |
| 검증계 배포 | **미배포** (브랜치만 반영) |
| 운영계 배포 | **미배포**, `main` 미승격. §8-2 릴리스 게이트 통과 전 금지 |

### 2-3. 프론트·앱 (별도 담당자)

| 대상 | 상태 | 확인한 것 |
|---|---|---|
| WebView 프론트 (`frontend-report-web`) | **작업 중이나 서버 계약과 불일치** | 프론트 담당이 `origin/feature/my-subscription` 브랜치에서 8/31~9/9 화면을 만들고 있는데, **WebView 안에서 Cognito IdToken을 직접 꺼내 쓰는 방식**이다. 서버 계약(티켓 → 쿠키 세션 → CSRF → `/pass/start` → `/apply`)을 호출하는 코드가 없다. §8-1 참조 |
| iOS / Android | **미착수** | 신규 API(`web-session-tickets`)를 호출하는 코드가 어느 브랜치에도 없다. 요청 내용은 §6 |
| Nginx | 미적용 | 신규 라우팅은 없고 기존 `report-*/api/** → backend-api-main` 규칙을 그대로 쓴다. 확인 항목은 §7-3 |

---

## 3. 무엇을 만들었나

### 3-1. 전체 흐름

```
[앱]  ① POST /api/app/v1/devices/{serial}/oasys/membership/web-session-tickets   (IdToken 헤더)
         ← { entryUrl, ticket, expiresInSeconds: 120 }
      ② WebView를 entryUrl로 "POST" 열기 (ticket은 form body)

[WebView: report-*.namuhx.com]
      ③ POST /api/web/v1/oasys/membership/entry     ← 303 + Set-Cookie(HttpOnly, Path 한정)
      ④ GET  .../session                             상태 + CSRF 토큰
      ⑤ POST .../pass/start                          NICE PASS URL 받아 이동
             (NICE에서 본인인증) → 콜백 GET /api/auth/pass/getAuthResult?authId=...
             서버: authId → WebView 세션 결합 → 전화번호 대조 → 302 화면 복귀
      ⑦ GET  .../session                             state == PASS_VERIFIED 확인
      ⑧ POST .../apply  { "plan": "BASIC" | "PLUS" }

[backend-api-main 내부]
      ⑨ user.icst_no / skm_sfky 확인, 없는 것만 R008 / R002로 채움
      ⑩ 원장 oasys_membership_apply_attempt 에 SUBMITTING INSERT
      ⑪ POST skix-security /internal/v1/devices/{serial}/oasys/membership/apply

[skix-security]
      ⑫ R011로 계약번호 확보 → ⑬ appType=1 + gdsCd + ctrtClsNo 조립 → R045 호출
      → 원장 SUCCEEDED / FAILED / UNKNOWN 갱신 → 앱은 WebView 닫힘 후 구독 상태 재조회
```

### 3-2. 저장소별 역할 분담

경계 원칙: **PASS와 사용자 신원은 `backend-api-main`, OASYS 계약과 R045는 `skix-security`.** `skix-security`에는 `user` 테이블과 PASS가 없고, `backend-api-main`에는 R011/R045가 없어서 이렇게 나눴다.

| 책임 | 저장소 | 주요 파일 (`src/main/java/...` 하위) |
|---|---|---|
| 앱용 티켓 발급 | backend-api-main | `api/oasys/membership/controller/MembershipLaunchController` |
| WebView 세션·CSRF·PASS·신청 4종 API | backend-api-main | `.../controller/MembershipWebController`, `.../service/MembershipWebService` |
| Redis 티켓·세션 저장 | backend-api-main | `.../service/MembershipWebSessionStore` |
| 통합고객번호·SafeKey 수렴 (R002/R008) | backend-api-main | `.../service/MembershipIdentityService`, `api/user/mapper/UserMapper` (조건부 UPDATE 2종) |
| skix-security 호출·결과 3분류 | backend-api-main | `.../service/MembershipApplyClient` |
| PASS 목적 `oasys_membership` 추가 | backend-api-main | `api/auth/pass/service/NiceAuthService`, `.../controller/NiceAuthController` |
| 내부 API 수신·R011·R045 | skix-security | `api/controller/InternalDeviceController`, `domain/service/DirectMembershipService` |
| 요금제 → gdsCd 변환 (코드값이 있는 유일한 곳) | skix-security | `domain/model/DirectMembershipPlan` |
| appType·ctrtClsNo 고정값 | skix-security | `domain/service/DirectMembershipConstants` |
| 직접가입 전용 전문 타입 | skix-security | `infra/http/oasys/dto/OasysDirectMembershipRequest` |
| 테스트 시리얼 스텁 | skix-security | `infra/http/oasys/OasysWhitelistClient` |

### 3-3. 데이터

**MySQL** — `oasys_membership_apply_attempt` (backend-api-main DB). 신청 시도의 **감사 원장**이며 중복 방지 장치가 아니다. UNIQUE는 `uk_omaa_correlation_id` 하나, 인덱스는 `idx_omaa_login_serial_created` 하나. CI·SafeKey·통합고객번호·계약번호·요금제 컬럼은 일부러 두지 않았다.

**Redis** (backend-api-main)

| 키 | TTL | 용도 |
|---|---|---|
| `oasys:membership:ticket:{sha256}` | 2분 | 1회용 진입 티켓. 평문이 아니라 해시로 저장 |
| `oasys:membership:session:{id}` | 15분 | WebView 세션 (CI는 여기에만 존재하고 세션과 함께 사라짐) |
| `oasys:membership:pass:state:{authId}` | 5분 | PASS 1회용 state |
| `oasys:membership:identity:lock:{loginId}` | 60초 | R002 중복 발급 방지 락 |

**설정** — WebView origin은 SSM이 아니라 **프로필별 yml 리터럴**이다(`app.oasys.membership.webview.origin`). 신규 환경변수·SSM 파라미터는 없다.

| 환경 | origin |
|---|---|
| dev | `https://report-dev.namuhx.com` |
| stg | `https://report-stg.namuhx.com` |
| prd | `https://report.namuhx.com` |

### 3-4. OASYS 확정 전문값 (2026-09-07 회신, 09-14 스펙 시트)

| 항목 | 값 | 위치 |
|---|---|---|
| SafeKey 필드명 | `safeKey` (R002 응답의 `skmSfky`와 이름이 다름) | `OasysDirectMembershipRequest` |
| gdsCd BASIC / PLUS | `10070726` / `10070727` | `DirectMembershipPlan` |
| ctrtClsNo | `1100098960` (고정) | `DirectMembershipConstants` |
| icstNo | **계약자** 통합고객번호. 직접 구독은 계약자 본인만 신청 가능 | – |
| 환경별 동일 여부 | gdsCd·ctrtClsNo 3환경 동일 확인 완료 (09-14) | `DirectMembershipPlan` javadoc에는 아직 "미확인"이라 적혀 있다. 주석만 낡은 것 |

두 가지 주의점.
- 조회 계열(R011)의 `anxtSvcDvsCd` `01`/`02`와 신청의 `gdsCd` `10070726`/`10070727`은 **다른 코드 체계**다. 조회에서 익숙한 `"01"`을 신청 전문에 넣는 혼동을 테스트가 막고 있다.
- OASYS 명세는 상위 요금제를 `Special`이라 부르지만 우리는 제품 표기인 `PLUS`를 쓴다. 조회 응답과 신청 요청에서 같은 요금제 이름이 갈리면 앱이 두 벌을 알아야 하기 때문이다.

---

## 4. API 계약

### 4-1. 앱 → 서버: 티켓 발급 (신규)

```http
POST https://app-api-{env}.namuhx.com/api/app/v1/devices/{serial}/oasys/membership/web-session-tickets
IdToken: <Cognito ID Token>
```

성공 `200`:

```json
{ "result": 200, "message": "OK", "success": true,
  "data": { "entryUrl": "https://report-dev.namuhx.com/api/web/v1/oasys/membership/entry",
            "ticket": "9xK2mQ...", "expiresInSeconds": 120 } }
```

실패 `409` `NOT_DEVICE_MEMBER`: 해당 기기의 홈 멤버가 아님.

- 티켓은 2분짜리 1회용이다.
- `entryUrl` 호스트는 앱 API 호스트와 다르다(`report-*`). 오타가 아니라 WebView 문서와 그 API가 같은 origin이어야 해서다. 앱에 하드코딩하지 않는다.

### 4-2. WebView → 서버: 4종 (`/api/web/v1/oasys/membership/**`)

공통 규약:
- 화면(`{origin}/oasys/membership`)과 API가 **같은 origin**이라 CORS 설정이 없다. 호출은 상대 경로.
- 인증은 **HttpOnly 쿠키** `oasys_membership_session`. JS에서 읽을 수 없고 읽을 필요도 없다. `Path=/api/web/v1/oasys/membership`으로 좁혀져 있다.
- 상태를 바꾸는 요청(`pass/start`, `apply`)은 `X-CSRF-Token` 헤더 필수. 값은 `GET /session`의 `csrfToken`(세션당 고정). 누락하면 `403 CSRF_INVALID`.
- 응답 형태는 기존 규격과 같다. `message`는 항상 `"OK"`이고 사용자 문구는 `error.message`에 있다. 성공 판정은 `success`, 실패 분기는 `error.code`.

**`POST /entry`** (form `ticket=...`) → `303 Location: {origin}/oasys/membership` + `Set-Cookie`. 같은 티켓 재사용 시 `403 ENTRY_TICKET_INVALID`.

**`GET /session`**

```json
{ "data": { "state": "PASS_PENDING", "serial": "ABT123456789", "csrfToken": "7pQ...",
            "expiresInSeconds": 842, "passFailureCode": null } }
```

| state | 의미 |
|---|---|
| `PASS_PENDING` | 본인인증 전 |
| `PASS_VERIFIED` | 본인인증 완료 |
| `SUBMITTING` | 신청 처리 중 |
| `SUCCEEDED` / `FAILED` | 접수 완료 / 접수 실패 |
| `UNKNOWN` | 접수 결과 불명 |

`401 WEB_SESSION_EXPIRED`는 화면에서 복구 불가. 앱에서 다시 시작해야 한다.

**`POST /pass/start`** → `{ "data": { "actionUrl": "https://nice.checkplus.co.kr/..." } }`. 화면은 같은 창을 이 URL로 이동시킨다. PASS가 끝나면 서버가 `{origin}/oasys/membership?status=pass_verified` 또는 `?status=pass_failed`로 돌려보낸다. **`status` 쿼리는 힌트일 뿐이고 판정은 반드시 `GET /session`으로** 한다.

PASS 실패 시 `state`는 `PASS_PENDING` 그대로 남고(재시도 가능해야 하므로) **`passFailureCode`로만 실패를 알 수 있다.**

| passFailureCode | 의미 |
|---|---|
| `IDENTITY_MISMATCH` | 로그인 계정의 전화번호와 다른 명의로 인증 |
| `AUTH_SESSION_EXPIRED` | 인증 요청이 만료됐거나 이미 처리됨 |
| `PASS_PROVIDER_ERROR` | NICE 결과 조회 실패 |

**`POST /apply`** `{ "plan": "BASIC" | "PLUS" }` → `200 { "data": { "state": "SUCCEEDED" } }`

| HTTP | error.code | 의미 |
|---|---|---|
| 400 | `PLAN_REQUIRED` | 요금제가 없거나 알 수 없는 값. 기본값을 채워주지 않는다 |
| 409 | `PASS_REQUIRED` | 본인인증 전 |
| 409 | `MEMBERSHIP_ALREADY_ACTIVE` | 이미 구독 중 |
| 409 | `MEMBERSHIP_APPLY_REJECTED` | OASYS 업무 거절 |
| 409 | `IDENTITY_RESOLVE_BUSY` | 같은 사용자의 다른 처리 진행 중(R002 락). 잠시 후 재시도 |
| 409 | `OASYS_IDENTITY_LINK_FAILED` | R002/R008 연동 실패 |
| 409 | `USER_NOT_FOUND` | 사용자 없음 |
| **202** | `MEMBERSHIP_APPLY_UNCERTAIN` | **접수 여부 불명.** 실패가 아니다. 화면은 "결과 확인 중" 안내, 자동 재신청 금지 |

**닫기 신호**: 화면이 URL에 `command/close`를 포함시키면 앱이 WebView를 닫는다. 두 앱이 이미 쓰는 규약이다.

### 4-3. backend-api-main → skix-security: 내부 API

```http
POST /internal/v1/devices/{serial}/oasys/membership/apply
{ "loginId": "...", "plan": "BASIC", "correlationId": "..." }
```

앱 계층 인증 없음. API Gateway가 `/internal/**`을 라우팅하지 않는다는 네트워크 격리 하나에 기댄다(§5의 8번).

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다. 나머지 세부 결정은 코드 주석과 테스트가 지킨다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **Cognito 토큰을 WebView에 넘기지 않는다.** 앱은 티켓만 받고, 브라우저는 티켓을 쿠키 세션으로 바꾼다. 티켓도 URL이 아니라 POST body로 보낸다 | 두 앱 모두 WebView URL을 로그로 남긴다. 토큰이 WebView의 JS·URL·로그에 그대로 노출된다. 지금 프론트 브랜치가 이걸 어기고 있다(§8-1) |
| 2 | **WebView origin을 SSM으로 옮기지 않는다** (yml 리터럴 유지) | `backend-api-main`은 `spring.config.import`로 SSM을 읽는데 그 값은 **yml보다 우선순위가 낮다.** 키가 양쪽에 있으면 SSM 값이 조용히 무시된다. 같은 함정을 겪은 기록이 `app.internal.enforce-api-key` 주석에 있다. 또 이 값은 각 프로필의 `nice.pass.redirect-url` 호스트와 같아야 하며 `MembershipWebviewOriginConfigTest`가 이를 고정한다 |
| 3 | **PASS 콜백에서 `normalizePurpose`를 거치지 않는다.** `NiceAuthService.getAuthResult`는 저장된 purpose 원문으로 분기한다 | 정규화는 모르는 값을 `user`로 낮춘다. 직접가입 세션이 낮아지면 공개 `pass:{uuid}` 경로로 흘러 **CI가 공개 조회 API로 새어나간다.** 역으로 같은 성질이 위조 방지다. 공개 진입점에 `oasys_membership`을 보내도 `user`로 떨어져 세션 결합 없는 직접가입 PASS를 만들 수 없다. "unknown purpose를 거절하도록 개선"하면 이 방어가 깨진다. `NiceAuthPurposeTest` 15건이 고정 |
| 4 | **PASS 복귀 목적지는 WebView 세션에 보관한 origin**으로 분기하고, 이 분기를 파라미터 분기보다 앞에 둔다 | 운영계는 콜백 호스트(`client.namuhx.com`)와 화면 호스트(`report.namuhx.com`)가 다르다. 전역 목적지로 가면 세션 쿠키를 읽을 수 없는 페이지에 사용자가 남는다. **개발계는 두 호스트가 같아 재현되지 않으므로** dev만 보고 통과시키면 운영계에서 처음 터진다 |
| 5 | `UserMapper.updateOasysInfo`를 재사용하지 않고 **컬럼별 조건부 UPDATE**(`updateSafeKeyIfMissing`, `updateIcstNoIfMissing`)를 쓴다 | 기존 매퍼는 `icst_no`·`skm_sfky`를 무조건 함께 SET한다. 한쪽만 결손인 상태에서 재사용하면 살아있는 값을 NULL로 덮는다. WHERE의 `IS NULL`이 compare-and-set 역할이라 동시 요청도 안전하다. `UserMapperOasysUpdateXmlTest`가 SQL 수준에서 고정 |
| 6 | **원장에 "활성 시도 UNIQUE" 제약을 두지 않는다.** 신청 중복 락도 없다 | 제출 중 프로세스가 죽으면(ECS 재배포·OOM) `SUBMITTING` 행이 남는다. UNIQUE가 있으면 시간 조건을 걸 수 없어 그 사용자가 그 기기에 **영구 재신청 불가**가 된다. 형제 테이블 `remote_mapping_session`은 관제 UI에서 강제 종결할 수 있어 UNIQUE를 쓰지만, 이 표는 소비자가 주체라 종결을 눌러줄 사람이 없다. 중복 신청 판정은 OASYS 몫이다(§8-2 게이트) |
| 7 | **408·429는 `FAILED`가 아니라 `UNKNOWN`.** UNKNOWN은 자동 재호출하지 않고 다음 신청도 막지 않는다 | 요청이 처리되지 않았다는 보장이 없다. FAILED로 확정하면 사용자가 재신청해 계약이 두 번 접수될 수 있다. 계약 체결은 되돌릴 수 없어 "실패"와 "모름"을 뭉개면 안 된다 |
| 8 | **OASYS 호출의 HTTP 자동 재시도를 양쪽 저장소에서 명시적으로 껐다** (`skix-security` `OasysApiConfig`의 `disableAutomaticRetries()`, `backend-api-main` `nonRetryingRestTemplate` 빈) | 실측 결과 `RestTemplate`은 429·503 POST를 2회 보낸다(본문을 byte[]로 실어 재생 가능). `RestClient`는 1회였지만 설정이 아니라 우연이다(GET은 2회). 이중 접수 위험. 주의: `skix-security` 회귀 테스트는 GET으로 검증한다. POST로는 설정을 제거해도 통과해서 회귀를 못 잡는다 |
| 9 | **`/internal/**`에 앱 계층 인증을 두지 않는다** (네트워크 격리 단일 계층) | API Gateway가 `/internal/**`을 라우팅하지 않음을 dev·prd 공개 호스트에서 실측(403). **`/internal/v1/devices/**`에 prefix 단위 인증 필터를 걸면 안 된다.** `skix-streaming`이 `streaming/uploads`·`s1/dispatch`를 인증 헤더 없이 호출하고 있어 **S1 긴급 호출이 401로 죽는다** |
| 10 | 요금제 코드는 `DirectMembershipPlan` 한 곳에만. 알 수 없는 요금제는 `400 PLAN_REQUIRED`로 거절하고 기본값을 채우지 않는다 | 과금이 갈리는 값이다. 코드를 두 곳이 알면 가격 개편 때 고칠 곳이 둘이 되고, 기본값을 채우면 침묵 실패가 된다 |

---

## 6. 앱·프론트에 요청한 내용

앱과 프론트 코드는 직접 수정하지 않았다. 아래가 각 담당자에게 전달한 요청의 핵심이다. 인수자는 이 내용이 실제 구현과 맞는지 확인하는 역할이다.

### 6-1. 앱 (iOS·Android)

앱이 할 일은 두 가지뿐이다. PASS·신청·판정은 전부 WebView 안과 서버에서 끝난다.

1. **티켓 발급 후 WebView를 POST로 열기.** `ticket`을 URL 쿼리가 아니라 form body로 보낸다.
   - Android: `webView.postUrl(entryUrl, "ticket=<urlencoded>".toByteArray())`
   - iOS: `URLRequest`에 `httpMethod = "POST"`, `Content-Type: application/x-www-form-urlencoded`, body `ticket=...`. 기존 `PassAuthWebView`가 이미 같은 분기를 갖고 있다.
   - 서버가 303으로 화면에 보내므로 WebView가 리다이렉트를 따라가고 쿠키를 저장하도록 기본 동작을 유지한다.
2. **닫기 신호 후 구독 상태 재조회.** URL에 `command/close`가 오면 화면을 닫고 `GET /security/v1/devices/{serial}/subscription`을 다시 조회한다. **닫힘은 신청 성공이 아니다.** 취소·불확실 상태일 수 있다.

**기존 보안 WebView 설정을 그대로 재사용하지 말 것.** 기존 화면은 `MIXED_CONTENT_ALWAYS_ALLOW`로 되어 있다. 이 화면은 `MIXED_CONTENT_NEVER_ALLOW`, `allowFileAccess=false`, `allowContentAccess=false`, 우리 도메인과 PASS 도메인 외 navigation 차단, 전체 URL·POST body 로그 금지, 종료 시 쿠키 제거가 필요하다. iOS는 운영 빌드에서 `isInspectable` 비활성.

### 6-2. WebView 프론트

- 호출은 상대 경로. 쿠키는 HttpOnly라 다루지 않는다. IdToken을 쓰지 않는다.
- `pass/start`·`apply`에 `X-CSRF-Token`.
- PASS 복귀 후 `?status=` 쿼리가 아니라 `GET /session`의 `state`·`passFailureCode`로 판정.
- `202 MEMBERSHIP_APPLY_UNCERTAIN`은 실패로 표시하지 않고 자동 재신청하지 않는다. 재시도 버튼은 사용자가 명시적으로 누를 때만.
- 화면·URL·로그에 통합고객번호·SafeKey·CI·전화번호·생년월일을 남기지 않는다.
- 미결 질문 1건: 요금제 이름·가격·혜택 문구를 화면 고정으로 둘지 서버 API로 내릴지. 현재는 API가 없다.

---

## 7. 배포 방법

### 7-1. 순서 (역순 금지)

```
1. db-schema DDL        완료 (3환경)
2. skix-security        ← 반드시 backend-api-main 보다 먼저
3. backend-api-main     ← 신규 설정 조치 없음
4. Nginx                ← 프론트 배포 전까지는 없어도 서버 정상
5. WebView 프론트 / 앱  ← 별도 담당자
```

**2·3 순서를 지켜야 하는 이유**: 내부 요청 본문에 `plan` 필드가 있다.

| 순서 | 결과 |
|---|---|
| skix-security 먼저 (정상) | 구 backend가 `plan`을 안 보내 `@NotNull` 위반 400. **시끄럽게** 실패하고 잘못된 값이 OASYS로 나가지 않는다 |
| backend-api-main 먼저 (금지) | 구 skix-security가 `fail-on-unknown-properties: false` 때문에 `plan`을 **조용히 버리고 옛 임시 상품코드 `SKMG_DIRECT_01`로 R045를 보낸다** |

두 배포 사이 창에서는 직접가입이 400으로 실패하지만 프론트가 없어 실사용자 영향은 없다.

### 7-2. 배포 후 검증 (개발계 예시. 검증계·운영계는 호스트만 바꾼다)

```bash
# 1) 내부 API가 외부로 열려 있지 않은지. 기대 403. 200/401/404/405면 릴리스 중단
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://app-api-dev.namuhx.com/internal/v1/devices/ZZZZTEST0000/oasys/membership/apply"

# 2) 티켓 발급. entryUrl 호스트가 report-dev 인지, /api 가 포함되는지
curl -s -X POST -H "IdToken: <Cognito ID Token>" \
  "https://app-api-dev.namuhx.com/api/app/v1/devices/<serial>/oasys/membership/web-session-tickets"

# 3) 티켓 → 쿠키. 기대 303. Set-Cookie 에 Domain= 이 없어야 함(host-only). 같은 티켓 재사용 시 403
curl -s -i -X POST -d "ticket=<ticket>" \
  "https://report-dev.namuhx.com/api/web/v1/oasys/membership/entry"

# 4) 세션. 기대 state: PASS_PENDING, csrfToken 존재. 쿠키 없이 호출하면 401
curl -s -b "oasys_membership_session=<값>" \
  "https://report-dev.namuhx.com/api/web/v1/oasys/membership/session"

# 5) CSRF. 토큰 없이 POST 하면 403 CSRF_INVALID
curl -s -X POST -b "oasys_membership_session=<값>" \
  "https://report-dev.namuhx.com/api/web/v1/oasys/membership/pass/start"
```

- **기존 PASS 회귀(필수)**: 앱에서 회원가입 본인인증, 시큐리티 PIN 설정 본인인증이 기존처럼 동작하는지. PASS 코드를 건드렸으므로 이 배포의 핵심 회귀 지점이다.
- 기존 상담 신청 스모크: `POST /security/v1/devices/{serial}/oasys/membership/apply`
- 전체 흐름은 부록 B 스크립트 한 번으로 확인한다(PASS 단계는 사람이 폰으로 인증).

IdToken 확보 방법 3가지는 부록 B 스크립트 안의 안내문에 있다.

### 7-3. Nginx 확인 항목 (프론트 배포 시점)

`report-*/api/** → backend-api-main` 기존 규칙을 그대로 쓴다.

| 항목 | 이유 |
|---|---|
| `/api/web/v1/oasys/membership/**` GET·POST 전달 | 신규 경로 |
| **form POST body 보존** | `/entry`가 `application/x-www-form-urlencoded`를 받는다 |
| **`Set-Cookie` 응답 헤더 보존** | 세션 쿠키가 여기서 발급된다 |
| **`X-Forwarded-Proto=https` 전달** | `Secure` 쿠키 판정 |
| API 응답 캐시 금지 | 세션 상태가 캐시되면 안 된다 |
| `/oasys/membership`은 SPA 문서로 라우팅 | 화면 경로 |
| WebView API를 `/api/app/**` 전용 필터에 넣지 않기 | 인증 방식이 다르다 |

### 7-4. 롤백

- 애플리케이션: 이전 이미지로 되돌린다. 신규 API만 추가된 배포라 기존 기능 영향 없음.
- DB: **테이블을 삭제하지 않는다.** 구버전이 참조하지 않고, 접수 시도 기록은 조사 자료가 된다.
- Nginx: 되돌리면 WebView 화면만 접근 불가. 앱·서버 기존 기능 영향 없음.

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **프론트 계약 불일치 정리.** `frontend-report-web` `feature/my-subscription`이 WebView 안에서 IdToken을 직접 쓴다. 서버 계약(§4-2)과 §5의 1번 전제가 어긋난다. 이대로 가면 티켓·쿠키·CSRF·`/pass/start`·`/apply` 전부 호출되지 않는다. 프론트 담당과 §4-2·§6-2 기준으로 맞출 것 | 백엔드 + 프론트 | 프론트가 만든 "구독 상태 조회·약관 동의" 부분은 별개 기능이라 유지 가능 |
| 2 | 개발계 ECS에 9/14 `skix-security` 변경(ctrtClsNo)이 배포됐는지 확인. 안 됐으면 배포 | 백엔드 | VDI에서 확인 |
| 3 | 검증계 배포(§7-1 순서) 후 **호스트 분리 시나리오** 검증(§5의 4번). PASS 복귀가 `report-stg` 화면으로 돌아오고 세션 쿠키를 읽는지 | 백엔드 | 개발계에서는 재현 불가 |
| 4 | 앱 작업 착수 확인. 요청은 전달됐으나 착수는 미확인 | iOS·Android 담당 | 범위는 §6-1 |

### 8-2. 운영계 릴리스 게이트 (전부 확정 전 prd 배포 금지)

| # | 확인할 것 | 상대 |
|---|---|---|
| 5 | 화이트리스트 **미등록** 시리얼로 **실호출 1건**. ctrtClsNo 포함 전문이 OASYS에 받아들여지는지. 스텁은 전문을 만들지 않아 필드명·상품코드 오류를 못 잡는다 | OASYS |
| 6 | **중복 신청**: 같은 사용자·계약·상품으로 R045를 여러 번 보내도 중복 계약·중복 과금이 안 생기는지, timeout 후 재호출이 안전한지, 이미 가입된 경우 결과 코드. 로컬 중복 차단을 두지 않은 근거(§5의 6번)가 이 전제다 | OASYS |
| 7 | R045 성공 응답 판정 기준과 업무 오류 코드 목록 | OASYS |
| 8 | R008에 빈 `emlAddr`·`auth_provider` 기반 가입유형을 보낼 때 기존 고객정보를 덮어쓰는지 | OASYS |
| 9 | R045 접수 외 별도 결제 절차가 필요한지 | 기획·OASYS |
| 10 | `main` 승격 및 운영 배포. 운영 순서도 skix-security 먼저 | 백엔드 |

### 8-3. 후속 (이 기능을 막지는 않음)

| # | 항목 |
|---|---|
| 11 | `DirectMembershipPlan` javadoc의 "환경별 동일 여부 미확인" 문구 정정 (확인 완료됨) |
| 12 | `report-*.namuhx.com`이 `/api/**`를 API Gateway 없이 backend-api-main으로 통째 프록시하는 **기존 구성**(3환경). WebView API가 이 호스트를 쓴다. 실측(9/7)으로 `GET report-{dev,stg,prd}/api/internal/security/check-member`가 무인증 200이었다. `/app/**`은 서명 검증이 있어 사칭은 불가하지만 `InternalSecurityController`의 게이팅 없는 엔드포인트(보안 스케줄 전체 삭제, 임의 푸시, 계약 조회)가 serial만 알면 열린다. 권장 조치는 nginx 한 줄 `location ^~ /api/internal/ { return 404; }`. report 프론트가 내부 API를 쓸 이유가 없어 기존 화면이 깨질 위험이 없다 |
| 13 | 재가입 시 `icst_no`를 NULL로 덮는 기존 결함(`UserService`, 1714행 부근). 이번 범위 밖이나 R008이 기존 고객을 갱신하게 되는 경로의 원인 |
| 14 | 원장에 요금제 컬럼 없음. 어느 요금제로 접수했는지는 OASYS 원본과 `skix-security` 아웃바운드 로그에 있다. 운영 문의 대응에 부족하면 별도 티켓 |
| 15 | 기존 네이티브 PASS 경로는 CI를 앱까지 내려보낸다(공개 `/app/user/pass/info`, Android bridge가 CI·이름·전화번호를 logcat에 기록). 이번 기능은 그 경로를 쓰지 않지만, 기존 경로의 보완은 별도 보안 작업 |

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| "PASS 했는데 진행이 안 돼요" | `GET .../session`의 **`passFailureCode`**를 본다. `state`는 실패해도 `PASS_PENDING` 그대로라 state로는 구분이 안 된다. 개발계 검증 중 반복 실패의 원인도 남의 IdToken으로 세션을 만들고 본인 명의로 PASS한 것이었다(`IDENTITY_MISMATCH`, 서버는 정상 동작) |
| 신청 결과 확인 | `SELECT correlation_id, login_id, serial, status, oasys_result_code, create_date, resolved_at FROM oasys_membership_apply_attempt WHERE serial = '<serial>' ORDER BY id DESC LIMIT 5;` |
| `SUBMITTING`이 오래 남아 있음 | 제출 중 프로세스 종료의 흔적. 다음 신청을 막지 않으므로 감사용으로 보존 |
| `UNKNOWN` | 자동 복구 없음. 사용자에게 구독 상태 조회로 확인 안내 |
| OASYS 없이 흐름 테스트 | `oasys_test_serial`에 등록된 시리얼은 `OasysWhitelistClient`(`@Primary`)가 스텁 응답. 실호출 경로는 완전히 구현되어 있고 화이트리스트 시리얼만 스텁으로 갈라진다 |
| 서버 로그 키워드 | PASS 결합 `[MEMBERSHIP_PASS]`, NICE 원본 코드 `[NICE]` |

---

## 부록 A. 핵심 커밋 (시간순)

| 날짜 | 저장소 | 커밋 | 내용 |
|---|---|---|---|
| 09-01 | db-schema | `98840dc` | 원장 테이블 `oasys_membership_apply_attempt` 추가 |
| 09-01 | backend-api-main | `a0b54cdd` | 티켓·세션·PASS 결합·신원 수렴·R045 연결 (feature 머지) |
| 09-01 | skix-security | `32a9913` | 직접 구독 내부 API (feature 머지) |
| 09-07 | skix-security | `5242e74` | 전문 확정 반영: `safeKey`, 요금제 BASIC/PLUS |
| 09-07 | backend-api-main | `546a00e3` | 요금제 선택 수신 |
| 09-08 | skix-security / backend-api-main | `4c7bdd2` / `a678cb58` | 자동 재시도 OFF, 408·429 UNKNOWN, 오류 본문 마스킹 |
| 09-08 | backend-api-main | `7e9b5109` | PASS 실패 사유 `passFailureCode` |
| 09-14 | skix-security | `0fa25b5` (dev) / `04b28dc` (stg) | `ctrtClsNo` 고정값 추가 |

테스트 규모(9/7 기준): backend-api-main 467건, skix-security 450건, 실패 0. 변이 테스트로 SafeKey 필드명 되돌리기·요금제 기본값 채우기·조건부 UPDATE에 반대 컬럼 끼워넣기가 각각 테스트를 깨뜨리는지 확인했다.

---

## 부록 B. 개발계 전체 흐름 검증 스크립트

`E2E-verify-dev.sh`로 저장해 `./E2E-verify-dev.sh <serial>`로 실행한다. `PLAN=PLUS`로 요금제를 바꿀 수 있다. 실제 PASS 인증을 포함하므로 5번 단계에서 사람이 URL을 열어 인증해야 한다. 콜백이 쿠키가 아니라 authId로 세션을 찾기 때문에 폰으로 인증해도 스크립트 쪽 세션이 갱신된다.

```bash
#!/usr/bin/env bash
# OASYS 직접 구독 신청 WebView — dev 전체 흐름 검증.
#
# 실제 PASS 본인인증을 포함한다. PASS 단계에서 사람이 브라우저로 인증을 끝내야 하며,
# 그 브라우저는 이 스크립트와 아무 관계가 없어도 된다 — 콜백이 쿠키가 아니라 authId 로
# 세션을 찾기 때문이다. 폰에서 인증해도 스크립트 쪽 세션이 갱신된다.
#
# 사용법:
#   ./E2E-verify-dev.sh <serial>
#   PLAN=PLUS ./E2E-verify-dev.sh <serial>     # 요금제 지정 (기본 BASIC)

set -uo pipefail

# ── 채워야 할 값 ──────────────────────────────────────────────
APP_API="https://app-api-dev.namuhx.com"     # 네이티브 앱 API 호스트
WEB_ORIGIN="https://report-dev.namuhx.com" # WebView origin (서버 설정과 같아야 함)
ID_TOKEN="${ID_TOKEN:-}"                     # Cognito IdToken — export 로 주입
SERIAL="${SERIAL:-MWPA1M10KRNGA0425Z00533}"   # 대상 기기 시리얼
PLAN="${PLAN:-BASIC}"                        # 신청 요금제 — BASIC | PLUS
# ─────────────────────────────────────────────────────────────

WEB_API="${WEB_ORIGIN}/api/web/v1/oasys/membership"
JAR="$(mktemp /tmp/oasys-e2e-cookies.XXXXXX)"
trap 'rm -f "$JAR"' EXIT

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'
FAILED=0

ok()   { printf "  ${GRN}PASS${RST}  %s\n" "$1"; }
bad()  { printf "  ${RED}FAIL${RST}  %s\n" "$1"; FAILED=$((FAILED+1)); }
note() { printf "  ${DIM}%s${RST}\n" "$1"; }
step() { printf "\n${YLW}=== %s ===${RST}\n" "$1"; }

expect() {  # expect <설명> <기대> <실제>
  if [ "$2" = "$3" ]; then ok "$1 ($3)"; else bad "$1 — 기대 $2, 실제 $3"; fi
}

jsonf() { python3 -c 'import sys,json;
d=json.load(sys.stdin)
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
    if d is None: break
print(d if d is not None else "")' "$1" 2>/dev/null; }

# ─────────────────────────────────────────────────────────────
if [ -z "$SERIAL" ]; then
  echo "사용법: $0 <serial>" >&2; exit 2
fi

if [ -z "$ID_TOKEN" ]; then
  cat >&2 <<'EOS'
ID_TOKEN 이 비어 있다. dev Cognito IdToken 이 필요하다. 세 가지 방법:

  (1) AWS CLI — client secret 이 있으면 가장 확실하다
      dev 앱 클라이언트가 client secret 을 쓰므로 SECRET_HASH 가 필요하다.
        CLIENT_ID=<dev 앱 클라이언트 ID — backend-api-main dev yml>
        SECRET=<aws.cognito.client.secret — SSM 또는 ECS 태스크 정의에서 확인>
        USER=<전화번호 형태의 로그인 아이디>
        HASH=$(printf "%s%s" "$USER" "$CLIENT_ID" \
                | openssl dgst -sha256 -hmac "$SECRET" -binary | base64)
        aws cognito-idp initiate-auth \
          --auth-flow USER_PASSWORD_AUTH \
          --client-id "$CLIENT_ID" \
          --auth-parameters USERNAME=$USER,PASSWORD=<pw>,SECRET_HASH=$HASH \
          --query 'AuthenticationResult.IdToken' --output text

  (2) 서버의 해시 발급 API 경유 — client secret 을 모를 때
      POST {APP_API}/api/system/user/phone?phone=<로그인아이디>
      단 이 경로는 permitAll 이 아니라 관제 Admin JWT 가 필요하다.
      (/login 으로 백오피스 계정 로그인 후 그 토큰 사용)
      받은 해시를 (1) 의 SECRET_HASH 자리에 넣는다.

  (3) 실제 앱에서 복사
      dev 빌드 앱의 네트워크 로그에서 IdToken 헤더를 복사한다. 가장 빠르지만
      만료가 짧다(기본 1시간).

  확보 후:  export ID_TOKEN='eyJ...'
EOS
  exit 2
fi

echo "대상: serial=${SERIAL}"
echo "앱 API: ${APP_API}"
echo "WebView: ${WEB_ORIGIN}"

# ── 0. 사전 확인 — 내부 API 가 외부에 열려 있지 않은지 ────────
step "0. 내부 API 외부 노출 확인 (릴리스 게이트)"
INTERNAL_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
  "${APP_API}/internal/v1/devices/ZZZZTEST0000/oasys/membership/apply")
if [ "$INTERNAL_CODE" = "403" ]; then
  ok "내부 경로 미노출 (403 = API Gateway 라우트 미등록)"
else
  bad "내부 경로가 열려 있다 (HTTP ${INTERNAL_CODE}) — 즉시 중단하고 게이트웨이 확인"
  exit 1
fi

# ── 1. 티켓 발급 ─────────────────────────────────────────────
step "1. WebView 진입 티켓 발급"
TICKET_RES=$(curl -s -w '\n%{http_code}' -X POST \
  -H "IdToken: ${ID_TOKEN}" \
  "${APP_API}/api/app/v1/devices/${SERIAL}/oasys/membership/web-session-tickets")
TICKET_CODE=$(echo "$TICKET_RES" | tail -1)
TICKET_BODY=$(echo "$TICKET_RES" | sed '$d')

if [ "$TICKET_CODE" != "200" ]; then
  bad "티켓 발급 실패 (HTTP ${TICKET_CODE})"
  echo "$TICKET_BODY"
  note "409 NOT_DEVICE_MEMBER 면 이 계정이 해당 기기의 홈 멤버가 아니다."
  note "401/IllegalArgument 면 IdToken 이 만료됐거나 strict 검증에 걸린 것이다."
  exit 1
fi

TICKET=$(echo "$TICKET_BODY"   | jsonf data.ticket)
ENTRY_URL=$(echo "$TICKET_BODY"| jsonf data.entryUrl)
EXPIRES=$(echo "$TICKET_BODY"  | jsonf data.expiresInSeconds)

ok "티켓 발급 (만료 ${EXPIRES}초)"
expect "entryUrl 이 WebView origin" "${WEB_ORIGIN}/api/web/v1/oasys/membership/entry" "$ENTRY_URL"
case "$ENTRY_URL" in
  *app-api*) bad "entryUrl 이 앱 API 호스트다 — webview.origin 설정 오류" ;;
  *//api*)   bad "entryUrl 에 슬래시 중복 — origin 끝 '/' 정규화 실패" ;;
esac

# ── 2. 티켓 → 쿠키 교환 ──────────────────────────────────────
step "2. 티켓을 세션 쿠키로 교환"
ENTRY_HDR=$(curl -s -D - -o /dev/null -c "$JAR" -X POST \
  -d "ticket=${TICKET}" "$ENTRY_URL")

ENTRY_CODE=$(echo "$ENTRY_HDR" | awk 'NR==1{print $2}')
LOCATION=$(echo "$ENTRY_HDR"   | tr -d '\r' | awk -F': ' 'tolower($1)=="location"{print $2}')
SETCOOKIE=$(echo "$ENTRY_HDR"  | tr -d '\r' | awk -F': ' 'tolower($1)=="set-cookie"{print $2}')

expect "HTTP 상태" "303" "$ENTRY_CODE"
expect "복귀 화면" "${WEB_ORIGIN}/oasys/membership" "$LOCATION"

case "$SETCOOKIE" in *HttpOnly*) ok "HttpOnly" ;; *) bad "HttpOnly 없음" ;; esac
case "$SETCOOKIE" in *Secure*)   ok "Secure" ;;   *) bad "Secure 없음" ;;   esac
case "$SETCOOKIE" in
  *SameSite=Lax*) ok "SameSite=Lax" ;; *) bad "SameSite=Lax 아님 — Strict 면 PASS 복귀가 깨진다" ;;
esac
case "$SETCOOKIE" in
  *"Path=/api/web/v1/oasys/membership"*) ok "Path 한정" ;;
  *) bad "Path 가 좁혀지지 않았다: ${SETCOOKIE}" ;;
esac
case "$SETCOOKIE" in
  *Domain=*) bad "Domain 속성이 있다 — host-only 여야 한다" ;;
  *) ok "Domain 미지정 (host-only)" ;;
esac

# 1회용 확인
REUSE_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -d "ticket=${TICKET}" "$ENTRY_URL")
expect "티켓 재사용 거절" "403" "$REUSE_CODE"

# ── 3. 세션 조회 ─────────────────────────────────────────────
step "3. 세션 상태 조회"
SESS=$(curl -s -b "$JAR" "${WEB_API}/session")
STATE=$(echo "$SESS" | jsonf data.state)
CSRF=$(echo "$SESS"  | jsonf data.csrfToken)
expect "초기 상태" "PASS_PENDING" "$STATE"
[ -n "$CSRF" ] && ok "CSRF 토큰 수신" || bad "CSRF 토큰 없음"
expect "세션의 serial" "$SERIAL" "$(echo "$SESS" | jsonf data.serial)"

case "$SESS" in
  *icstNo*|*safeKey*|*skmSfky*|*\"ci\"*|*passCi*) bad "세션 응답에 민감정보가 실렸다" ;;
  *) ok "세션 응답에 민감정보 없음" ;;
esac

NOCOOKIE=$(curl -s -o /dev/null -w '%{http_code}' "${WEB_API}/session")
expect "쿠키 없이 조회 거절" "401" "$NOCOOKIE"

# ── 4. CSRF ──────────────────────────────────────────────────
step "4. CSRF 방어"
NOCSRF=$(curl -s -o /dev/null -w '%{http_code}' -X POST -b "$JAR" "${WEB_API}/pass/start")
expect "CSRF 토큰 없이 거절" "403" "$NOCSRF"

# ── 5. PASS 시작 ─────────────────────────────────────────────
step "5. PASS 본인인증 시작"
PASS_RES=$(curl -s -X POST -b "$JAR" -H "X-CSRF-Token: ${CSRF}" "${WEB_API}/pass/start")
ACTION_URL=$(echo "$PASS_RES" | jsonf data.actionUrl)

if [ -z "$ACTION_URL" ]; then
  bad "PASS URL 발급 실패"; echo "$PASS_RES"; exit 1
fi
ok "PASS URL 발급"

cat <<EOS

${YLW}────────────────────────────────────────────────────────────${RST}
아래 URL 을 브라우저에서 열어 본인인증을 완료하세요.
${DIM}이 스크립트와 같은 브라우저가 아니어도 됩니다 — 폰으로 열어도 됩니다.
콜백이 쿠키가 아니라 authId 로 세션을 찾기 때문입니다.${RST}

${ACTION_URL}

${DIM}주의: 로그인 계정의 전화번호와 같은 명의로 인증해야 합니다.
다른 명의면 IDENTITY_MISMATCH 로 떨어집니다(의도된 동작).${RST}
${YLW}────────────────────────────────────────────────────────────${RST}
EOS

# ── 6. PASS 완료 대기 ────────────────────────────────────────
step "6. PASS 완료 대기 (최대 5분)"
DEADLINE=$(( $(date +%s) + 300 ))
while :; do
  SESS_NOW=$(curl -s -b "$JAR" "${WEB_API}/session")
  STATE=$(echo "$SESS_NOW" | jsonf data.state)
  FAILCODE=$(echo "$SESS_NOW" | jsonf data.passFailureCode)
  case "$STATE" in
    PASS_VERIFIED) ok "PASS 완료"; break ;;
    "")            bad "세션이 만료됐다 (15분 TTL)"; exit 1 ;;
  esac

  # PASS 실패는 상태를 바꾸지 않는다(재시도 가능해야 하므로). 사유로만 알 수 있다.
  if [ -n "$FAILCODE" ]; then
    printf "\n"
    bad "PASS 실패 — ${FAILCODE}"
    case "$FAILCODE" in
      IDENTITY_MISMATCH)    note "로그인 계정의 전화번호와 다른 명의로 인증했다. 본인 번호로 재시도하라." ;;
      AUTH_SESSION_EXPIRED) note "같은 인증 URL 이 두 번 콜백됐거나 5분 TTL 이 지났다. 스크립트를 다시 실행하라." ;;
      PASS_PROVIDER_ERROR)  note "NICE 결과 조회 자체가 실패했다. 서버 로그의 [NICE] 항목에 원본 코드가 있다." ;;
      *)                    note "알 수 없는 사유. 서버 로그의 [MEMBERSHIP_PASS] 항목을 확인하라." ;;
    esac
    exit 1
  fi

  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    bad "5분 내 완료되지 않음 (마지막 상태: ${STATE})"
    note "인증 창을 아직 안 끝냈거나, 콜백이 서버에 도달하지 않았다."
    exit 1
  fi
  printf "\r  ${DIM}대기 중... (현재 %s)${RST}   " "$STATE"
  sleep 3
done
printf "\n"

# ── 7. 신청 ──────────────────────────────────────────────────
step "7. 직접 구독 신청"

# 요금제 검증이 먼저다. 상태를 바꾸지 않고 400 으로 떨어지므로 본 신청 전에 확인할 수 있다.
BADPLAN=$(curl -s -o /dev/null -w '%{http_code}' -X POST -b "$JAR" \
  -H "X-CSRF-Token: ${CSRF}" -H 'Content-Type: application/json' \
  -d '{"plan":"GOLD"}' "${WEB_API}/apply")
expect "알 수 없는 요금제 거절" "400" "$BADPLAN"

NOPLAN=$(curl -s -o /dev/null -w '%{http_code}' -X POST -b "$JAR" \
  -H "X-CSRF-Token: ${CSRF}" -H 'Content-Type: application/json' \
  -d '{}' "${WEB_API}/apply")
expect "요금제 누락 거절" "400" "$NOPLAN"

# 가격 코드를 그대로 보내도 받아주면 안 된다 — 화면이 코드를 알 이유가 없다.
RAWCODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST -b "$JAR" \
  -H "X-CSRF-Token: ${CSRF}" -H 'Content-Type: application/json' \
  -d '{"plan":"10070726"}' "${WEB_API}/apply")
expect "가격 코드 직접 지정 거절" "400" "$RAWCODE"

echo "  ${DIM}요금제: ${PLAN}${RST}"
APPLY_RES=$(curl -s -w '\n%{http_code}' -X POST -b "$JAR" \
  -H "X-CSRF-Token: ${CSRF}" -H 'Content-Type: application/json' \
  -d "{\"plan\":\"${PLAN}\"}" "${WEB_API}/apply")
APPLY_CODE=$(echo "$APPLY_RES" | tail -1)
APPLY_BODY=$(echo "$APPLY_RES" | sed '$d')

echo "$APPLY_BODY"
case "$APPLY_CODE" in
  200) ok "접수 성공 (SUCCEEDED)" ;;
  202) note "${YLW}MEMBERSHIP_APPLY_UNCERTAIN — 접수 여부 불명.${RST}"
       note "실패가 아니다. 자동 재호출하지 않는 것이 정상 동작이다." ;;
  409) note "업무 거절: $(echo "$APPLY_BODY" | jsonf error.code)"
       note "화이트리스트 미등록 시리얼이면 실제 OASYS 응답이다." ;;
  *)   bad "예상치 못한 응답 (HTTP ${APPLY_CODE})" ;;
esac

case "$APPLY_BODY" in
  *icstNo*|*safeKey*|*skmSfky*|*ctrtNo*) bad "신청 응답에 서버 권위값이 실렸다" ;;
  *) ok "신청 응답에 민감정보 없음" ;;
esac

# ── 정리 ─────────────────────────────────────────────────────
step "결과"
if [ "$FAILED" -eq 0 ]; then
  printf "  ${GRN}전 항목 통과${RST}\n"
else
  printf "  ${RED}실패 %d건${RST}\n" "$FAILED"
fi

cat <<EOS

${DIM}원장 확인 (dev DB):

  SELECT correlation_id, login_id, serial, status, oasys_result_code,
         create_date, resolved_at
    FROM oasys_membership_apply_attempt
   WHERE serial = '${SERIAL}'
   ORDER BY id DESC LIMIT 5;

  민감정보 컬럼이 없어야 한다 — CI·SafeKey·통합고객번호·계약번호 미저장.

요금제는 원장에 남지 않는다(의도). 어느 요금제로 접수했는지는 OASYS 원본과
skix-security 아웃바운드 로그에서 확인한다.

재실행하면 새 correlation_id 로 새 행이 생긴다. 이전 시도 상태와 무관하게
재신청이 허용되는 것이 의도된 동작이다(중복 판정은 OASYS 몫).${RST}
EOS

exit "$([ "$FAILED" -eq 0 ] && echo 0 || echo 1)"
```
