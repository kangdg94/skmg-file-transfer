# [작업] Cognito 토큰 개인정보 제거 · 인증 주체 sub 전환

| 항목 | 내용 |
|---|---|
| **상태** | 서버 구현 완료(남은 서버 작업 0) · **개발계·검증계 S0~S3 완주** · **운영계 미배포**(5저장소 전부 `main` 미승격) · 앱은 양쪽 다 개발 브랜치 구현 완료·스토어 미릴리스 |
| **작업 기간** | 2026-08-19 ~ 2026-08-24 (설계·실측 8/19~8/23, 구현·머지·개발계/검증계 배포 8/22~8/24) |
| **직접 수정한 저장소** | `backend-api-main`, `skix-medcare`, `skix-security`, `skix-streaming`, `skix-streaming-signal` |
| **요청서를 전달한 대상** | iOS(`benjaminios`), Android(`benjaminandroid`), 백오피스 프론트(`frontend-web-backoffice`) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §2 를 읽고 **지금이 어느 단계인지** 확정한 뒤, §8-1 의 1번(개발계·검증계 S4 관찰 결과 확인)과 2번(운영계 `main` 승격 계획 확인) |

---

## 0. 세 줄 요약

1. Cognito ID 토큰에 실려 다니던 **전화번호·이름·생일·성별·이메일**을 걷어내고, 서버가 토큰에서 읽는 값을 **`sub` 하나로** 좁혔다. 사용자 정보가 필요하면 서버가 DB 에서 조회한다.
2. 한 사람이 전화번호·카카오·네이버·애플 계정을 여러 개 갖기 때문에(운영 실측 19%) `user.sub` 컬럼 하나로는 사람을 특정할 수 없었다. 그래서 **`user_accounts` 를 권위 테이블로 삼는 3층 해석**을 만들고, 행이 없는 옛 사용자 약 1,160명을 백필로 채우는 절차를 함께 준비했다.
3. 서버 5저장소는 **`dev`·`stg` 에만** 들어가 있고 **운영(`main`)에는 한 줄도 없다**. 남은 일은 운영계 배포(§7-2)와, 그 뒤 앱 스토어 릴리스가 보급되면 하는 **Cognito 읽기 속성 축소(S6)·속성 파기(S7)** 다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **Cognito** | AWS 의 사용자 인증 서비스. 앱 로그인을 여기서 처리한다. 환경별로 사용자 풀이 따로 있다. |
| **ID 토큰(IdToken)** | Cognito 가 로그인 성공 시 발급하는 JWT. 앱은 이 토큰을 모든 API 호출의 `IdToken` 헤더에 싣는다. **JWT 는 암호화가 아니라 서명**이라 Base64 디코딩만으로 누구나 내용을 읽는다. |
| **클레임(claim)** | 토큰 안에 담긴 키-값. `phone_number`, `name`, `custom:login_id` 같은 것들. |
| **`sub`** | Cognito 가 계정마다 부여하는 UUID. 불투명 가명이라 그 자체로는 개인정보가 아니다. |
| **`loginId`** | 우리 DB 의 `user.login_id`. 실제 값은 **전화번호**(`01012345678`)다. 서버 코드 전체가 이것을 사용자 식별자로 쓴다. |
| **`user_accounts`** | `user_id` ↔ `sub` ↔ `auth_provider` 를 담는 테이블. 한 사람이 여러 Cognito 계정을 가지면 행이 여러 개다. **이 작업에서 권위(authoritative) 테이블로 승격시켰다.** |
| **provider** | 로그인 수단. `EMAIL`(=전화번호 가입) · `Kakao` · `Naver` · `Google` · `SignInWithApple`. |
| **shim** | 호출부를 건드리지 않고 함수 내부만 바꿔 끼우는 얇은 교체층. 여기서는 `AwsTokenUtil.getLoginId()` 내부를 말한다. |
| **자가치유(lazy backfill)** | 로그인 요청을 처리하다가 `user_accounts` 에 행이 없다는 걸 알면 그 자리에서 심는 동작. |
| **S0~S7** | 이 작업의 배포 단계 이름. §7-1 에 전체 표가 있다. |
| **읽기 속성 축소(S6)** | Cognito 앱 클라이언트가 읽을 수 있는 속성 목록을 줄이는 **설정** 변경. 되돌릴 수 있다. |
| **속성 파기(S7)** | Cognito 에 저장된 속성 **값 자체**를 지우는 것. 비가역이다. |
| **`check-member`** | 플랫폼(`backend-api-main`)의 내부 API. "이 사용자가 이 기기의 홈 멤버인가"를 다른 백엔드가 물어보는 곳. |
| **플랫폼** | 이 문서에서 `backend-api-main` 을 가리킨다. `user` 테이블을 가진 유일한 서버다. |

### 1-2. 문제

Cognito ID 토큰에 사용자 개인정보가 그대로 실려 있었다.

```
phone_number · name · birthdate · gender · email
custom:login_id(전화번호) · custom:phone_number
```

앱은 이 토큰을 **매 API 호출마다** 보낸다. 즉 호출할 때마다 개인정보가 함께 이동하고, 토큰이 남는 모든 곳(앱 로그, 프록시 로그, 서버 접근 로그, WebView URL)에 함께 남는다.

### 1-3. 왜 단순하지 않았나

서버 코드 전체가 **전화번호를 사용자 식별자로** 쓰고 있었다. `AwsTokenUtil.getLoginId()` 가 토큰에서 전화번호를 꺼내 돌려주고, 그 값이 **126곳**에서 쓰인다(조사 결과). 여기에 두 가지가 겹쳤다.

**(1) `user.sub` 컬럼을 믿을 수 없다.**

소셜 연동은 **별개 Cognito 계정**을 만든다.

```
전화번호 가입 → Cognito user A · username=UUID            · sub=subA
카카오 연동   → Cognito user B · username=kakao_<회원번호>   · sub=subB   ← 새 계정
```

우리 DB 의 `user` 행은 하나인데 `sub` 컬럼도 하나다. 그리고 `updateSocialUser` 는 `sub` 를 갱신하지 않는다(`login_id`·`gender`·`name`·`birth_date`·`google_key`·`ble_key` 만 건드린다). 그래서 `user.sub` 에는 **최초 가입 provider 의 sub 만 영원히 남는다** — 카카오로 로그인하면 빗나간다.

provider 별 sub 를 전부 담는 곳은 `user_accounts` 인데, 이 기능이 2025-10 에 도입돼 **그 이전 가입자 1,169명에게는 행이 아예 없다.**

**(2) 인가가 이 함수의 예외 하나에 매달려 있다.**

`/app/**` 전체가 Spring 설정상 `permitAll` 이다. 즉 `getLoginId()` 가 던지는 예외가 **사실상 유일한 접근 통제**다. 해석에 실패했을 때 `null` 을 돌려주면 126개 엔드포인트가 열린다.

### 1-4. 운영계 실측으로 확정한 것 (2026-08-20 ~ 08-24)

| 항목 | 값 | 의미 |
|---|---|---|
| 활성 사용자 | 8,411명 | |
| `user_accounts` 결손 | 1,169명 — **전원 `user.sub` 보유**(`user.sub` NULL 0건) | **Cognito 대조가 불필요해졌다.** 순수 DB 작업으로 해결 |
| 복수 Cognito 계정 보유자 | 1,378명 (19%) | `user.sub` 단일 컬럼을 믿을 수 없는 이유 |
| 중복 sub | 4그룹 (활성 2명이 같은 sub) | 해석 시 **오귀속 위험**. 원인은 초기 소셜 가입의 `login_id` 폴백 — 2025-11-27 SMS 인증 패치로 재발 없음 |
| sub 선점 | 5건 (남의 sub 가 내 행에 등록됨) | 2026-08-24 세 환경 모두 정정 완료 |
| 이메일 형식 `login_id` | 0건 | |
| `+82`·공백 포함 `login_id` | 0건 (하이픈 1건) | 정규화가 거의 불필요 |
| Cognito username | 전화번호 가입자는 **UUID**, 소셜은 `kakao_…`·`google_…`·`naver_…`·`signinwithapple_…` | 어느 쪽도 전화번호가 아니다. **"제거할 수 없는 PII 천장"은 없었다** |
| 앱 최신버전 점유율 | 30일 접속자의 96% (Android 97.1% / iOS 95.0%) | 앱 릴리스 보급은 병목이 아니다 |
| `nickname` 등 변형 속성 7종 | 운영계 전수 **0건** (`ListUsers`, 2026-08-24) | S7 파기에서 이 7종은 실질 no-op |

> `auth_provider = 'EMAIL'` 은 **라벨이 잘못 붙은 것**이다. 이 풀은 전화번호 로그인(`UsernameAttributes = phone_number`) 구성이라 실제로는 전화번호 가입이며, 이메일 형식 `login_id` 는 실측 0건이다. 코드가 관행적으로 그렇게 부른다.

### 1-5. 클라이언트 실사용 조사 (2026-08-23, 세 클라이언트 코드 전수)

파기 범위를 확정하려고 앱·백오피스 코드를 직접 확인했다.

| 속성 | iOS | Android | 백오피스 |
|---|---|---|---|
| `name` `gender` `birthdate` | 사용 | 사용(서버 값으로 덮어씀) | 표시 + 검색 |
| `phone_number` | 사용 + **소셜 로그인 필수 경로** | 사용(서버 값으로 덮어씀) | 표시 + 검색 |
| `sub` | 사용(BLE 키 복호화) | 사용 | — |
| `email` | **버림** | 관측 도구 식별자 1곳 | 표시(검색 없음) |
| `nickname` | **버림** | **소비처 0건** | — |
| `custom:socialSignUp` | 소셜 로그인 판별 (**제거 대상 아님**) | 미사용 | — |
| `username` `identities` | — | — | 사용 |

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 서버 코드

`--is-ancestor` 로 각 원격 브랜치에 실제로 들어가 있는지 확인했다.

| 저장소 | dev | stg | main(운영) | 비고 |
|---|---|---|---|---|
| `backend-api-main` | **반영** `d028844f` | **반영** | **미반영** | 15커밋 머지. `main` 은 2026-09-01 `7d9ac170`(8월 핫픽스 revert 상태) |
| `skix-medcare` | **반영** `f8ddf59` | **반영** `628ac40` | **미반영** | 2커밋 |
| `skix-security` | **반영** `105d453`+`cf5f56b` | **반영** `12bb524` | **의도적 제외** | §2-4 참조 |
| `skix-streaming` | **반영** `c651497` | **반영** `b92dc4a` | **미반영** | `main`·`master` 둘 다 미반영 |
| `skix-streaming-signal` | **반영** `210813c` | **반영** `7103a66` | **미반영** | `main`·`master` 둘 다 미반영 |
| `db-schema` | – | – | – | **이번 작업의 신규 DDL 은 없다.** `user_accounts` 는 기존 테이블 |

> ⚠️ **다른 자료에서 `backend-api-main` 커밋 해시로 `dbe6b143`·`f4eee345` 같은 값을 보게 되면 그것은 낡은 것이다.** 2026-08-24 에 `origin/dev` 최신으로 리베이스하면서 해시가 전부 바뀌었다. 실제로 `dev` 에 들어간 것은 `6ba4293c` ~ `f7b23a96` 15개이며 전체 목록은 **부록 A** 에 있다. 옛 해시들은 저장소에 객체로는 남아 있지만 **어느 브랜치의 조상도 아니다** — 인용하면 "미반영"으로 잘못 읽힌다.

### 2-2. 단계별 진행 (S0~S7)

| 단계 | 내용 | 개발계 | 검증계 | 운영계 |
|---|---|---|---|---|
| **S0** | 데이터 사전 점검(선점·중복·UNIQUE) | 완료 | 완료 | **미실행** |
| **S1** | `backend-api-main` 배포 | 완료 | 완료 | **미실행** |
| **S2** | `user_accounts` 백필 | 완료 | 완료 | **미실행** |
| **S3** | 소비자 3개(medcare·security·streaming) 배포 | 완료 | 완료 | **미실행** |
| **S4** | 로그 관찰 | **미확인** | **미확인** | 미실행 |
| **S5** | 앱 반영 | 개발 완료·실기기 확인 미확인 | 개발 완료·실기기 확인 미확인 | 스토어 미릴리스 |
| **S6** | Cognito 읽기 속성 축소 | **미실행** | **미실행** | 미실행 |
| **S7** | Cognito 속성 파기 (비가역) | **미실행** | **미실행** | 미실행 |

- S0~S3 은 2026-08-24 개발계·검증계에서 완주했다(배포 후 curl 검증 포함).
- **S4 이후는 이 문서 기준일까지 진행 기록이 없다.** 로컬 맥에서는 ECS·CloudWatch·Cognito 콘솔에 접근할 수 없어 실측이 불가능하다 — VDI 에서 §부록 E 의 명령으로 확인해야 한다.
- 개발계·검증계는 원래 **S7 까지 완주해 운영계 리허설로 삼기로** 한 계획이었다. 아직 S6 앞에 멈춰 있다.

### 2-3. 앱·백오피스 (별도 담당자)

| 대상 | 상태 | 실측 근거 |
|---|---|---|
| **iOS** | **개발 브랜치 구현 완료. 운영 브랜치 미반영** | `origin/develop` 의 `4fb6649f`(2026-09-02, "코그니토 읽기 관련 역할 축소"). 16개 파일 변경. `origin/main` 은 `4aa2843c`(2026-07-15, v2.2.1)로 미반영 |
| **Android** | **개발 브랜치 구현 완료. 운영 브랜치 미반영** | `origin/develop` 의 `293d51c70`(2026-08-24, "코그니토 토큰의 값 참조 제거"). `origin/main` 은 `1661b5aa1`(2026-07-10, v2.2.1)로 미반영 — `HomeViewModel:292` 에 아직 `email = user.email` 이 있다 |
| **백오피스 프론트** | **미확인** | 서버가 응답 필드를 하나도 빼지 않아 **프론트 수정 없이도 동작한다**. `loginId` 활용은 별건 티켓으로 넘겼다(§6-2) |
| **스토어 릴리스** | **미확인** | 두 앱 모두 운영 브랜치가 2026-07 버전이다. 릴리스 일정은 앱 담당자에게 확인해야 한다 |

