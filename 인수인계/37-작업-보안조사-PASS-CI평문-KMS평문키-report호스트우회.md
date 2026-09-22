# [작업] 보안점검 지적 3건 조사 — PASS CI 평문 왕복 · 보안영상 키 평문 전달 · report 호스트 API Gateway 우회

| 항목 | 내용 |
|---|---|
| **상태** | **조사 완료 · 조치 미착수.** 3건 모두 권고안까지 정리했고, 서버 코드 변경은 1건(PASS CI)만 있으며 그것도 협력사가 넣은 것이다 |
| **작업 기간** | 2026-09-07 (report 호스트 실측) ~ 2026-09-14 (PASS CI·KMS 조사) |
| **직접 수정한 저장소** | **없음.** 세 건 모두 조사·권고 단계다 |
| **다른 담당에게 넘긴 것** | PASS CI는 협력사 백엔드가 `backend-api-main` 에 직접 수정을 넣었다(§3-4). KMS·report 호스트는 아직 아무에게도 배정되지 않았다 |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §3-4의 **AXDO-2102 리뷰**. 협력사 수정이 이미 `dev`·`stg` 두 브랜치에 들어가 있고 테스트가 0건이며, **Android 가입자 전원의 OASYS 연동이 조용히 빠지는 회귀**가 있다(§3-4-3 P1). 다음이 §5-4의 nginx 한 줄 |

---

## 0. 세 줄 요약

1. 사내 보안점검에서 지적된 3건을 코드로 실측해 **무엇이 실제 위험이고 무엇이 아닌지**를 갈랐다. 세 건 다 "권고안 + 선행조건 + 담당"까지 정리했고, 인수자가 바로 착수할 수 있게 실행 절차와 검증 명령을 §6에 넣었다.
2. **PASS CI 평문 왕복**만 코드가 움직였다. 협력사 백엔드가 2026-09-14~15에 5개 커밋을 넣었고 지금 `dev`·`stg` 에 반영되어 있다. **방향은 권고안과 같지만, Android가 가입 본문에 `messageAuthCode` 를 보내지 않아 Android 가입이 전부 오류 없이 OASYS 미연동으로 끝난다. 테스트도 0건이다.** 서버만 고쳐서 푸는 방법이 있다(§3-4-4). 리뷰가 첫 과제다.
3. **보안영상 키 평문 전달**은 AWS KMS로 바꿔도 지적이 해소되지 않는다(§4-3). **report 호스트 우회**는 nginx 한 줄로 끝나고 부작용이 없다(§5-4) — 세 건 중 비용 대비 효과가 가장 크다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **PASS / NICE 본인인증** | 휴대폰 본인확인 서비스. `backend-api-main` 이 회원가입·PIN 설정·OASYS 직접구독에 쓴다 |
| **CI (연계정보)** | 본인확인기관이 주민등록번호를 단방향 변환해 만든 88바이트 개인식별값. 기관이 달라도 같은 사람이면 같은 값이 나오므로 **사실상 주민번호를 대신하는 준식별자**다. 유출되면 서비스 간 동일인 추적이 가능해 법·규정상 취급 수준이 가장 높은 축에 든다 |
| **OASYS** | SK매직 계약·고객 시스템. HTTP 전문(인터페이스 ID별)으로 호출한다 |
| **R008** | OASYS 인터페이스 `IF_NMX_R008`. 통합고객 연동 전문. CI·이름·생년월일·성별을 실어 보내고 **통합고객번호(icstNo)** 를 받는다 |
| **SafeKey (skm_sfky)** | OASYS 고객 식별 키. R002로 발급받는다 |
| **messageAuthCode** | 원래는 SMS 인증번호를 담는 필드인데, PASS 인증을 거친 경우에는 **서버가 발급한 UUID** 가 여기에 들어간다. 가입 요청 본문에 실려 오며 서버가 `history_sms_auth_code` 테이블과 대조해 검증한다 |
| **DEK (Data Encryption Key)** | 콘텐츠 1건을 암호화하는 대칭키. 여기서는 보안영상·썸네일을 암호화한 AES-256 키 |
| **봉투 암호화 / wrap** | DEK 자체를 다시 공개키로 암호화해 보관하는 방식. 푸는 것이 unwrap |
| **RSA-OAEP** | RSA 공개키 암호의 패딩 방식. 해시 알고리즘과 MGF1 해시 두 가지를 맞춰야 양쪽이 호환된다 |
| **사내 KMS** | `skix-streaming-kms` 저장소의 Node/Express 서버. AWS KMS가 아니라 자체 구현이다 |
| **AWS KMS** | AWS 관리형 키 서비스. 개인키가 서비스 밖으로 나오지 않는다 |
| **API Gateway (API GW)** | AWS API Gateway. `app-api-*.namuhx.com` 앞단에서 Cognito Authorizer로 1차 인증을 한다 |
| **report 호스트** | `report-dev / report-stg / report(prd).namuhx.com`. 앱 안 WebView가 쓰는 웹 화면 호스트 |
| **`/internal/**`** | 서버 간 호출 전용 경로. 앱 인증 계층이 없고 네트워크 격리에 기댄다 |
| **AXDO-2102** | 협력사 이슈 번호. PASS CI 조치 커밋의 제목 접두사 |

### 1-2. 세 건의 공통 성격

세 건은 서로 다른 지적이지만 구조가 같다. **"인증·암호 자체는 있는데, 값이 지나가는 경로가 하나 더 있어서 그 경로에는 통제가 없다"** 는 형태다.

| # | 지적 | 통제가 없는 경로 |
|---|---|---|
| 1 | PASS 본인인증 CI 평문 왕복 | 서버가 이미 가진 CI를 **웹·앱을 한 바퀴 돌려** 다시 서버로 받는다. 서버는 돌아온 값을 검증하지 않는다 |
| 2 | 보안영상 암호화 키 평문 전달 | 영상은 AES-GCM으로 암호화하는데, **그 키를 푸는 API가 인증 없이 평문으로 내려준다** |
| 3 | report 호스트 API GW 우회 | 앱 호스트는 API Gateway를 지나지만, **report 호스트는 같은 백엔드를 게이트웨이 없이 통째로 프록시**한다 |

### 1-3. 조사 범위와 한계 — 먼저 읽을 것

- **보안점검 원문 문구를 확보하지 못했다.** 세 건 모두 "이렇게 지적받았다"는 전언에서 출발했고, 조사는 코드 실측으로만 했다. 따라서 **지적의 정확한 요구 수준(예: "평문 전달 금지"가 서버 구간을 말하는지 앱 화면까지를 말하는지)이 확정되지 않았다.** 착수 전 원문을 받아 권고안의 범위를 다시 맞춰야 한다. 이것이 세 건 공통의 첫 번째 선행 조건이다.
- 로컬 맥에서는 AWS 콘솔·CLI에 접근할 수 없다. ECS 배포 여부, NLB가 internal인지 internet-facing인지, 운영 KMS 서버의 `NODE_ENV` 실값은 **전부 미확인**이며 회사 VDI에서만 확인할 수 있다. 본문에서 미확인 항목은 그때마다 명시했다.
- 앱(iOS·Android)·펌웨어·인프라(nginx)는 다른 담당 영역이다. 이 문서는 서버 관점에서 "무엇을 요청해야 하는가"까지만 정리한다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 지적별 진행 상태

| # | 지적 | 조사 | 코드 변경 | 배포 | 누가 들고 있나 |
|---|---|---|---|---|---|
| 1 | PASS CI 평문 왕복 | 완료(09-14) | **있음 — 협력사 5커밋, `dev`·`stg` 반영** | **미확인**(ECS 배포 여부는 VDI에서 확인) | 협력사 백엔드가 작성, **리뷰 담당 없음** |
| 2 | 보안영상 키 평문 전달 | 완료(09-14) | 없음 | – | **미배정** |
| 3 | report 호스트 API GW 우회 | 완료(09-07 실측) | 없음 | – | **미배정**(인프라 협조 필요) |

### 2-2. 저장소별 반영 (지적 1 관련 커밋)

`backend-api-main` 의 AXDO-2102 커밋 5건 + 머지 5건. 전부 `origin/dev`·`origin/stg` 에 있고 `origin/main`(운영)에는 없다.

| 커밋 | 날짜 | 내용 | dev | stg | main |
|---|---|---|---|---|---|
| `3bfd5321` | 09-14 | CI를 HTTP 세션에 저장 | 반영 | 반영 | 미반영 |
| `bdd9a2bc` | 09-14 | 세션 확인용 로그 추가 | 반영 | 반영 | 미반영 |
| `633b2496` | 09-14 | **위 세션 방식 전체 원복** | 반영 | 반영 | 미반영 |
| `c7b20f20` | 09-15 | **최종안 — 가입 시 `messageAuthCode` 로 Redis에서 CI 조회** | 반영 | 반영 | 미반영 |
| `4dcbdc33` | 09-15 | 확인용 로그 삭제 | 반영 | 반영 | 미반영 |

