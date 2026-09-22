# [작업] 오픈API 포털 복수 로그인 — identity 정규화 · 탈퇴 파기 · 내부 공유키

| 항목 | 내용 |
|---|---|
| **상태** | 개발계·검증계 **완료**(코드 반영 + 릴리스 R1~R5 완주, 2026-08-27) · **운영계 미반영·미배포**(`main` 브랜치에 한 줄도 없음) |
| **작업 기간** | 2026-08-26 ~ 2026-09-04 |
| **직접 수정한 저장소** | `skix-openapi`, `backend-api-main` |
| **요청서를 전달한 대상** | 없음. 서버 2개 저장소로 완결되는 작업이다(앱·프론트 변경 없음) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §8-1의 1번(개발계·검증계에 9/4 공유키 쌍이 **배포 순서대로** 올라갔는지 확인)과 2번(운영계 진입 조건 §8-2 체크리스트 현재값 확인) |

---

## 0. 세 줄 요약

1. 한 사람이 **소셜 로그인 수단마다 다른 Cognito sub**를 갖는 탓에, 오픈API 개발자 포털에서 "권한은 받았는데 로그인하면 403"이 나는 갭이 있었다. 포털이 로그인 sub를 그대로 키로 쓰기 때문이다. 이것을 **canonical sub(대표 키)로 정규화**해 해소했다.
2. 같은 트랙에서 두 가지를 함께 닫았다 — **회원 탈퇴 시 오픈API 잔존 데이터 파기**(권한·자격증명·토큰·웹훅), 그리고 **내부 API 공유키 검증**(오픈API의 `/internal/v1/**`은 그때까지 무인증이었다).
3. 개발계·검증계는 실측(S0)부터 플래그 ON(R4)·강제 ON(R5)까지 **완주**했고, 운영계는 **의도적으로 플래그 false**인 채 브랜치 반영조차 안 된 상태다. 운영계 진입의 최대 게이트는 **다른 트랙(Cognito 토큰 PII 제거)의 운영계 `user_accounts` 백필**이다(§8-2).

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **skix-openapi (나무엑스 OPEN API)** | 파트너사가 우리 기기를 조회·제어하는 **외부 개방 API** 서버. 같은 애플리케이션이 **개발자 포털**(자격증명 발급·조회·로테이션·사용량 조회)도 서빙한다. 운영 호스트 `openapi.namuhx.com`, ECS Fargate |
| **개발자 포털** | 파트너 개발자가 Cognito 로그인해서 client_id/secret을 발급받는 화면. 서버 경로는 `/v1/developer/**`이고 `CognitoJwtFilter`가 **ID 토큰**을 검증한다 |
| **파트너 API** | 파트너 서버가 발급받은 자격증명으로 호출하는 `/v1/**`. 포털과 별개의 자체 발급 JWT(`JwtAuthFilter`)를 쓴다 |
| **backend-api-main** | 앱·백오피스용 메인 백엔드. `user`·`user_accounts` 테이블의 주인이자 **신원(identity)의 권위**다. 오픈API 포털 권한 부여(enable/disable)도 이쪽 백오피스 경로를 통한다 |
| **sub** | Cognito가 사용자 계정 하나에 부여하는 UUID. **provider(로그인 수단)마다 다른 값이 발급된다** — 이 풀은 `AdminLinkProviderForUser`로 계정 연결을 하지 않기 때문이다 |
| **provider** | 로그인 수단. 이 풀에서 열려 있는 것은 `COGNITO`(전화번호 가입)·Google·Kakao·Naver·SignInWithApple 5종 |
| **user_accounts** | `backend-api-main`의 테이블. "한 사람(user.id)에게 딸린 sub들"의 목록. 한 행 = 한 provider 계정 |
| **user.sub (레거시)** | `user` 테이블의 단일 sub 컬럼. `user_accounts`가 생기기 전부터 있던 컬럼이고, 오픈API 권한 부여가 이 값으로 키잉돼 있었다 |
| **canonical sub / ownerKey** | 한 사람의 대표 sub. 선정 규칙은 **같은 `user_id`의 `user_accounts` 중 `create_date` 오름차순, 동률이면 `id` 오름차순의 첫 행**. 규칙의 소유자는 `backend-api-main`의 `SubIdentityResolver` 한 곳이다 |
| **identity API** | `GET {backend}/api/internal/user/identity?sub=...` → `{ownerKey, aliases}`. 매핑이 없으면 **404**(= "정규화 대상 없음"이 정상 응답) |
| **재키잉(rekey)** | 이미 legacy sub로 저장돼 있는 오픈API 행(`open_api_user_permissions.user_id`, `open_api_clients.user_id`)을 canonical sub로 바꿔 쓰는 DB 작업 |
| **파기(purge)** | 회원 탈퇴 시 오픈API 쪽 권한·자격증명·토큰·웹훅을 지우거나 무효화하는 것 |
| **R1~R5** | 이 트랙의 릴리스 단계 이름. R1 오픈API 배포(다크) → R2 backend 배포 → R3 재키잉 → R4 정규화 ON → R5 내부키 강제 ON |
| **S0** | 배포 전 실측 단계. 재키잉 대상 수·고아 행 수·백필 상태를 재서 **릴리스 절차 자체를 결정**한다 |
| **token-pii 트랙** | 별개 작업. Cognito 토큰에서 PII 클레임을 빼고 sub 기준으로 전환하는 트랙이며, 그 안에 **`user_accounts` 백필**이 들어 있다. 이 문서의 운영계 게이트가 거기에 걸려 있다 |

### 1-2. 문제

세 가지가 한 덩어리로 묶여 있었다.

**(1) 포털 403 락아웃 — 이 트랙의 본론.**

오픈API 포털의 권한 부여는 백오피스에서 전화번호로 사람을 찾아 `enable`을 누르는 방식이다. 그때 `backend-api-main`이 그 사람의 **`user.sub`**(레거시 단일 컬럼)를 키로 오픈API에 권한 행을 만든다. 반면 포털에 로그인할 때 오픈API가 키로 쓰는 값은 **그 로그인에 쓰인 provider의 sub**다.

Cognito 풀이 provider 연결을 하지 않으므로 이 둘은 **같은 사람인데도 다른 값**이다. 즉 카카오로 가입한 사람에게 권한을 줬는데 그 사람이 구글로 포털에 로그인하면, 권한 행을 찾지 못해 **403**이 난다. 자격증명 발급·조회·로테이션이 전부 막힌다.

이게 이론상의 이야기가 아니라는 근거는 S0 실측이다 — 검증계·운영계 앱 클라이언트가 **5종 provider를 전부 개방**하고 있어서(개발계만 Apple 없음), "원 가입 provider ≠ 포털 로그인 수단"이 되는 즉시 발생한다. 개발계에는 계정 3개를 가진 실사용자(`user.id=617`)가 실제로 있다.

**(2) 탈퇴해도 오픈API 데이터가 남는다.**

`backend-api-main`의 탈퇴 체인에 오픈API 연동이 아예 없었다. 탈퇴한 사람의 권한 행·클라이언트·발급 토큰·웹훅이 그대로 살아 있었다. 특히 웹훅은 **클라이언트 상태를 보지 않고 발송**되므로, 탈퇴한 사람의 엔드포인트로 데이터가 계속 나가는 형태였다.

**(3) 오픈API의 `/internal/v1/**`이 무인증.**

오픈API의 내부 관리 경로(권한 부여·클라이언트 발급·시크릿 로테이션·요금제 변경)는 `SecurityConfig`에서 `permitAll`이고, 보호는 앞단 ALB 경로 규칙 하나뿐이다. 그런데 VPC 안에서 태스크 IP로 직접 호출하면 그 규칙에 걸리지 않는다. 호출자가 `backend-api-main` 하나뿐이므로 애플리케이션 레벨 2차 방어선을 닫을 수 있는 상태였다.

### 1-3. 목표

- 포털 로그인 sub를 **canonical sub로 정규화**해서 로그인 수단과 무관하게 같은 사람이 같은 자격증명을 본다.
- 정규화는 **되돌릴 수 있게**(env 플래그) 넣고, 실패·미매핑은 전부 **현행 동작(raw sub)으로 폴백**한다. 신원 조회가 죽어도 포털이 잠기면 안 된다.
- 이미 legacy 키로 저장된 기존 행은 **배포 전에 세어보고**, 있으면 재키잉한다. 없으면 그 단계를 건너뛴다.
- 탈퇴 시 오픈API 데이터를 파기한다. 실패해도 **탈퇴 자체는 계속** 진행한다(기존 medcare 연동과 같은 정책).
- 내부 경로에 공유키 검증을 건다. 단 **log-only로 먼저 배포**해 호출자가 실제로 헤더를 붙이는지 확인한 뒤 강제한다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 코드 반영

`git merge-base --is-ancestor` 로 원격 브랜치 포함 여부를 직접 확인한 값이다.

| 저장소 | 커밋 | 날짜 | 내용 | dev | stg | main(운영) |
|---|---|---|---|---|---|---|
| `skix-openapi` | `2c444ba` | 08-27 | 포털 sub 정규화 · 내부 공유키 검증 · 탈퇴 파기 EP | 반영 | 반영 | **미반영** |
| `skix-openapi` | `769707a` | 08-27 | 리뷰 반영(파기 클라이언트 조회 단계 분리 등) | 반영 | 반영 | **미반영** |
| `skix-openapi` | `0a08a5f` | 08-27 | 작업 문서 2종을 저장소에서 제거 | 반영 | 반영 | **미반영** |
| `skix-openapi` | `e8bffe8` | 08-27 | task-definition 3환경에 내부키·정규화 env 추가 | 반영 | 반영 | **미반영** |
| `skix-openapi` | `e8db0f6` | 08-27 | dev·stg task-definition 플래그를 R4·R5 완료 상태로 고정 | 반영 | 반영 | **미반영** |
| `skix-openapi` | `9ba32fa` | 09-04 | 상류(backend) 호출 전체에 공유키를 싣는다 | 반영 | 반영 | **미반영** |
| `backend-api-main` | `ee2b1fc5` | 08-27 | canonical 정규화 · 공유키 부착 · 탈퇴 파기 연동 | 반영 | 반영 | **미반영** |
| `backend-api-main` | `00b10674` | 09-04 | `/internal/v1/openapi/**` 전체에 공유키 검증 | 반영 | 반영 | **미반영** |