> iOS 는 `develop` 브랜치에 `docs/cognito-attr-migration/` 아래 자체 체크리스트를 두고 작업했고, 거기서 서버 요청 §6-1 의 항목을 전부 다뤘다. 특히 **A-2(소셜 로그인의 `phone_number` 의존 제거)** 가 반영돼 있다.

### 2-4. `skix-security` 의 `main` 은 "의도적 제외" 상태다 — 통째 머지 금지

`skix-security` 의 `main` 은 `dev` 전체가 아니라 **선별 반영**돼 있다. 이 트랙의 2커밋을 일부러 뺐다.

- `b1b148b` 토큰에서 loginId 클레임 의존 제거
- `cf5f56b` check-member 응답을 아웃바운드 로그에 저장하지 않음

제외한 이유는 코드 결함이 아니라 **플랫폼(`backend-api-main`)의 운영 배포가 아직 안 됐기 때문**이다. 플랫폼 신버전 없이 security 만 올리면 `check-member` 응답에 `loginId` 가 없어 Cognito 경로가 전부 죽는다.

**다음 승격 때 주의 3가지**

1. `git merge dev` into `main` 을 그냥 하면 제외한 2건이 조상으로 딸려온다. 지금까지는 다른 트랙의 머지 커밋만 골라 머지하고 나머지는 `cherry-pick -x` 로 피해 왔다.
2. 일부 커밋은 `dev` 와 `main` 의 **해시가 다르다**(cherry-pick 때문). 나중에 `dev` 를 통째 머지하면 같은 내용이 양쪽에 있는 상태가 되고, 그 사이 같은 줄이 더 바뀌었으면 충돌한다. 충돌은 "중복 적용"이 아니라 정상 상황이니 내용 기준으로 해소한다.
3. 검증: `git merge-base --is-ancestor <제외커밋> origin/main` 이 **실패해야** 정상이다. `git diff --stat origin/main origin/dev` 가 정확히 제외분의 파일만 나오는지 본다. 이 조합은 CI 가 없어 어디서도 빌드된 적이 없으므로 **push 전 `./gradlew clean test` 필수**다.

---

## 3. 무엇을 만들었나

### 3-1. 설계 원칙 셋

1. **초크포인트 하나만 바꾼다** — `AwsTokenUtil.getLoginId()` **내부만** 교체해 호출부 126곳은 손대지 않는다. 함수는 여전히 전화번호를 돌려주지만, 그 값을 토큰이 아니라 DB 에서 얻는다.
2. **인가 = 해석** — `skix-security`·`skix-streaming` 은 어차피 플랫폼의 `check-member` 를 거치므로, 그 응답에 `loginId` 를 실어 **추가 왕복 0회**로 해석한다.
3. **모든 부재를 즉발 실패로** — 이 코드베이스의 병리는 "없으면 null 로 계속 간다"였다. 해석 실패는 반드시 예외로 만든다.

### 3-2. 핵심 — sub 3층 해석

`backend-api-main` 의 `AwsTokenUtil.getLoginId()` 가 하는 일이다.

```
토큰 서명 검증 → sub 추출
        │
   ┌────┴─────────────────────────────────────────────────────────┐
   │ 1층  user_accounts.sub    권위 테이블. provider 별 sub 전부   │ 약 7,242명
   │      → 히트하면 즉시 반환 (단, 전환기 클레임과 대조해 불일치  │
   │         시 SUB_IDENTITY_MISMATCH 를 남긴다)                    │
   ├──────────────────────────────────────────────────────────────┤
   │ 2층  user.sub             백필 미도달 레거시                  │ 약 1,169명
   │      → 같은 sub 를 가진 활성 행이 2개 이상이면 거부(오귀속 방지)│
   │      → 히트하면 user_accounts 에 심는다 (자가치유)            │
   ├──────────────────────────────────────────────────────────────┤
   │ 3층  전환기 클레임         custom:login_id → custom:phone_number│ 10여 명
   │      → phone_number 순으로 읽고 +82·하이픈·공백 정규화         │
   │      → 해석되면 user_accounts 에 심는다 (자가치유)            │
   └────┬─────────────────────────────────────────────────────────┘
        │ 전부 실패
        ▼
   IllegalArgumentException  ← null 반환 금지. /app/** 이 permitAll 이라 인가가 이 예외에 의존한다
```

캐시는 **요청 스코프(request attribute)만** 쓴다. Redis 가 아니라서 탈퇴·번호변경으로 인한 stale 문제가 원천적으로 없다.

**3층이 실제로 타는 경우는 둘뿐이다.**

1. **중복 sub**(실측 4그룹 8명) — 2층이 오귀속을 막으려고 **일부러 거부**하고 내려보낸다. 앱 토큰에는 Cognito 가 서명한 `custom:login_id` 가 있어 모호성이 해소되고, 3층이 정답을 심으면 그 그룹은 영구히 풀린다. 서버 간 경로에는 클레임이 없으므로 거부를 유지한다.
2. **연동 시 `setUserAccount` 가 실패했던 잔여**(실측 3건) — 1층에 행이 없고 2층은 다른 provider 라 빗나간다.

정상 흐름은 3층에 도달하지 않는다. 소셜 연동 시 `setUserAccount(userId, subB, 'Kakao')` 가 호출되므로 연동을 마친 사용자는 카카오 로그인도 1층에서 잡힌다.

**1층이 아닌 경로로 해석되면 반드시 `user_accounts` 에 심는다.** 심고 나면 다음 로그인부터 1층에서 잡히므로 사용자당 한 번만 돈다. **2층에서 심기를 빠뜨리면 복수 계정 사용자가 영구히 잠긴다** — 자세한 이유는 §5 의 4번에 있다.

### 3-3. 저장소별 역할과 주요 파일

경계 원칙: **사용자 해석은 `backend-api-main` 이 독점한다.** 나머지 서버에는 `user` 테이블이 없어 sub 를 해석할 수 없고, 해석 규칙이 여러 곳에 흩어지면 한쪽만 느슨해진다.

| 책임 | 저장소 | 주요 파일 |
|---|---|---|
| 3층 해석 shim (앱 경로) | backend-api-main | `core/component/AwsTokenUtil.java` — `getLoginId()`·`resolveLoginId()`·`healUserAccount()` |
| 1·2층 공용 해석기 (서버 간 경로) | backend-api-main | `core/component/SubLoginIdResolver.java` |
| sub 그룹 해석 (소유자 키 통합용) | backend-api-main | `core/component/SubIdentityResolver.java` |
| 해석 쿼리 | backend-api-main | `api/user/mapper/UserMapper.java` + `resources/mapper/user/UserMapper.xml` (`findLoginIdBySubViaAccounts`·`findLoginIdsBySubViaUser`·`findUserIdByLoginId`·`createUserAccount`) |
| `check-member` 에 sub 수용·loginId 회신 | backend-api-main | `api/internal/controller/InternalSecurityController.java` |
| `/internal/user` 에 sub 수용 | backend-api-main | `api/user/controller/InternalUserController.java` |
| `hello-world` 응답에 sub 추가 | backend-api-main | `api/user/model/SessionInfo.java` |
| 무인증 `updateSub` 제거 | backend-api-main | `api/user/controller/UserController.java`, `api/user/service/UserService.java` (90줄 삭제) |
| 이름 조회를 Cognito → DB | backend-api-main | `api/user/service/UserService.java`, `api/callcenter/service/CallCenterService.java` |
| 백오피스 사용자 목록을 Cognito → DB | backend-api-main | `api/user/service/UserService.java`, `api/user/model/BackofficeUser.java` |
| 토큰 3단 PII 폴백 제거 → sub 단독 | skix-medcare | `security/CognitoAuthenticationToken.java`, `security/AuthenticatedUser.java` |
| 전화번호가 필요한 1곳(PHR 스크래퍼) 서버 해석 | skix-medcare | `adapter/out/platform/PlatformApiAdapter.java`, `application/service/PhrService.java` |
| 필터 sub 단독 · `CallerIdentity` 재설계 | skix-security | `api/security/JwtAuthenticationFilter.java`, `api/security/CallerIdentity.java`, `api/security/AuthenticatedUser.java` |
| `check-member` 응답으로 loginId 해석 | skix-security | `domain/service/BuildingMemberAccessService.java`, `infra/http/platform/PlatformApiClient.java` |
| `check-member` 응답 아웃바운드 로그 미저장 | skix-security | `infra/http/OutboundApiLogInterceptor.java` |
| 필터 sub 단독 + PII 로그 6곳 제거 | skix-streaming | `shared/security/JwtAuthenticationFilter.java`, `shared/security/AuthenticatedUser.java`, `shared/security/SecurityUser.java` |
| 데드코드 제거 | skix-streaming | `security/cognito/CognitoJwtValidator.java` (`extractLoginId` 27줄, 호출자 0건) |
| 토큰 원문·헤더 전문 로그 제거 | skix-streaming-signal | `signaling/src/auth/apiKey.js`, `signaling/src/middleware/apiKeyMiddleware.js`, `signaling/src/routes/api.js` |

> **부수 효과**: `skix-streaming` 의 S1 긴급호출에 **멤버 검증이 처음 생겼다.** 종전에는 토큰만 있으면 아무 기기로나 긴급출동을 호출할 수 있었다.

### 3-4. 데이터

**MySQL** — 신규 테이블·신규 컬럼은 없다. 기존 `user_accounts` 를 권위 테이블로 쓴다.

```sql
CREATE TABLE `user_accounts` (
  `id`            bigint NOT NULL AUTO_INCREMENT,
  `user_id`       bigint DEFAULT NULL,
  `auth_provider` varchar(20)  DEFAULT NULL COMMENT '인증타입 : EMAIL, GOOGLE, NAVER, KAKAO',
  `sub`           varchar(128) DEFAULT NULL,
  `create_date`   datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `user_accounts_pk` (`sub`),
  KEY `fk_users_user_accounts_user_id` (`user_id`),
  CONSTRAINT `fk_users_user_accounts_user_id` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

두 가지 함정이 여기 있다.

- ⚠️ **`auth_provider` 컬럼 주석이 실데이터와 다르다.** 주석은 `EMAIL, GOOGLE, NAVER, KAKAO`(대문자)라고 적혀 있지만 실제 값은 **`EMAIL` · `Kakao` · `Naver` · `Google` · `SignInWithApple`**(원형 표기)이다. `user` 테이블 쪽은 또 다르다 — `auth_provider` 가 `NULL`·`K`·`N`·`G`·`S` 단문자다. 백필 SQL 의 `CASE` 문이 이 둘을 변환한다. **주석을 믿지 말 것.**
- ⚠️ **두 테이블의 `sub` 컬레이션이 다르다.** `user.sub` 는 `utf8mb4_0900_ai_ci`, `user_accounts.sub` 는 `utf8mb4_general_ci` 다. 대조 쿼리에 `COLLATE utf8mb4_general_ci` 를 빼면 `ERROR 1267` 이 난다. 이 문서의 모든 SQL 에 그 절이 붙어 있는 이유다.
- **`user.sub` 에 인덱스가 없다.** 저장소의 `db-schema` 에도 없다(실측). 백필 전에 반드시 만들어야 한다 — §5 의 8번.

**로그 마커** — 이 트랙이 내는 마커는 넷뿐이고, `backend-api-main` 의 `AwsTokenUtil` 과 `SubLoginIdResolver` **두 클래스만** 낸다. 그래서 마커 문자열만 grep 해도 오탐이 없다.

| 마커 | 레벨 | 의미 |
|---|---|---|
| `SUB_FALLBACK layer=user.sub` | WARN | 2층 해석. 백필 미도달 사용자. 여기서도 자가치유가 돈다 |
| `SUB_FALLBACK layer=claim` | WARN | 3층 해석. **이 값이 0 으로 수렴해야 S6 로 갈 수 있다** |
| `SUB_FALLBACK layer=claim reason=ambiguous_sub` | WARN | 중복 sub 사용자가 3층으로 내려옴 |
| `SUB_IDENTITY_MISMATCH sub=…` | **ERROR** | 1층 해석이 전환기 클레임과 불일치. **최우선 조사** |
| `SUB_PREEMPTED sub=…` | **ERROR** | 자가치유 INSERT 가 UNIQUE 충돌. 타인이 그 sub 를 점유 중 |

> `sub` 는 불투명 가명이라 로그에 남겨도 개인정보가 아니며, 남겨야 특정이 가능하다. 반대로 `loginId`(전화번호)는 마커 어디에도 싣지 않는다.

**`skix-security` 의 아웃바운드 로그** — `check-member` 응답은 **경로 단위로 본문을 저장하지 않는다**. 사내 3저장소 공통 로그 규약에 따라 값을 비우는 대신 `[REDACTED:PII]` 센티널을 기록한다(빈 자리/`null` 은 "결함 신호"로 예약돼 있다). 이 처리가 없으면 유출면을 없앤 게 아니라 **토큰에서 로그 DB 로 옮긴 셈**이 된다.

### 3-5. 백오피스 사용자 목록을 DB 조회로

`GET /api/system/user/list` 가 Cognito `ListUsers` 로 이름·이메일을 읽던 **마지막 서버 소비자**였다. 이것이 남아 있으면 속성 파기 시 백오피스가 깨지므로 함께 옮겼다(`f7b23a96`). 이로써 **파기 대상 속성의 서버 소비가 0** 이 됐다.

구버전 프론트가 그대로 동작하도록 **응답 필드를 하나도 빼지 않았다.** 달라진 것은 셋이다.

| 필드 | 변경 |
|---|---|
| `email` | **항상 빈 문자열.** 필드는 유지(프론트가 컬럼으로 참조) |
| `username` | Cognito username(`kakao_…`) → **`sub`**. DB 에 provider 회원번호가 없어 재구성 불가 |
| `loginId` | **신규** — 마스킹되지 않은 전화번호 |

함께 바뀐 것.

- **페이징이 커서 방식으로** — `paginationToken` 자리에 마지막 행의 `id` 를 담는다. 프론트는 이 값을 불투명 문자열로만 다루므로 **수정 0줄**이다. offset 이 아닌 이유는 스크롤 중 신규 가입이 끼어들어도 중복·누락이 없기 때문이고, 정렬이 PK 고정이라 성립한다.
- **기간 검색이 이번에 처음 동작한다** — 프론트는 `dateKey`·`startDate`·`endDate` 를 보내고 있었으나 Cognito 필터가 날짜 범위를 지원하지 않아 서버가 받지 않았다.
- **검색이 부분 일치로** — 전화번호는 하이픈·`+82` 를 넣고 검색해도 찾힌다.

테스트 11건으로 계약을 고정했다. 특히 **커서 종료 조건**(덜 차면 `null`)이 틀리면 무한스크롤이 멈추지 않거나 마지막 페이지가 잘린다.

### 3-6. 왜 토큰을 안 바꾸는 동안은 안전한가

**앱 릴리스 전까지 토큰을 1비트도 바꾸지 않는다.** 읽는 쪽만 바뀐다. 토큰에는 `sub` 와 PII 클레임이 계속 둘 다 실려 있으므로:

| 조합 | 결과 |
|---|---|
| 신버전 서버 + 구버전 앱 | 정상 (서버가 sub 로 해석) |
| 구버전 서버 + 신버전 앱 | 정상 (토큰이 그대로라 클레임이 살아 있음) |
| 서버 간 신구 혼재 (ECS 롤링) | 정상 (내부 API 가 `loginId`·`sub` 둘 다 받음) |

**토큰이 실제로 바뀌는 것은 S6(읽기 속성 축소)·S7(파기)뿐이며, 그 앞에 게이트가 있다**(§8-2).

---

## 4. API 계약

### 4-1. `GET /api/app/hello-world` — 앱 프로필 조회 (기존 API, `sub` 추가)

앱이 `fetchUserAttributes()`(Cognito 직접 읽기) 대신 쓸 경로다. 응답은 `SessionInfo` 다.

```json
{ "result": 200, "message": "OK", "success": true,
  "data": {
    "name": "홍길동",
    "sub": "94183dcc-….",
    "gender": "Male",
    "birthDate": "1989-03-01",
    "loginId": "01012345678",
    "installer": 0,
    "passwordChangedAt": "2026-05-01T10:00:00",
    "passwordExpired": false,
    "oasysSyncRequired": false
  } }