- **중간 3건은 최종 형상이 아니다.** `3bfd5321`·`bdd9a2bc` 를 `633b2496` 이 되돌렸으므로, 지금 코드를 읽을 때는 `c7b20f20` 이후만 보면 된다. 구두 전달이나 이전 기록에 "세션 보관 방식"으로 남아 있다면 낡은 정보다.
- `origin/dev` 와 `origin/stg` 는 **커밋 차이 0** 이다(`git rev-list --count origin/stg..origin/dev` = 0, 09-18 머지). `origin/main` 은 `origin/stg` 보다 371 커밋 뒤에 있다.
- **테스트는 0건 추가되었다.** 이 커밋 범위가 건드린 파일은 `NiceAuthController.java`·`UserController.java`·`UserService.java` 셋뿐이고 `src/test` 는 하나도 없다. 그리고 이 경로들(`/app/user/pass/info`, `syncOasysOnJoin`)에 **기존 테스트도 없다** — `src/test` 전체에서 해당 이름으로 잡히는 파일이 0건이다. 즉 이 변경을 지켜주는 자동 검증이 전혀 없다.

### 2-3. 관련되지만 아직 안 나간 것

| 대상 | 상태 | 비고 |
|---|---|---|
| `backend-api-main` 로컬 브랜치 `backup/with-internal-key` (`c9ecdd78`, 08-23) | **원격에 없음(작성자 맥에만 있음, 미push)** | `/internal/security/check-member` 의 sub 경로에 공유키를 강제하는 변경(컨트롤러 18줄 + 테스트 113줄). 지적 3의 후속 트랙(§5-5) 출발점. **휴직 전 push되지 않으면 인수자는 받을 수 없다** — 그 경우 §5-5 설명대로 새로 만든다 |
| `backend-api-main` `00b10674` (09-04) | dev·stg 반영, main 미반영 | 내부 openapi 구간 공유키. 같은 패턴을 `/internal/security` 에도 적용하면 되는 선례 |

### 2-4. 타 팀 상태 (지적 1·2 관련, 각 저장소 원격 브랜치 실측)

| 대상 | 상태 |
|---|---|
| iOS | 가입 본문 `ci` 제거 커밋 `21186ce8`(09-15)이 `develop` 에만 있다. `main`·`release/*` 에는 원래 `ci` 가 없다. 가입 본문 `messageAuthCode` 는 모든 브랜치에 있다. 릴리스 os Logger가 본문을 평문 기록하는 문제는 미조치 |
| Android | 가입 본문 `ci` 가 `develop` 에만 있다(`524d6c57`, 07-23). **가입 본문 `messageAuthCode` 는 어느 브랜치에도 없다** — 지적 1 보완 설계를 좌우하는 사실(§3-4-3 P1) |
| 웹(`frontend-report-web`) | PASS 성공 페이지가 CI 포함 응답 전체를 콘솔에 찍고, 프로덕션 빌드도 콘솔을 제거하지 않는다. 미조치 |
| 사내 KMS(`skix-streaming-kms`) | 최신 커밋 `788d383`(06-15). 이번 지적 관련 변경 없음 |
| 스토어 배포 버전 ↔ 브랜치 대응 | **미확인.** 앱 담당에게 확인 |

---

## 3. 지적 1 — PASS 본인인증 CI가 평문으로 왕복한다

### 3-1. 원래 흐름 (서버 `main` 기준, AXDO-2102 이전)

```
[NICE]  본인인증 완료 → 콜백 GET /auth/pass/getAuthResult
[backend-api-main]
   ① NICE 결과(이름·생년월일·성별·전화번호·CI …) 를 Redis pass:{uuid} 에 3분 저장
   ② history_sms_auth_code 에 (전화번호, uuid) 기록 + Redis sms:verified:{전화번호} = true (180초)
   ③ 302 → report 웹 /pass/success?uuid=...

[report 웹 frontend-report-web  src/pages/passSuccess/index.vue]
   ④ GET /app/user/pass/info?uuid=...        ← permitAll, 응답에 ci 포함
   ⑤ console.log('Sent to AndroidBridge:', messageData)  ← CI 원문이 콘솔에
   ⑥ JS 브리지로 앱에 응답 전체 전달 (messageAuthCode = uuid 함께)

[앱]
   ⑦ 가입 화면에서 POST /app/user/join 또는 /app/my/social/signup 본문에 ci 를 다시 실어 보냄
      (실제로 싣는지는 앱 브랜치마다 다르다 — §3-2 표)

[backend-api-main  UserService.syncOasysOnJoin]
   ⑧ 본문의 ci·이름·생년월일·성별을 그대로 OASYS R008 에 전달 → 통합고객번호 수신
      (CI 는 DB 에 저장하지 않는다 — user 테이블에 ci 컬럼 없음)
```

### 3-2. 무엇이 문제인가 — 두 갈래

| 갈래 | 내용 | 운영계에서 실제로 일어나는가 |
|---|---|---|
| **노출** | 서버가 이미 가진 CI를 웹·앱으로 내려보낸다. 웹 콘솔, 앱 브리지, 앱 로그에 남을 수 있다 | **예.** 운영 `main` 의 `PassController.java` 338행이 `/app/user/pass/info` 응답에 `ci` 를 싣는다 |
| **무결성** | 서버는 가입 요청의 `ci` 를 검증하지 않는다. 검증하는 것은 전화번호 인증 여부(`sms:verified:{전화번호}`) 뿐이다. 따라서 **다른 사람의 CI·이름·생년월일을 본문에 넣으면 그대로 R008로 간다** — 남의 통합고객번호에 내 계정이 연결될 수 있다 | **앱 버전에 따라 다르다**(아래 표). 현재 앱 저장소 `main` 브랜치는 가입 본문에 `ci` 를 싣지 않는다 |

**노출이 실제로 남는 곳 (실측)**

| 위치 | 내용 | 릴리스 빌드에서도 남나 |
|---|---|---|
| 웹 `src/pages/passSuccess/index.vue` 41·44·46행 | `console.log('Sent to AndroidBridge:', messageData)` 등. `messageData.data` 가 응답 전체라 CI 원문 포함 | **예.** `vite.config.ts` 의 `esbuild.drop` 이 `mode === 'production' ? [] : []` — **주석은 "프로덕션에서 console 제거"인데 양쪽 분기가 모두 빈 배열이라 아무것도 제거하지 않는다**(dev·stg·main 세 브랜치 동일) |
| Android `presentation/.../ui/common/AndroidBridge.kt` 32행 | `Log.e("pass auth : ", "json : $json")` 수신 JSON 전체 | **아니오.** 앱 공통 `Log` 래퍼가 전 레벨 `if (DEBUG)` 게이트 |
| iOS `MagicIot/Core/Utils/Log.swift` 184~199행 | os Logger에 **전 레벨 `privacy: .public`, `#if DEBUG` 없음** | **예.** 소셜가입(`Api/AppMy.swift` 56~58행)은 `log_encrypt` 를 지정하지 않아 본문이 평문으로 기록된다. 릴리스 기기에서도 Console.app·sysdiagnose로 수집 가능 |
| iOS `MagicIot/Api/Api.swift` 18~23행 | 이메일가입은 `log_encrypt: true` 로 로그를 AES 암호화하지만 **키·IV가 소스에 문자열 리터럴**(뒤집기만 함) | 바이너리에서 복원 가능 → 실질 보호 없음 |

**앱별 가입 본문 실측 — 이게 조치 설계를 좌우한다**

| 앱 / 브랜치 | 기준 커밋 | 본문의 `ci` | 본문의 `messageAuthCode` |
|---|---|---|---|
| iOS `origin/main` | `4aa2843c` (07-15) | 없음 | **있음** |
| iOS `origin/develop` | `f4e44ec0` (09-21) | 없음 — `21186ce8`(09-15) "[FIX] PASS 인증 CI 제거" | **있음** |
| Android `origin/main` | `1661b5aa1` (07-10) | 없음 | **없음** |
| Android `origin/develop` | `0b1443552` (09-14) | **있음** — `524d6c57`(07-23) "회원가입 시 CI 추가 재작업" | **없음** |

- **Android는 어느 브랜치에서도 가입 본문에 `messageAuthCode` 를 보내지 않는다.** Android는 이 값을 SMS 인증·전화번호 변경·비밀번호 재설정 요청에만 쓴다(`data/.../request/ReqUserJoin.kt`, `ReqSocialSignup.kt` 필드 전수 확인).
- 스토어에 나가 있는 버전이 각 저장소의 어느 브랜치에 대응하는지는 **미확인**이다. 앱 담당에게 확인해야 한다.
- 두 앱 모두 `/app/user/pass/info` 응답에서 `ci` 가 빠져도 깨지지 않는다(null-safe). Android `AndroidBridge.kt` 45행 `if (data.has("ci")) ... else null`, iOS `AppUserPassInfoData.swift` 77행 `response["ci"] as? String ?? ""`. 웹은 응답을 가공 없이 넘기므로 무변경.

### 3-3. 권고안 (09-14 조사 시점)

**CI를 클라이언트로 내보내지 않는다. NICE 결과는 서버가 uuid 키로 보관하고, 가입 시 서버가 보관한 값을 쓴다.**

선례가 이미 있다. OASYS 직접구독(`api/oasys/membership/service/MembershipIdentityService.java` 123행)은 CI를 WebView 서버 세션(`session.getPassCi()`)에서만 꺼내 R008에 넣는다. 클라이언트는 CI를 한 번도 보지 않는다.