브랜치 헤드(참고): `skix-openapi` dev `d6e5fd2`(09-04) / stg `8c7f3ca`(09-04) / **main `85b8b76`(2026-08-20)**. `backend-api-main` dev `3fdd8447`(09-18) / stg `ed6dc871`(09-18) / **main `7d9ac170`(09-01, 핫픽스 revert 상태)**.

즉 **두 저장소 모두 `main`이 이 트랙 이전 형상**이다. 운영계 승격은 이 트랙만 따로 떼서 올릴 수 없고, 각 저장소의 운영 릴리스에 묶여 간다.

### 2-2. 환경별 플래그 실상태

저장소의 `deploy/task-definition-*.json`은 **실제 배포 상태를 반영하도록 의도적으로 고정**해 둔 것이다(`e8db0f6`). 아래는 `origin/dev`·`origin/stg`에서 읽은 값이며 두 브랜치가 동일하다.

| 환경 | `IDENTITY_NORMALIZE_ENABLED` (정규화) | `APP_INTERNAL_ENFORCE_API_KEY` (오픈API 수신 강제) | 비고 |
|---|---|---|---|
| dev | **true** | **true** | R4·R5 완료 |
| stg | **true** | **true** | R4·R5 완료 |
| prd | **false** | **false** | **의도적 유지**. 플립 시 이 파일도 함께 고친다 |

`backend-api-main` 쪽 수신 강제(`app.internal.enforce-api-key`, 프로필 yml 리터럴)는 dev **true** / stg **true** / prd **false** 다.

`origin/main`의 `skix-openapi` task-definition에는 이 env 4종이 **아예 없다**(`APP_INTERNAL_API_KEY`·`NAMUHX_INTERNAL_API_KEY`·플래그 2종 전부). 운영 배포 때 SSM 등록과 task-definition 개정이 반드시 선행해야 한다(§6-1·§8-2).

### 2-3. 배포·검증 상태

| 항목 | 상태 | 근거 / 확인 방법 |
|---|---|---|
| 개발계 R1~R5 완주 | **완료 기록 있음**(2026-08-27). 배포·정규화 ON·강제 ON | 작업 기록. ECS 실물은 로컬에서 볼 수 없어 **미확인** — VDI에서 `skix-openapi-dev-svc`의 활성 태스크 정의 env를 확인한다 |
| 검증계 R1~R5 완주 | **완료 기록 있음**(2026-08-27). 재키잉 1건 포함 | 위와 동일. VDI 확인 필요 |
| 개발계·검증계에 **9/4 공유키 쌍** 배포 | **미확인** | 두 저장소 dev·stg 브랜치에는 들어 있으나 ECS 반영 여부는 확인 못 했다. **배포 순서 제약이 있다**(§6-6, §8-1) |
| 운영계 | **미반영·미배포** | `main` 브랜치에 코드 없음 |
| S0 실측(3환경 DB + Cognito IdP) | **완료**(2026-08-27). 결과는 §2-4 | 측정 SQL 전문은 부록 B |
| 검증계 재키잉 1건 | **완료**(2026-08-27) | 런북 전문은 부록 C |
| 운영계 재키잉 | **불필요**(대상 0건) | S0 §C |
| 탈퇴 파기 실패 알람 | **미확인** | R2 게이트로 요구한 항목이다. 백엔드 ERROR 로그 패턴 `openapi 탈퇴 파기 실패: stage=PURGE_REQUEST` 에 알람이 걸려 있는지 VDI에서 확인 |

### 2-4. S0 실측 결과 (2026-08-27, 3환경)

이 숫자가 릴리스 절차를 결정했다. 재측정이 필요하면 부록 B의 SQL을 그대로 다시 실행한다.

| 게이트 | 뜻 | dev | stg | prd |
|---|---|---|---|---|
| ① REKEY | 활성 사용자인데 오픈API 키가 canonical이 아닌 행 | **0** | **1** | **0** |
| ② legacy ≠ canonical | `user.sub`가 canonical과 다른 활성 사용자 수(정보값) | 14 | 13 | 1,230 |
| ③ sub 오귀속 재검 | `preempted · duplicated · dup_account_sub · unique_index` | 0·0·0·1 통과 | 0·0·0·1 통과 | 0·0·0·1 통과 |
| ④ ORPHAN | 활성 사용자 어디에도 안 잡히는 오픈API 키(과거 탈퇴자 잔존 추정) | **0** | **0** | **0** |
| ⑤ 백필 상태 | `user_accounts` 미등재 활성 sub 수 | 0(완료) | 0(완료) | **1,222(미완)** |
| — | 오픈API 보유 sub 총수 | 4건 전부 OK_CANONICAL | — | 6건(OK 5 + LEGACY_ONLY 1) |
| — | 결정된 경로 | R3 생략·단계 배포 | **R2~R4 단일 창구** | R3 생략·단계 배포, **R4는 백필 후** |

- Cognito 앱 클라이언트 허용 provider: **stg·prd는 5종 전부**(COGNITO·Google·Kakao·Naver·SignInWithApple), **dev만 Apple 없음**. 그래서 개발계 교차 로그인 검증은 Apple을 뺀 조합으로 한다.
- 검증계 재키잉 쌍(실행 완료): legacy `5468cd5c-1011-7074-3716-dca8ec33d14f` → canonical `24087d6c-f001-703c-3dd1-d8c0bee3867b` (`user_id=9`, 계정 3개, 권한+클라이언트 둘 다 보유, PK 충돌 없음).
- 운영계 `LEGACY_ONLY` 1건: `24b8ed2c-e041-7036-5ab9-89c80da354be`. `user_accounts` 미등재 활성 사용자의 `user.sub` 키다. **백필이 이 sub를 유일 행으로 등재하면 canonical == 현재 키가 되어 자연 해소**된다. 고아가 아니므로 파기 대상이 아니다.
- 운영계 게이트②의 1,230명은 대부분 게이트⑤의 1,222명과 겹친다(부계정이 canonical 첫 행인 사람들). 이들은 백필 전에는 identity API가 404를 주므로 `user.sub` 폴백으로 **현행 동작 그대로**다. 백필 후에야 canonical로 수렴한다. 운영계 R4에 백필을 선행 조건으로 건 이유가 이것이다 — 부분 활성화를 막는다.

---

## 3. 무엇을 만들었나

### 3-1. 전체 흐름

```
[포털 로그인]  개발자 → GET/POST /v1/developer/credentials  (Authorization: Bearer <Cognito ID Token>)
                         │
        skix-openapi     ▼ CognitoJwtFilter
                    서명·iss·token_use=id·aud 검증          ← 여기까지는 종전과 동일
                         │  (검증 통과한 sub 만 다음으로)
                         ▼ IdentityResolver.resolve(loginSub)
                    IDENTITY_NORMALIZE_ENABLED=false → 호출 없이 raw 반환 (다크)
                                             =true  → GET {backend}/api/internal/user/identity?sub=...
                                                      200 → ownerKey(canonical)
                                                      404 → raw 폴백 (정규화 대상 없음 = 정상)
                                                      타임아웃·5xx → raw 폴백 + WARN
                         │
                         ▼ principal · developer.user_id · audit.user_id  ← 3곳 모두 canonical 로 통일
                    자격증명 발급/조회/로테이션이 canonical 키로 수행됨

[권한 부여]  백오피스 → backend-api-main OpenApiUserService
                         │ user.sub → SubIdentityResolver.resolve() → ownerKey (미등재면 user.sub 폴백)
                         ▼ POST {openapi}/internal/v1/permissions/{ownerKey}/enable
                           + 헤더 X-Internal-Api-Key

[회원 탈퇴]  backend-api-main UserService.withdraw
                         │ purgeMedcareOnWithdraw(...)        ← 기존
                         ▼ purgeOpenApiOnWithdraw(...)        ← 신규
                           후보 sub = user_accounts 전체 ∪ 토큰 sub ∪ user.sub
                           각 sub 마다 POST {openapi}/internal/v1/users/{sub}/purge?apply=true
                         │ 실패해도 log + continue (탈퇴는 계속 진행)
                         ▼ deleteAllCognitoByUserAccounts → deleteUserAccountByLoginId
                           (여기서 user_accounts 가 hard delete 된다)

[오픈API 파기]  ① 권한 삭제 → ② 클라이언트 REVOKED → ③ 토큰 폐기 시도
                → ④ ③의 성패와 무관하게 웹훅 비활성화
                   부분 실패는 failures[] 로 200 응답. 재호출이 곧 재시도(멱등)
```

### 3-2. 저장소별 역할과 주요 파일

경계 원칙: **신원의 권위는 `backend-api-main`**(`user_accounts`가 거기 있다), **오픈API는 받은 키를 그대로 쓴다.** canonical 선정 규칙을 두 곳이 알면 규칙이 갈라진다.