```

- **`sub` 는 `user.sub` 가 아니라 "이 요청 토큰의 sub"** 다. `ble_key`·`google_key` 가 토큰 sub 로 파생·저장되므로 그 값을 그대로 돌려줘야 앱의 복호화가 성립한다. `user.sub` 는 최초 가입 provider 의 것으로 고정돼 있어 2차 provider 연동자(19%)에게서 어긋난다.
- 전화번호는 `phoneNumber` 가 아니라 **`loginId`** 다(이미 `+82` → `0` 정규화됨). 필드명이 Cognito 와 달라 앱 매핑 누락이 나기 쉬운 자리다.
- 소셜 가입자는 Cognito 에 `birthdate` 가 없는 경우가 많다. **DB 값이 더 완전하므로 이 전환은 기능 축소가 아니라 개선이다.**
- DB 에 사용자 행이 없으면 404 다.

### 4-2. `GET /internal/security/check-member` — 내부 인가 조회 (파라미터·응답 확장)

```http
GET /internal/security/check-member?serial={serial}&sub={sub}
GET /internal/security/check-member?serial={serial}&loginId={전화번호}   # 구버전 호환
```

```json
{ "data": { "isMember": true, "isAdmin": false, "loginId": "01012345678" } }
```

- `sub` 와 `loginId` 를 **둘 다 받는다.** `sub` 가 오면 그쪽을 쓴다. ECS 롤링 배포 중 구/신 태스크가 공존하므로 한쪽을 먼저 필수로 만들면 그 순간 400 이 난다. 전환이 끝나면 `loginId` 파라미터를 제거한다.
- 둘 다 없거나 `sub` 가 해석되지 않으면 **400**.
- ⚠️ **`loginId` 는 `isMember == true` 일 때만 응답에 싣는다.** 이 엔드포인트는 `/internal/**` 전역 `permitAll` 대상이라 인증이 없고, 존재하지 않는 serial 도 예외 없이 `isMember=false` 로 200 을 돌려준다. 무조건 실으면 **임의 serial + 임의 sub 로 전화번호를 얻는 해석 오라클**이 된다. `sub` 는 비밀이 아니라 모든 ID 토큰과 여러 서비스 로그에 남는 값이다. 소비자 둘 다 판정이 참일 때만 `loginId` 를 쓰므로 이 게이팅으로 깨지는 곳은 없다.

### 4-3. `GET /internal/user` — 내부 사용자 정보 조회 (파라미터 확장)

```http
GET /internal/user?sub={sub}          # 또는 ?loginId={전화번호}
X-Internal-Api-Key: <공유키>
```

`sub`·이름·이메일·성별·생년월일을 통째로 담으므로 **공유키를 검증한다.** 헤더는 `required = false` 로 받는다 — 없을 때 Spring 이 400 을 내는 대신 검증기가 401 을 내야 계약이 일관된다.

### 4-4. `GET /internal/user/identity` — sub 그룹 조회

```http
GET /internal/user/identity?sub={sub}
X-Internal-Api-Key: <공유키>
→ { "ownerKey": "...", "aliases": ["...", "..."] }
```

"어떤 Cognito 계정들이 같은 사람인가"를 돌려준다. `skix-medcare` 의 소유자 키 통합 트랙과 `skix-openapi` 의 로그인 sub 정규화 트랙이 소비자다.

- ⚠️ **이 API 는 `SubLoginIdResolver` 를 타지 않는다.** 저쪽의 2층(`user.sub`) 폴백은 "`user_accounts` 에 없으면 매핑 없음"이라는 이 API 의 계약을 깬다. 매핑이 없으면 **404** 이고, 소비자는 404 를 "raw sub 유지(현행 동작)"로 해석한다.
- 응답에 PII 가 없는데도 공유키를 검증하는 이유는, 이 매핑 자체가 "누가 같은 사람인가"라는 연결 정보이기 때문이다.

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다. 나머지 세부 결정은 코드 주석과 테스트가 지킨다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **`getLoginId()` 는 해석 실패 시 반드시 예외를 던진다. `null` 반환 금지** | `/app/**` 전체가 Spring 에서 `permitAll` 이라 앱 API 의 인가가 사실상 이 예외 하나에 의존한다. `null` 을 돌려주면 **126개 엔드포인트가 인증 없이 열린다.** "null 이면 로그만 남기고 계속" 같은 선의의 수정이 가장 위험하다 |
| 2 | **`getLoginId` 를 관제용 `JwtTokenUtil.getLoginId` 와 한데 묶지 않는다** | 두 개의 다른 세계다 — `AwsTokenUtil` 은 앱용(Cognito, 126곳), `JwtTokenUtil` 은 관제 백오피스용(자체 JWT, 127곳). 이름이 같다고 공통화하면 관제 admin 계정이 sub 해석기를 타게 되고 전부 실패한다 |
| 3 | **2층에서 같은 sub 를 가진 활성 행이 2개 이상이면 임의로 고르지 않고 거부한다** | 실측 4그룹(활성 2명이 같은 sub). 임의로 고르면 **타인 신원으로 해석**된다. 오귀속보다 실패를 택한다. 앱 경로에서는 이 거부를 삼키고 3층으로 내려보내 서명된 클레임으로 모호성을 해소하지만, **서버 간 경로에는 클레임이 없으므로 거부를 유지한다** |
| 4 | **2층으로 해석됐으면 반드시 `user_accounts` 에 심는다** (2층 자가치유) | 심지 않으면 자가치유가 반쪽이 된다. 기존 코드 `getUserInfoByLoginIdChek` 안에 sub 대조 게이트가 있는데, 그 게이트는 **`user_accounts` 행이 0개면 검사 자체를 건너뛴다**. 3층으로 치유된 계정의 첫 행이 생기는 순간 게이트가 깨어나고, 2층으로만 해석되는 다른 provider 계정은 등재된 적이 없어 대조에 걸려 `/app/hello-world`·`/app/sign-check` 가 404 가 된다. 앱은 그것을 로그인 실패로 처리한다. 백필도 사용자 단위로 제외하므로 **스스로 회복되지 않는다** |
| 5 | **1층이 히트해도 전환기 클레임과 대조해 불일치를 ERROR 로 남긴다**(`SUB_IDENTITY_MISMATCH`). 단 **거부하지는 않는다** | 거부하면 번호를 막 바꾼 사용자가 토큰 만료까지 잠긴다(구 토큰은 옛 번호를 담고 있고 그때는 DB 가 옳다). 반대로 남기지 않으면 **sub 선점(실측 5건)으로 인한 오귀속이 로그 한 줄 없이 지나간다** — 1층은 히트하면 즉시 반환하므로 자가치유의 `SUB_PREEMPTED` 는 이 경우에 도달하지 못한다. 관측 자체가 불가능해진다 |
| 6 | **캐시는 요청 스코프(request attribute)만. Redis 를 쓰지 않는다** | Redis 캐시는 탈퇴·번호 변경으로 stale 이 된다. 한 요청 안에서만 사는 캐시는 그 문제가 원천적으로 없으면서 중복 서명검증·DB조회를 없애는 효과는 같다 |
| 7 | **배포 순서 S1(플랫폼) → S2(백필). 뒤집으면 안 된다** | **백필은 잠든 게이트를 깨우는 행위다.** 결손 사용자는 `user_accounts` 행이 0개라 위 4번의 sub 대조 게이트가 잠들어 있다. 백필이 행을 심는 순간 일제히 깨어나는데, 백필은 `user.sub`(= 최초 가입 provider 의 sub) 하나만 심으므로 **다른 provider 로 로그인하면 그 sub 가 없어 게이트에 걸린다.** 구버전 서버에는 자가치유가 없어 구해 줄 코드가 없다 → 백필 직전까지 멀쩡히 로그인되던 복수 계정 사용자가 로그인 불능이 된다. shim 이 먼저 배포돼 있으면 `hello-world` 컨트롤러가 **`getLoginId()`(자가치유) → `getUserInfoByLoginIdChek`(게이트)** 순서로 돌기 때문에 같은 요청 안에서 치유된 뒤 검사를 통과한다 |
| 8 | **백필 전에 `CREATE INDEX ix_user_sub ON user(sub)` 를 반드시 먼저 한다** | 없으면 백필의 상관 서브쿼리가 8,411 × 8,481 ≈ 7,100만 행을 읽고 그동안 **`user` 테이블이 약 15초간 쓰기 잠긴다**(실측 15.4s → 0.028s). 그 15초 동안 로그인·가입이 함께 멈춘다. 저장소의 `db-schema` 에 이 인덱스가 없으므로 운영계에서도 직접 만들어야 한다 |
| 9 | **S0(선점·중복 정정)은 배포 진입 조건이다. 관찰로 갈음할 수 없다** | 선점 상태(A 의 sub 가 B 의 `user_accounts` 행에 들어가 있음)에서 shim 을 배포하면 1층이 **히트**하므로 A 가 로그인하는 순간 **B 의 신원으로 API 를 쓴다.** 자가치유는 1층 미스일 때만 도달하므로 `SUB_PREEMPTED` 도 안 뜬다. 즉 **배포 후에는 자동으로 잡아낼 수단이 없다.** 30초짜리 확인을 건너뛸 이유가 없다 |
| 10 | **S0 체크는 "정정했다"가 아니라 "배포 직전에 확인했다"로 한다** | 정정 자체는 2026-08-24 세 환경 모두 끝냈지만, 정정 시점과 배포 시점 사이에는 무인증 `updateSub` 가 살아 있어 재오염 경로가 열려 있다. 그 간격을 메우는 것이 이 단계의 존재 이유다. 또 S0 의 4번 항목은 데이터가 아니라 **스키마**(UNIQUE 인덱스 존재)를 본다 — 데이터가 깨끗해도 인덱스가 사라졌으면 백필의 선점 방어가 무너진다 |
| 11 | **`check-member` 응답의 `loginId` 는 `isMember == true` 일 때만 싣는다** | 이 엔드포인트는 인증이 없고 미존재 serial 도 200 을 돌려준다. 무조건 실으면 임의 serial + 임의 sub 조합으로 전화번호를 얻는 해석 오라클이 된다(§4-2) |
| 12 | **내부 API 는 `sub` 와 `loginId` 를 둘 다 받는다(additive)** | ECS 롤링 배포 중 구/신 태스크가 공존한다. 한쪽을 먼저 필수로 바꾸면 그 순간 400 이 난다. 전환이 완전히 끝난 뒤에 `loginId` 를 뺀다 |
| 13 | **`skix-streaming` 의 sub 전환 배포는 S6 의 선행 조건이다** | 종전 코드는 클레임이 없으면 예외가 아니라 **`null` 을 반환하고 인증을 통과**시킨다. 그러면 인증 계층이 아니라 비즈니스 계층에서 400/403/500 으로 흩어져 터진다. **라이브뷰·긴급호출이 조용히 죽는다.** (`skix-streaming-signal` 은 JWT 를 파싱하지 않아 S6 과 무관하다 — 로그 정리 목적) |
| 14 | **S6 은 "뺄 목록"이 아니라 "남길 목록"으로 설정한다** | "sub 만 남기고 전부 해제"하면 `custom:socialSignUp` 까지 빠져 **iOS 소셜 로그인 판별이 깨진다.** 게다가 풀 스키마에 같은 뜻의 속성이 변형으로 중복돼 있어(`custom:ble_key`/`custom:bleKey`, `birthdate`/`custom:birthday`/`custom:birthiday`(오타)/`custom:birthyear` 등) 제거 목록 방식이면 반드시 빠뜨린다. 지금은 값이 0건이지만, 빠뜨린 속성에 나중에 값이 생기면 **아무도 모르게 토큰에 다시 실린다** |
| 15 | **`phone_number` 는 읽기에서 빼되 저장은 유지한다** | 저장 유지와 읽기 허용은 다르다. 로그인·비밀번호 재설정·중복검사는 전부 **서버가 IAM 자격으로** 호출하므로 앱 클라이언트의 읽기 속성과 무관하다. 빼지 않으면 토큰에 전화번호가 영구히 남아 목적이 달성되지 않고, **파기하면 로그인 식별자가 사라져 전원 로그인 불능**이 된다 |
| 16 | **`custom:ble_key`·`custom:googleKey`·`custom:create_date` 는 파기하지 않는다** | 서버가 `adminGetUser`(IAM 자격)로 읽는다. `ble_key` 는 읽기 속성에서만 뺀다 — 읽기에 두면 BLE 출입 키가 매 ID 토큰에 실리는데, 양쪽 앱 모두 서버 API 로 받으므로 빼도 무영향이다 |
| 17 | **백필은 대상 스냅샷 테이블 + 단일 트랜잭션으로 한다** | 대상 선정과 INSERT 를 같은 트랜잭션에 두면 그 사이 자가치유·가입·탈퇴가 끼어들어도 결과가 흔들리지 않는다. 동시성 원인을 사후에 분류할 필요가 없다. 롤백도 `id` 범위가 아니라 스냅샷으로 **정확히 이 실행이 넣은 행만** 지운다 |
| 18 | **백필은 `user` 테이블을 읽기만 한다. `ble_key`·`google_key`·`masking_key` 를 건드리지 않는다** | 이 세 키는 클라이언트가 `sub` 로 직접 복호화하므로 재생성하면 **BLE 출입이 깨진다.** 삭제한 무인증 `updateSub` 배치가 바로 그걸 했다 |
| 19 | **`check-member` 응답을 `skix-security` 아웃바운드 로그에 저장하지 않는다** | 이 응답의 `loginId` 는 전화번호다. 토큰에서 PII 를 걷어내면서 사용자 해석을 이 응답으로 옮겼는데, 그대로 저장하면 **매 인가 호출마다 전화번호가 로그 DB 로 옮겨간다.** 유출면을 없앤 게 아니라 이동시킨 셈이 된다. 대상 전체를 가리는 방식이 아니라 **경로 단위**로 가린다 — PLATFORM 대상의 다른 호출은 장애 분석에 본문이 필요하다 |
| 20 | **백오피스 목록 API 의 응답 필드를 빼지 않는다** | 구버전 프론트가 그대로 동작해야 한다. `email` 은 빈 문자열로 내려도 프론트가 깨지지 않지만, 필드를 없애면 깨진다. `paginationToken` 도 내부 의미만 바꾸고 형태(불투명 문자열)를 유지해 프론트 수정 0줄로 만들었다 |

---

## 6. 앱·백오피스에 요청한 내용

앱·프론트 코드는 직접 수정하지 않았다. 아래가 각 담당자에게 전달한 요청의 핵심이며, 2026-08-24 에 **검토 후 이견 없음 회신**을 받았다. 인수자는 이 내용이 실제 구현과 맞는지 확인하는 역할이다.

### 6-1. 앱 (iOS · Android)

**A-1. 프로필 조회를 `fetchUserAttributes()` → `GET /api/app/hello-world` 로**

| 지금 (`fetchUserAttributes`) | 전환 후 (`hello-world`) |
|---|---|
| `name` / `gender` / `birthDate` | `name` / `gender` / `birthDate` |
| `phoneNumber` | **`loginId`** (필드명이 다르다 — 매핑 누락 주의) |
| `sub` | `sub` ★신규★ |
| `email` `nickname` | 필요 없음 |

- iOS 는 `UserData(attr:)` 로 수렴하는 5곳 전부가 대상이다.
- Android 의 자동 로그인 경로는 이미 `hello-world` 결과로 덮어쓰고 있어 무관하다. **`changePhone` 성공 후 경로만** 서버 병합이 없어 같은 방식으로 병합을 태워야 한다.
- `sub` 는 읽기 허용을 유지한다(iOS BLE 키 복호화). `custom:socialSignUp` 도 유지한다(iOS 소셜 로그인 판별).

**A-2. 🚨 iOS 소셜 로그인 — `phone_number` 클레임 의존 제거 (가장 중요)**

iOS 가 카카오·구글·애플 로그인 시 `fetchUserAttributes()` 의 `phone_number` 로 전화번호를 얻는데, 읽기 속성 축소 후에는 이 값이 사라져 **소셜 로그인이 전건 "휴대폰 번호가 등록된 계정을 사용해주세요"로 실패한다.** 프로필 표시가 아니라 **로그인 경로**라서, 빠뜨리면 카카오·구글·애플이 한꺼번에 죽는다.

그 시점에 인증은 이미 끝나 토큰이 있으므로 `hello-world` 의 `loginId` 로 대체된다. 서버 추가 개발은 없다.

> **"DB 행이 없는 신규 소셜 사용자가 이 경로를 타는가"** 를 확인 요청했고, **실재하지 않음으로 확정**됐다(코드 분석 + 앱 담당자 검토 일치). 그래서 본인 전화번호 조회용 신규 엔드포인트는 만들지 않았다.

**A-3. Android — 관측 도구(RUM) 사용자 정보에서 `email` 제거**

Cognito 에서 email 을 파기하므로 값이 비게 되고, 관측 도구로 개인정보를 내보내지 않는 방향이기도 하다. 식별은 `sub`·`name` 으로 충분하다.

**A-4. 참고 — 기존 버그로 보이는 것 (이번 작업과 무관)**

iOS `BuildingMember.updateAsync()` 가 `memberId` 파라미터에 일련번호를 보내는데 서버는 그것을 전화번호(`u.login_id`)로 조회한다. 원인은 이름 교차다 — 서버 응답은 `u.login_id AS memberId` 라 `memberId` 필드에 전화번호가 담기는데, iOS 는 `memberId`/`phone` 두 이름을 뒤집어 두었다. 같은 화면의 권한 저장 경로는 올바르게 보내고 있어 **새로고침 경로만 어긋난** 상태다. 조용히 `false` 가 반환되고 목록 값이 그대로 표시돼 눈에 띄지 않는다.

### 6-2. 백오피스 프론트

**제품등록 사용자 선택 목록**(`GET /api/system/user/list`)이 Cognito 조회에서 DB 조회로 바뀌었다. **필드는 하나도 빼지 않아 지금 프론트가 그대로 동작한다.**

- `email` 은 앞으로 **항상 빈 값**이다. 이메일 칸이 비어 보인다. 컬럼을 뺄지는 프론트 판단에 맡겼다. (원래도 전원이 값을 갖고 있지 않았다 — 네이버 가입자는 email 이 없고, 애플은 릴레이 주소다)
- 검색이 부분 일치로, 기간 검색이 **이번에 처음 동작**한다.

**🔎 `loginId` 를 추가한 이유 — 기존 계약에 결함이 있었다.**

프론트의 사용자 선택 로직이 세 갈래로 식별자를 만들고 있었는데 **셋 다 우리 DB 의 `login_id` 와 맞지 않았다.**

| 갈래 | 프론트가 만드는 값 | 실제 `login_id` |
|---|---|---|
| 카카오 | 카카오 회원번호 (10자리 숫자) | 전화번호 |
| 구글 | 구글 회원번호 (20자리 숫자) | 전화번호 |
| 그 외 | `010****1060` 같은 **마스킹된 전화번호** | 마스킹 안 된 전화번호 |

특히 마지막이 결정적이다 — `phone_number` 는 표시용으로 마스킹돼 나가는데 그 값을 식별자로 되돌려 보내고 있었다. 그래서 **홈 목록 조회가 계속 비어 보였을 것**이다. `row.loginId` 를 그대로 쓰면 분기 로직 전체가 불필요해진다. **이번 변경으로 나빠지는 것은 없다**(종전에도 맞지 않던 값이다). 별건 프론트 티켓으로 잡아 달라고 전달했다.

---

## 7. 배포 방법

### 7-1. 단계 정의 (S0~S7)

| 단계 | 내용 | 되돌리기 |
|---|---|---|
| **S0** | 데이터 사전 점검 — 선점·중복·UNIQUE 확인 (부록 B) | – |
| **S1** | `backend-api-main` 배포 (15커밋 전부) | 이전 태그로 재배포 |
| **S2** | `user_accounts` 백필 (부록 C) | 스냅샷 테이블로 정확한 행만 삭제 |
| **S3** | 소비자 3개 배포 — medcare · security · streaming(+signal) | 개별 롤백 가능 |
| **S4** | 로그 관찰 (부록 E) | – |
| **S5** | 앱 반영 — 스토어 릴리스 보급 | – |
| **S6** | Cognito 읽기 속성 축소 (부록 F) | 콘솔에서 설정 복원(즉효 아님) |
| **S7** | Cognito 속성 파기 | **불가 (비가역)** |

**환경별 전략**

```
개발계   S0 ─ S1 ─ S2 ─ S3 ─ S4 ─ S5(dev 앱) ─ S6 ─ S7      ← 운영계 리허설
검증계   S0 ─ S1 ─ S2 ─ S3 ─ S4 ─ S5(stg 앱) ─ S6 ─ S7
운영계                                    ↓ 여기까지 확인 후
         S0 ─ S1 ─ S2 ─ S3 ─ S4 ─── (배포일자) ─── S5(스토어) ─ S6 ─ S7
```

**환경 간 의존은 하나뿐이다** — 운영계 S1 진입 전에 검증계가 S4 까지 통과해 있어야 한다. Cognito 사용자 풀·앱 클라이언트가 환경별로 분리돼 있으므로 S6·S7 은 환경 간에 서로 영향을 주지 않는다.

> ⚠️ **S7 은 환경 내에서 비가역이다.** 개발계에서 파기하면 그 환경에서는 3층(전환기 클레임) 자가치유 경로를 다시 시험할 수 없다. 그래서 **S2 백필과 S4 관찰을 먼저 마쳐 자가치유가 필요한 사용자를 다 흘려보낸 뒤에** S6·S7 로 간다. 개발계에서 시험하고 싶은 것이 남아 있으면 S6 앞에서 멈춘다(S6 은 설정이라 되돌아온다).

### 7-2. 운영계 배포 순서

```
0. main 승격            5저장소. skix-security 는 §2-4 주의사항대로 선별 머지
1. S0 사전 점검         부록 B. 기대 0·0·0·1 이상
2. backend-api-main     ← 반드시 백필·소비자보다 먼저
3. S2 백필              부록 C. 반드시 2번 뒤 (§5의 7번)
4. skix-security        ← backend-api-main 직후. 순서 이유는 아래
5. skix-streaming
6. skix-streaming-signal
7. skix-medcare
8. S4 관찰              부록 E
```

**2 → 4 순서를 지켜야 하는 이유**: 운영 `backend-api-main` 의 `check-member` 는 아직 `loginId` 가 필수다. security 를 먼저 올리면 security 의 Cognito 경로가 전부 400 이 된다. (OASYS 직접구독 작업 때의 "security 먼저"와 **반대**다 — 그쪽은 내부 요청 본문 필드 문제였고, 이쪽은 인가 해석 문제다.)

**3 → 5·7 순서**: 소비자(`skix-streaming`·`skix-medcare`)는 플랫폼 신버전이 없으면 즉시 실패한다. streaming 로그에 `check-member 응답에 loginId 가 없다` 가 나오면 **플랫폼 신버전 미배포 신호**다.

**배포 자체의 주의**

- `skix-streaming`·`skix-streaming-signal` 은 **CI 가 없고 한 EC2 에서 수동 운영**된다(streaming·signal·kms 세 프로세스). 절차는 담당자에게 확인한다.
- `skix-streaming` 은 **빌드 시점에 프로필이 정해진다**(`-Pprofile=prod`). 개발 프로필로 빌드하면 개발 설정으로 운영이 뜬다.
- `skix-streaming-signal` 은 변경 파일 3개만 교체하는 방식이고, 박스의 `.env` 는 **덮지 않는다**.

### 7-3. S1 배포 후 확인

```bash
# 무인증 updateSub 제거가 실제로 반영됐는지. 기대 404
curl -s -o /dev/null -w "%{http_code}\n" -X PUT https://<host>/api/app/user/update/sub
```

이 엔드포인트가 살아 있으면 **백필 직후 `user.sub` 가 다시 오염될 수 있다.**

> 첫 커밋 `6ba4293c` 는 순수 삭제(추가 0줄)라 의존이 없다. 무인증 엔드포인트 제거만 먼저 내보내고 싶으면 그 커밋만 분리 배포할 수 있다.

### 7-4. S3 배포 후 확인 (개발계·검증계에서 실제로 쓴 것)

`{{IdToken}}` 은 **그 환경의** Cognito 풀에서 받은 것이어야 한다(환경마다 풀이 다르다).

```bash
ENV=dev   # dev | stg
SERIAL=<대상 기기 시리얼>

# ① S1 긴급호출 — sub 해석 경로의 핵심. 기대 200
#    ⚠️ 운영계에서는 호출하지 않는다. 실제 출동 요청이 나간다
curl -s -w "\n%{http_code}\n" -X POST \
  "https://streaming-${ENV}.namuhx.com:8082/streaming/v1/devices/${SERIAL}/s1/dispatch" \
  -H "IdToken: {{IdToken}}" -H "Content-Type: application/json" \
  -d '{"userName":"테스트","eventTime":1756000000000}'

# ② 라이브뷰 시작 / ③ 종료 / ④ 도킹 해제 — 전부 200
# ⑤ 타인 기기 시리얼로 호출 → 403
# ⑥ 깨진 토큰 → 401
# ⑦ signal 헬스체크 → 200   (라우트가 /signal 접두어 아래다. /health 로 부르면 404)
curl -s -o /dev/null -w "%{http_code}\n" "https://streaming-${ENV}.namuhx.com:3000/signal/health"
# ⑧ signal — 키 없이 → 401
curl -s -o /dev/null -w "%{http_code}\n" "https://streaming-${ENV}.namuhx.com:3000/signal/rooms"

# ⑨ 플랫폼 세션 정보에 sub 가 실리는지
curl -s -H "IdToken: {{IdToken}}" "https://app-api-${ENV}.namuhx.com/api/app/hello-world"

# ⑩ medcare — PHR·대화 목록이 sub 단독으로 동작하는지
curl -s -H "IdToken: {{IdToken}}" "https://medcare-${ENV}.namuhx.com/medcare/v1/phr"
curl -s -H "IdToken: {{IdToken}}" "https://medcare-${ENV}.namuhx.com/medcare/v1/conversations"
```

- ②③④ 에서 **500 + `"check-member 응답에 loginId 가 없다"`** 가 나오면 **플랫폼 신버전 미배포 신호**다.
- `GET /streaming/v1/devices/{serial}/subscription` 은 **판정에서 제외한다** — 이 트랙과 무관한 기존 배선 오류이고 앱 실사용도 0건이다(§8-3 의 별건 1번).
- medcare 만 `X-Internal-Api-Key` 를 싣는다(기존 동작 그대로). security·streaming 은 키 없이 호출한다.

### 7-5. 롤백

| 대상 | 방법 | 주의 |
|---|---|---|
| S1 (plaform) | 이전 태그로 재배포 | 소비자가 이미 `sub` 로 호출 중이면 400 — **함께 되돌릴 것** |
| S3 (소비자 3개) | 개별 롤백 가능 | 구버전은 `loginId` 로 호출하고 신 플랫폼이 계속 수용한다 |
| S2 (백필) | 스냅샷 테이블로 정확한 행만 삭제 (부록 C §3) | **`id` 범위로 지우지 않는다** — 그 사이 자가치유가 넣은 행까지 지워진다 |
| S0 정정 | 백업 테이블로 복구 (부록 D §3) | `create_date` 까지 복구해야 한다 |
| S6 (읽기 축소) | 콘솔에서 설정 복원 | 신 토큰에만 적용돼 즉효가 아니다(구 토큰 최대 1시간 공존) |
| S7 (파기) | **불가** | 비가역. 게이트를 반드시 통과한 뒤에만 |

모든 머지는 `--no-ff` 머지 커밋이므로 `git revert -m 1 <머지커밋>` 하나로 저장소 단위 되돌리기가 된다. 리베이스 전 상태는 각 저장소의 `backup/pre-rebase-20260824` 태그에 있다.

> **피처 브랜치를 지우지 말 것.** `backend-api-main` 은 `stg`·`main` 승격도 피처 브랜치를 각 환경 브랜치에 직접 머지하는 방식이다(`Merge branch 'x' into stg`). 브랜치가 없으면 승격 경로가 사라진다.
>
> 살아 있어야 하는 브랜치: `backend-api-main` `fix/remove-unauthenticated-updatesub` · `skix-medcare` `feature/sub-only-token` · `skix-security` `feature/sub-only-token` · `skix-streaming` `feature/sub-only-token` · `skix-streaming-signal` `fix/remove-pii-logging`

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **개발계·검증계 S4 관찰 결과 확인.** 부록 E 의 명령으로 `SUB_IDENTITY_MISMATCH`·`SUB_PREEMPTED` 가 0 인지, `SUB_FALLBACK layer=claim` 이 줄고 있는지 본다. 8/24 배포 후 한 달 가까이 지났으므로 충분한 표본이 쌓여 있다 | 백엔드 | 로컬 맥에서 불가. VDI 필요 |
| 2 | **운영계 `main` 승격 계획 확인.** 5저장소 전부 미승격이고, `skix-security` 는 선별 머지가 필요하다(§2-4). 승격 자체가 운영 릴리스와 묶여 있으므로 릴리스 담당과 일정을 맞춘다 | 백엔드 | |
| 3 | **앱 스토어 릴리스 일정 확인.** iOS·Android 모두 개발 브랜치에는 구현돼 있으나 운영 브랜치는 2026-07 버전이다. **이 릴리스가 S6·S7 의 게이트다** | 앱 담당 | §2-3 |
| 4 | 개발계·검증계에서 **앱 실기기 확인** — 특히 소셜 로그인 3종(카카오·구글·애플). 해당 환경 빌드가 나와 있는지부터 확인 | 앱 담당 + 백엔드 | S5 |
| 5 | 개발계·검증계 **S6 진행 여부 결정.** 원래 계획은 두 환경에서 S7 까지 완주해 운영계 리허설로 삼는 것이었다. 4번이 끝나야 진입할 수 있다 | 백엔드 | §7-1 게이트 |

### 8-2. 운영계 릴리스 게이트

**(a) S1~S3 배포 전**

| # | 확인할 것 |
|---|---|
| 6 | **S0 사전 점검 4종 통과** (부록 B). 기대 `0 · 0 · 0 · 1 이상`. 하나라도 어긋나면 배포하지 않는다. 배포일 당일, S1 직전에 돌린다 |
| 7 | 검증계가 S4 까지 통과해 있을 것 |
| 8 | RDS 스냅샷 확보 |

**(b) S6(읽기 속성 축소) 전 — 전부 확정되기 전에는 진행 금지**

| # | 확인할 것 |
|---|---|
| 9 | `backend-api-main` 의 `SUB_FALLBACK layer=claim` 이 그 환경에서 **7일간 0건**(운영계 기준. dev/stg 는 트래픽이 적으니 기간보다 **주요 경로 실호출 확인**으로 대체) |
| 10 | 앱 전환 버전이 그 환경에 보급됨 (운영계는 스토어 보급률 확인. 실측상 30일 내 96% 도달) |
| 11 | 🚨 **iOS 소셜 로그인이 `phone_number` 의존을 끊은 버전인지 확인.** 빠뜨리면 카카오·구글·애플이 한꺼번에 죽는다(§6-1 A-2) |
| 12 | 파기 대상 속성의 **서버 소비 0건** — 코드상으로는 충족됐다(`f7b23a96` 으로 백오피스 목록이 DB 조회가 되어 마지막 소비자가 사라졌다). **그 커밋이 그 환경에 배포됐는지만** 본다 |
| 13 | `skix-streaming` 의 sub 전환이 그 환경에 배포됐는지 (§5 의 13번) |

> ⚠️ **`layer=claim` 을 "서버 4개 모두 0건"으로 읽지 말 것.** 나머지 3개 서버는 폴백을 **제거**했으므로 이 마커를 애초에 내지 않는다. 그쪽에 필요한 확인은 **신버전이 배포돼 실트래픽을 받고 있는지**다. 로그가 없는 것을 통과 신호로 읽으면 안 된다.

**(c) S7(파기) 전**

| # | 확인할 것 |
|---|---|
| 14 | S6 을 마치고 확인 5종(부록 F) 통과 |

### 8-3. 후속 — 이 트랙에서 파생됐으나 별건으로 넘긴 것

**(A) 정책까지 확정하고 후속 트랙으로 넘긴 것 — 탈퇴 행 부활 / 익명화 / retention 테이블**

Phase 0 실측 중에 발견한 별건 버그다. **탈퇴한 사용자가 같은 전화번호로 재가입하면 `user` 행이 새로 생기지 않고 기존 행이 되살아나, `user.id` 에 FK 로 물린 과거 데이터가 통째로 복원된다.** 현재 96건이다.

- **원인**: `UserMapper.xml` 의 가입 SQL 2개(`createUser`·`insertSocialUser`)가 `ON DUPLICATE KEY UPDATE` 를 쓰는데, `user_login_id_IDX`(UNIQUE) 충돌 시 `delete_yn = 'N'` 만 되돌리고 `exit_*`·`create_date` 는 그대로 두며 **`id` 를 유지한다.**
- **영향 판정(실측 완료)**: 96건 전부 12개월 이내 재가입(72%는 1개월 이내), 12개월 이상 0건 → 번호 재배정으로 타인이 된 사례는 없다. 승계된 건물 멤버십 43행은 전부 **이미 삭제된 건물**의 것이라 제3자 실노출은 **0건**이다. 따라서 이건 노출 사고가 아니라 **탈퇴 시 파기 의무 위반**이다. 심각도 중간.
- **확정된 설계 (2026-08-24)**:
  - **①완전 신규 취급** — 재가입 시 과거 데이터를 승계하지 않는다.
  - **②탈퇴 시 즉시 파기(익명화)** — hard DELETE 는 FK 8개 때문에 불가하므로 행은 남기되 식별 값을 지운다. `login_id = CONCAT('WTHD:', id)`, `name`·`birth_date`·`gender`·`sub`·`ble_key`·`google_key`·`masking_key` 는 NULL. `exit_code`·`exit_reason` 은 PII 가 아니므로 통계용으로 남긴다.
    - **핵심 통찰**: `login_id` 를 익명화하면 UNIQUE 충돌이 사라져 **ODKU 부활 버그가 원인째 죽는다.** ODKU 제거는 방어적 정리일 뿐이다.
  - **③법정 보존분은 `retention_*` 별도 테이블** + `purge_after` 만료 배치. 실측상 `contract` 는 계약자명·사용자명·주소·통합고객번호 등 **PII 를 직접 보유**하므로 스냅샷 이관 후 원본 컬럼을 파기하고, `user_terms` 는 자체 PII 는 없으나 **익명화하면 동의 주체 입증력이 죽으므로** 당시 `login_id`·`name` 스냅샷을 함께 보관해야 한다.
    - ⚠️ **별도 스키마가 아니라 별도 테이블을 택했으므로, 운영 앱 DB 계정에서 `retention_*` SELECT 권한을 제외**해야 한다. 안 하면 분리 보관이 이름뿐이고 그 선택이 규정 위반으로 남는다.
  - **④만료 파기 배치** — `purge_after` 가 지난 행을 삭제한다. "기한 도래 시 지체 없이 파기"까지가 한 세트다.
  - **소급**: 기존 탈퇴자 행에는 ①②③을 소급 적용한다. **96건 부활 행은 건드리지 않는다** — 전부 동일인 재가입이라 그건 현재 활성 사용자의 자기 데이터이고, 소급 파기하면 살아 있는 사용자의 홈·기기가 끊긴다. `exit_*` 잔재만 정리한다.
- **남은 선행 확인 1건**: 공개된 개인정보 처리방침의 보유·이용기간 표와 대조(개인정보 담당·법무). **표에 보존 예외가 없다면 처리방침 개정이 구현보다 먼저**다.
- **착수 조건과 브랜치**: 이 Cognito 트랙의 배포 안정화 후에 착수한다. `backend-api-main` 의 `feature/medcare-withdrawal-purge`(`b2138c78`, 탈퇴 시 medcare 데이터 파기 연계)를 이 트랙으로 흡수해 **한 브랜치·단독 배포**로 간다. 넷 다 같은 탈퇴 시나리오를 바꾸므로 쪼개 배포하면 "medcare 는 파기됐는데 플랫폼엔 개인정보가 남는" 중간 상태가 운영에 생긴다.
  - ⚠️ **기준일 실측: `b2138c78` 은 이미 `origin/dev`·`origin/stg` 에 들어가 있다**(`main` 미반영). "미push" 로 적힌 옛 메모는 낡았다. 흡수 계획을 세울 때 현재 브랜치 상태부터 다시 확인할 것.
- **이 Cognito 트랙과 무충돌 확인 완료**: 탈퇴자는 Cognito 계정 자체가 삭제되므로 토큰이 없어 shim 에 도달하지 않고, 백필·게이트 쿼리는 전부 `delete_yn='N'` 필터라 익명화된 행을 보지 않는다.

**(B) 개발계·검증계 검증 중 발견한 별건 6종 — 전부 이 트랙과 무관**

| # | 항목 | 내용 |
|---|---|---|
| 1 | **`GET /streaming/v1/devices/{serial}/subscription` 이 항상 404** | `skix-streaming` 의 `PlatformApiClient` 안에 `/security/v1/...` 로 시작하는 메서드 **8개**가 들어 있는데, 이 인터페이스는 `platform.url`(NLB **8082**)로 바인딩된다. security 서버는 **8080** 이다. `SecurityClientConfig` 라는 별도 설정이 이미 있으므로 그쪽으로 옮기면 된다. **현재 실사용 0** — Android 가 같은 API 를 security 직행판과 streaming 경유판 두 벌로 갖고 있는데 화면은 전자만 쓴다. ⚠️ **누가 streaming 판을 화면에 연결하는 순간 터진다** |
| 2 | **`HttpMessageNotReadableException` 이 500 으로 나간다** (`skix-streaming`) | 요청 바디가 잘못된 것은 클라이언트 잘못(400)인데 `GlobalExceptionHandler` 에 핸들러가 없어 최후 보루로 떨어진다. 이번에 `BusinessException` → 400 을 추가한 것과 같은 종류의 결함 |
| 3 | **PHR `userBirthDate` 가 미가공 7자리로 나가는 계정이 있다** | 예: `8903011`(= YYMMDDG). `GET /internal/user` 는 DB `birth_date` 를 가공 없이 돌려주는데, 그 계정은 가입 시점의 원본이 그대로 남아 있다. **iOS 는 이 값을 본인인증 파라미터로 그대로 전달**하므로 인증이 형식 오류로 실패할 수 있다. 해당 계정의 `birth_date` 를 ISO 형식으로 정정하는 **1회성 UPDATE** 로 해결 |
| 4 | **Cognito 스키마에 같은 뜻의 속성이 변형으로 중복** | `custom:ble_key`/`custom:bleKey`, `custom:googleKey`/`custom:crgoogleKey`, `birthdate`/`custom:birthdate`/`custom:birthday`/**`custom:birthiday`**(오타)/`custom:birthyear`, `nickname`/`custom:nickName`, `email`/`custom:email`. **값은 전부 0건**이라 지금 위험은 없다. Cognito 는 스키마 속성 **삭제가 불가**하므로 새 풀 없이는 정리할 수 없다. S6 목록을 "남길 것"으로 짜야 하는 이유가 여기 있다(§5 의 14번) |
| 5 | **streaming·signal·kms 가 재부팅에 살아남지 못한다** | systemd·pm2·cron·rc.local 이 전부 없고 세 프로세스 모두 `nohup` 으로 떠 PPID=1 이다. 덧붙여 streaming 은 `-Xmx8192m` 인데 박스 전체 RAM 이 **7.6GB** 다(같은 박스에 Node 프로세스 2개 동거) |
| 6 | **`skix-streaming-kms` 저장소가 비어 있다** | 실물은 운영 EC2 의 `/home/appadmin/skix-streaming-kms` 에서 Node 로 돌고 있는데(포트 4000) 소스가 형상관리에 없다 |

**(C) 그 밖에 이 트랙에서 기록만 하고 넘긴 것**

| # | 항목 |
|---|---|
| 7 | **`skix-streaming` 의 운영 설정 파일에 평문 시크릿** — DB 비밀번호·클라우드 액세스 키가 저장소에 노출돼 있다. 시크릿 정리 트랙 담당자에게 전달했다 |
| 8 | **`/internal/**` 전반의 인증 부재** — `check-member` 가 멤버일 때 전화번호를 돌려주는 것 포함. **내부망 한정**(API Gateway 에 라우트 없음)이라 급하지 않으나, `enforce-api-key` 운영 플래그 상향과 묶어 별도 과제로 다룬다. 상향의 선행 조건은 **security·streaming 에 키 배선**이다 — 두 저장소는 `InternalUserController` 를 키 없이 호출하므로 플래그를 올리면 401 이 된다. (외부 협의는 선행 조건에서 빠졌다 — 오아시스 수신구가 이 공유키를 더는 쓰지 않는다. 그 공유키가 medcare 와 같은 시크릿이라 외부 파트너에 넘기면 medcare 내부 API 까지 열리기 때문이고, 오아시스 수신구의 방어선은 **NLB 소스 IP 허용목록**이다) |
| 9 | **signal 의 기존 로그 30일치에 토큰 원문 잔존** — 코드는 고쳤지만 이미 쌓인 파일에는 남아 있다. 파기 검토 필요 |
| 10 | **유령 `user` 행 정리** — 초기 소셜 가입으로 `login_id` 에 provider 회원번호가 들어간 행(`login_id` 에 10자리 provider 회원번호가 들어 있다). 원인은 2025-11-27 SMS 인증 패치로 막혔고 이번 배포는 `sub` 정정만으로 통과하므로 별건이다. 정리하려면 `user_id` 를 참조하는 **22개 테이블**을 전수 이관해야 한다 |
| 11 | **`user_accounts` DDL 컬럼 주석이 실데이터와 불일치** (§3-4) |
| 12 | **삭제된 건물의 멤버십이 탈퇴 정리에서 누락된다** — `Building.xml` 의 `getBuildingListByLoginId` 에 `ub.delete_yn = 'N'` 필터가 있어 삭제된 건물의 멤버십은 정리 루프가 돌지 않는다. `user_building_member` 에는 `delete_yn` 컬럼이 없어 하드 삭제만 가능한데 그 삭제가 호출되지 않는다. 현재 노출 영향은 없으나 데이터가 계속 쌓인다. (A) 트랙에 함께 넣는 것이 자연스럽다 |
| 13 | **iOS `BuildingMember.updateAsync()` 파라미터 불일치** (§6-1 A-4) |
| 14 | **재가입 시 `icst_no`(통합고객번호)를 NULL 로 덮는 기존 결함** — 이번 범위 밖 |

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| **"로그인이 안 돼요" / `hello-world` 가 404** | 먼저 `SUB_FALLBACK`·`SUB_IDENTITY_MISMATCH`·`SUB_PREEMPTED` 를 grep 한다(부록 E). 3층 자가치유가 돌지 않았다면 그 사용자의 `user_accounts` 행 상태를 본다:<br>`SELECT ua.* FROM user_accounts ua JOIN user u ON u.id = ua.user_id WHERE u.login_id = '<전화번호>';` |
| **소셜 로그인만 실패** | `user_accounts` 에 그 provider 의 행이 있는지 본다. 없으면 2층으로 해석되는데 `user.sub` 는 최초 가입 provider 것이라 빗나간다 → 3층 자가치유가 받아야 정상이다. **S6(읽기 속성 축소) 이후라면 3층이 비어 있으므로 실패가 정상 동작**이고, 그 사용자는 수동으로 행을 심어야 한다 |
| **`SUB_IDENTITY_MISMATCH` 가 찍혔다** | 최우선 조사. 두 가지 경우가 같은 신호를 낸다 — (가) 전화번호를 막 바꾼 직후의 구 토큰(정상, 토큰 만료까지 기다리면 사라진다) (나) **sub 선점 — 타인 신원으로 동작 중**(즉시 조치). 부록 D 의 판정 쿼리로 가른다 |
| **`SUB_PREEMPTED` 가 찍혔다** | 자가치유 INSERT 가 UNIQUE 충돌. 그 sub 를 다른 사용자가 점유 중이다. 부록 D 케이스 A 로 정정한다 |
| **`SUB_FALLBACK layer=user.sub` 가 같은 sub 로 반복** | 자가치유 실패다. 정상이면 사용자당 1회만 찍힌다. `healUserAccount` 가 삼킨 예외를 `user_accounts 자가치유 실패` WARN 으로 찾는다 |
| **백필 대상이 몇 명인지** | 고정값이 아니다. 부록 C 의 `target_rows` 가 그 시점의 실제 수다. 운영 실측 기준 약 1,160명 예상 |
| **특정 사용자가 몇 개의 Cognito 계정을 갖는지** | `SELECT ua.sub, ua.auth_provider, ua.create_date FROM user_accounts ua JOIN user u ON u.id = ua.user_id WHERE u.login_id = '<전화번호>' ORDER BY ua.create_date;` |
| **서버 로그 키워드** | `SUB_FALLBACK` · `SUB_IDENTITY_MISMATCH` · `SUB_PREEMPTED` · `자가치유`. 마커를 내는 클래스가 `AwsTokenUtil`·`SubLoginIdResolver` 둘뿐이라 오탐이 없다 |
| **`skix-security` 로그에서 `check-member` 응답 본문이 안 보인다** | 의도다. `[REDACTED:PII]` 가 기록된다(§3-4). 값이 그냥 비어 있으면 그건 결함 신호다 |

---

## 부록 A. 핵심 커밋 (시간순)

기준일에 `--is-ancestor` 로 실측한 것이다. 모든 머지는 `--no-ff` 다.

### `backend-api-main` — 브랜치 `fix/remove-unauthenticated-updatesub`, dev 머지 `d028844f`

| 날짜 | 커밋 | 내용 |
|---|---|---|
| 08-22 | `6ba4293c` | 무인증 `updateSub` 엔드포인트 삭제(90줄). 호출자 전수 0건 확인 |
| 08-22 | `1b87419d` | 이름 조회를 Cognito `adminGetUser` → DB 로(121줄 삭제) |
| 08-22 | `d4df3322` | **핵심** — `getLoginId()` 를 sub 기반 3층 해석으로 |
| 08-22 | `dcbb2ea4` | 내부 API 가 `sub` 파라미터 additive 수용. `SubLoginIdResolver` 추출 |
| 08-22 | `59b359a4` | `check-member` 응답에 해석된 `loginId` 포함 |
| 08-22 | `8a89ba67` | 세션 정보(`hello-world`) 응답에 `sub` 추가 |
| 08-22 | `e47952af` | 중복 sub 를 클레임으로 복구. 오픈API 해석기를 공용 규칙으로 통일 |
| 08-22 | `3868ec59` | 무인증 `check-member` 의 전화번호 노출 차단 + 프로필 `sub` 를 토큰 기준으로 |
| 08-22 | `36e684d0` | 1층 해석이 클레임과 어긋나면 `SUB_IDENTITY_MISMATCH` 로 남긴다 |
| 08-23 | `4fa26de4` | `getLoginId` 의 예외 계약을 공개 메서드 수준에서 고정(테스트) |
| 08-23 | `86cb9efe` | sub 조회에 내부 API 키 강제 |
| 08-23 | `64e7a8d5` | **2층 자가치유** — 레거시 폴백으로 해석된 계정도 권위 테이블에 심는다 |
| 08-23 | `de32f7f6` | 비밀번호 변경이 토큰 `phone_number` 클레임을 직접 읽지 않게 |
| 08-24 | `bac0492d` | 내부 API 키 검증을 `verify` 하나로 되돌림(`verifyAlways` 제거) |
| 08-24 | `f7b23a96` | 백오피스 사용자 목록을 Cognito → DB. 커서 페이징·기간검색 신규. 테스트 11건 |

### 소비자 4저장소

| 날짜 | 저장소 | 커밋 | 내용 |
|---|---|---|---|
| 08-22 | skix-medcare | `04084ec` | 토큰 3단 PII 폴백 제거 → sub 단독 식별 |
| 08-22 | skix-medcare | `f7a26e0` | 스크래퍼에 전달되는 값이 해석된 전화번호임을 고정(테스트) |
| 08-22 | skix-security | `b1b148b` | 필터 sub 단독 + `CallerIdentity` 재설계 + `check-member` 응답으로 해석 |
| 08-23 | skix-security | `cf5f56b` | `check-member` 응답을 아웃바운드 로그에 미저장(`[REDACTED:PII]`) |
| 08-22 | skix-streaming | `a681e06` | 인증 필터와 S1·스트리밍 경로의 PII 로그 제거 |
| 08-22 | skix-streaming | `48af14c` | 호출자 없는 `CognitoJwtValidator.extractLoginId` 제거(데드코드) |
| 08-22 | skix-streaming | `1b629e2` | 토큰 PII 클레임 의존 제거 + 인가 응답으로 `loginId` 해석 |
| 08-22 | skix-streaming | `280b6da` | 인가만 필요한 호출의 `X-Target-Login-Id` 강제 해제 + `BusinessException` → 400 |
| 08-22 | skix-streaming-signal | `3871180` | 토큰 원문·요청 헤더 로깅 중단 |

### 머지 커밋

| 저장소 | dev | stg |
|---|---|---|
| backend-api-main | `d028844f` | (dev 머지) |
| skix-medcare | `f8ddf59` | `628ac40` |
| skix-security | `105d453` (+ `cf5f56b` 직접 cherry-pick) | `12bb524` |
| skix-streaming | `c651497` | `b92dc4a` |
| skix-streaming-signal | `210813c` | `7103a66` |

> `skix-medcare` 의 stg 는 **두 단계로 나눠 머지했다.** dev 를 통째로 넣으면 별개 트랙(7커밋)이 이 트랙과 한 머지 커밋에 섞여 되돌릴 때 분리할 수 없기 때문이다. 먼저 그 트랙만 stg·main 에 올리고(`02d2a29`·`9cb2d05`), 그 다음 dev 를 머지해 이 트랙 2커밋만 넘겼다(`628ac40`).

### 검증 규모 (2026-08-24 기준)

| 저장소 | 테스트 |
|---|---|
| backend-api-main | 159 통과 (dev 기존 실패 3건은 이 트랙과 무관) |
| skix-medcare | 554 통과 |
| skix-security | 433~435 통과 |
| skix-streaming | 신규 실패 0 (기존 깨진 7건은 dev 동일) |
| skix-streaming-signal | `node --check` 통과 |

구현 후 **10회 적대적 리뷰**로 제기 86 · 확정 43 · 반박 22를 거쳤고 확정분은 전부 수정했다.

---

## 부록 B. S0 — 배포 직전 데이터 사전 점검

각 환경에서 **S1 배포 직전에** 한 번 돌린다. 확인 30초다. 미리 돌려 두고 체크하는 것은 무의미하다 — 이 단계의 존재 이유가 정정 시점과 배포 시점 사이의 간격을 메우는 것이기 때문이다(§5 의 10번).

```sql
SELECT
    -- ① 선점: 내 sub 를 남의 user_accounts 행이 물고 있다
    (SELECT COUNT(*) FROM `user` u
      WHERE u.delete_yn = 'N' AND u.sub IS NOT NULL
        AND EXISTS (SELECT 1 FROM user_accounts ua
                     WHERE ua.sub = u.sub COLLATE utf8mb4_general_ci
                       AND (ua.user_id IS NULL OR ua.user_id <> u.id))
    )                                                                AS preempted,

    -- ② 중복: 활성 사용자 둘 이상이 같은 user.sub 를 갖는다
    (SELECT COUNT(*) FROM `user` u
      WHERE u.delete_yn = 'N' AND u.sub IS NOT NULL
        AND EXISTS (SELECT 1 FROM `user` u2
                     WHERE u2.id <> u.id AND u2.sub = u.sub AND u2.delete_yn = 'N')
    )                                                                AS duplicated,

    -- ③ 권위 테이블 자체의 sub 중복
    (SELECT COUNT(*) FROM (
        SELECT sub FROM user_accounts WHERE sub IS NOT NULL
         GROUP BY sub HAVING COUNT(*) > 1) x
    )                                                                AS dup_account_sub,

    -- ④ user_accounts(sub) 단일 컬럼 UNIQUE 가 실제로 존재하는가
    (SELECT COUNT(*) FROM (
        SELECT index_name FROM information_schema.statistics
         WHERE table_schema = DATABASE() AND table_name = 'user_accounts'
           AND non_unique = 0
         GROUP BY index_name
        HAVING COUNT(*) = 1 AND MAX(column_name) = 'sub') z
    )                                                                AS unique_index_on_sub_only;
```

**기대: `0 · 0 · 0 · 1 이상`. 하나라도 어긋나면 배포하지 않는다.**

- **①②** 는 데이터를 본다. 어긋나면 **부록 D** 로 정정한 뒤 다시 돌린다.
- **③④** 는 정정과 무관한 검사다. 특히 ④ 는 **데이터가 아니라 스키마**를 본다. 백필의 선점 방어가 이 UNIQUE 존재를 전제하므로, 데이터가 깨끗해도 인덱스가 사라졌거나 복합 인덱스로 바뀌었으면 백필을 돌리면 안 된다. `dup_account_sub = 0` 은 근거가 되지 못한다 — 인덱스가 없어도 지금 중복이 없으면 0 이 나온다.
- 저장소의 DDL 기준 `user_accounts_pk` 가 `sub` 단독 UNIQUE 이므로 ④ 는 `1` 이 나와야 정상이다.

---

## 부록 C. S2 — `user_accounts` 백필 SQL

**선행 조건: 부록 B 통과 + S1 이 그 환경에 배포 완료.** 커밋만으로는 부족하다. 배포 확인은 §7-3.

운영계에서는 스냅샷 테이블 이름을 `backfill_targets_prd` 등 환경이 드러나게 바꾼다. 아래는 그 형태로 적었다.

### 0. 선행 DDL — 반드시 먼저, 트랜잭션 밖에서

```sql
-- user.sub 에 인덱스가 없으면 아래 상관 서브쿼리가 약 7,100만 행을 읽고
-- 그동안 user 테이블이 15초간 쓰기 잠긴다(실측 15.4s → 0.028s). 로그인·가입이 함께 멈춘다.
CREATE INDEX ix_user_sub ON `user` (sub);
-- 이미 있으면 ERROR 1061 — 무시하고 다음으로.

-- 실행 기록용 스냅샷 테이블. DDL 이라 반드시 트랜잭션 밖에서 만든다
-- (START TRANSACTION 안에서 CREATE TABLE 을 하면 암묵적 커밋이 일어나 트랜잭션이 깨진다).
--
-- ⚠️ sub 의 COLLATE 를 명시한다. 생략하면 DB 기본값(utf8mb4_0900_ai_ci)을 상속받는데,
--    user_accounts.sub 는 utf8mb4_general_ci 라 §3 복구의 JOIN 이 ERROR 1267 로 실패한다.
--    되돌려야 하는 순간에 못 되돌리게 되는 함정이라 여기서 맞춰 둔다.
CREATE TABLE backfill_targets_prd (
    user_id       BIGINT       NOT NULL PRIMARY KEY,
    sub           VARCHAR(128) COLLATE utf8mb4_general_ci NOT NULL,
    auth_provider VARCHAR(20)  NOT NULL
);
```

### 1. 실행 — 하나의 트랜잭션 안에서 끝낸다

동시 자가치유가 같은 sub 를 먼저 넣으면 UNIQUE 위반으로 트랜잭션 전체가 실패한다. 그건 **안전한 실패**이므로 ROLLBACK 하고 그대로 다시 돌리면 된다(멱등).

```sql
START TRANSACTION;

-- 1a. 대상 집합을 고정한다.
INSERT INTO backfill_targets_prd (user_id, sub, auth_provider)
SELECT
    u.id,
    u.sub,
    CASE u.auth_provider
        WHEN 'K' THEN 'Kakao'
        WHEN 'N' THEN 'Naver'
        WHEN 'G' THEN 'Google'
        WHEN 'S' THEN 'SignInWithApple'
        ELSE 'EMAIL'                      -- auth_provider IS NULL
    END
FROM `user` u
WHERE u.delete_yn = 'N'
  AND u.sub IS NOT NULL
  -- 실측으로 확인된 값만 허용. 예상 밖 값이 생기면 조용히 넘어가지 말고 제외되게 둔다.
  AND (u.auth_provider IS NULL OR u.auth_provider IN ('K','N','G','S'))
  -- 이미 등재된 sub 제외. **사용자 단위가 아니라 sub 단위**로 본다 —
  -- 부(副)계정으로만 치유된 사용자는 행이 있어도 user.sub 가 여전히 미등재다.
  AND NOT EXISTS (SELECT 1 FROM user_accounts ua
                   WHERE ua.sub = u.sub COLLATE utf8mb4_general_ci)
  -- 같은 user.sub 를 가진 다른 활성 사용자가 있으면 양쪽 다 제외.
  -- 상대의 등재 여부를 조건에 넣으면 안 된다 — 한쪽만 치유된 경우 타인이 소유자로 등재된다.
  AND NOT EXISTS (SELECT 1 FROM `user` u2
                   WHERE u2.id <> u.id
                     AND u2.sub = u.sub
                     AND u2.delete_yn = 'N');

-- 1b. 대상 수 확인. 운영계 실측 기준 약 1,160건 예상(고정값이 아니다).
SELECT COUNT(*) AS target_rows FROM backfill_targets_prd;

-- 1c. 등재.
INSERT INTO user_accounts (user_id, sub, auth_provider)
SELECT t.user_id, t.sub, t.auth_provider
FROM backfill_targets_prd t;

-- 1d. 검증 — 같은 트랜잭션·같은 스냅샷이므로 **정확히 일치해야 한다.**
SELECT ROW_COUNT()                                  AS inserted_rows,
       (SELECT COUNT(*) FROM backfill_targets_prd)  AS target_rows;
-- 다르면 여기서 ROLLBACK 하고 원인을 확인한다. 추측하지 않는다.

-- 1e. 일치하면 커밋, 아니면 롤백.
COMMIT;
-- ROLLBACK;
```

### 2. 커밋 후 검증

```sql
-- 2a. 잔존 확인 — **두 값의 기대치가 다르다.** 같은 숫자를 기대하면 정상을 실패로 판정한다.
SELECT
    -- user.sub 가 미등재인 사용자 = 백필이 고치려던 대상. 정정을 마쳤다면 0.
    (SELECT COUNT(*) FROM `user` u
      WHERE u.delete_yn = 'N' AND u.sub IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM user_accounts ua
                         WHERE ua.sub = u.sub COLLATE utf8mb4_general_ci)
    )                                                                AS sub_not_registered,
    -- 계정 행이 아예 없는 사용자. user.sub 가 NULL 인 사람이 여기 남는다 — 0 이 아닌 것이 정상.
    (SELECT COUNT(*) FROM `user` u
      WHERE u.delete_yn = 'N'
        AND NOT EXISTS (SELECT 1 FROM user_accounts ua WHERE ua.user_id = u.id)
    )                                                                AS no_account_row;
-- 기대: sub_not_registered = 0

-- 2b. 무결성 (전부 0)
SELECT SUM(sub IS NULL)           AS null_sub,
       SUM(user_id IS NULL)       AS null_user_id,
       SUM(auth_provider IS NULL) AS null_provider
FROM user_accounts;

-- 2c. 고아 행 (0)
SELECT COUNT(*) AS orphan_rows
FROM user_accounts ua
LEFT JOIN `user` u ON u.id = ua.user_id
WHERE ua.user_id IS NOT NULL AND u.id IS NULL;

-- 2d. provider 분포 — 스냅샷과 대조한다. 하드코딩된 기대값은 두지 않는다.
SELECT auth_provider, COUNT(*) AS added
FROM backfill_targets_prd
GROUP BY auth_provider ORDER BY added DESC;
```

> **매핑 근거 (실측 확정 값 도메인)**
> `user.auth_provider` : `NULL` · `K` · `N` · `G` · `S` (단문자)
> `user_accounts.auth_provider` : `EMAIL` · `Kakao` · `Naver` · `Google` · `SignInWithApple` (원형)
> ⚠️ `user_accounts` DDL 컬럼 주석은 `'EMAIL, GOOGLE, NAVER, KAKAO'`(대문자)로 **틀리게** 적혀 있다. 주석을 믿지 말 것.

### 3. 복구 (커밋 후에 되돌려야 할 때)

```sql
-- 스냅샷이 이 실행이 넣은 행을 정확히 지목한다. id 범위로 지우지 않는다 —
-- 그 사이 자가치유가 넣은 행까지 함께 지워질 수 있다.
START TRANSACTION;
DELETE ua FROM user_accounts ua
  JOIN backfill_targets_prd t
    ON t.user_id = ua.user_id AND t.sub = ua.sub;
SELECT ROW_COUNT() AS deleted_rows;   -- target_rows 와 같아야 한다
COMMIT;
```

스냅샷 테이블은 백필이 안정됐다고 판단될 때까지 **남긴다**(`sub` 는 UUID 라 개인정보가 아니다).

### 4. 여기서 안 들어간 사람들

`user.sub` 가 NULL 인 사용자, 미접속 사용자, 그 밖의 제외분은 shim 의 자가치유가 로그인 시 등재한다. 다만 그 경로는 전환기 클레임에 의존하므로 **S6(읽기 속성 축소) 전에 로그인해야 한다.** 그 뒤에는 클레임이 없어 치유되지 않는다. 명단은 S4 관찰 기간에 개별 추적한다.

---

## 부록 D. 선점·중복 정정 절차 (부록 B 가 ①②에서 0 이 아닐 때)

정정 자체는 2026-08-24 에 세 환경 모두 끝냈다. 이 절차는 **재오염이 생겼을 때**를 위해 남긴다.

### 1. 대상 확보

```sql
-- A. 선점 — 내 sub 를 남의 user_accounts 행이 물고 있다
SELECT u.id AS user_id, COALESCE(u.auth_provider,'(NULL=전화번호가입)') AS provider, u.create_date
FROM `user` u
WHERE u.delete_yn = 'N' AND u.sub IS NOT NULL
  AND EXISTS (SELECT 1 FROM user_accounts ua
               WHERE ua.sub = u.sub COLLATE utf8mb4_general_ci
                 AND (ua.user_id IS NULL OR ua.user_id <> u.id))
ORDER BY u.id;

-- B. 중복 — 활성 사용자 둘 이상이 같은 user.sub 를 갖는다. login_id 형식도 함께 본다
SELECT u.id, u.login_id, u.sub, u.name, u.auth_provider, u.create_date,
       CASE WHEN u.login_id REGEXP '^010[0-9]{7,8}$' THEN '전화번호' ELSE '⚠️비정상' END AS 형식
FROM `user` u
WHERE u.delete_yn = 'N' AND u.sub IS NOT NULL
  AND EXISTS (SELECT 1 FROM `user` u2
               WHERE u2.id <> u.id AND u2.sub = u.sub AND u2.delete_yn = 'N')
ORDER BY u.sub, u.id;
-- 형식이 '⚠️비정상' 인 쪽이 유령 행이다(초기 소셜 가입 시 login_id 에 provider 회원번호가 들어간 행).

-- C. 그 sub 들을 현재 누가 물고 있는가
--    ⚠️ COLLATE 를 빼면 ERROR 1267 이 난다
SELECT ua.id, ua.user_id, ua.sub, ua.auth_provider
FROM user_accounts ua
WHERE ua.sub IN (
    SELECT u.sub COLLATE utf8mb4_general_ci FROM `user` u
    WHERE u.id IN ( /* A·B 결과의 user_id 전체 */ ));