구앱이 `ci` 누락에 null-safe이므로 **서버 단독 선배포가 가능하다** — 이것이 이 권고안의 가장 큰 장점이다.

### 3-4. 협력사 수정(AXDO-2102) 리뷰 — 방향은 맞지만 그대로 운영에 가면 안 된다

#### 3-4-1. 최종 형상이 하는 일 (`c7b20f20` + `4dcbdc33`)

| 파일 (`src/main/java/com/skmagic/...`) | 변경 |
|---|---|
| `api/auth/pass/controller/NiceAuthController.java` `/app/user/pass/info` | 응답에서 `ci` 제거(주석 처리). **Redis `pass:{uuid}` 를 읽은 뒤 지우지 않는다**(가입 때 쓰려고) |
| `api/user/service/UserService.java` `syncOasysOnJoin` | 본문 `ci` 대신 **본문 `messageAuthCode` 로 Redis `pass:{messageAuthCode}` 를 `getAndDelete` 해서 CI를 꺼낸다.** `messageAuthCode` 가 없거나 Redis에 없으면 `log.warn` 후 **조용히 return** |
| `api/user/controller/UserController.java` | 클라이언트 CI를 받던 `POST /app/user/oasys/sync` 의 매핑을 주석 처리(메서드는 남음). 앱 호출처 0건이라 영향 없음 |

`messageAuthCode` 와 `pass:{uuid}` 의 uuid는 같은 값이다(`NiceAuthService.java` 510·519행이 둘을 같은 uuid로 만든다). 그래서 이 설계가 성립한다. **권고안과 방향이 같다.**

#### 3-4-2. 잘한 점

- 클라이언트가 보낸 `ci` 를 더 이상 쓰지 않는다. 무결성 갈래의 핵심(남의 CI 주입)이 닫힌다.
- 응답에서 `ci` 를 뺐다. 노출 갈래의 서버 쪽이 닫힌다.
- `getAndDelete` 로 1회용이다.

#### 3-4-3. 문제 — 인수자가 반드시 확인할 것

| # | 문제 | 영향 | 근거 |
|---|---|---|---|
| P1 | **Android 가입은 전부 OASYS 연동이 조용히 빠진다.** Android는 가입 본문에 `messageAuthCode` 를 보내지 않으므로 `syncOasysOnJoin` 이 첫 줄에서 return한다. 가입 자체는 성공한다 — 두 가입 경로 모두 인증 행을 `smsMapper.getLastAuthCodeCheck` 로 찾는데, 이 쿼리는 `messageAuthCode` 가 비어 있으면 그 조건을 생략하고(`SmsMapper.xml` 176행 `<if>`) 전화번호만으로 최신 행을 찾기 때문이다. 이메일가입은 여기에 `sms:verified:{전화번호}` 플래그를 추가로 본다. **그래서 아무 오류 없이 통합고객번호·SafeKey 없는 사용자가 만들어진다** | Android 신규 가입자 전원 OASYS 미연동. 앱 버전과 무관 | §3-2 앱별 표, `UserService.syncOasysOnJoin` |
| P2 | R008에 가는 **이름·생년월일·성별은 여전히 요청 본문 값**이다. CI만 서버 값이고 나머지는 클라이언트 값이라, 한 사람의 CI와 다른 사람의 이름이 섞여 나갈 수 있다 | 무결성 갈래가 절반만 닫힘 | `syncOasysOnJoin` 의 `user.getName()`, `resolveBirthdateForOasys(user)`, `user.getGender()`. Redis `pass:{uuid}` 에는 이 값들이 전부 있다(`NiceAuth.NiceResultRes`) |
| P3 | `/app/user/pass/info` 가 이제 **3분 동안 반복 조회 가능**하다(전에는 첫 조회 때 삭제). 이름·전화번호·생년월일·성별이 응답에 남아 있다 | 낮음. uuid가 UUIDv4라 추측은 어렵다. 단 §5의 report 호스트 경로로는 API GW 없이 닿는다 | `NiceAuthController.java` 164행 주석 처리된 `delete` |
| P4 | **테스트 0건.** 이 경로에는 기존 테스트도 없다 | 위 P1을 어떤 자동 검증도 잡지 못했다 | §2-2 |
| P5 | 코드에 작성자 개인 표식 주석(`### <작성자> 2026-09-14 ... start/end`)과 중복 캐스트 `(String) (String)` 이 남아 있다 | 품질. 리뷰 때 정리 | `UserService.java` 1757~1769행 |
| P6 | `syncOasysForExistingUser` 는 여전히 인자 `ci` 를 `dbUser.setCi()` 에 넣지만 이제 아무도 읽지 않는다. 매핑도 없다 | 죽은 코드. 삭제 대상 | `UserService.java` 1743~1752행 |

참고로 **3분 제한은 새 제약이 아니다.** `pass:{uuid}` TTL이 3분인데, 이메일가입이 확인하는 `sms:verified:{전화번호}` TTL도 180초(`SmsService.java` 55행)라 원래부터 PASS 후 3분 안에 가입해야 했다.

#### 3-4-4. 권고 수정 — 서버만 고쳐서 Android도 살리는 방법

P1을 앱 수정 없이 푸는 방법이 있다. **uuid를 본문에서 받지 말고, 서버가 이미 찾아 둔 값을 쓴다.**

- 두 가입 경로 모두 **검증 단계에서 이미 최신 인증 행을 조회한다.** 이메일가입 `UserService.java` 194행, 소셜가입 1004행의 `HistorySmsAuthCode latestAuthCode = smsMapper.getLastAuthCodeCheck(authCode)`. PASS로 인증했다면 이 행의 `messageAuthCode` 가 바로 uuid다(`NiceAuthService.java` 510행이 그렇게 기록한다).
- 따라서 `latestAuthCode.getMessageAuthCode()` 를 `syncOasysOnJoin` 에 인자로 넘기고, 거기서 `pass:{uuid}` 를 조회하면 된다. 새 조회도, 새 Redis 키도 필요 없다.
- 전화번호와 uuid는 서버가 NICE 결과로 직접 기록한 값이라 클라이언트가 위조할 수 없다. **"서버가 인증한 전화번호 → 서버가 기록한 uuid → 서버가 보관한 NICE 결과"** 로 사슬 전체가 서버 안에서 닫힌다.
- SMS 문자 인증으로 가입한 경우 이 값은 숫자 인증번호라 `pass:{숫자}` 가 없다 → 기존처럼 연동을 건너뛴다(CI가 없으니 정상).
- **함정**: 두 경로 모두 `syncOasysOnJoin` 직전에 `invalidateUnusedAuthCodes(...)` 로 인증 행을 `used` 로 바꾼다(`UserService.java` 325행·1027행). 그 뒤에 `getLastAuthCodeCheck` 를 **다시** 호출하는 식으로 구현하면 `used = 0` 조건 때문에 항상 빈다. 반드시 검증 단계에서 잡은 `latestAuthCode` 를 들고 내려간다.
- 본문에 `messageAuthCode` 가 있으면(iOS) 서버 값과 같은지 대조해도 좋다. 다르면 경고 로그.
- P2도 같이 고친다: R008에 가는 이름·생년월일·성별을 Redis `pass:{uuid}` 의 NICE 결과에서 꺼낸다.

| 항목 | 값 |
|---|---|
| 담당 | **서버**(`backend-api-main`) |
| 선행 조건 | 없음. 앱·웹 변경 불필요 |
| 서버 선배포 가능 여부 | **가능.** 구앱이 보내는 `ci` 는 무시되고, 응답의 `ci` 누락에 두 앱 모두 null-safe |
| 테스트로 고정할 것 | (1) 본문에 `messageAuthCode` 가 없어도 R008이 호출된다 (2) 본문 `ci`·`name` 을 조작해도 R008 인자는 Redis 값이다 (3) 인증 행 무효화(`invalidateUnusedAuthCodes`) 이후에 재조회하도록 바꾸면 테스트가 깨진다 (4) PASS를 거치지 않은 SMS 가입은 R008을 호출하지 않는다 |

### 3-5. 클라이언트 쪽 정리 (서버와 독립, 각 담당)

서버가 CI를 안 내려주면 아래 로그들에서 CI는 사라진다. 그래도 **같은 로그 경로에 이름·전화번호·생년월일과 보안영상 키(§4)가 계속 실린다**. 로그 자체를 고쳐야 한다.

| # | 대상 | 조치 | 담당 |
|---|---|---|---|
| C1 | 웹 `vite.config.ts` | `drop: mode === 'production' ? ['console', 'debugger'] : []` 로 주석대로 동작하게 | 프론트 |
| C2 | 웹 `passSuccess/index.vue` 41·44·46행 | `messageData` 를 찍는 로그 삭제 | 프론트 |
| C3 | iOS `Log.swift` | 릴리스 빌드에서 `privacy: .public` 을 쓰지 않거나 요청·응답 본문 로깅을 끈다 | iOS |
| C4 | iOS `Api.swift` 로그 암호화 키 | 하드코딩 키로 암호화한 로그는 보호가 아니다. C3로 본문 로깅을 끄면 이 기능 자체가 불필요 | iOS |
| C5 | Android `ReqUserJoin`·`ReqSocialSignup` | `ci` 필드 제거(`develop` 에만 있다). §3-4-4를 하면 `messageAuthCode` 추가는 **불필요** | Android |