| 책임 | 저장소 | 파일 (`src/main/java/...` 하위) |
|---|---|---|
| 포털 로그인 sub → canonical 정규화 | skix-openapi | `common/identity/IdentityResolver.java` (신규) |
| 정규화를 인증 파이프라인에 연결 | skix-openapi | `common/filter/CognitoJwtFilter.java` |
| 내부 공유키 검증 컴포넌트 | skix-openapi | `common/security/InternalApiKeyVerifier.java` (신규, backend에서 포팅) |
| 내부 EP 4종에 검증 호출 | skix-openapi | `permission/controller/InternalPermissionController`, `client/controller/InternalClientController`, `plan/controller/InternalPlanController`, `purge/controller/InternalUserPurgeController` |
| 탈퇴 파기 | skix-openapi | `purge/service/UserPurgeService.java`, `purge/model/PurgeResult.java` (신규) |
| 파기용 조회 쿼리 | skix-openapi | `client/mapper/ClientMapper`(`findClientsByUserId`), `permission/mapper/UserPermissionMapper`(`deleteByUserId`) + 각 XML |
| 상류(backend) 호출에 공유키 부착 | skix-openapi | `common/config/NamuhxClientConfig.java` (WebClient 필터 1곳) |
| canonical 선정 규칙의 단독 소유자 | backend-api-main | `core/component/SubIdentityResolver.java` (owner-unification 트랙에서 기존 구현) |
| identity 내부 API 제공 | backend-api-main | `api/user/controller/InternalUserController.java` (`/internal/user/identity`) |
| 오픈API 연동 키 정규화·공유키 부착 | backend-api-main | `api/internal/service/OpenApiUserService`, `OpenApiClientService`, `OpenApiPlanService` |
| 탈퇴 체인 연결 | backend-api-main | `api/user/service/UserService.java` (`purgeOpenApiOnWithdraw`, `collectPurgeSubs`) |
| 파기 응답 모델 | backend-api-main | `api/internal/model/OpenApiPurgeResponse.java` (신규) |
| 오픈API 수신 구간 공유키 검증 | backend-api-main | `core/configuration/SpringMvcConfiguration`, `core/security/InternalOpenApiKeyInterceptor` (09-04) |

회귀 고정 테스트: `common/security/InternalEndpointSecurityTest`(오픈API 내부 EP 16개 전수 401 확인), `common/identity/IdentityResolverTest`, `purge/service/UserPurgeServiceTest`, `security/InternalOpenApiKeyInterceptorTest`(backend).

### 3-3. 설정 키와 환경변수

**skix-openapi** (`application-ecs.yml` → task-definition)

| 프로퍼티 | env 변수 | 뜻 | 기본값 |
|---|---|---|---|
| `app.internal.api-key` | `APP_INTERNAL_API_KEY` | **오픈API가 수신할 때 검증하는 키**(키 A). SSM SecureString | 빈 문자열 |
| `app.internal.enforce-api-key` | `APP_INTERNAL_ENFORCE_API_KEY` | 위 검증 강제 여부. false면 도달 여부만 로깅 | `false` |
| `namuhx.internal.api-key` | `NAMUHX_INTERNAL_API_KEY` | **오픈API가 backend를 호출할 때 제시하는 키**(키 B = backend의 기존 공유키 값) | 빈 문자열 |
| `namuhx.internal.base-url` | `NAMUHX_INTERNAL_URL` | backend 내부 NLB. 기존 값 | — |
| `openapi.identity.normalize-enabled` | `IDENTITY_NORMALIZE_ENABLED` | 포털 sub 정규화 ON/OFF | `false` |

**backend-api-main**

| 프로퍼티 | 출처 | 뜻 |
|---|---|---|
| `app.openapi.api.api-key` | SSM `/backend-api-main/<env>/` (`spring.config.import`) | backend가 오픈API를 호출할 때 제시하는 키(= 키 A와 **같은 값**) |
| `app.openapi.api.url` | 프로필 yml 리터럴 | 오픈API 내부 NLB. dev `...nlb-dev-524d99ac8d560247...:8084` / stg `...stg-was-nlb-77245f9e5410621b...:8084` / prd `...prd-was-nlb-cd674400fc69f0f5...:8084` |
| `app.internal.api-key` | `app.myHealthCare.api.api-key`의 별칭(SSM) | backend가 수신할 때 검증하는 키(키 B) |
| `app.internal.enforce-api-key` | 프로필 yml 리터럴 | dev·stg `true`, prd `false` |

**열쇠가 두 개인 이유**: 문이 두 개다. backend→오픈API 방향(키 A)과 오픈API→backend 방향(키 B)은 서로 다른 수신자가 검증한다. 키 B는 이미 존재하는 값(medcare 연동용)을 오픈API 쪽에 복사만 하므로 **신규 시크릿은 키 A 하나**다.

**주의**: `app.internal.enforce-api-key`를 SSM에 넣으면 안 된다. `spring.config.import`로 들어오는 값은 `application.yml`보다 **우선순위가 낮아서**, 공통 yml에 박힌 `false`에 덮여 조용히 무시된다. 같은 함정이 `backend-api-main`의 그 프로퍼티 주석에 적혀 있다.

### 3-4. 오픈API DB 테이블 (파기·재키잉 대상)

| 테이블 | 키 컬럼 | 파기 시 처리 |
|---|---|---|
| `open_api_user_permissions` | `user_id` (**PK**, = Cognito sub) | DELETE |
| `open_api_clients` | `user_id` (비유니크 인덱스) | `status = 'REVOKED'` (삭제 아님) |
| `open_api_tokens` | `client_id` | 폐기 시도(Redis 경유) |
| `open_api_webhooks` | `client_id` | `is_active = 0` |

`open_api_user_permissions.user_id`가 **PK**라서 재키잉 시 legacy 행과 canonical 행이 둘 다 있으면 UPDATE가 충돌한다. `open_api_clients`는 비유니크라 충돌이 없다. 재키잉 런북(부록 C)이 이 분기를 다룬다.

DB 토폴로지(실측): `backend-api-main`과 오픈API는 **같은 Aurora 인스턴스의 다른 스키마**다. main 스키마는 dev `airbotdev`, stg·prd `benjamin`. 오픈API 스키마는 SSM `/skix-openapi/<env>/DB_URL`로 확인한다(로컬 기본은 `openapi`). 같은 인스턴스이므로 S0 분류 쿼리가 **교차 스키마 JOIN 한 방**으로 끝난다.

### 3-5. 09-04 후속 — 반대 방향 공유키

08-27 작업은 backend→오픈API 방향만 닫았다. 09-04에 반대 방향을 마저 닫았다.

- `backend-api-main` `00b10674`: `/internal/v1/openapi/**` 아래 18개 EP 중 공유키를 검증하던 것이 4개(날씨 1·설정 2·사용량 1)뿐이었다. 나머지 14개는 무검증이었다. 메서드마다 `verify()`를 부르는 대신 **경로 인터셉터**로 옮겼다. 호출자가 오픈API 하나뿐인 하위 경로만 좁혀 걸었으므로 skix-security·medcare·streaming은 영향이 없고, 그 사실을 테스트로 고정했다.
- `skix-openapi` `9ba32fa`: 상류 호출 16곳(REST 12 · 웹훅 폴링 3 · WS 푸시 1) 중 3곳만 키를 싣고 있었다. 호출부마다 붙이는 대신 `NamuhxClientConfig`의 WebClient 필터 **한 곳**으로 옮겼다. 스케줄러 4곳이 특히 위험했다 — 백그라운드라 빠뜨려도 즉시 안 드러나고 웹훅 전송이 조용히 멈춘 뒤에야 알게 된다.

**배포 순서 제약**: `skix-openapi`의 키 부착(`9ba32fa`)이 `backend-api-main`의 검증(`00b10674`)보다 **먼저** 배포돼야 한다. 반대로 하면 dev·stg가 즉시 죽는다(그 두 환경은 backend `enforce-api-key`가 이미 true). 운영계는 `false`라 순서를 어겨도 로그만 남는다.

---

## 4. API 계약

### 4-1. identity 조회 (backend 제공 → 오픈API 소비)

```http
GET {backend}/api/internal/user/identity?sub=<로그인 sub>
X-Internal-Api-Key: <키 B>
```

| 응답 | 의미 | 오픈API의 해석 |
|---|---|---|
| `200 {"ownerKey":"<canonical sub>","aliases":["...","..."]}` | 매핑 있음 | `ownerKey`를 포털 키로 사용 |
| `404` | **매핑 없음(정상 데이터)** — `user_accounts` 미등재 | raw sub 폴백. 현행 동작 유지 |
| `400` | `sub` 누락 | — |
| `401` | 공유키 불일치(backend enforce=true 환경) | raw 폴백 + WARN |

`ownerKey`는 항상 `aliases`에 포함된다. `aliases` 순서는 canonical 선정 순서 그대로다.

이 API는 PII를 담지 않는다(sub는 불투명 가명). 그래도 공유키를 검증하는 이유는 **"어떤 계정들이 같은 사람인가"라는 연결 정보 자체**가 보호 대상이기 때문이다.

### 4-2. 탈퇴 파기 (오픈API 제공 → backend 소비)

```http
POST {openapi}/internal/v1/users/{sub}/purge?apply=true
X-Internal-Api-Key: <키 A>
```

- `{sub}`는 **raw sub**다. canonical 해석을 하지 않는다 — 호출측이 후보 sub 전체를 순회하고 오픈API는 받은 키 그대로 지운다.
- `apply=false`(기본값)는 **dry-run**. 읽기 전용으로 대상 건수만 센다.
- 이 EP는 `verifyEnforced`를 쓴다. **`APP_INTERNAL_ENFORCE_API_KEY` 값과 무관하게 항상 키를 강제**한다.

응답은 부분 실패여도 **HTTP 200**이다. 판정은 본문의 `success` 필드로 한다.

```json
{
  "success": false,
  "applied": true,
  "permissionDeleted": 1,
  "clientsRevoked": 2,
  "webhooksDeactivated": 3,
  "failures": [ { "clientId": "abc123", "stage": "TOKEN_REVOKE", "errorCode": "REDIS_UNAVAILABLE" } ]
}
```