```

### 2. Cognito 에서 실소유자 판정

**판정 기준은 Cognito 다.** DB 의 `user.sub` 는 이 문제의 원인일 수 있는 값이라 근거로 쓸 수 없다. VDI 의 CloudShell 에서 실행한다.

```bash
POOL=<해당 환경의 user-pool-id>

# 이 sub 를 가진 Cognito 계정의 전화번호를 확인한다
aws cognito-idp list-users --user-pool-id "$POOL" \
  --filter 'sub = "<확인할 sub>"' \
  --query 'Users[].{username:Username,attrs:Attributes[?Name==`phone_number`||Name==`identities`]}'

# 한 사람이 여러 계정을 가질 수 있다(실측 19%). 어느 sub 가 어느 provider 것인지 전부 뽑는다
aws cognito-idp list-users --user-pool-id "$POOL" \
  --filter 'phone_number = "+8210XXXXXXXX"' \
  --query 'Users[].{sub:Attributes[?Name==`sub`]|[0].Value,
                    idp:Attributes[?Name==`identities`]|[0].Value}'
```

- 이 풀은 전화번호 로그인 구성이라 `Username` 은 UUID(= `sub`)이고 전화번호는 `phone_number` 속성에 있다. 나온 값을 `+82` → `0` 으로 되돌린 것이 실소유자의 `user.login_id` 다.
- `identities` 가 없으면 전화번호 가입 → `auth_provider = 'EMAIL'`. 있으면 그 안의 `providerName` 이 `Google`/`Kakao`/`Naver`/`SignInWithApple` (**원형 표기**).
- **Cognito 에 그 sub 를 가진 계정이 아예 없으면** `user.sub` 가 오염된 값이다 → 케이스 C.

### 3. 정정

`user` 테이블은 **`sub` 컬럼만** 건드린다. `ble_key`·`google_key`·`masking_key` 는 절대 손대지 않는다(§5 의 18번).

```sql
-- ① 백업 먼저. 트랜잭션 밖에서 끝낸다.
--    ⚠️ START TRANSACTION 안에서 CREATE TABLE 을 하면 암묵적 커밋이 일어나 ROLLBACK 이 무력해진다.
CREATE TABLE user_accounts_bak_<YYYYMMDD> AS SELECT * FROM user_accounts;
CREATE TABLE user_sub_bak_<YYYYMMDD> AS
SELECT u.id, u.sub, u.login_id FROM `user` u
WHERE u.id IN ( /* §1 A·B 로 확보한 user_id 전체 */ );