---

## 4. 지적 2 — 보안영상 암호화 키가 평문으로 전달된다

### 4-1. 현재 구조 (실측)

에스원 보안 이벤트(출동 요청 등)가 발생하면 기기가 영상·썸네일을 찍어 암호화 업로드한다. 앱은 이벤트 목록·VOD를 볼 때 복호화 키를 받아 푼다.

```
[skix-security]  이벤트 1건마다
   ① provisionKeyForDevice(serial) ─ HTTP ─▶ [사내 KMS] RSA-2048 키쌍 새로 생성
                                                개인키 → Redis kms:keypair:{uuid} (평문 PEM, TTL 없음)
   ② MQTT UPLOAD 명령 + 공개키 ──────────▶ [기기 EventAssetAgent]
                                                DEK(32B) 생성 → AES-256-GCM 으로 파일 암호화
                                                DEK 를 공개키로 RSA-OAEP-SHA256 wrap
   ③ wrapped DEK 저장 ◀──────────────────── [skix-streaming] ─▶ [사내 KMS] kms:content:{serial}:data:{contentId}

[앱]  이벤트 목록 / VOD 조회
   ④ GET /security/v1/devices/{serial}/events | /vod-url   (Cognito 인증 + 구독·멤버 확인)
   ⑤ skix-security ─ HTTP ─▶ [사내 KMS] /contents/.../keys/decrypt
                                  개인키로 unwrap → aes_key 평문(base64) 응답
   ⑥ skix-security ─▶ 앱  응답 필드 aesKey = 평문 DEK
   ⑦ 앱이 DEK 로 영상 복호화
```

핵심 코드 위치:

| 무엇 | 위치 |
|---|---|
| 이벤트당 키쌍 생성 호출 | `skix-security` `domain/service/DeviceEventService.java` 318행 (`publishWithSlot` 안). 라이브뷰는 `KvsLiveViewDispatchService.java` 159행 |
| KMS 클라이언트 | `skix-security` `infra/kms/KmsApiClient.java`, `infra/kms/config/KmsClientConfig.java` (인증 헤더 없음) |
| 앱 응답에 평문 키 | `skix-security` `domain/model/result/AesKeyInfo.java`(`String aesKey`) → `api/dto/response/VodUrlResponse.java`·`EventListResponse.java` |
| 주입 지점 | `DeviceMediaService.java` 60행(VOD), `DeviceEventService.java` 525~529행(이벤트 목록 — **한 페이지 분량의 DEK가 한 번에** 내려간다) |
| 앱 API 계약 | `skix-security` `docs/api-spec.md` "AES 키 객체" 절: `aesKey` = "복호화된 AES 키 (Base64)" |
| 기기 wrap | `packages-mr7` `apps/EventAssetAgent/.../crypto/content/RsaOaep256ContentKeyWrapper.java` 25행 `RSA/ECB/OAEPWithSHA-256AndMGF1Padding`, 47~52행 `MGF1ParameterSpec.SHA256` |
| 기기 암호화 | `packages-mr7` `.../upload/crypto/AesGcmFileEncryptor.java` (`AES/GCM/NoPadding`, IV 12B, 태그 128bit) |
| skix-streaming 중계 | `skix-streaming` `module/kms/adapter/in/web/KmsController.java` — KMS의 복호화 응답을 가공 없이 그대로 노출(77·91행) |

앱 경계에는 인가가 있다. `DeviceController` 의 이벤트·VOD 조회는 `@SubscriptionRequired` + `assertMemberOrAdmin` 을 거친다. 문제는 그 뒤편이다.

### 4-2. 무엇이 문제인가 — 여섯 가지, 심각도 순

지적은 "키 평문 전달" 하나였지만, 실측해 보니 사내 KMS 서버 자체에 더 급한 결함이 있다.

| # | 결함 | 근거 (`skix-streaming-kms` 는 `origin/main` 기준) | 심각도 |
|---|---|---|---|
| K1 | **KMS 서버에 인증이 전혀 없다.** serial·contentId만 알면 누구나 평문 DEK를 받는다 | `src/server.js` 17~19행 미들웨어가 `cors()`·`express.json()`·`requestLogger` 뿐. `src/**` 전체에서 auth·token·apikey·jwt 검색 0건. `cors()` 무인자 = 모든 Origin 허용. `express-rate-limit` 은 의존성에만 있고 미사용 | **높음** (네트워크 도달 가능 범위에 따라) |
| K2 | **개인키가 Redis에 평문 PEM으로 영구 보관된다.** Redis 덤프 한 번이면 과거 전체 영상 복호화 가능 | `src/services/keyStore.js` `hset(kms:keypair:{uuid}, {publicKey, privateKey, ...})`, `expire` 호출 없음 | **높음** |
| K3 | **평문 DEK가 KMS 로그에 남는다.** 응답 body를 통째 INFO로 파일·stdout에 기록, 마스킹 없음, 10일 보관 | `src/middleware/requestLogger.js` 47~62행, `src/utils/logger.js` 100·109·111행. README 실행 명령이 stdout도 `app.log` 로 리다이렉트 → 최소 2벌 | **높음** |
| K4 | **전 구간 평문 HTTP.** skix-security → KMS가 3환경 모두 `http://...nlb...:4000` | `skix-security` `deploy/task-definition-{dev,stg,prd}.json` 45행 `KMS_API_URL`. skix-streaming은 `http://localhost:4000`(`resources-prod`·`resources-stag` `application.yml`) | 중 (VPC 내부라면) |
| K5 | **API가 키를 풀어서 앱에 내려준다.** 점검이 지적한 바로 그 지점 | §4-1 ⑤⑥ | 중 — 앱 구간은 TLS + 인가가 있다 |
| K6 | `NODE_ENV` 가 `release` 가 아니면(**미설정 포함**) 복호화를 건너뛰고 wrapped DEK를 `aes_key` 이름으로 그대로 반환 | `src/config/index.js` 6행 `decryptBypass: (NODE_ENV || 'develop') !== 'release'` | 운영 설정 의존. **운영 실값 미확인** |

K1의 실제 심각도는 **KMS의 NLB가 internal인지, 보안그룹이 어디까지 열려 있는지**에 달려 있다. 이것은 로컬에서 확인할 수 없어 **미확인**이다. skix-security는 NLB 외부 주소로, skix-streaming은 localhost로 부르므로 같은 KMS가 두 입구로 노출되어 있다는 것까지만 확정이다.

KMS 운영 형태도 특이하다. `skix-streaming-kms` 는 `origin/master` 에 README 1개뿐이고 **코드는 `origin/main` 에만** 있다. Dockerfile이 없고(03-31 `f5240ca` "remove Docker files"), README의 실행 방법이 `nohup node src/server.js &` 다. **ECS 서비스가 아니라 호스트에 직접 띄운 프로세스로 보인다**(추정 — 실물은 VDI에서 확인). 이 저장소는 스트리밍 측 다른 담당이 관리한다.

### 4-3. 판단 — AWS KMS로 바꾸는 것만으로는 해소되지 않는다

"사내 KMS를 AWS KMS로 교체하자"는 안이 자연스럽게 나오는데, **그것만으로는 지적(K5)이 해소되지 않는다.** AWS KMS `Decrypt` 도 평문 DEK를 반환한다. 서버가 그걸 받아 지금처럼 `aesKey` 로 앱에 내려주면 흐름은 똑같다. 지적의 본질은 **키 저장소의 종류가 아니라 "API가 키를 풀어서 내려주는 설계"** 다.

AWS KMS 전환이 해결하는 것은 K2(개인키가 서비스 밖으로 안 나옴)와 K1·K3·K4(IAM 인증·TLS·CloudTrail이 기본)다. 즉 **서버 뒤편 결함은 해소하지만 앱 구간 평문은 그대로다.**

K5를 해소하는 조건은 하나다. **앱이 요청마다 임시 키쌍을 만들어 공개키를 보내고, 서버는 DEK를 그 공개키로 다시 wrap해서 내려준다.** 그러면 네트워크·서버 로그·프록시 캡처 어디에도 평문 DEK가 없고, 앱만 자기 개인키로 풀 수 있다.

유리한 사실:

- **펌웨어 무변경 전환이 가능하다.** 기기는 `RSA/ECB/OAEPWithSHA-256AndMGF1Padding` + MGF1 SHA-256을 명시적으로 쓴다(Java 기본값 SHA-1을 오버라이드). 이는 AWS KMS의 `RSAES_OAEP_SHA_256` 과 동일하다. 서버가 기기에 내려주는 공개키만 AWS KMS 키의 공개키로 바꾸면 기기는 그대로 동작한다. 단, 상호운용 성공 여부 자체는 실기기로 검증하지 않았다.
- **AWS KMS는 이미 사내에서 쓰고 있다.** `skix-medcare` 가 PHR 컬럼을 AWS KMS 봉투 암호화로 저장한다(`adapter/out/crypto/KmsColumnCryptoAdapter.java`). 계정 권한·SDK 경험이 있다.