| `stage` | 단계 |
|---|---|
| `PERMISSION_DELETE` | 권한 행 삭제 |
| `CLIENT_LOOKUP` | 대상 클라이언트 조회 |
| `CLIENT_REVOKE` | 클라이언트 `REVOKED` 전환 |
| `TOKEN_REVOKE` | 발급 토큰 폐기(Redis) |
| `WEBHOOK_DEACTIVATE` | 웹훅 비활성화 |

건수는 "이번 호출이 처리한 대상 수"다. 멱등 재호출이면 같은 대상을 다시 처리하므로 건수가 반복될 수 있고, 대상이 아예 없으면 전부 0에 `success=true`다. `errorCode`는 예외 클래스명 또는 내부 ErrorCode 이름만 나가고 원본 예외 메시지는 응답에 싣지 않는다(상세는 서버 로그).

### 4-3. 공유키가 걸린 오픈API 내부 EP 전수 (16개)

`InternalEndpointSecurityTest`가 이 목록을 고정한다. **새 내부 EP를 추가하면 반드시 이 목록에도 추가한다.**

```
GET    /internal/v1/permissions
GET    /internal/v1/permissions/{userId}
POST   /internal/v1/permissions/{userId}/enable
POST   /internal/v1/permissions/{userId}/disable
POST   /internal/v1/clients?userId=...
GET    /internal/v1/clients
GET    /internal/v1/clients/{clientId}
POST   /internal/v1/clients/{clientId}/revoke
POST   /internal/v1/clients/{clientId}/rotate
PUT    /internal/v1/clients/{clientId}/plan?planId=...
GET    /internal/v1/plans
POST   /internal/v1/plans
PUT    /internal/v1/plans/{planId}
PATCH  /internal/v1/plans/{planId}/deprecate
DELETE /internal/v1/plans/{planId}
POST   /internal/v1/users/{userId}/purge          ← 항상 강제
```

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **정규화 실패는 전부 raw sub 폴백이다.** 404·타임아웃·5xx·플래그 OFF 어느 경우든 예외를 던지지 않고 현행 동작으로 계속한다 | "정합성을 위해" 실패 시 401/403을 내면, backend나 identity API가 흔들릴 때 **포털이 통째로 잠긴다.** 원래 해결하려던 문제가 락아웃이었는데 더 넓은 락아웃을 만드는 셈이다. 정규화는 개선이지 필수 경로가 아니다 |
| 2 | **`404`는 오류가 아니라 정상 데이터다.** `retrieve()` 대신 `exchangeToMono`를 쓰는 이유가 이것이다 | `retrieve()`는 4xx를 예외로 만든다. 그러면 백필 미도달 사용자(운영계 1,222명)가 전부 WARN 로그를 쏟고, 정상 폴백과 실제 장애가 로그에서 구분되지 않는다 |
| 3 | **정규화는 Cognito 검증을 전부 통과한 sub에만 적용한다** (서명·iss·`token_use=id`·`aud` 뒤에 둔다) | 검증 전에 두면 **미인증 요청 한 줄이 내부 API 호출을 무제한 증폭**시킨다. 같은 파일의 JWKS 재조회 제한(5분)이 막으려는 것과 동일한 공격면이다 |
| 4 | **공용 `namuhx` 서킷브레이커를 쓰지 않는다.** 타임아웃 5초 + 폴백만 | identity 실패는 폴백으로 무해한데, 공용 CB에 묶으면 그 실패가 **건물·기기 API를 30초 차단**한다. 비대칭이 너무 크다. 전용 CB는 실제 장애가 관측되면 그때 넣는다 |
| 5 | **canonical 선정 규칙은 `SubIdentityResolver` 한 곳만 안다.** 오픈API는 규칙을 모르고 결과만 받는다 | 규칙(`create_date`,`id` 오름차순 첫 행)을 두 곳이 구현하면 언젠가 갈라진다. 갈라지는 순간 **재키잉한 DB 행과 런타임 판정이 어긋나** 조용한 403이 돌아온다 |
| 6 | **`user_accounts`는 활성 사용자 생명주기 안에서 append-only다** — 연동·자가치유는 INSERT만, 삭제는 탈퇴 시 전량. 그래서 한 번 정해진 canonical은 바뀌지 않는다 | 이 불변이 **재키잉(DB 일괄 변경)과 런타임 판정을 잇는 유일한 근거**다. 중간 행을 지우거나 `create_date`를 갱신하는 코드를 넣으면 canonical이 움직이고, 이미 재키잉된 행이 다시 고아가 된다 |
| 7 | **탈퇴 파기의 후보 sub에 `user.sub`(레거시)를 포함한다.** medcare 파기보다 1종 넓다 | 오픈API 권한이 `user.sub`로 키잉되던 시기의 행은 `user_accounts`에 없는 sub로 남아 있을 수 있다. 빼면 그 행이 영원히 안 지워진다 |
| 8 | **파기에 `@Transactional`을 쓰지 않는다. 부분 성공이 계약이다** | 한 트랜잭션으로 묶으면 Redis 장애 하나로 권한 삭제까지 롤백된다. 단계 독립 + 멱등 재호출이 훨씬 복구가 쉽다 |
| 9 | **웹훅 비활성화는 토큰 폐기의 성패와 무관하게 반드시 실행한다** (별도 try/catch) | 웹훅 발송 조회(`findAllActive`)가 **클라이언트 status를 보지 않는다**(실측). 클라이언트가 `REVOKED`여도 비활성화 전까지 **탈퇴자 엔드포인트로 발송이 계속된다.** 토큰 폐기가 Redis 장애로 죽었다고 여기서 멈추면 그 발송이 멈추지 않는다 |
| 10 | **클라이언트 조회(`CLIENT_LOOKUP`)를 권한 삭제 이후의 독립 단계로 둔다** | 조회를 앞에 두고 예외를 그대로 던지면 **최선행 단계(권한 삭제)가 실행되지 않은 채 500**이 난다. 권한 삭제는 포털의 신규 자격증명 발급부터 막는 가장 중요한 단계다. 조회 실패는 500이 아니라 `failures`(200)로 돌려서 재호출이 남은 것을 처리하게 한다 |
| 11 | **파기 EP는 `verifyEnforced`로 항상 키를 강제한다.** 전역 `enforce` 플래그를 타지 않는다 | 파괴적 엔드포인트가 log-only 전환기 동안 무인증으로 열려 있게 된다. 사용자 데이터를 지우는 EP는 처음부터 닫힌 채 나가야 한다 |
| 12 | **파기 실패 로그에 sub를 남긴다** (다른 로그는 값을 안 남기는 규약인데 여기만 예외) | 파기 직후 `user_accounts`가 **hard delete** 된다. 그 순간 DB에서 그 사람의 sub 목록을 복원할 방법이 사라진다. **이 로그가 재시도의 유일한 작업 목록**이다 — 그래서 알람이 필수다(§8-1) |
| 13 | **탈퇴 파기 실패는 탈퇴를 막지 않는다** (log + continue) | 외부 연동 실패로 탈퇴가 거부되면 사용자가 탈퇴할 수 없게 된다. 기존 medcare 연동이 쓰는 정책과 같고, 이 정책 위에 이 표 12번의 로그+알람+재시도 런북이 얹혀 있다 |
| 14 | **공유키 검증을 전역 필터가 아니라 컨트롤러(오픈API) / 좁은 경로 인터셉터(backend)로 건다** | 오픈API에서 `/internal/**` 전역 필터로 걸면 `/vital`·WebSocket 경로까지 삼킨다. backend에서 `/internal/**` 전역으로 걸면 키를 보내지 않는 호출자 20여 개가 전부 죽는다. **`/internal/v1/openapi/**` 처럼 호출자가 하나뿐인 하위 경로로 좁혀야만** 안전하다 |
| 15 | **오픈API→backend 공유키는 WebClient 필터 한 곳에서 붙인다** (`set`으로) | 호출 지점이 16곳이고 그중 4곳이 스케줄러다. 호출부마다 붙이면 새 호출을 추가하는 사람이 규칙을 알아야 하고, 스케줄러는 빠뜨려도 **웹훅이 조용히 멈춘 뒤에야** 드러난다. `add`가 아니라 `set`인 이유는 헤더가 두 개가 되면 상류에서 쉼표로 합쳐져 검증에 실패하기 때문이다. 키가 비면 아예 안 붙인다 — 빈 값을 보내면 상류가 "키를 보냈는데 틀렸다"로 읽어 401이 된다 |
| 16 | **`enforce-api-key`를 SSM에 넣지 않는다** (프로필 yml 리터럴 유지) | `spring.config.import`로 들어오는 값이 `application.yml`보다 우선순위가 낮다. SSM에 넣으면 공통 yml의 `false`에 덮여 **조용히 무시**된다 |
| 17 | **운영계 R4는 `user_accounts` 백필 완료 전에는 금지** | 백필 전 운영계는 1,222명이 미등재다. 켜면 등재된 사람만 canonical로 움직이고 나머지는 폴백이라 **한 환경 안에 두 가지 키 체계가 공존**한다. 백오피스 enable 경로도 같은 조건으로 갈린다. 백필 게이트 하나가 양쪽을 함께 보호한다 |

---

## 6. 운영계 배포 순서 (R1~R5)

개발계·검증계는 이 순서로 완주했다. **운영계도 같은 순서를 따른다.** 환경 순서는 dev → stg → prd.

### 6-1. R1 — skix-openapi 배포 (다크, 기동 후 동작 무변경)

**선행: SSM 등록 + task-definition 개정.** env 4종이 `deploy/task-definition-prd.json`에 들어 있지만 `origin/main`에는 없으므로, 운영 승격 시 함께 올라간다.