-- ② 백업 확인 (0 행이면 대상 목록이 잘못된 것이다)
SELECT (SELECT COUNT(*) FROM user_accounts_bak_<YYYYMMDD>) AS ua_rows,
       (SELECT COUNT(*) FROM user_sub_bak_<YYYYMMDD>)      AS user_rows;

-- ③ 트랜잭션을 연다
START TRANSACTION;

-- 케이스 A — user_accounts 행의 주인이 틀렸다 (선점)
UPDATE user_accounts
   SET user_id = <실소유 user.id>, auth_provider = '<Cognito 에서 확인한 provider>'
 WHERE sub = '<문제 sub>';

-- 케이스 B — 엉뚱한 사람이 그 sub 를 user.sub 로 갖고 있다 (중복)
UPDATE `user` SET sub = '<B 의 실제 sub>' WHERE id = <B.id>;

-- 케이스 C — 유령 행 / Cognito 에 없는 sub
UPDATE `user` SET sub = NULL WHERE id = <해당 user.id>;

-- 커밋 전에 부록 B 게이트를 먼저 돌려 0 인지 확인한다. 아니면 ROLLBACK.
COMMIT;
```

> 🔎 **유령 행을 지우거나 병합하지 않는다.** `user.id` 를 참조하는 곳이 FK 8개 + `user_id` 컬럼을 가진 테이블 22개라 옮기는 작업은 위험도가 전혀 다르다. 배포를 막는 것은 **`sub` 충돌 하나뿐**이므로 그것만 푼다. 유령 행 정리는 별건이다(§8-3 (C) 10번).
>
> 케이스 C 로 `user.sub` 를 NULL 로 만들면 2층이 미스가 되고 다음 로그인에서 3층이 자가치유로 올바른 행을 심는다. **단 S6 전에 로그인해야 한다** — 그 뒤에는 전환기 클레임이 없어 3층도 미스가 되고 로그인이 막힌다. 해당 사용자는 S4 관찰 기간에 `user_accounts` 행이 생겼는지 개별 확인한다.

### 4. 복구 (커밋한 뒤에 잘못을 발견했을 때)

```sql
START TRANSACTION;