제약:

- **AWS KMS는 키당 과금**이다. 지금처럼 이벤트마다 키쌍을 만들면 비용이 선형으로 는다. AWS KMS로 가면 **환경별 비대칭 키 1개**를 두고 이벤트별로는 DEK만 다르게 한다. "이벤트마다 다른 개인키"라는 현재 성질은 사라지지만, 개인키가 KMS 밖으로 나오지 않으므로 보안상 손해가 아니다.

### 4-4. 앱 쪽 실측 — 재래핑 전환의 부담

| 항목 | iOS | Android |
|---|---|---|
| 키 수신 | `Networking/Domain/Security/SecurityEventsGetRes.swift` 83~108행 `SecurityEmbeddedAESKey.aesKey` | `data/.../security/response/ResSecurityDecryptionKey.kt` 12행 |
| 복호화 | `Features/Security/VideoPlayer/SecurityAsyncImage.swift` 108~129행 CryptoKit `AES.GCM.open` | `domain/.../util/DecryptUtil.kt` 20~45행 `AES/GCM/NoPadding` |
| 키가 로그에 남나 | **예.** 이벤트·VOD 조회가 `log_encrypt` 미지정(`Features/Security/Service/SecurityService.swift` 235·255행)이라 응답 JSON 전체(`aesKey` 포함)가 릴리스에서도 os Logger `.public` 로 기록된다. §3-5 C3와 같은 원인 | 아니오(DEBUG 게이트, 릴리스 HTTP 로깅은 no-op) |
| 키가 메모리 문자열로 상주 | `SecurityAsyncImage.swift` 165~167행 뷰 identity에 키 base64 포함 | 해당 없음 |
| 복호화 결과 캐시 | 이미지 캐시가 URL만 키로 쓰고 **평문 이미지를 디스크에 캐시**(`ImageCacheActor.swift`, `.memoryAndDisk`) | 암호문만 파일 캐시 |
| 최소 OS | 17.5 (`project.pbxproj` 6곳 동일) | minSdk 31 (`gradle/libs.versions.toml`) |
| 임시 키쌍 생성 코드 | **없음.** CryptoKit은 이미 링크됨. Secure Enclave·Keychain 키 코드는 0건 | **없음.** `AndroidKeyStore` 직접 사용 0건(`security-crypto` 경유 간접 사용만) |

두 앱 다 OS 하한이 충분히 높아 Secure Enclave(iOS)·AndroidKeyStore(Android)로 임시 키쌍을 만드는 데 API 제약이 없다. 하지만 **키 교환 코드가 전무해 전부 신규 구현**이다.

기기 쪽 참고: `packages-mr7` `EventAssetAgent` 의 `LocalDekFallbackProtector.java` 는 공개키를 받기 전 DEK를 임시 보호하는데, 키를 `ANDROID_ID + ":" + packageName` 의 SHA-256에서 유도하고 IV도 같은 시드에서 고정 유도한다(AES-128-CBC). 주석에 "임시"로 명시돼 있고 기기 내부에서만 쓰인다. 이번 지적 범위 밖이지만 펌웨어 담당에게 알려 둘 가치가 있다.

### 4-5. 권고안 — 3단계. 앞 단계는 뒤 단계 없이도 독립적으로 가치가 있다

**단계 1 (즉시, 서버·인프라만) — KMS 서버 뒤편 막기. 앱·펌웨어 무변경**

| # | 조치 | 담당 |
|---|---|---|
| 1-1 | KMS 서버 NLB 리스너(:4000)와 보안그룹을 **skix-security·skix-streaming 태스크에서만** 도달하게 제한. 먼저 NLB가 internal인지, 보안그룹 소스가 어디까지 열려 있는지 확인 | 인프라 |
| 1-2 | `requestLogger` 가 응답 body를 기록하지 않게(최소한 `aes_key` 마스킹). 기존 로그 파일(`./logs/app-*.log`, `app.log`)에 이미 남은 평문 키 **파기** | KMS 담당(스트리밍 측) |
| 1-3 | 운영 KMS 프로세스의 `NODE_ENV=release` 확인. 아니면 복호화가 우회된다(K6) | KMS 담당 / 인프라 |
| 1-4 | skix-security → KMS에 공유키 헤더 추가, KMS가 검증 | 서버 + KMS 담당 |

**단계 2 (중기) — 사내 KMS를 AWS KMS로 교체. 앱·펌웨어 무변경**

- 환경별 비대칭 키 1개(`RSA_2048`, 용도 `ENCRYPT_DECRYPT`). skix-security가 기기에 내려주는 공개키를 이 키의 공개키로 바꾼다. 기기는 이미 `RSAES_OAEP_SHA_256` 호환이라 그대로 동작한다.
- 복호화는 skix-security가 IAM 역할로 `Decrypt` 를 호출한다. 개인키가 AWS 밖으로 나오지 않고, 호출이 CloudTrail에 남는다.
- **구 콘텐츠는 계속 사내 KMS 개인키로만 풀린다.** 전환 후에도 과거 영상 보존 기간 동안은 사내 KMS를 읽기 전용으로 남기거나, 과거 wrapped DEK를 일괄 재래핑하는 이관 작업이 필요하다. 보존 기간은 **미확인**.
- 이 단계는 K1~K4를 해소하지만 **K5(지적 자체)는 그대로다.**

**단계 3 (지적 해소) — 앱 임시 공개키로 재래핑**

```
앱:   요청마다 임시 키쌍 생성(Secure Enclave / AndroidKeyStore)
      GET /events?...  헤더 또는 본문에 임시 공개키
서버: DEK 를 복호화(단계 2라면 AWS KMS Decrypt) → 즉시 앱 공개키로 재암호화
      응답에 aesKey 대신 wrappedAesKey
앱:   자기 개인키로 풀어 DEK 획득
```

- 앱 API 계약이 바뀐다(`aesKey` → 새 필드). `skix-security` `docs/api-spec.md` 의 "AES 키 객체"도 고쳐야 한다.
- **서버 선배포 가능.** 서버는 요청에 임시 공개키가 있으면 재래핑 필드를, 없으면 기존 `aesKey` 를 내려준다. 신앱이 깔린 뒤에만 효과가 난다.
- **구앱 평문 경로 제거는 앱 강제 업데이트가 게이트다.** 공개키 없는 요청에 `aesKey` 를 계속 주는 한 지적은 "신앱에 한해" 해소된 상태다.
- 알고리즘 선택(RSA-OAEP 또는 ECDH+HKDF+AES-GCM)은 두 앱이 동일하게 구현할 수 있는 것으로 고른다. iOS Secure Enclave는 P-256 ECDH만 지원하므로 **ECDH 계열이 현실적**이다(판단).

### 4-6. 선행 조건과 담당 요약

| 단계 | 선행 조건 | 서버 | 앱 | 펌웨어 | 인프라 | KMS 담당 |
|---|---|---|---|---|---|---|
| 공통 | **점검 원문 확인** — 지적이 앱 프록시 캡처(앱 구간)인지 서버 구간인지. 앱 구간이면 단계 3까지 가야 하고, 서버 구간이면 단계 1·2로 해소될 수 있다 | ○ | | | | |
| 1 | NLB·보안그룹 실상태, 운영 `NODE_ENV` 확인 (VDI) | ○ | | | ○ | ○ |
| 2 | 과거 영상 보존 기간 확정, 재래핑 이관 여부 결정 | ○ | | | ○ | |
| 3 | 알고리즘 합의, 앱 강제 업데이트 일정 | ○ | ○ | | | |

펌웨어 담당의 작업은 **어느 단계에도 없다.** 이것이 이 설계의 핵심 장점이다.

---

## 5. 지적 3 — report 호스트가 API Gateway를 거치지 않는다

### 5-1. 무엇이 문제인가

`report-dev / report-stg / report(prd).namuhx.com` 은 WebView 화면을 서비스하는 호스트인데, **`/api/**` 를 `backend-api-main` 으로 통째로 프록시**한다. 이 경로는 API Gateway를 지나지 않으므로 Cognito Authorizer 1차 인증이 없다. **3환경 모두, 운영계 포함**이다.

이번 작업들이 만든 구성이 아니다. **기존 인프라 구성**이며 OASYS 직접구독 WebView 작업 중에 발견했다.

실측 (2026-09-07):

| 요청 | 결과 | 해석 |
|---|---|---|
| `POST report-dev/api/app/v1/...` | `500` | 컨트롤러까지 도달 |
| 같은 요청을 `app-api-dev` 로 | `401` | API GW가 거절 |
| `GET report-{dev,stg,prd}/api/internal/security/check-member` | **`200`, 무인증** | 내부 전용 API가 공개 호스트로 열려 있음 |

프록시 형태 방증: `frontend-report-web` 의 `vite.config.ts` 개발 서버 설정이 `/api` 를 떼고 백엔드로 통째 넘긴다(`rewrite: path => path.replace(/^\/api/, '')`). 운영 nginx 설정 파일은 어느 저장소에도 없다 — **인프라가 관리한다.**