⚠️ **순서 제약**: `secrets`의 `valueFrom`이 가리키는 SSM 파라미터가 없으면 **ECS 태스크 기동 자체가 실패**한다. SSM 등록을 마친 환경에만 새 task-definition을 배포한다.

SSM 등록 명령은 부록 E에 있다.

**배포 후 게이트**
1. 백오피스의 오픈API 화면(권한 조회·클라이언트 목록)이 정상 동작.
2. 오픈API 로그에 `Internal API key check (enforce=false) - present=false` 관측. backend가 아직 헤더를 안 붙이므로 **`false`가 정상**이다.

### 6-2. R2 — backend-api-main 배포

**선행 게이트**
1. 키 A가 `/backend-api-main/<env>/app.openapi.api.api-key`에 등록돼 있을 것. **파라미터 네이밍 형식은 기존 `app.myHealthCare.api.api-key`를 실측해 똑같이 맞춘다** — 형식이 어긋나면 프로퍼티가 미바인딩되어 조용히 빈 값이 된다.
2. R1의 env 4종 실설정 확인.

backend는 task-definition 변경 없이 재기동 시 `spring.config.import`가 SSM을 자동 로드한다.

**배포 후 게이트**
1. 백오피스 조회로 트리거 → 오픈API 로그가 `present=true, valid=true` **100%**.
2. 탈퇴 1건 실검증 — 확인 SQL은 부록 D의 검증 절.
3. **알람 설정**: backend ERROR 패턴 `openapi 탈퇴 파기 실패: stage=PURGE_REQUEST`. 이 로그가 파기 재시도의 유일한 작업 목록이라 알람이 없으면 유실된다.

### 6-3. R3 — 재키잉 (대상이 있을 때만)

S0 §C에서 `classification='REKEY'`가 **0건이면 전체 생략**한다. 개발계·운영계는 0건이라 생략, 검증계만 1건 수행했다(완료).

대상이 1건 이상인 환경은 **R2~R4를 단일 변경 창구**로 묶는다: R2 배포 → 백오피스 enable/disable 변경 동결 → 재키잉 apply → R4 플립 → smoke → 동결 해제. R2 이후 백오피스는 canonical로 조회·쓰는데 오픈API 행이 legacy면 권한이 비활성으로 보이거나 canonical 중복 행이 생기는 불일치 구간이 생기기 때문이다.

런북 전문은 부록 C.

### 6-4. R4 — 정규화 ON (`IDENTITY_NORMALIZE_ENABLED=true`)

**선행 게이트**
- 오귀속 재검(부록 B §A) 재실행 → `0 · 0 · 0 · 1`.
- **운영계 한정**: token-pii 트랙의 운영계 `user_accounts` 백필 완료(부록 B §D가 `sub_not_registered=0`) **+** 부록 B §C 재실행으로 `LEGACY_ONLY`(`24b8ed2c-…354be`)가 `OK_CANONICAL`로 바뀐 것 확인. 그 전엔 플립 금지.
- 플립 시 `deploy/task-definition-prd.json`의 값도 `true`로 함께 고친다(파일이 실상태를 반영한다는 규약).

**플립 후 검증 (교차 provider 실검증이 핵심)**
1. 복수 provider 계정으로 백오피스에서 권한 enable → **원 가입과 다른 provider로 포털 로그인** → 자격증명 발급·조회·로테이션 성공. 플립 전이라면 403이 났을 경로다. (개발계 후보: `user.id=617`, 계정 3개. dev는 Apple 없음. 검증계는 재키잉된 사용자 또는 전화 가입+카카오 연동 신규 시나리오)
2. 감사 로그의 `audit.user_id`가 canonical인지 확인.
3. backend를 내린 상태에서 포털 로그인 → WARN 폴백 + 현행 키로 동작(다운 아님) 확인. 개발계에서 1회만 한다.

**롤백**: env를 `false`로 되돌린다. 단 재키잉을 한 환경은 R4만 끄면 재키잉된 사용자가 다시 403이 된다 — 되돌리려면 부록 C-3(롤백)을 R4 OFF와 함께 수행한다.

### 6-5. R5 — 내부키 강제 ON (`APP_INTERNAL_ENFORCE_API_KEY=true`)

**선행 게이트**: log-only 로그가 `valid=true` 100%로 지속 + backend 구버전 태스크 드레인 완료.

**플립 후**: 키 없는 curl → `401`, 백오피스 전 기능 정상. 파기 EP는 R1부터 이미 강제였다.

### 6-6. 09-04 공유키 쌍의 배포 순서

R1~R5와 별개로 **반드시 지켜야 하는 순서**다.

```
skix-openapi 9ba32fa (키 부착)  →  backend-api-main 00b10674 (검증)
```

역순이면 backend `enforce-api-key`가 `true`인 환경(dev·stg)은 오픈API의 상류 호출이 **전부 401**로 죽는다. 운영계는 backend가 `false`라 역순이어도 로그만 남지만, 습관적으로 순서를 지킨다.

### 6-7. 배포 명령과 함정

`skix-openapi`는 CI 파이프라인이 없고 스크립트 2개로 배포한다.

```bash
# 로컬: 이미지 빌드 + 아카이브 (Dockerfile 의 -Duser.timezone=UTC 유무를 스크립트가 검사한다)
./deploy/build-export.sh          # → openapi.tar.bz2

# 환경계정 CloudShell: 아카이브 업로드 후
./cloudshell-push.sh prd          # dev | stg | prd
```

⚠️ **함정**: `cloudshell-push.sh`는 이미지를 ECR에 올린 뒤 **현재 AWS에 등록돼 있는 최신 활성 태스크 정의**를 그대로 재사용한다. 저장소의 `deploy/task-definition-*.json`을 **등록하지 않는다.** 따라서 env를 바꾸는 배포(= 이 트랙의 R1·R4·R5 전부)는 그 전에 리비전을 직접 등록해야 한다.

```bash
aws ecs register-task-definition --region ap-northeast-2 \
  --cli-input-json file://deploy/task-definition-prd.json
# 그 다음에 ./cloudshell-push.sh prd
```

이 단계를 빠뜨리면 이미지만 새것이고 **플래그는 옛 값 그대로**인 채 배포가 "성공"한다.

### 6-8. 롤백

| 대상 | 방법 |
|---|---|
| 정규화 | `IDENTITY_NORMALIZE_ENABLED=false` + 태스크 정의 재등록·재배포. 코드 롤백 불필요 |
| 내부키 강제 | `APP_INTERNAL_ENFORCE_API_KEY=false`. 재배포 없이 값만 내리면 된다 |
| 재키잉 | 부록 C-3. **`WHERE user_id='<canonical>'` 전체를 되돌리면 안 된다**(이유는 부록 C에) |
| 파기 | 되돌릴 수 없다. 그래서 dry-run(`apply=false`)이 있다 |
| 애플리케이션 | 이전 이미지. 신규 EP 추가와 플래그 OFF 기본값이라 기존 기능 영향 없음 |
| DB | DDL 변경이 없다. 되돌릴 스키마가 없다 |

---

## 7. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| "권한을 받았다는데 포털에서 403" | 그 사람의 오픈API 키가 legacy인지 확인한다. 부록 B §C를 해당 환경에서 실행해 그 sub의 `classification`을 본다. `REKEY`면 부록 C, 정규화가 꺼져 있으면 §6-4 |
| 정규화가 실제로 먹고 있는지 | 오픈API 로그에 `identity normalize fallback:` 이 **안 보이면** 정상 해석. `[REDACTED:null-owner]`는 백필 미도달(정상 폴백), `[REDACTED:upstream-error]`는 identity API 장애(WARN, 폴백 동작) |
| 공유키 롤아웃 진행도 | 오픈API 로그 `Internal API key check (enforce=false) - present=?, valid=?`. `present=true, valid=true`가 100%가 되면 강제 전환 가능 |
| 강제 후 401이 난다 | 키 A가 양쪽(오픈API `APP_INTERNAL_API_KEY` / backend `app.openapi.api.api-key`)에 **같은 값**인지. 부록 E의 diff 명령으로 대사 |
| 탈퇴자 데이터가 남아 있다 | backend ERROR 로그에서 `openapi 탈퇴 파기 실패: stage=PURGE_REQUEST sub=<sub>` 를 찾아 그 sub로 파기 API를 재호출(부록 D). **로그가 유일한 작업 목록**이다 |
| 파기했는데 `success=false` | `failures[].stage`·`errorCode`를 본다. `REDIS_UNAVAILABLE`(stage=`TOKEN_REVOKE`)이면 Redis 회복 후 재호출. **그 상태에서도 클라이언트는 REVOKED(신규 발급 차단)이고 웹훅은 이미 비활성**이다. 기존 토큰의 잔존 상한은 만료(1시간)다 |
| "그때 어느 provider로 로그인했나" | 감사행에 **남지 않는다.** `audit.user_id`는 canonical이다. 연결된 provider별 sub는 backend `user_accounts`에서 조회 가능하지만 활성 사용자에 한한다 |
| 오픈API 내부 EP가 외부에서 열려 있는지 | 실측(2026-08-03)으로 `https://openapi.namuhx.com/internal/v1/clients`가 **공개 인터넷에서 200**이었다. 개발계도 동일. 운영계는 아직 `enforce=false`라 애플리케이션 방어선도 없다 — §8-3의 최우선 항목 |
| 새 내부 EP를 추가했다 | 컨트롤러에서 `internalApiKeyVerifier.verify(apiKey)`를 호출하고 **`InternalEndpointSecurityTest`의 목록에도 추가**한다. 목록에 없으면 회귀가 안 잡힌다 |

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **개발계·검증계에 09-04 공유키 쌍이 배포됐는지, 순서대로 됐는지 확인.** 두 저장소 dev·stg 브랜치에는 들어 있으나 ECS 반영은 미확인이다. 오픈API가 먼저여야 한다(§6-6) | 백엔드 | VDI에서 두 서비스의 활성 태스크 정의 이미지 태그·배포 시각을 본다. 순서가 어긋났으면 오픈API 로그에 상류 401이 보인다 |
| 2 | **운영계 진입 조건 현재값 확인**(§8-2 체크리스트). 특히 token-pii 운영계 백필 상태 | 백엔드 | 부록 B §D 한 줄로 확인 가능 |
| 3 | **파기 실패 알람이 실제로 걸려 있는지 확인.** 없으면 건다 | 백엔드 | 패턴: backend ERROR `openapi 탈퇴 파기 실패: stage=PURGE_REQUEST`. R2 게이트로 요구했으나 설정 여부 미확인 |
| 4 | 개발계·검증계 플래그 실상태가 저장소 파일과 맞는지 대사 | 백엔드 | 저장소는 dev·stg 둘 다 `true/true`로 고정돼 있다. 어긋나면 파일이 아니라 **실상태를 기준으로** 파일을 고친다 |