-- user_accounts 복구 — 정정으로 바뀐 행만 되돌린다(백필로 새로 들어온 행은 건드리지 않는다)
UPDATE user_accounts ua
  JOIN user_accounts_bak_<YYYYMMDD> b ON b.id = ua.id
   SET ua.user_id = b.user_id, ua.sub = b.sub, ua.auth_provider = b.auth_provider
 WHERE ua.user_id <> b.user_id OR ua.sub <> b.sub OR ua.auth_provider <> b.auth_provider;

-- 정정 중에 삭제한 행이 있었다면 되살린다.
-- ⚠️ create_date 를 빠뜨리면 안 된다 — DEFAULT CURRENT_TIMESTAMP 라 복구 시각으로 덮여
--    "원상복구"가 아니게 되고, 가입 시점을 근거로 하는 조사에서 잘못된 결론이 나온다.
INSERT INTO user_accounts (id, user_id, sub, auth_provider, create_date)
SELECT b.id, b.user_id, b.sub, b.auth_provider, b.create_date
FROM user_accounts_bak_<YYYYMMDD> b
WHERE NOT EXISTS (SELECT 1 FROM user_accounts ua WHERE ua.id = b.id);

-- user.sub 복구.  <=> 는 NULL 도 비교한다(케이스 C 대응)
UPDATE `user` u
  JOIN user_sub_bak_<YYYYMMDD> b ON b.id = u.id
   SET u.sub = b.sub
 WHERE NOT (u.sub <=> b.sub);