**제출 문서와 충돌한다.** 2026-08-10 보안점검 자료(서버 API 명세·인증 방식)에 "`/internal/**` 경로는 ALB 리스너 규칙으로 외부 유입을 전량 차단"이라고 제출했다. 그 판단은 `app-api` 호스트만 확인한 것이었고, report 호스트라는 **두 번째 입구**를 빠뜨렸다. 즉 제출 문서의 해당 문장은 실측과 다르다. 조치 후 재실측으로 문장을 사실로 되돌리는 것이 이 건의 목표다.

### 5-2. 위험도 판정 — 무엇이 위험하고 무엇이 아닌가

**위험하지 않은 부분 (조치 불요)**

| 경로 | 왜 괜찮은가 |
|---|---|
| `/api/app/**` | API GW는 없지만 **애플리케이션이 직접 인증한다.** `AwsTokenUtil.getLoginId()` 가 `IdToken` 헤더를 JWKS 서명·audience로 재검증한다. 토큰 없이 들어오면 신원을 못 얻고 실패한다(위 실측의 500). 사칭 불가 |
| `/api/web/**` | OASYS 직접구독 WebView 전용. 원래 report 호스트에서 쓰라고 만든 경로이고 쿠키 세션·CSRF로 자체 방어한다 |
| `/internal/security/check-member` | 전화번호(`loginId`)를 응답에 싣는 것은 `isMember=true` 일 때만이다(코드에 게이팅 주석이 있다). 임의 serial·sub로 전화번호를 캐는 오라클이 되지 않는다 |

**실질 위험 — `InternalSecurityController` 의 게이팅 없는 엔드포인트**

`backend-api-main` `src/main/java/com/skmagic/api/internal/controller/InternalSecurityController.java`. `/internal/**` 는 `SystemConstants.getExcludeUrls()` 에서 전역 permitAll이고, 이 컨트롤러에는 공유키 검증(`InternalApiKeyVerifier`)이 **걸려 있지 않다**(같은 패키지의 `InternalMedcareController`·`InternalUserController` 에는 걸려 있다). **serial 하나만 알면 된다.**

| 엔드포인트 | 할 수 있는 일 | 심각도 |
|---|---|---|
| `DELETE /internal/security/{serial}/schedules` | 해당 기기의 **보안 스케줄 전체 삭제**. DB 삭제에 더해 기기로 `remove` 명령까지 보낸다(`ScheduleService.deleteSecuritySchedulesBySerial`). 되돌릴 수 없다 | **높음** |
| `POST /internal/security/send-fcm` | 그 기기 홈 멤버 전원에게 푸시 발송. `code` 가 모르는 값이면 `SEC0001` 로 떨어져(`SecurityFcmCode.of`) **"Safe Care 보안 상황 감지" 알림**이 나간다. `data` 는 호출자 값이 그대로 실린다 | **높음** — 가짜 침입 경보 |
| `GET /internal/security/contract?serial=` | 기기의 계약 ID·계약번호 조회 | 중 |
| `POST /internal/security/area-names` | 맵 영역 이름 조회 | 낮음 |
| `GET /internal/security/admin/permission-check` | 관제 관리자 권한 여부 조회 | 낮음 |

serial은 비밀이 아니다(기기 라벨·앱 화면·여러 로그에 나온다). 무작위 대입은 어렵지만 **아는 사람의 기기를 겨냥하는 공격**에는 충분하다.

### 5-3. 영향 범위 — 막아도 아무것도 안 깨지는가

**안 깨진다.** 실측 근거:

- 이 API의 정상 소비자는 `skix-security`(`infra/http/platform/PlatformApiClient`)와 `skix-streaming`(`infrastructure/client/platform/PlatformApiClient`) 두 곳이다.
- 두 소비자는 report 호스트가 아니라 **내부 NLB** 로 호출한다. `skix-security` 의 `deploy/task-definition-{dev,stg,prd}.json` 에 `PLATFORM_API_URL` 이 각 환경 `...-nlb-....elb.ap-northeast-2.amazonaws.com` (dev `:8082`, stg·prd `:80`)으로 들어 있고, `skix-streaming` 도 `src/main/resources-{prod,stag}/application.yml` 의 `platform.url` 이 같은 NLB(`:80`)다.
- report 프론트(`frontend-report-web`)는 `/api/internal/**` 을 호출할 이유가 없다.

따라서 **report 호스트의 nginx에서만** `/api/internal/` 을 막으면 공격 경로만 닫히고 정상 경로는 그대로다.

### 5-4. 권고안 A (즉시) — report 호스트 nginx 한 줄

```nginx
# report-dev / report-stg / report 세 호스트의 server 블록, 기존 location /api/ 보다 앞에
location ^~ /api/internal/ { return 404; }
```

- `^~` 는 prefix 일치 시 정규식 location 검사를 건너뛴다. 기존 `/api/` 규칙이 정규식이어도 이 줄이 이긴다.
- `403` 이 아니라 `404` 로 둔다. 경로 존재 여부를 알려주지 않기 위해서다.
- **app-api 호스트에는 넣지 않는다.** 거기는 API GW가 이미 `/internal/**` 을 라우팅하지 않는다(실측 403).

| 항목 | 값 |
|---|---|
| 담당 | **인프라**(nginx 설정 보유자). 서버 코드 변경 없음 |
| 선행 조건 | 없음 |
| 서버 선배포 필요 여부 | 해당 없음 |
| 되돌리기 | 줄 삭제 후 reload |

### 5-5. 권고안 B (후속) — 애플리케이션 계층 2차 방어

nginx는 입구 하나를 막을 뿐이다. VPC 안에서 태스크 IP로 직접 호출하는 경로는 여전히 열려 있다. 근본 조치는 `InternalSecurityController` 에 공유키(`X-Internal-Api-Key`)를 거는 것이다.

- 코드 패턴은 이미 있다. `InternalApiKeyVerifier`(`core/security/`) 가 `app.internal.enforce-api-key` 로 **2단계 배포**를 지원한다 — `false` 로 먼저 배포해 호출자가 헤더를 붙이는지 로그(`Internal API key check (enforce=false) - present=..., valid=...`)로 확인한 뒤 `true` 로 올린다.
- 출발점: 작성자 로컬 브랜치 `backup/with-internal-key` 의 `c9ecdd78`(check-member sub 경로 한정, 테스트 113줄 포함). **원격 미push**라 없을 수 있다. 없으면 `InternalMedcareController` 가 `InternalApiKeyVerifier.verify(@RequestHeader(X-Internal-Api-Key))` 를 부르는 방식을 그대로 따라 만든다.
- **배포 순서가 전부다.** 소비자(`skix-security`·`skix-streaming`)의 헤더 탑재 버전이 **먼저** 나가야 한다. 반대로 하면 라이브뷰와 보안 인가가 **전건 401** 이 된다.
- **`/internal/**` 전역 필터로 걸지 말 것.** 키를 안 보내는 호출자가 20여 곳 남아 있고(`InternalApiKeyVerifier` javadoc), 특히 `skix-streaming` 이 인증 헤더 없이 부르는 경로가 있어 **S1 긴급 호출이 401로 죽는다.** 컨트롤러 단위로만 건다.

| 항목 | 값 |
|---|---|
| 담당 | **서버**(`backend-api-main`, `skix-security`, `skix-streaming` 3저장소) |
| 선행 조건 | 권고안 A 완료(급한 입구부터 닫는다) |
| 서버 선배포 가능 여부 | `enforce=false` 상태면 backend 선배포 무해. `true` 전환은 소비자 배포 후 |

---

## 6. 권고안별 실행 절차

세 건은 서로 독립이다. **비용 대비 효과 순서로 하면 지적 3 → 지적 1 → 지적 2** 다.

### 6-1. 지적 3 — report 호스트 nginx (가장 먼저)

**순서**: 인프라에 §5-4 한 줄 요청 → dev 적용 → 검증 → stg → prd. 서버 배포 없음.

**적용 전 확인 (현재 상태 재현).** 로컬 맥에서 실행 가능하다. **읽기 전용인 `check-member` 만 쓴다. `DELETE .../schedules` 와 `POST .../send-fcm` 은 실제로 스케줄을 지우고 가짜 경보를 보내므로 절대 검증에 쓰지 않는다.**

```bash
# 기대(조치 전): 200   /   기대(조치 후): 404
for h in report-dev report-stg report; do
  printf "%-12s " "$h"
  curl -s -o /dev/null -w "%{http_code}\n" \
    "https://$h.namuhx.com/api/internal/security/check-member?serial=ZZZZTEST0000&loginId=00000000000"
done
```

**적용 후 회귀 확인**

```bash
# 1) report 호스트의 정상 API 는 그대로 — 쿠키 없이 부르면 401(컨트롤러 도달). 404 면 규칙이 너무 넓다
curl -s -o /dev/null -w "%{http_code}\n" "https://report-dev.namuhx.com/api/web/v1/oasys/membership/session"

# 2) app-api 쪽 내부 경로는 원래대로 403 (API GW 미라우팅)
curl -s -o /dev/null -w "%{http_code}\n" "https://app-api-dev.namuhx.com/internal/v1/devices/ZZZZTEST0000/oasys/membership/apply"
```