### 8-2. 운영계 릴리스 진입 조건 체크리스트

**전부 충족 전에는 운영계 플립(R4·R5) 금지.** R1·R2는 아래 1~4만 충족하면 진행할 수 있다(다크 배포라 동작이 바뀌지 않는다).

| # | 조건 | 확인 방법 | 현재 |
|---|---|---|---|
| 1 | 두 저장소 `main` 승격 | `git merge-base --is-ancestor <hash> origin/main` | **미충족**(두 저장소 모두 미반영) |
| 2 | 운영계 SSM에 키 A 등록(`/skix-openapi/prd/APP_INTERNAL_API_KEY` **와** `/backend-api-main/prd/app.openapi.api.api-key`가 같은 값) | 부록 E의 diff 명령 `KEY_A MATCH` | **미확인** |
| 3 | 운영계 SSM에 키 B 복사(`/skix-openapi/prd/NAMUHX_INTERNAL_API_KEY` = backend 기존 공유키) | 부록 E의 diff 명령 `KEY_B MATCH` | **미확인** |
| 4 | 운영계 task-definition 리비전 등록(env 4종 포함) | `aws ecs describe-task-definition --task-definition skix-openapi-prd-tdef` 의 environment·secrets | **미충족**(`main`에 env가 없다) |
| 5 | R1 배포 후 `present=false` 로그 확인 | 오픈API 운영 로그 | — |
| 6 | R2 배포 후 `present=true, valid=true` 100% | 오픈API 운영 로그 | — |
| 7 | 파기 실패 알람 설정 | CloudWatch 로그 메트릭 필터 | **미확인** |
| 8 | **token-pii 트랙의 운영계 `user_accounts` 백필 완료** | 부록 B §D → `sub_not_registered = 0` | **미충족**(2026-08-27 실측 1,222) |
| 9 | 백필 후 `LEGACY_ONLY` 해소 확인 | 부록 B §C 재실행 → `24b8ed2c-e041-7036-5ab9-89c80da354be`가 `OK_CANONICAL` | **미충족**(8번 선행) |
| 10 | 오귀속 재검 재실행 통과 | 부록 B §A → `0 · 0 · 0 · 1` | 2026-08-27 통과. **플립 직전 재실행 필요** |
| 11 | 재키잉 대상 재확인 | 부록 B §C → `REKEY` 건수 | 2026-08-27 기준 0건. 그 사이 권한 부여가 있었다면 다시 센다 |
| 12 | R5 전: 구버전 backend 태스크 드레인 완료 | ECS 배포 이력 | — |

8번(백필)이 실질적인 최대 게이트다. token-pii 트랙의 운영계 작업은 **운영 배포일자에 S0~S3을 묶어서** 수행하도록 설계돼 있고, 백필은 그 S2다. 즉 이 트랙의 운영계 R4는 그 날짜에 종속된다.

### 8-3. 후속 (이 기능을 막지는 않음)

| # | 항목 | 근거 |
|---|---|---|
| 13 | **오픈API `/internal/v1/**`의 공개 노출 차단**(앞단 경로 규칙). 실측으로 공개 인터넷에서 `200`이 확인된 상태다. 인증 없이 클라이언트 목록 조회·자격증명 발급(평문 시크릿)·시크릿 로테이션·임의 권한 부여가 가능하다. 이번 작업의 공유키는 **애플리케이션 2차 방어선**이라 운영계처럼 `enforce=false`인 환경에서는 아무것도 막지 못한다 | 인프라 변경이라 별도 작업으로 잡혀 있다. 또 `/internal/**` 호출은 앱 감사 로그에 남지 않으므로(감사 필터가 `/v1/` 외를 skip) 침해 확인 단서는 앞단 액세스 로그뿐이다 |
| 14 | `IdentityResolver`에 **쓰이지 않는 필드**가 남아 있다(`namuhx.internal.api-key`를 주입받지만 09-04에 헤더 부착을 WebClient 필터로 옮기면서 사용처가 사라졌다). 제거 | `IdentityResolver.java` 39·43·46행 |
| 15 | identity API 전용 서킷브레이커 도입 여부 판단. 지금은 의도적으로 없다(§5의 4번) | 실제 장애가 관측되면 그때 |
| 16 | 정규화 결과 캐시. 지금은 없다 — 포털 트래픽이 극소(허용 개발자 소수)라는 전제다 | 파트너가 늘면 재검토 |
| 17 | 재키잉 런북의 "실행 기록" 표를 실제 실행값으로 채워 보관. 검증계 1건이 비어 있다 | 부록 C-4 |

---

## 부록 A. 핵심 커밋 (시간순)

| 날짜 | 저장소 | 커밋 | 내용 |
|---|---|---|---|
| 08-27 | skix-openapi | `2c444ba` | 포털 sub 정규화(`IdentityResolver`+`CognitoJwtFilter`) · 내부 EP 15개 공유키 검증(log-only 시작) · 탈퇴 파기 EP 신설 |
| 08-27 | skix-openapi | `769707a` | 리뷰 반영 — 파기의 클라이언트 조회를 권한 삭제 뒤 독립 단계로 분리, 재키잉 롤백 절차 정밀화 |
| 08-27 | backend-api-main | `ee2b1fc5` | enable/조회/발급 경로를 canonical로 정규화(미등재면 `user.sub` 폴백) · 오픈API 호출 ~15개소에 `X-Internal-Api-Key` 부착 · 탈퇴 체인에 `purgeOpenApiOnWithdraw` |
| 08-27 | skix-openapi | `0a08a5f` | 작업 문서 2종을 저장소에서 제거 |
| 08-27 | skix-openapi | `e8bffe8` | task-definition 3환경에 내부키 secrets 2종 + 플래그 2종 추가 |
| 08-27 | skix-openapi | `e8db0f6` | dev·stg task-definition 플래그를 R4·R5 완료 실상태로 고정(prd는 false 유지) |
| 09-04 | skix-openapi | `9ba32fa` | 상류 호출 16곳 전체에 공유키를 WebClient 필터 한 곳에서 부착 |
| 09-04 | backend-api-main | `00b10674` | `/internal/v1/openapi/**` 18개 EP 전체를 경로 인터셉터로 검증(종전 4개만) |

---

## 부록 B. S0 측정 SQL (실행 가능 전문)

`backend-api-main`과 오픈API는 **같은 Aurora 인스턴스의 다른 스키마**이므로 한 세션에서 전부 실행한다. main 스키마를 기본 스키마로 잡고(`USE airbotdev;` 또는 `USE benjamin;`) 실행한다. 실행 대상은 dev / stg / prd 각각.

- main 스키마: dev `airbotdev`, stg·prd `benjamin`.
- 오픈API 스키마: 아래 `OPENAPI_SCHEMA` 두 곳을 실제 이름으로 치환한다(로컬 기본은 `openapi`, 실환경은 SSM `/skix-openapi/<env>/DB_URL`로 확인).
- **COLLATE 주의**: `user_accounts.sub`는 `utf8mb4_general_ci`, `user.sub`는 DB 기본(`utf8mb4_0900_ai_ci`)이다. 오픈API 쪽 `user_id`의 collation은 미실측이라 비교마다 명시 COLLATE를 붙였다. `ERROR 1267`이 나면 실제 컬럼 collation을 확인해 맞춘다(추측 금지).

### B-1. 오픈API 스키마 전수 스냅샷 (기록용)

분류·매핑은 B-2 §C가 교차 JOIN으로 직접 하므로, 여기는 원본 스냅샷을 남기는 것이 목적이다. 행수는 극소(허용 개발자 소수) 전제다.

```sql
-- ① 권한 전수 (user_id = Cognito sub, PK)
SELECT user_id, enabled, enabled_at, enabled_by, created_at, updated_at
FROM open_api_user_permissions
ORDER BY created_at;

-- ② 클라이언트 전수 (user_id 비유니크 — 1인 다건 여부도 여기서 드러난다)
SELECT client_id, user_id, status, plan_id, created_at
FROM open_api_clients
ORDER BY created_at;
```

### B-2. main 스키마 기준 판정 쿼리 4종