COMMIT;
```

복구 후에는 행 수뿐 아니라 `create_date` 까지 원상인지 확인한다.

---

## 부록 E. S4 — 관찰 명령

### 로그 위치 (`backend-api-main`)

| 환경 | 경로 | 로테이션 |
|---|---|---|
| 개발계 | `/opt/www/airbot/back-end/logs/app.log` | `app-%d{yyyy-MM-dd}.%i.log` |
| 검증계 · 운영계 | `/app/log/app.log` | 같음 · max 50MB · 총 400MB 상한 |

`root: INFO` 라 WARN·ERROR 는 전부 파일에 남는다. 로그 한 줄 형태:

```
2026-08-24 16:40:45.287  WARN AwsTokenUtil.resolveLoginId          239 : SUB_FALLBACK layer=claim
2026-08-24 16:41:02.115 ERROR AwsTokenUtil.warnIfClaimMismatch     260 : SUB_IDENTITY_MISMATCH sub=…
```

### 명령

```bash
# ① 최우선 — ERROR 두 개가 0건인가 (0 이면 통과)
grep -h "SUB_IDENTITY_MISMATCH\|SUB_PREEMPTED" /app/log/app*.log | wc -l
grep -h "SUB_IDENTITY_MISMATCH\|SUB_PREEMPTED" /app/log/app*.log    # 0 이 아니면 그대로 본다