3) 정상 소비자 확인: 적용 후 30분간 `skix-security`·`skix-streaming` 로그에 `/api/internal/security/check-member` 호출 실패가 늘지 않는지. 두 서비스는 내부 NLB(`PLATFORM_API_URL`)로 부르므로 영향이 없어야 정상이다. 늘었다면 누군가 report 호스트로 부르고 있다는 뜻이니 즉시 되돌리고 호출처를 찾는다.

**롤백**: 해당 `location` 줄 삭제 후 nginx reload.

**완료 후**: 제출 문서의 "`/internal/**` 외부 유입 전량 차단" 문장이 비로소 사실이 된다. 3환경 404를 캡처해 두면 점검 대응 자료가 된다.

### 6-2. 지적 1 — PASS CI (AXDO-2102 보완)

**원칙: AXDO-2102를 지금 형태 그대로 `main` 에 올리지 않는다.** `stg` 와 `dev` 가 같은 형상이라, 다음 `stg → main` 통째 승격 때 함께 올라간다. 9월 운영 릴리스가 `stg → main` 승격을 전제로 하므로 **릴리스 전에 §3-4-4 보완을 넣거나, 보완 전이면 AXDO-2102 커밋을 승격 범위에서 빼야 한다.**

**순서**

1. 인수자가 AXDO-2102 최종 형상을 리뷰하고, 협력사 백엔드와 §3-4-4 보완 방향을 합의한다(작성자가 협력사에 직접 수정하지 않고 리뷰로 요청하는 방식을 써 왔다).
2. 보완 커밋 + 테스트 4종(§3-4-4 표) → `dev`.
3. dev 배포 후 아래 검증.
4. `stg` → 검증 → `main` 은 운영 릴리스 일정에 맞춤.
5. 앱·웹 로그 정리(§3-5)는 서버와 독립으로 각 담당 일정에.

**서버 선배포 가능 여부**: **가능.** 앱 변경 없이 서버만 먼저 나가도 된다. 근거는 §3-2(두 앱 null-safe, 웹 pass-through)와 §3-4-4(Android도 `messageAuthCode` 없이 동작).

**배포 후 검증 (dev 예시)**

```bash
# 1) PASS 후 웹이 받는 응답에 ci 가 없는지.
#    실제 PASS 를 한 번 하고, report-dev /pass/success?uuid=... 로 복귀한 URL 의 uuid 를 쓴다(3분 안에).
curl -s "https://report-dev.namuhx.com/api/app/user/pass/info?uuid=<uuid>" | python3 -m json.tool
#    기대: name·phoneNumber·birthDate·gender·nationalInfo 만 있고 "ci" 키가 없다
```

2) **Android dev 빌드로 신규 가입 1건, iOS dev 빌드로 1건.** 가입 직후 dev DB에서:

```sql
-- 기대: 두 계정 모두 icst_no·skm_sfky 가 채워짐
SELECT login_id, create_date, icst_no IS NOT NULL AS has_icst, skm_sfky IS NOT NULL AS has_sfky
  FROM `user`
 WHERE login_id IN ('<Android 가입 전화번호>', '<iOS 가입 전화번호>');
```

3) 서버 로그 키워드. 가입 시각 전후로 아래가 **0건**이어야 한다.

| 로그 문구 | 뜻 |
|---|---|
| `messageAuthCode 누락으로 OASYS 통합고객번호 연동 스킵` | uuid를 못 찾음 (현재 Android 가입에서 발생) |
| `Redis pass 누락으로 OASYS 통합고객번호 연동 스킵` | uuid는 있는데 Redis에 결과가 없음(3분 초과 또는 이미 소비) |
| `CI 누락으로 OASYS 통합고객번호 연동 스킵` | NICE 결과에 CI가 없음 |
| `OASYS 가입 연동 실패: type=` | R002/R008 호출 실패 |

**이미 생긴 피해 규모 파악** — AXDO-2102가 배포된 뒤 dev·stg에서 Android로 가입한 계정은 OASYS 미연동으로 남았을 수 있다. 배포 시각은 **미확인**(ECS 이력은 VDI에서 확인)이므로 넉넉히 09-15부터 본다. DB 시각은 UTC다.

```sql
SELECT login_id, create_date, icst_no, skm_sfky
  FROM `user`
 WHERE create_date >= '2026-09-14 15:00:00'   -- KST 09-15 00:00
   AND delete_yn = 'N'
   AND icst_no IS NULL
 ORDER BY create_date DESC;
```

이 계정들은 OASYS 직접구독(WebView)을 신청하면 그 흐름이 `icst_no` 를 R008로 다시 채우므로 자연 회복 경로가 있다. dev·stg 테스트 계정이면 무시해도 된다.

**롤백**: 이전 이미지로. DB 변경 없음.

### 6-3. 지적 2 — 보안영상 키

**단계 1 사전 확인 (VDI에서만 가능)**

| 확인 | 방법 | 판정 |
|---|---|---|
| KMS NLB가 internal인가 | `aws elbv2 describe-load-balancers --query "LoadBalancers[?contains(DNSName,'was-nlb')].[LoadBalancerName,Scheme]"` (환경별 계정에서) | `internal` 이어야 한다. `internet-facing` 이면 K1이 **즉시 조치** 등급 |
| 4000 포트를 누가 칠 수 있나 | NLB 대상 그룹의 대상(인스턴스/태스크)의 보안그룹 인바운드 4000 소스 | skix-security·skix-streaming 쪽 SG만 있어야 한다 |
| KMS 프로세스가 어디서 도나 | 대상 그룹의 대상 확인. README상 `nohup` 실행이라 EC2 호스트일 가능성(추정) | 이후 로그 파기·설정 변경 위치 |
| 운영 `NODE_ENV` | 해당 호스트에서 `ps eww -C node | tr ' ' '\n' | grep NODE_ENV` | `release` |
| 로그에 평문 키가 있나 | 해당 호스트에서 `grep -l '"aes_key"' logs/app-*.log app.log 2>/dev/null` | 나오면 1-2 파기 대상 |

**로컬 맥에서 할 수 있는 간접 확인 — 복호화 우회(K6) 여부.** 앱 API가 내려주는 `aesKey` 의 길이로 판정한다. 32바이트면 정상 복호화된 DEK, 256바이트면 RSA-2048로 싸인 채 그대로 나온 것(우회 상태)이다.

```bash
curl -s -H "IdToken: <Cognito ID Token>" \
  "https://app-api.namuhx.com/security/v1/devices/<serial>/events?startDate=2026-09-01&endDate=2026-09-22&limit=1" \
| python3 -c 'import sys,json,base64
d=json.load(sys.stdin)["data"]
for it in (d.get("items") or []):
    k=(it.get("aesKey") or {}).get("aesKey")
    if k: print(len(base64.b64decode(k)), "bytes"); break'
# 기대: 32 bytes
```

- 목록 필드는 `items`, 키 객체는 각 항목의 `aesKey`, 그 안의 문자열 필드도 `aesKey` 다(`EventListResponse`·`AesKeyInfo`). 아무것도 출력되지 않으면 조회 기간에 이벤트가 없는 것이다.
- **출력한 키 값을 어디에도 남기지 않는다.** 길이만 본다.

**단계 2·3 순서** (착수 시)

1. AWS KMS 키 생성(환경별 1개) → skix-security에 IAM 권한 → 기기에 내려줄 공개키를 AWS KMS 공개키로 교체(신규 이벤트부터).
2. 복호화 경로를 "`pubKeyId` 가 AWS KMS 키면 AWS KMS, 아니면 사내 KMS"로 분기. 과거 콘텐츠 보존 기간 동안 유지.
3. 앱 임시 공개키를 받는 경로를 서버에 추가(없으면 기존 `aesKey`). **서버 선배포.**
4. 앱 두 종 구현·출시 → 강제 업데이트 → 서버에서 `aesKey` 평문 경로 제거.

검증 포인트: (1) AWS KMS 공개키로 wrap한 실기기 업로드를 AWS KMS `Decrypt` 로 풀어 영상이 열리는지 — **기기 무변경 전환의 실증은 아직 없다** (2) CloudTrail에 `Decrypt` 이벤트가 남는지 (3) 재래핑 응답을 네트워크 캡처해도 평문 DEK가 보이지 않는지.

---

## 7. 되돌리면 안 되는 판단

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다.