```sql
-- ═══════════════════════════════════════════════════════════════════════════
-- §A. 게이트③ — sub 오귀속 재검
-- 기대: preempted=0 · duplicated=0 · dup_account_sub=0 · unique_index_on_sub_only>=1
-- 어긋나면 정규화 플립(R4) 금지. user_accounts 자체가 오염된 상태이므로
-- canonical 해석의 전제가 깨진다.
-- ═══════════════════════════════════════════════════════════════════════════
SELECT
    (SELECT COUNT(*) FROM `user` u
      WHERE u.delete_yn = 'N' AND u.sub IS NOT NULL
        AND EXISTS (SELECT 1 FROM user_accounts ua
                     WHERE ua.sub = u.sub COLLATE utf8mb4_general_ci
                       AND (ua.user_id IS NULL OR ua.user_id <> u.id))
    )                                                                AS preempted,
    (SELECT COUNT(*) FROM `user` u
      WHERE u.delete_yn = 'N' AND u.sub IS NOT NULL
        AND EXISTS (SELECT 1 FROM `user` u2
                     WHERE u2.id <> u.id AND u2.sub = u.sub AND u2.delete_yn = 'N')
    )                                                                AS duplicated,
    (SELECT COUNT(*) FROM (
        SELECT sub FROM user_accounts WHERE sub IS NOT NULL
         GROUP BY sub HAVING COUNT(*) > 1) x
    )                                                                AS dup_account_sub,
    (SELECT COUNT(*) FROM (
        SELECT index_name FROM information_schema.statistics
         WHERE table_schema = DATABASE() AND table_name = 'user_accounts'
           AND non_unique = 0
         GROUP BY index_name
        HAVING COUNT(*) = 1 AND MAX(column_name) = 'sub') z
    )                                                                AS unique_index_on_sub_only;

-- ═══════════════════════════════════════════════════════════════════════════
-- §B. 게이트② — user.sub ≠ canonical 인 활성 사용자 수 (정보값, 게이트 아님)
-- canonical = 같은 user_id 의 user_accounts 중 create_date, id 오름차순 첫 행.
-- 이 규칙은 backend 의 SubIdentityResolver 와 반드시 동일해야 한다 — 규칙 원본은 그 클래스다.
-- ═══════════════════════════════════════════════════════════════════════════
SELECT COUNT(*) AS legacy_ne_canonical
FROM `user` u
JOIN user_accounts c
  ON c.user_id = u.id
 AND c.id = (SELECT ua2.id FROM user_accounts ua2
              WHERE ua2.user_id = u.id
              ORDER BY ua2.create_date, ua2.id LIMIT 1)
WHERE u.delete_yn = 'N'
  AND u.sub IS NOT NULL
  AND c.sub <> u.sub COLLATE utf8mb4_general_ci;
-- 상세가 필요하면 SELECT 절을 u.id, u.login_id, u.sub, c.sub 로 바꿔 실행한다.

-- ═══════════════════════════════════════════════════════════════════════════
-- §C. 게이트①·④ — 오픈API sub 분류
--   REKEY        : 활성 사용자인데 오픈API 키가 canonical 이 아님 → 재키잉 대상
--   OK_CANONICAL : 이미 canonical 키
--   LEGACY_ONLY  : user_accounts 미등재 활성 사용자의 user.sub 키 — 백필 후 자연 해소(고아 아님)
--   ORPHAN       : 활성 사용자 어디에도 안 잡힘 — 과거 탈퇴자 잔존 추정 → 파기 1회
-- `OPENAPI_SCHEMA` 두 곳을 실제 스키마명으로 치환할 것.
-- ═══════════════════════════════════════════════════════════════════════════
SELECT
    s.sub                                                AS openapi_user_id,
    s.sources,
    au.id                                                AS active_user_id,
    (SELECT ua2.sub FROM user_accounts ua2
      WHERE ua2.user_id = au.id
      ORDER BY ua2.create_date, ua2.id LIMIT 1)          AS canonical_sub,
    (SELECT COUNT(*) FROM user_accounts x
      WHERE x.user_id = au.id)                           AS account_cnt,
    CASE
      WHEN au.id IS NOT NULL
       AND (SELECT ua2.sub FROM user_accounts ua2
             WHERE ua2.user_id = au.id
             ORDER BY ua2.create_date, ua2.id LIMIT 1)
           <> s.sub COLLATE utf8mb4_general_ci           THEN 'REKEY'
      WHEN au.id IS NOT NULL                             THEN 'OK_CANONICAL'
      WHEN EXISTS (SELECT 1 FROM `user` lu
                    WHERE lu.delete_yn = 'N'
                      AND lu.sub = s.sub COLLATE utf8mb4_0900_ai_ci)
                                                         THEN 'LEGACY_ONLY'
      ELSE 'ORPHAN'
    END                                                  AS classification
FROM (
    SELECT user_id AS sub, GROUP_CONCAT(DISTINCT src) AS sources
    FROM (
        SELECT user_id, 'permission' AS src FROM OPENAPI_SCHEMA.open_api_user_permissions
        UNION ALL
        SELECT user_id, 'client'    AS src FROM OPENAPI_SCHEMA.open_api_clients
    ) u
    GROUP BY user_id
) s
LEFT JOIN user_accounts ua ON ua.sub = s.sub COLLATE utf8mb4_general_ci
LEFT JOIN `user` au        ON au.id = ua.user_id AND au.delete_yn = 'N';

-- ═══════════════════════════════════════════════════════════════════════════
-- §D. 게이트⑤ — user_accounts 백필 상태 (운영계 R4 의 선행 게이트)
-- sub_not_registered = 0 이면 백필 완료. > 0 이면 운영계 정규화 플립 금지.
-- dev·stg 는 0 이어야 정상이다.
-- ═══════════════════════════════════════════════════════════════════════════
SELECT COUNT(*) AS sub_not_registered
FROM `user` u
WHERE u.delete_yn = 'N' AND u.sub IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM user_accounts ua
                   WHERE ua.sub = u.sub COLLATE utf8mb4_general_ci);
```

### B-3. Cognito 허용 provider 확인

포털 앱 클라이언트가 어떤 로그인 수단을 열고 있는지 = 락아웃이 실제로 도달 가능한지 판정한다.

| 환경 | user-pool-id | client-id |
|---|---|---|
| dev | `ap-northeast-2_DqWqiuAGs` | `6qjm7prgu92dv1ec6p2bc1qude` |
| stg | `ap-northeast-2_xqiZK4qBs` | `2rtsm3qcb3vd7pr3cbqs0tjmk9` |
| prd | `ap-northeast-2_emKW4fu8G` | `2v9rlu5de4ovrbgu2hh69fq23m` |

```bash
# 환경계정 AssumeRole 후 실행. 로컬 맥은 API 도달만 가능하므로 VDI에서 수행한다.
aws cognito-idp describe-user-pool-client \
  --user-pool-id <pool> --client-id <client> \
  --query 'UserPoolClient.SupportedIdentityProviders'
```

결과가 단일 provider여도 **작업 축소의 근거가 되지 않는다** — "원 가입 provider ≠ 포털 로그인 수단"인 사용자에게 락아웃은 동일하게 발생한다. 이 값은 발생 빈도를 해석하는 데만 쓴다.

---

## 부록 C. 재키잉 런북 (legacy sub → canonical)

**조건부 실행**: 부록 B §C에서 `classification='REKEY'`가 **0건이면 이 런북 전체를 생략**한다.

**실행 시점 제약**: R2(backend canonical enable 배포) 이후, R4(정규화 ON) 이전. 대상이 1건 이상이면 R2→R4를 단일 변경 창구로 묶는다 — R2 배포 → 백오피스 enable/disable 변경 일시 동결 → 이 런북 apply → R4 플립 → smoke → 동결 해제. (R2 이후 백오피스는 canonical로 조회·쓰는데 오픈API 행이 legacy면, 권한이 비활성으로 보이거나 canonical 중복 행이 생기는 불일치 구간이 생긴다.)

### C-1. dry-run — 대상·충돌 확정

부록 B §C 결과의 `(legacy = openapi_user_id, canonical = canonical_sub)` 쌍을 대상 목록으로 쓴다. 쌍마다:

```sql
-- 대상 행 현황 (실행 전 값 — 역-UPDATE 의 근거이므로 출력을 그대로 보관한다)
SELECT 'permissions' AS t, user_id, enabled FROM open_api_user_permissions
 WHERE user_id IN ('<legacy>', '<canonical>');
SELECT 'clients' AS t, client_id, user_id, status FROM open_api_clients
 WHERE user_id IN ('<legacy>', '<canonical>');
```

**PK 충돌 검사**: `open_api_user_permissions`에 legacy 행과 canonical 행이 **둘 다** 있으면 UPDATE가 PK 충돌한다. 해소 규칙은

- canonical 행 유지, legacy 행 삭제.
- 단 두 행의 `enabled` 값이 다르면 **자동 병합 금지**. 어느 쪽이 운영 의도인지 수동 판단 후 진행한다(백오피스 부여 이력·`enabled_by` 참고).

`open_api_clients.user_id`는 비유니크 인덱스라 충돌이 없다.

### C-2. apply — 쌍마다 한 트랜잭션

```sql
START TRANSACTION;
-- (C-1 에서 충돌이 있었던 경우에만) legacy 권한 행 삭제
-- DELETE FROM open_api_user_permissions WHERE user_id = '<legacy>';

UPDATE open_api_user_permissions SET user_id = '<canonical>' WHERE user_id = '<legacy>';
UPDATE open_api_clients          SET user_id = '<canonical>' WHERE user_id = '<legacy>';

-- 건수 검증: dry-run 의 legacy 행 수와 일치해야 커밋
SELECT (SELECT COUNT(*) FROM open_api_user_permissions WHERE user_id = '<canonical>') AS perm_rows,
       (SELECT COUNT(*) FROM open_api_clients          WHERE user_id = '<canonical>') AS client_rows,
       (SELECT COUNT(*) FROM open_api_user_permissions WHERE user_id = '<legacy>')    AS perm_legacy_left,
       (SELECT COUNT(*) FROM open_api_clients          WHERE user_id = '<legacy>')    AS client_legacy_left;
-- perm_legacy_left = 0, client_legacy_left = 0 확인 후
COMMIT;
-- 어긋나면 ROLLBACK. 추측하지 않는다.
```

### C-3. 롤백