# ② S6 게이트 — layer=claim 이 날짜별로 줄어 0 으로 수렴하는가
grep -h "SUB_FALLBACK" /app/log/app*.log | awk '{print $1, $NF}' | sort | uniq -c

# ③ 자가치유가 실제로 도는가 — 같은 sub 가 반복되면 치유 실패
grep -h "SUB_FALLBACK layer=user.sub\|자가치유 완료" /app/log/app*.log | tail -30

# ④ 실시간
tail -f /app/log/app.log | grep --line-buffered "SUB_"
```

⚠️ **`app*.log` 로 쓰는 이유는 로테이션이다.** `app.log` 는 오늘치뿐이고 과거는 `app-2026-08-23.0.log` 로 갈라져 있어, "7일간 0건"을 보려면 로테이션 파일까지 포함해야 한다. 개발계는 경로만 바꾼다.

**컨테이너라면** `/app/log` 는 컨테이너 내부 경로다. `aws ecs execute-command` 로 들어가거나, CloudWatch 로 보내고 있다면 Logs Insights 가 낫다(태스크 정의의 `awslogs-group` 확인):

```
fields @timestamp, @message
| filter @message like /SUB_IDENTITY_MISMATCH|SUB_PREEMPTED/ | sort @timestamp desc

fields @timestamp, @message
| filter @message like /SUB_FALLBACK/ | stats count() by bin(1d), @message
```

---

## 부록 F. S6·S7 — Cognito 속성 목록과 명령

### S6 — 읽기 속성 축소 (설정, 되돌릴 수 있음)

콘솔에서 **"남길 목록"으로 설정한다.** 이유는 §5 의 14번.

| 속성 | 읽기 | 이유 |
|---|---|---|
| `sub` | **유지** | iOS 가 BLE 키 복호화 키로 쓴다 |
| `custom:socialSignUp` | **유지** | iOS 소셜 로그인이 가입 완료 여부 판별에 쓴다 |
| `custom:create_date` | 유지(무해) | 가입일시. 개인정보 아님 |
| 그 밖의 전부 | **제거** | 아래 |

**제거 대상**
`phone_number` · `name` · `birthdate` · `gender` · `email` · `nickname` · `custom:login_id` · `custom:phone_number` · `custom:ble_key` · `custom:googleKey`

**값은 0건이지만 함께 제거**(변형 속성)
`custom:nickName` · `custom:birthday` · `custom:birthiday` · `custom:birthyear` · `custom:email` · `custom:id` · `custom:bleKey` · `custom:crgoogleKey` · `custom:birthdate` · `custom:delete_yn` · `custom:update_date`

> **값이 0건인 것도 읽기 제거를 그대로 한다.** readable 로 두면 이후 신규 가입이나 IdP 속성 매핑 변경으로 값이 생기는 순간 아무도 모르게 토큰에 다시 실린다. **읽기 제거는 지금의 값이 아니라 앞으로의 값을 막는 조치다.**

**확인 (5종 전부)**

```
1. 앱 재로그인 → ID 토큰을 Base64 디코딩해 제거 대상 클레임 부재 확인
2. 전화번호 로그인 정상 동작
3. 소셜 로그인 3종(카카오·구글·애플) 정상 동작
4. 프로필 화면에 이름·생일·성별·전화번호가 정상 표시 (= hello-world 경로 동작)
5. 백오피스 제품등록 사용자 목록 정상 표시
```

> 이 설정은 ID 토큰과 `GetUser`/`fetchUserAttributes()` **양쪽**에 적용된다. 구 토큰은 만료까지 최대 1시간 공존하므로 즉시 반영을 기대하지 않는다.

### S7 — 속성 파기 (비가역)

`AdminDeleteUserAttributes` 로 값 자체를 삭제한다. **S6 확인 5종이 전부 통과한 뒤에만** 진행한다.

| 속성 | 처리 |
|---|---|
| `name` `birthdate` `gender` `email` `custom:login_id` `custom:phone_number` | **파기** ← 실제 대상은 이 6종뿐 |
| `nickname` 외 변형 속성 7종 | 파기 대상이나 **값 0건**(운영계 전수 실측) → 실질 no-op |
| **`phone_number`** | **저장 유지** — 로그인 식별자다. 파기하면 로그인 불능 |
| `custom:ble_key` `custom:googleKey` `custom:create_date` | **파기하지 않는다** — 서버가 `adminGetUser`(IAM 자격)로 읽는다 |
| `sub` `cognito:username` | 대상 아님. `sub` 는 UUID, `username` 은 속성이 아니라 식별자라 삭제 불가 |

**왜 S6 만으로 부족한가** — 읽기 축소는 **유통 경로**를 끊지만 값은 Cognito 에 그대로 남는다. 파기는 **보관**을 끝낸다. 설정은 되돌려지므로 누가 복원하면 다음 토큰부터 개인정보가 다시 실린다. 값이 남아 있는 한 재유입 경로가 살아 있는 셈이다.

**전수 확인에 쓴 명령 (CloudShell)**

```bash
POOL=<해당 환경의 user-pool-id>

# 값 보유자 수 세기.
# ⚠️ --attributes-to-get 을 쓰려면 sub 를 반드시 함께 줘야 한다
#    (sub 없이 다른 속성만 요청하면 InvalidParameterException)
aws cognito-idp list-users --user-pool-id "$POOL" --region ap-northeast-2 \
  --query 'length(Users[?Attributes[?Name==`nickname`]])'

# 스키마 전체 훑기 — 변형 속성을 빠뜨리지 않으려면 이걸 먼저 본다
aws cognito-idp describe-user-pool --user-pool-id "$POOL" --region ap-northeast-2 \
  --query 'UserPool.SchemaAttributes[].Name' --output text
```

⚠️ `--max-results` 를 붙이면 그 페이지만 세므로 **붙이지 않는다**(CLI v2 가 자동 페이징).

**확인**: 임의 계정에 `AdminGetUser` 를 호출해 속성 부재 확인 + 전화번호 로그인 정상 동작.

---

## 부록 G. 이 트랙에 의존하는 다른 작업

세 트랙이 이 작업의 산출물을 전제로 한다. 인수자가 순서를 잘못 잡으면 서로 막힌다.

| 트랙 | 의존하는 것 | 현재 상태 (기준일 실측) |
|---|---|---|
| **skix-medcare 소유자 키 통합** | `GET /internal/user/identity`(§4-4). medcare 가 raw sub 로 저장한 데이터를 canonical sub 로 재키잉한다 | dev·stg 완주. **운영계는 이 트랙의 운영 배포가 선행 조건**(`main` 미반영) |
| **skix-openapi 복수 로그인 sub 정규화** | 같은 identity API + `user_accounts` 백필 | dev·stg 완주(`2c444ba`·`769707a` 반영, `main` 미반영). 운영계에서 정규화 플래그를 켜는 것은 **이 트랙의 운영 백필 후**여야 한다 — 백필 전에는 `user_accounts` 미등재 사용자가 있어 정규화가 빗나간다 |
| **탈퇴 시 파기 / retention** | 이 트랙의 배포 안정화 | §8-3 (A). `backend-api-main` `feature/medcare-withdrawal-purge`(`b2138c78`)는 이미 dev·stg 에 반영돼 있다 |

---

## 부록 H. 이 문서에서 "미확인"으로 남은 것과 확인 방법

로컬 맥에서는 AWS 콘솔·CLI 에 접근할 수 없다(회사 VDI 에서만 가능). 아래는 전부 VDI 작업이다.

| 항목 | 확인 방법 |
|---|---|
| 개발계·검증계 ECS 에 이 트랙이 실제로 배포돼 있는지 | 태스크 정의 이미지 태그 확인. 또는 §7-3 의 `updateSub` 404 확인 |
| 개발계·검증계 S4 관찰 결과 | 부록 E |
| 개발계·검증계 백필이 실제로 몇 건 들어갔는지 | `SELECT COUNT(*) FROM backfill_targets_*;` (스냅샷 테이블을 남겨 뒀다) |
| 운영계 현재 `preempted`/`duplicated` 값 | 부록 B |
| 운영계 Cognito 읽기 속성 현재 설정 | `aws cognito-idp describe-user-pool-client` |
| 앱 스토어 릴리스 일정·보급률 | 앱 담당자 / 스토어 통계 |
| 백오피스 프론트가 `loginId` 를 쓰도록 바뀌었는지 | 프론트 담당 확인 |