| # | 판단 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **report 호스트 차단은 nginx에서, `/api/internal/` prefix로만.** `/internal/**` 전체에 애플리케이션 필터를 거는 것으로 대체하지 않는다 | 키를 안 보내는 내부 호출자가 20여 곳 있고, `skix-streaming` 이 인증 헤더 없이 부르는 경로 때문에 **S1 긴급 호출이 401로 죽는다.** 애플리케이션 방어는 컨트롤러 단위·2단계 배포(§5-5)로만 한다 |
| 2 | **nginx 규칙을 app-api 호스트나 내부 NLB에 넣지 않는다** | 정상 소비자(`skix-security`·`skix-streaming`)가 NLB의 `/api/internal/security/**` 로 들어온다. 거기서 막으면 보안 인가·라이브뷰·FCM·구독해지 스케줄 정리가 전부 멈춘다 |
| 3 | **"AWS KMS로 교체했으니 지적 해소"로 보고하지 않는다** | AWS KMS `Decrypt` 도 평문을 돌려준다. `aesKey` 를 앱에 그대로 주는 한 점검 재지적 대상이다. 단계 2의 성과는 "서버 뒤편 강화"로 정확히 표현한다 |
| 4 | **AWS KMS 키는 환경별 1개.** 지금처럼 이벤트마다 키를 만들지 않는다 | AWS KMS는 키당 월 과금이라 이벤트 수만큼 비용이 는다. 이벤트별로 다른 것은 DEK로 충분하다 |
| 5 | **PASS CI 보완에서 uuid를 요청 본문에서 받지 않는다**(§3-4-4). 본문 값은 대조용으로만 쓴다 | Android가 보내지 않으므로 Android 가입 전원이 오류 없이 OASYS 미연동이 된다. 본문 값에 의존하면 이 문제를 앱 릴리스 전까지 못 고친다 |
| 6 | **uuid는 검증 단계에서 잡은 `latestAuthCode` 를 들고 내려간다.** `invalidateUnusedAuthCodes` 뒤에서 다시 조회하지 않는다 | 무효화 뒤엔 `used = 0` 행이 없어 조회가 항상 빈다. 가입은 성공하고 연동만 조용히 빠진다 — P1과 같은 증상이 다른 원인으로 재발한다 |
| 7 | **CI를 DB에 저장하지 않는다**(현행 유지) | 가장 민감한 준식별자를 영구 보관하게 된다. R008 한 번 쓰고 버리는 값이다 |

---

## 8. 다른 담당에게 요청할 내용

작성자는 앱·웹·펌웨어·인프라·KMS 서버 코드를 직접 고치지 않았다. 아래는 **아직 전달하지 않은** 요청이다. 협력사·타 팀 요청은 지시서가 아니라 "문제 + 제안 + 검토 요청" 형식으로 써 왔다.

| 대상 | 요청 | 근거 절 | 급한 정도 |
|---|---|---|---|
| **인프라** | report 3호스트 nginx `location ^~ /api/internal/ { return 404; }` | §5-4 | **즉시** |
| **인프라** | KMS NLB scheme·보안그룹·KMS 프로세스 위치 확인 | §6-3 | 즉시(확인만) |
| **협력사 백엔드** | AXDO-2102 보완: uuid 서버 조회, R008 인자를 Redis 값으로, 테스트 4종, 개인 표식 주석·죽은 코드 정리 | §3-4 | **운영 릴리스 전** |
| **KMS 담당(스트리밍 측)** | 응답 body 로깅 중단·기존 로그 파기, `NODE_ENV=release` 확인, 공유키 검증 | §4-5 단계 1 | 즉시 |
| **프론트** | `vite.config.ts` `drop` 수정, `passSuccess` 콘솔 로그 삭제 | §3-5 C1·C2 | 다음 배포 |
| **iOS** | 릴리스 os Logger `.public` 본문 로깅 중단(CI·보안영상 키 둘 다 여기로 샌다) | §3-5 C3, §4-4 | 다음 릴리스 |
| **Android** | 가입 본문 `ci` 필드 제거 | §3-5 C5 | 서버 보완 후 |
| **iOS·Android** | 보안영상 키 재래핑 — 임시 키쌍 생성·공개키 전송·unwrap | §4-5 단계 3 | 점검 원문 확인 후 |
| **펌웨어** | 작업 없음. `LocalDekFallbackProtector` 고정 IV 참고 공유만 | §4-4 | 참고 |

---

## 9. 남은 일

### 9-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 |
|---|---|---|
| 1 | **AXDO-2102 리뷰.** §3-4-3 P1(Android 연동 누락)을 dev에서 Android 빌드로 재현하고 협력사와 보완 합의 | 서버(인수자) + 협력사 백엔드 |
| 2 | **9월 운영 릴리스 범위 확인.** `stg → main` 통째 승격이면 AXDO-2102가 딸려 간다. 보완 전이면 제외 여부 결정 | 서버(인수자) |
| 3 | report 호스트 nginx 한 줄 요청·적용·검증(§6-1) | 인프라 + 서버 |
| 4 | **보안점검 원문 확보.** 세 건 모두 지적 문구 없이 조사했다. 특히 지적 2는 원문이 "앱 구간"인지 "서버 구간"인지에 따라 필요한 단계가 1개냐 3개냐로 갈린다 | 서버(인수자) |
| 5 | KMS 인프라 실상태 확인(§6-3 표). NLB가 internet-facing이면 K1을 최우선으로 올린다 | 인프라 |

### 9-2. 운영 반영 게이트

| # | 조건 |
|---|---|
| 6 | AXDO-2102가 `main` 에 가기 전: §3-4-4 보완 + Android·iOS 양쪽 가입에서 `icst_no` 채워짐 확인 |
| 7 | 보안영상 키 `aesKey` 평문 경로 제거 전: 신앱 강제 업데이트 |

### 9-3. 후속

| # | 항목 |
|---|---|
| 8 | `InternalSecurityController` 공유키(§5-5). 로컬 브랜치 `backup/with-internal-key`(원격 미push)가 출발점. 소비자 먼저 배포 |
| 9 | AWS KMS 전환(§4-5 단계 2) + 과거 콘텐츠 보존 기간 확정 |
| 10 | 앱 재래핑(§4-5 단계 3) |
| 11 | 보안점검 제출 문서의 "`/internal/**` 전량 차단" 문장 — §6-1 완료 후 사실 확인 기록을 남기거나, 완료 전 재제출 기회가 있으면 정정 |
| 12 | iOS 보안영상 평문 이미지 디스크 캐시(§4-4). 이번 지적 범위 밖이나 같은 계열 |

---

## 10. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| "가입했는데 구독·계약 조회가 안 돼요"(신규 가입자) | `SELECT icst_no, skm_sfky FROM \`user\` WHERE login_id = '<전화번호>';` 둘 다 NULL이면 가입 시 OASYS 연동이 빠진 것이다. 서버 로그에서 가입 시각의 `OASYS 통합고객번호 연동 스킵` 문구로 사유를 본다(§6-2 표). AXDO-2102 보완 전이면 Android 가입자가 거의 확실히 여기 해당한다 |
| 가입 연동 누락 계정 복구 | 사용자가 OASYS 직접구독 WebView를 거치면 그 흐름이 PASS를 다시 받아 `icst_no` 를 채운다. 별도 백필 도구는 없다 |
| "보안 상황 감지" 알림이 왔는데 실제 이벤트가 없다 | `skix-security` 쪽에 해당 시각 보안 이벤트가 없는데 `backend-api-main` 에 `/internal/security/send-fcm` 수신 로그가 있으면 외부 호출을 의심한다. report 호스트 접근 로그(nginx)에서 `/api/internal/` 요청을 찾는다. §6-1 적용 후엔 404로 남는다 |
| 보안 스케줄이 통째로 사라졌다 | 구독 해지 시 정상 동작(`skix-security` 가 호출)일 수 있다. 해지 이력이 없는데 사라졌다면 위와 같은 방식으로 `DELETE /internal/security/{serial}/schedules` 외부 호출을 의심한다 |
| 보안영상이 앱에서 안 열린다 | §6-3의 `aesKey` 길이 확인. 256바이트면 KMS가 `NODE_ENV != release` 로 떠서 복호화를 건너뛰고 있다 |
| 점검 대응으로 "CI 어디에 저장하나" 질문 | DB 저장 없음(`user` 테이블에 ci 컬럼 없음). Redis `pass:{uuid}` 에 3분, OASYS 직접구독은 WebView 서버 세션(`oasys:membership:session:{id}`, 15분)에만 둔다 |

---

## 부록 A. 핵심 커밋 (시간순)

작성자가 이 세 건으로 직접 만든 커밋은 없다. 아래는 조사·판단의 근거가 된 커밋이다.

| 날짜 | 저장소 | 커밋 | 내용 | 반영 |
|---|---|---|---|---|
| 06-16 | backend-api-main | `d9def788` | OASYS R008·R002 연동 추가 — 가입 본문 CI를 R008로 보내는 구조의 시작 | dev·stg·main |
| 07-23 | benjaminandroid | `524d6c57` | 회원가입 본문에 CI 추가 | `develop` 만 |
| 08-23 | backend-api-main | `c9ecdd78` | check-member sub 경로 공유키 강제 | **원격 없음**(작성자 로컬 `backup/with-internal-key`) |
| 09-04 | backend-api-main | `00b10674` | 내부 openapi 구간 공유키 — 같은 패턴의 선례 | dev·stg |
| 09-14 | backend-api-main | `3bfd5321` → `bdd9a2bc` → `633b2496` | AXDO-2102 1차: HTTP 세션 보관 → **원복** | dev·stg |
| 09-15 | backend-api-main | `c7b20f20`, `4dcbdc33` | AXDO-2102 최종: `messageAuthCode` 로 Redis 조회 | dev·stg, main 미반영 |
| 09-15 | benjaminios | `21186ce8` | "[FIX] PASS 인증 CI 제거" | `develop` 만 |
| 06-15 | skix-streaming-kms | `788d383` | 사내 KMS 현재 형상(`origin/main` 최신) | 브랜치 체계 없음 |

반영 여부는 `git merge-base --is-ancestor <커밋> origin/<브랜치>` 로 2026-09-22에 실측했다.