**되돌릴 대상은 apply가 실제로 옮긴 행뿐이다.** `WHERE user_id='<canonical>'` 전체를 되돌리면 ① 원래부터 canonical 키였던 행 ② R2 이후 백오피스가 새로 만든 canonical 행까지 legacy로 오염된다. C-1에서 보관해 둔 dry-run 출력(실행 전 값)이 "apply가 옮긴 행"의 유일한 명세다.

```sql
-- 클라이언트: dry-run 보관본에 legacy 키로 기록돼 있던 client_id 만 지정 복원.
UPDATE open_api_clients SET user_id = '<legacy>'
 WHERE client_id IN ('<보관본의 legacy client_id 목록>')
   AND user_id = '<canonical>';

-- permission, C-1 에서 PK 충돌이 없었던 경우:
-- canonical 행 = apply 가 개명한 legacy 행 하나뿐이므로 역-UPDATE 가 정확하다.
UPDATE open_api_user_permissions SET user_id = '<legacy>' WHERE user_id = '<canonical>';

-- permission, C-1 에서 충돌을 해소했던 경우:
-- canonical 행은 원래 canonical 소유이므로 **건드리지 않는다.**
-- 삭제했던 legacy 행만 보관본 값으로 재 INSERT 한다.
-- (위 역-UPDATE 를 함께 실행하면 canonical 원소유 행이 legacy 로 넘어간 뒤
--  재 INSERT 가 PK 충돌한다 — 두 문장은 케이스별 택일이다.)
-- INSERT INTO open_api_user_permissions (user_id, enabled, enabled_at, enabled_by)
-- VALUES ('<legacy>', <보관본 enabled>, <보관본 enabled_at>, '<보관본 enabled_by>');
```

롤백 후 정규화(R4)가 켜져 있으면 해당 사용자는 다시 403이다(legacy 키 ≠ canonical 조회). **롤백은 R4 OFF와 함께 간다.**

### C-4. 실행 기록

| 환경 | 실행일 | 쌍 수 | 충돌 | 결과 |
|---|---|---|---|---|
| dev | — | 0 | — | 대상 0건, 생략 |
| stg | 2026-08-27 | 1 | 없음 | legacy `5468cd5c-1011-7074-3716-dca8ec33d14f` → canonical `24087d6c-f001-703c-3dd1-d8c0bee3867b` (`user_id=9`, 권한+클라이언트). **dry-run 출력 보관본 미부착 — §8-3의 17번** |
| prd | — | — | — | 미수행(대상 0건이나 플립 직전 재확인 필요) |

---

## 부록 D. 탈퇴 파기 재시도 · 고아 행 정리 런북

대상 엔드포인트: `POST /internal/v1/users/{sub}/purge`. 코드는 오픈API `purge/service/UserPurgeService.java`, 호출측은 `backend-api-main` `UserService.purgeOpenApiOnWithdraw`.

### D-1. 언제 쓰나

1. **탈퇴 파기 실패** — backend ERROR 로그에 다음이 남았을 때.
   ```
   openapi 탈퇴 파기 실패: stage=PURGE_REQUEST sub=<실패한 sub>
   ```
   **이 로그가 재호출 대상의 유일한 작업 목록이다.** 탈퇴 직후 `user_accounts`가 hard delete 되므로 DB에서는 그 사람의 sub 목록을 복원할 수 없다. 그래서 이 패턴에 알람이 걸려 있어야 한다.
2. **과거 탈퇴자 잔존 행 정리** — 부록 B §C에서 `classification='ORPHAN'`으로 분류된 sub(파기 연동이 없던 시기의 잔존). 2026-08-27 기준 3환경 모두 0건이다.

### D-2. 절차 (멱등 — 몇 번을 다시 호출해도 안전)

부분 성공을 허용하고 재호출이 곧 재시도다. 남은 것부터 이어서 처리하고 이미 처리된 항목은 no-op이다.

```bash
# 내부 NLB(8084)를 통해서만 도달 가능하다. 키는 오픈API 의 APP_INTERNAL_API_KEY 값.
# 이 엔드포인트는 enforce 설정과 무관하게 항상 키를 강제한다.

# 1) dry-run — 남은 대상 확인 (읽기 전용)
curl -s -X POST "http://<오픈API 내부 NLB>:8084/internal/v1/users/<sub>/purge?apply=false" \
  -H "X-Internal-Api-Key: <키 A>"

# 2) apply
curl -s -X POST "http://<오픈API 내부 NLB>:8084/internal/v1/users/<sub>/purge?apply=true" \
  -H "X-Internal-Api-Key: <키 A>"
```

내부 NLB 호스트는 `backend-api-main`의 `app.openapi.api.url`과 같은 값이다(§3-3).

**응답 판정**: `success=true`면 완료. `false`면 `failures[{clientId, stage, errorCode}]`를 본다. 상세는 오픈API 서버 로그의 `탈퇴 파기 단계 실패: stage=... clientId=... errorCode=...`.

- `errorCode=REDIS_UNAVAILABLE` (`stage=TOKEN_REVOKE`): Redis·서킷 회복 후 재호출. **이 상태에서도 클라이언트는 REVOKED(신규 발급 차단)이고 웹훅은 이미 비활성화됐다.** 기존 토큰의 잔존 상한은 만료(1시간)다.
- DB 오류: DB 회복 후 재호출.

### D-3. 검증 SQL (오픈API DB)

```sql
-- 전부 0 행이어야 완료다. 단 clients 는 삭제가 아니라 REVOKED 전환이므로 status 로 본다.
SELECT * FROM open_api_user_permissions WHERE user_id = '<sub>';

SELECT client_id, status FROM open_api_clients
 WHERE user_id = '<sub>' AND status <> 'REVOKED';

SELECT w.webhook_id FROM open_api_webhooks w
  JOIN open_api_clients c ON c.client_id = w.client_id
 WHERE c.user_id = '<sub>' AND w.is_active = 1;

-- 활성 토큰 잔존(만료 전 + 미폐기)
SELECT t.jti FROM open_api_tokens t
  JOIN open_api_clients c ON c.client_id = t.client_id
 WHERE c.user_id = '<sub>' AND t.revoked = 0 AND t.exp > NOW();
```

### D-4. 감사 식별자 참고

정규화(R4) 이후 `audit.user_id`와 `open_api_clients.user_id`는 canonical sub다. **"그 시점에 어느 provider로 로그인했는가"는 감사행에 남지 않는다.** 필요하면 `backend-api-main`의 `user_accounts`로 canonical → provider별 sub를 역추적한다(활성 사용자에 한함).

---

## 부록 E. 공유키 SSM 등록 (환경마다 1회)

환경계정 CloudShell에서 실행한다. 계정: dev `010928196421` · stg `087432099373` · prd `779846811758`.

```bash
ENV=prd   # dev / stg / prd

# 0) backend 파라미터 네이밍 실측 — 형식이 어긋나면 조용히 빈 값이 바인딩되므로 필수다
aws ssm get-parameters-by-path --path /backend-api-main/$ENV/ --recursive \
  --query 'Parameters[].Name' --output table
MEDCARE_KEY_PARAM="/backend-api-main/$ENV/app.myHealthCare.api.api-key"  # ← 실측 결과로 맞춘다

# 1) 키 A 생성 (환경마다 다른 값)
KEY_A=$(openssl rand -hex 32)

# 2) 키 A — 오픈API 수신측
aws ssm put-parameter --name "/skix-openapi/$ENV/APP_INTERNAL_API_KEY" \
  --type SecureString --value "$KEY_A"

# 3) 키 A — backend 제시측 (같은 값)
aws ssm put-parameter --name "/backend-api-main/$ENV/app.openapi.api.api-key" \
  --type SecureString --value "$KEY_A"

# 4) 키 B 복사 (오픈API → backend 호출용). backend 의 기존 공유키 값을 그대로 옮긴다
KEY_B=$(aws ssm get-parameter --name "$MEDCARE_KEY_PARAM" --with-decryption \
          --query Parameter.Value --output text)
aws ssm put-parameter --name "/skix-openapi/$ENV/NAMUHX_INTERNAL_API_KEY" \
  --type SecureString --value "$KEY_B"

# 5) 검증 — 두 diff 모두 MATCH 가 나와야 다음 단계로 간다
diff <(aws ssm get-parameter --name "/skix-openapi/$ENV/APP_INTERNAL_API_KEY" --with-decryption --query Parameter.Value --output text) \
     <(aws ssm get-parameter --name "/backend-api-main/$ENV/app.openapi.api.api-key" --with-decryption --query Parameter.Value --output text) && echo "KEY_A MATCH"
diff <(aws ssm get-parameter --name "/skix-openapi/$ENV/NAMUHX_INTERNAL_API_KEY" --with-decryption --query Parameter.Value --output text) \
     <(aws ssm get-parameter --name "$MEDCARE_KEY_PARAM" --with-decryption --query Parameter.Value --output text) && echo "KEY_B MATCH"

unset KEY_A KEY_B
```

`--overwrite`는 일부러 넣지 않았다 — 파라미터가 이미 있으면 **실패하는 쪽이 안전**하다(기존 키를 덮어쓰면 그 키를 쓰는 모든 호출이 즉시 죽는다). 값을 바꿔야 한다면 의도적으로 `--overwrite`를 붙이되, 양쪽을 같은 창에서 함께 바꾼다.

SSM 등록만으로는 값이 주입되지 않는다.
- **오픈API**: task-definition 개정이 필요하다(`secrets` 2종 + 평문 플래그 2종). `deploy/task-definition-<env>.json`에 이미 들어 있으니 `aws ecs register-task-definition`으로 등록한 뒤 배포한다(§6-7).
- **backend-api-main**: task-definition 변경 없이 재기동 시 `spring.config.import`가 자동 로드한다.
