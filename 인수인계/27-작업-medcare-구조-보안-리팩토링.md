# [작업] skix-medcare 구조·보안 리팩토링

| 항목 | 내용 |
|---|---|
| **상태** | **개발계·검증계·운영계 브랜치 전부 반영 완료 · 3환경 배포 완료** · 잔여는 DDL 확인 4건과 실호출 확인 1건 |
| **작업 기간** | 2026-07-31 ~ 2026-08-13 (리팩토링 본체 07-31~08-05, 설정 하드닝 08-13) |
| **직접 수정한 저장소** | `skix-medcare` (단독) |
| **요청서를 전달한 대상** | 없음(서버 단독 작업). 앱에 전달한 것은 "SSE `ERROR` 이벤트를 오류 화면으로 이어달라" 1건뿐이고, 이는 서버 wire 검증과 앱 UI 검증이 갈리는 지점이다(부록 C-3) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §2-2 표에서 **미확인**으로 남은 DDL 4건을 세 환경에서 직접 조회해 확정한다(§7-1의 1번, SQL은 부록 D). 그다음 §3-2 패키지 지도와 §5를 읽는다 |

---

## 0. 세 줄 요약

1. My Healthcare(AI 건강 상담) 백엔드 `skix-medcare`를 2026-07-31부터 2주간 **구조·보안·정합성 관점에서 전면 정비**했다. 패키지 경계를 다시 긋고, 로그·설정·인증의 구멍을 막고, 대화 데이터를 외부 AI 서버에서 우리 DB로 옮기는 내재화를 완주했다. 리팩토링 본체만 **비병합 커밋 102건, 224파일, +11,125/-2,264줄**이며 그중 테스트가 48파일 +6,929줄이다(전부 신규).
2. **세 브랜치(`dev`·`stg`·`main`)에 모두 반영됐고 세 환경 모두 배포됐다.** 메모리에 남아 있던 "stg/prd 롤아웃 대기"는 낡은 기록이다 — 실측하면 리팩토링 브랜치는 2026-08-05에 `main`까지 올라갔고, 운영계 대화 라우팅도 2026-08-11에 내재화로 전환됐다.
3. 남은 것은 **적용 여부를 확인하지 못한 DDL 4건**, **한 글자 검색의 개발계 실호출 확인**, 그리고 저장소 `README.md`의 패키지 구조 설명이 리팩토링 이전 상태로 남아 있는 문서 부채다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **My Healthcare** | 앱 안의 AI 건강 상담 기능. 사용자가 질문하면 의학 논문·웹 검색을 근거로 답변이 스트리밍된다. iOS에서는 `AIChat`, Android에서는 `MyHealthcare` 모듈이다 |
| **skix-medcare** | 그 기능의 백엔드. Java 21 / Spring Boot 3.4.2 **WebFlux**(리액티브) + Spring Data **R2DBC** + MySQL. ECS Fargate 단일 태스크로 돈다 |
| **케이론(Cheiron)** | 답변을 실제로 생성하는 외부 AI 서버. 우리가 HTTP로 호출한다. 대화 원본의 **원천(system of record)** 이기도 하다 |
| **내재화 / kmeta** | 대화 목록·검색·상세를 케이론에 매번 물어보던 것을, 케이론 데이터를 우리 MySQL(`kmeta_*` 8개 테이블)에 복사해 두고 거기서 읽도록 바꾼 것. `kmeta`는 그 사본 테이블군의 접두사다 |
| **라우팅 변수 3종** | 목록·검색·상세를 각각 케이론에서 읽을지(`CHEIRON`) 우리 DB에서 읽을지(`INTERNAL`) 고르는 환경변수 3개. 이름은 §3-6 |
| **PHR** | Personal Health Record. 국민건강보험공단 건강검진 이력을 외부 스크래퍼 서비스를 통해 받아오는 기능 |
| **PHI / PII** | 건강·의료 정보 / 개인식별 정보. 이 서버의 주된 민감정보는 PHI다(대화 전문·PHR 원문이 전부 의료 정보다) |
| **IdToken** | 앱이 모든 일반 API 호출에 싣는 Cognito ID Token. 헤더 이름이 그대로 `IdToken`이다 |
| **내부 API** | `/medcare/v1/internal/**`. 관제 백엔드(`backend-api-main`)와 운영자만 부른다. Cognito가 아니라 공유 시크릿 헤더 `X-Internal-Api-Key`로 인증한다 |
| **SSE** | Server-Sent Events. 채팅 답변을 토막으로 흘려보내는 방식. `POST /medcare/v1/chat/stream`이 이 형식이다 |
| **IDOR** | 남의 리소스 ID를 넣으면 남의 데이터가 나오는 취약점. 소유권 검증이 없을 때 생긴다 |

### 1-2. 문제

리팩토링 직전 형상(2026-07-23, 커밋 `d34fc56`)에는 아래가 모두 있었다. 하나씩 고친 것이 아니라 **한 브랜치에서 묶어** 처리했다.

- **소유권 검증 부재.** 대화 상세·수정·삭제, 폴더·폴더채팅 작업이 "그 리소스가 이 사용자 것인가"를 확인하지 않았다. 대화 ID만 알면 남의 의료 상담 내용을 읽을 수 있었다.
- **로그로 새는 민감정보.** 전화번호(로그인 아이디), 외부 API 오류 응답 본문(실명·생년월일·의료 질의·대화 전문), 내부 API 키가 그대로 CloudWatch에 쌓였다.
- **시크릿이 저장소에 평문.** 로컬 프로필 설정 파일에 케이론 API 키·PHR 시크릿·DB 비밀번호가 들어 있었다.
- **서버측 한도 미적용.** 채팅 사용량 한도가 클라이언트 표시용이었고 서버가 강제하지 않았다.
- **경계가 무너진 패키지 구조.** R2DBC 엔티티가 `domain/entity`에 있고 컨트롤러가 그 엔티티를 그대로 응답으로 내보냈다. DB 컬럼을 바꾸면 앱 응답이 바뀌는 구조였다.
- **테스트 0건.** 자동 테스트가 사실상 없었다. 어떤 변경이 출시된 앱을 깨는지 확인할 방법이 코드 읽기뿐이었다.
- **내재화 미완.** `kmeta_*` 테이블과 변환 코드는 있었지만 기존 대화를 옮길 수단이 없었고, 검색은 ngram 인덱스에 맞지 않는 질의식을 써서 **항상 0건**이었다.

### 1-3. 목표

1. 출시된 앱을 깨지 않는다. 두 앱은 배포 일정이 따로 있고, 서버가 먼저 나간다.
2. 앱을 깨지 않는 선에서 **소유권·로그·설정의 구멍을 전부 막는다.**
3. 기존 대화를 잃지 않고 내재화를 완주한다. 되돌릴 수단을 반드시 남긴다.
4. "문서로만 지키던 계약"을 **테스트로 고정**한다. 다음 사람이 선의로 정리하다가 앱을 깨는 일을 컴파일·빌드 단계에서 막는다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 브랜치 반영

`git merge-base --is-ancestor`로 각 브랜치 끝 커밋이 세 원격 브랜치에 포함되는지 확인한 결과다.

| 브랜치 | 끝 커밋 | 마지막 커밋일 | `origin/dev` | `origin/stg` | `origin/main` |
|---|---|---|---|---|---|
| `refactor/security-and-structure` (본체) | `74834d8` | 2026-08-05 | 반영 | 반영 | **반영** |
| `feature/log-masking-and-config-hardening` (설정 하드닝) | `96698f6` | 2026-08-13 | 반영 | 반영 | **반영** |
| `chore/remove-completed-migration-apis` (마이그레이션 도구 정리) | `d04b1a2` | 2026-08-13 | 반영 | 반영 | **반영** |

합류 지점도 실측했다. 본체는 `c05abf3`(dev, 08-05) → `dad4eb7`(stg, 08-05) → `6453e64`(main, 08-05)로 **같은 날 운영 브랜치까지 올라갔다.** 리팩토링 브랜치는 작업 중에도 dev로 여러 번 나눠 머지됐기 때문에(`fcff3a8`·`ed3493a`·`b00c666`·`2038468`·`56462ab`), "dev 대비 93커밋"처럼 한 덩어리로 세는 표현은 더 이상 맞지 않는다.

**`main`이 리팩토링 이전 구조라는 가정은 사실이 아니다.** 현재 `origin/main`의 tip은 `9cb2d05`(2026-08-24 `Merge branch 'stg'`)이고, `dev`와의 차이 30커밋·137파일(+6,492/-1,372)은 전부 **8월 하순 이후의 별개 작업**이다(소유자 키 통합, 미완료 턴 복구, 제목 요약, PHR 비동기화, PHR 실패 분류). 리팩토링 산출물은 `main`에 이미 들어 있다.

`origin/dev`와 `origin/stg`는 **트리가 완전히 같다**(`git diff --quiet` 무출력). 2026-09-04 승격분까지 반영된 상태다.

### 2-2. 배포·DB 적용

| 항목 | 상태 | 근거 |
|---|---|---|
| 개발계 애플리케이션 배포 | **완료** (2026-08-05 스모크 전 항목 통과, `convertAll` 425/425 실패 0, 라우팅 `INTERNAL` 전환) | 작업 기록 |
| 검증계·운영계 배포 | **완료** — 세 환경 모두 배포됐다. 운영계 대화 라우팅은 2026-08-11 커밋 `212133d`로 `INTERNAL` 전환 | 2026-08-24 3환경 배포 기록, 태스크 정의 파일 |
| 세 환경 태스크 정의의 라우팅 3종 | **3환경 모두 `INTERNAL`** — `deploy/task-definition-{dev,stg,prd}.json` 29~31행에서 확인 | 파일 실측 |
| `kmeta_*` 8종 테이블 생성 | **3환경 적용 완료** (2026-08-03) | `SHOW CREATE TABLE` 실측 기록 |
| `customer_if_log` 컬럼·인덱스 변경 | **3환경 적용 완료** (2026-08-03) | 같음 |
| `customer_if_phr` LOGIN_ID 인덱스 | **3환경 적용 완료** (2026-08-03) | 같음 |
| `customer_if_phr` 세션 해시 컬럼 | **적용됐을 것으로 보이나 미확인.** 없으면 `GET /phr`·`PATCH /phr/usage`가 실패하는데 그 두 경로가 운영 중이므로 적용된 것이 거의 확실하다. 그래도 직접 조회해 확정할 것 | §7-1의 1번 |
| `customer_folder_chat` 유니크 제약 | **미확인** | §7-1의 1번 |
| `customer_folder_chat` 폴더 FK | **미확인** | §7-1의 1번 |
| `customer_if_phr*` collation 통일 | **미확인** (저장소 DDL 파일은 `be2d645`로 갱신됐지만 그건 목표 상태다) | §7-1의 1번 |

> **저장소의 `src/main/resources/db/*.sql`은 "적용된 상태"가 아니라 "적용 목표"다.** 이 프로젝트에는 마이그레이션 도구(Flyway 등)가 없고 도입하지 않기로 결정했다. 두 상태가 갈라질 수 있으므로 판단은 항상 실 DB 조회를 기준으로 한다.

### 2-3. 규모 (실측)

| 측정 | 값 | 방법 |
|---|---|---|
| 리팩토링 본체 비병합 커밋 | **102건** (전부 작성자 단독) | `git rev-list --no-merges --count d34fc56..c05abf3` |
| 같은 구간 변경량 | **224파일 +11,125 / -2,264** | `git diff --stat d34fc56 c05abf3` |
| 그중 `src/main` | 160파일 +3,906 / -1,481 | 같음 |
| 그중 `src/test` | 48파일 **+6,929 / -0** (전부 신규) | 같음 |
| 2026-07-31 ~ 08-31 작성자 비병합 커밋(dev 기준) | **187건** | `git rev-list --no-merges --author=... --since/--until` |
| 현재 테스트 클래스 수 (dev) | **84개** | `find src/test -name "*Test.java"` |

---

## 3. 무엇이 어떻게 바뀌었나

### 3-1. 작업 축 6가지

리팩토링은 아래 여섯 축으로 나뉜다. 커밋 메시지 접두사(`[refactor]`·`[fix]`·`[security]`·`[test]`)가 축과 대체로 일치한다.

| 축 | 무엇을 했나 | 대표 커밋 |
|---|---|---|
| ① 패키지·경계 | 영속 계층을 `adapter/out/persistence`로 내리고, 컨트롤러가 엔티티를 직접 내보내던 것을 DTO로 막고, 포트 방향을 바로잡았다 | `6c935f1` `439dfd2` `2fca737` `97b09af` |
| ② 인가(소유권) | 대화·폴더·폴더채팅의 모든 읽기/쓰기에 소유자 스코프를 붙였다 (IDOR 차단) | `9b5a099` `f21c103` `d7216dd` |
| ③ 로그·시크릿 | PHI·PII·키가 로그로 나가던 경로를 전부 막고, 로컬 시크릿을 `.env`로 빼고, "값이 없는 이유"를 마커로 구별하는 규약을 넣었다 | `bcdeec6` `25f740b` `45bed06` `61000cc` `96698f6` |
| ④ 스트림·한도 | SSE 종료 경로를 하나로 모으고, 서버측 사용량 한도를 강제하고, 느린 클라이언트용 유한 버퍼를 넣었다 | `e701389` `1b4e59c` `da8473e` `33c76d2` |
| ⑤ 내재화 완주 | `convertAll`을 사용자 단위 순회로 재설계해 기존 대화를 옮길 수 있게 하고, 드리프트 점검·ID 대사·잔존 정리를 붙였다 | `861d86f` `5bfad6c` `2e41fa1` `965e03a` |
| ⑥ 테스트 기반 | 0건이던 테스트를 84클래스까지 올리고, **문서로만 지키던 앱 계약 10종을 테스트로 고정**했다 | `515e447` `f4527bf` `a557b52` |

### 3-2. 패키지 구조 — 무엇이 어디로 갔나

리팩토링 **이전**(`d34fc56`, 2026-07-23):

```
com.skix.medcare
├── adapter/in, adapter/out
├── application/{dto, mapper, port, repository, service}    ← repository 가 여기 있었다
├── config
├── domain/{entity, exception, model, validation}           ← R2DBC 엔티티가 여기 있었다
└── security
```

리팩토링 **이후**(현재 `dev`):

```
com.skix.medcare
├── adapter
│   ├── in/web                  컨트롤러 · 요청/응답 DTO · 전역 예외 핸들러
│   └── out
│       ├── cheiron             케이론 HTTP 어댑터 (+ 응답 sanitizer, 전용 예외)
│       ├── platform            플랫폼 HTTP 어댑터 (사용자 정보·한도)
│       ├── phr                 PHR 스크래퍼 HTTP 어댑터
│       ├── crypto              KMS 컬럼 암복호 어댑터 (+ 무동작 구현)
│       └── persistence
│           ├── entity          R2DBC 엔티티 17개 (kmeta/ 하위 포함)
│           └── repository      R2DBC 리포지토리 23개 (kmeta/ 하위 포함)
├── application/{dto, mapper, port, service}
├── config                      Security · WebClient · Jackson · 로그 마스킹 · 시각 · 감사 필터
├── domain/{exception, identity, model, validation}
└── security                    Cognito 인증 필터 · 디코더 · 인증 사용자 리졸버 · 내부 키 필터
```

핵심 이동 세 가지다.

1. **`domain/entity` → `adapter/out/persistence/entity`** (`6c935f1`, 54파일). R2DBC 엔티티는 도메인 개념이 아니라 DB 테이블의 모양이다. 도메인 패키지에 두면 "DB 스키마가 곧 도메인"이 되어 컬럼 변경이 곧바로 응답 변경이 된다.
2. **`application/repository` → `adapter/out/persistence/repository`** (같은 커밋). 리포지토리 인터페이스는 출력 어댑터지 유스케이스가 아니다.
3. **컨트롤러 응답 DTO화** (`439dfd2`). 엔티티를 그대로 반환하던 지점을 전부 응답 DTO로 감쌌다. 지금 앱이 보는 JSON 키와 DB 컬럼 이름이 분리돼 있다.

그 밖에:
- `97b09af` — `R2dbcEntityTemplate`을 서비스에서 직접 쓰던 곳을 리포지토리 fragment로 캡슐화했다. 쿼리가 서비스 코드에 흩어지지 않는다.
- `a7199ca` — 컨트롤러마다 다르게 하던 인증 principal 접근을 `AuthenticatedUser` + 리졸버 하나로 통일했다. 이후 소유자 키가 전화번호에서 Cognito sub로 넘어갈 때 고칠 지점이 한 곳이 됐다.
- `2bf65f0` — `ChatService`의 SSE 파이프라인을 재설계했다. 종료 경로(정상·오류·타임아웃·클라이언트 끊김)를 하나로 모아 사용량 로그가 정확히 한 번 저장되게 했다.

> **저장소 `README.md`의 "프로젝트 구조" 절은 아직 리팩토링 이전 트리를 그리고 있다**(`domain/entity`, `application/repository`가 그대로 적혀 있다). 코드를 처음 여는 사람이 가장 먼저 읽는 자리라 오해를 부른다. §7-3의 후속 항목이다.

### 3-3. 보안·설정 하드닝

두 묶음이다. 앞쪽은 리팩토링 본체(07-31~08-05), 뒤쪽은 2026-08-13의 설정 하드닝 7커밋이다.

**본체에서 막은 것**

| 무엇 | 어떻게 | 커밋 |
|---|---|---|
| 로그의 PHI·PII | 파싱 실패 시 찍히던 대화 원문, 전화번호(로그인 아이디), 외부 API 오류 body를 전부 차단. 마스킹 규칙을 `config/LogMasking` 한 곳에 모음 | `bcdeec6` `45bed06` `61000cc` |
| 시크릿 평문 | 로컬 프로필 시크릿을 저장소 밖 `.env`로 분리 | `25f740b` |
| 내부 API | 키 불일치·미설정이면 전부 차단(fail-closed). 일반 체인과 **별도 `SecurityFilterChain`** 으로 격리 | `cb90280` |
| CORS | 기본값을 "허용 오리진 없음"으로. 소비자가 전부 비브라우저임을 실측하고 내린 결정 | `68e629a` |
| 404 응답 | 내부 구현(스택·클래스명)이 새던 것을 막음 | `e5e1c58` |
| PHI 저장 위치 | `customer_if_log`의 질의 원문 컬럼 제거 — 읽는 코드가 없는 사본이었다 | `a7d93df` |
| 외부 응답 원문 누출 | §3-4의 ② | `70be82b` `a82b14c` |

**2026-08-13 설정 하드닝 7커밋** (`feature/log-masking-and-config-hardening`)

| 커밋 | 무엇 | 왜 |
|---|---|---|
| `87b896f` | 요청 로그에서 **헤더를 아예 남기지 않는다** | 그전에는 블랙리스트 방식이었다. 새 헤더가 추가되면 기본이 "기록"이라 조용히 샌다. 화이트리스트도 아니고 아예 끊는 쪽을 택했다 — 실제로 읽어야 할 헤더는 코드가 이미 알고 있다 |
| `73e4793` | 내부 API 파라미터에 경계 검증 | 되돌릴 수 없는 작업(암호화 마이그레이션·사용자 파기)의 입력에 상한이 없었다. `limit`은 `@Min(1) @Max(1000)`, `authSub`은 `@Size(max=128)`. 오타 하나(`limit=200000`)가 전 행을 훑는 KMS 호출이 되는 것을 막는다 |
| `4c7af66` | `.env` import를 **non-optional**로 | 로컬 프로필이 기본 활성 프로필이다. `.env` 없이도 뜨면 컨테이너에서 실수로 로컬 프로필이 잡혔을 때 평문 모드로 기동한다. non-optional이면 그 경우 **기동 자체가 실패**한다 |
| `e7c2f89` | 평문 컬럼 모드(`off`)를 `localhost`·`test` 프로필로 제한 | 위 항목과 **같은 사고를 두 겹으로** 막는다. 둘 중 하나를 손댈 때는 나머지가 여전히 막는지 확인해야 한다 — 그 경고를 `application.yml` 주석에 남겼다 |
| `f61123a` | CORS 와일드카드를 **기동 단계에서 거부**, 허용 헤더는 화이트리스트 | 원래 `CORS_ALLOWED_ORIGINS=*`가 롤백 수단이었다. 그런데 응답에 PHR·의료 질의가 실리므로, 롤백 수단과 사고 경로가 같은 문자열 하나로 묶이면 오타 한 번이 전 오리진 개방이 된다. 되돌릴 일이 생기면 코드로 되돌린다 |
| `90eeadf` | 내부 API의 중복 진입 로그 제거 | 감사 필터가 이미 남긴다 |
| `96698f6` | **로그 마커 규약** — §3-5 |

`chore/remove-completed-migration-apis`(08-13, 2커밋)는 소진된 레거시 암호화 마이그레이션 도구 2개(`phr/encrypt-legacy`, `chat-report/encrypt-legacy`)를 제거하고(`7ac1a97`, -424줄), 남은 수동 내부 API의 운영 절차를 `docs/operations.md`로 문서화했다(`d04b1a2`). 후자에는 이유가 있다 — 이 내부 API들은 **평시 호출이 0**이라 다음 사람이 "안 쓰는 마이그레이션 잔재"로 오해하고 지우기 쉽다. 실제로 그 판단이 한 번 제기됐고, 전수 확인 끝에 전부 운영 도구로 남기기로 했다.

### 3-4. 잡은 결함 3종 — 원리를 알아야 재발을 막는 것들

**① `@Modifying` 누락** (`9a64042` 수정 / `a557b52` 계약 테스트, 2026-08-05)

Spring Data R2DBC는 `@Query`로 쓴 `INSERT`/`UPDATE`/`DELETE`에 `@Modifying`이 없으면 **그것을 조회처럼 다룬다.** 문장은 실행되므로 DB는 바뀌는데, 영향 행 수를 내보내지 않고 **빈 `Mono`로 완료**한다. (3.4.2 바이트코드 확인: `AbstractR2dbcQuery.getExecutionToWrap`이 `isModifyingQuery()`일 때만 `FetchSpec.rowsUpdated()`를 쓴다. count/exists 분기에는 `defaultIfEmpty`가 있는데 일반 단건 분기에는 그것조차 없다.)

고약한 점은 **증상이 소비 방식에 따라 갈린다**는 것이다.

| 호출측이 어떻게 받느냐 | 증상 |
|---|---|
| `.then()` / `.thenReturn()` | 빈 `Mono`도 그대로 통과 — **무증상** |
| `.flatMap(count -> ...)` | 그 아래가 **통째로 건너뛰어진다** |

그래서 "행 수를 실제로 읽는 첫 코드"가 등장하는 순간에야 터진다. 실제 증상은 `POST /phr/response`가 데이터를 저장하고 세션까지 소비하고도 **200 + 빈 body**를 반환한 것이었다. 이 저장소에는 `@Modifying`이 **하나도 없었고**, 당시 갱신 쿼리 10개 전부에 붙였다(현재 `dev` 기준 42개소).

**mock 단위 테스트로는 원리적으로 못 잡는다** — 스텁이 시킨 대로 emit하기 때문이다. 그래서 `ModifyingQueryContractTest`로 애노테이션 계약 자체를 빌드 시점에 강제한다. 리포지토리 패키지를 스캔해 `@Query` 값이 `INSERT|UPDATE|DELETE`로 시작하는데 `@Modifying`이 없는 메서드를 찾는다. **이 테스트에는 "대상이 1개 이상 잡혔는지" 단언이 함께 들어 있다** — 스캔이 깨지면 단언이 공허하게 통과하기 때문이다. 이 패턴은 같은 성질의 다른 함정(`PendingQueryShapeTest`)에도 그대로 쓰였다.

**② 외부 응답 원문이 예외 사슬로 새던 경로** (`70be82b` `a82b14c`, 2026-08-04~05)

HTTP 200인데 body가 JSON이 아니면 Jackson이 **파싱하려던 원문을 예외 메시지에 담는다**(`Unrecognized token '...'`). 그 예외를 cause로 달거나 `log.error(..., error)`로 넘기면 스택트레이스로 전부 찍힌다. 상태 오류 body는 이미 막아뒀는데 이 경로를 놓쳤다.

`ExternalApiException` **하위 3종 전부가 같은 형태**였다 — PHR(실명·생년월일·전화번호), 케이론(**대화 전문**), 플랫폼(이름·이메일·생년월일). 지금은 원인 예외를 어디로도 넘기지 않고 작업명·traceId·원인 타입만 남긴다. 재도입을 막으려고 **기반 클래스에서 cause 생성자와 자유형 message 생성자를 제거**했다 — 메시지 조립 경로가 `describe`/`describeWithoutStatus` 둘뿐이라 컴파일 단계에서 막힌다.

> 여기서 얻은 교훈은 일반적이다. **한 어댑터에서 이런 결함을 찾으면 즉시 동종 전수 조사를 하라.** 개별 확인만 하다가 리뷰를 3회 왕복했다. `grep -rl "extends ExternalApiException"` 같은 전수 조사부터 하는 편이 빠르다.

**③ 스니펫 생성의 잠복 크래시** (`3058b38`에 포함)

검색 결과 스니펫을 `toLowerCase()` 후 `indexOf`로 찾고 그 인덱스로 **원문을 `substring`** 하고 있었다. 소문자 변환은 길이를 보존하지 않는다(`İ` → `i̇`, 1자 → 2자). 변환문 인덱스를 원문에 쓰면 범위 초과로 500이 난다. 이것은 한 글자 검색과 무관하게 **기존 FULLTEXT 경로에도 있던 결함**이었다. `regionMatches` 기반 원문 좌표 매처로 교체했다.

### 3-5. 로그 마커 규약 (`96698f6`)

민감정보 로깅에서 **"값이 없다"를 이유별로 구별하는 규약**이다. 세 저장소(`skix-medcare`·`skix-security`·`skix-openapi`)에 같은 형태로 적용했다.

핵심 원칙은 **빈 자리를 결함 신호로 예약하는 것**이다. 정상적인 미기록이 전부 마커를 거치게 해 두면, 로그에서 그냥 비어 있는 자리는 자동으로 버그가 된다 — 별도 모니터링 없이 결함이 스스로 드러난다. 그전에는 "저장 로직이 고장났다"와 "원래 없었다"가 구별되지 않아 마스킹 필터가 죽어도 알 방법이 없었다.

| 상태 | 표기 |
|---|---|
| 값 자체가 없었다 (토큰·요청에 필드 부재) | `[ABSENT]` |
| 필드는 왔는데 빈 값이었다 | `[EMPTY]` |
| 있었지만 정책상 남기지 않았다 | `[REDACTED:<사유>]` |
| 마스킹 | `010****5678` |

사유 코드는 `PHI`(PHR 원문·의료 질의·답변·신고 문구) · `PII`(로그인 아이디·이름·생년월일) · `CREDENTIAL`(IdToken·내부 API 키·외부 API 키) · `EXTERNAL_BODY`(외부 응답 원문)다. `PHI`가 이 저장소에만 있는 사유 코드다 — 주된 민감정보이고 법적 취급이 다르다.

구현은 `config/LogMasking` 한 클래스에 모여 있다. 형제 저장소는 본문을 DB 컬럼에 적재하므로 `null`을 "값 없음"으로 쓸 수 있지만, 여기는 로그 라인이라 `null`이라는 값이 없어 `[ABSENT]`가 필요하다. `[NOT_CAPTURED]`(있었지만 포착 실패)는 WebFlux라 본문 캡처 지점 자체가 없어 두지 않았고, 도입하면 같은 이름을 쓴다고 주석에 예약해 뒀다.

**저장소마다 형태가 갈리면 규약이 아니라 각자의 습관이 된다.** 사유 코드 문자열과 `[REDACTED:...]` 형태를 일부러 동일하게 맞춘 이유다.

같은 발상의 기존 사례가 이미 코드에 있다 — `ExternalApiException`의 `bodyLength=N`, `ConversationMapper`의 `length=`. 내용 대신 길이를 남겨 "빈 응답"과 "있었지만 안 남긴 응답"을 구별한다. 마커 규약은 그 발상을 식별자 쪽으로 일반화한 것이다.

### 3-6. 라우팅 변수 3종과 설정

**변수 이름과 기본값** (`src/main/resources/application-ecs.yml` 16~18행)

| 환경변수 | 프로퍼티 | 기본값 | 무엇을 고르나 |
|---|---|---|---|
| `CHEIRON_ROUTING_CONVERSATION_LIST` | `cheiron.api.routing.conversation-list` | `CHEIRON` | 대화 목록을 케이론에서 읽을지 `kmeta_*`에서 읽을지 |
| `CHEIRON_ROUTING_CONVERSATION_SEARCH` | `cheiron.api.routing.conversation-search` | `CHEIRON` | 대화 검색 |
| `CHEIRON_ROUTING_CONVERSATION_DETAIL` | `cheiron.api.routing.conversation-detail` | `CHEIRON` | 대화 상세 |

값은 `CHEIRON` 또는 `INTERNAL` 둘뿐이다.

**기본값이 `CHEIRON`인 것이 설계다** (`5a3939a`). 설정이 누락됐을 때 `INTERNAL`로 떨어지면 아직 변환되지 않은 사용자의 **대화가 통째로 사라진 것처럼 보인다.** 게다가 그 실패는 예외가 아니라 **빈 목록**이라 서버 로그에 아무것도 남지 않는다. 기본값이 `CHEIRON`이면 설정 누락의 최악 결과가 "예전처럼 케이론에서 읽음"이다.

**되돌리는 수단도 이 변수다.** 라우팅은 읽기 전용 스위치이고, `ChatService`는 라우팅과 무관하게 **항상 `kmeta_*`에 적재**한다. 그래서 변수 3종을 `CHEIRON`으로 되돌리면 공백 없이 즉시 복구된다. DB를 지울 일이 없다.

**"최초 배포 때 변수 3종 제거"가 왜 필요했나.** 저장소의 `deploy/task-definition-*.json` 3개는 **최종 상태(`INTERNAL`)** 를 담고 있다. 새 환경에 처음 배포할 때 그 파일을 그대로 쓰면, `convertAll`로 기존 대화를 옮기기 **전에** 이미 `INTERNAL`로 읽게 되어 변환이 끝날 때까지 목록·검색이 빈다. 그래서 최초 배포는 반드시 **변수 3종을 빼거나 `CHEIRON`으로 바꿔서** 하고, `convertAll` 완료·검증 후에 저장소 파일 그대로 재배포해 전환한다. 코드가 강제하지 않으므로 사람이 지켜야 한다.

> **이 절차는 dev·stg·prd 세 환경 모두 이미 끝났다.** 세 태스크 정의 파일 전부 `INTERNAL`이고 운영계 전환 커밋은 `212133d`(2026-08-11)다. **새 환경을 만들거나 태스크 정의를 처음부터 다시 쓸 때만** 이 주의사항이 되살아난다.
>
> 함께 알아야 할 것: `deploy/cloudshell-push.sh`는 **AWS에 등록된 최신 태스크 정의 리비전을 그대로 재사용**한다. 저장소의 json 파일을 고쳐도 자동 반영되지 않는다. 라우팅을 바꾸려면 콘솔이나 `aws ecs register-task-definition`으로 **새 리비전을 먼저 등록**해야 한다.

### 3-7. 한 글자 검색 LIKE 폴백 (`3058b38`)

검색 인덱스는 MySQL FULLTEXT의 ngram 파서를 쓰고 `ngram_token_size`가 기본값 2다. 즉 **1글자 검색어는 인덱스에 매칭 토큰이 없어 FULLTEXT로는 항상 0건**이다. 한국어에서 한 글자 검색은 흔하고, 두 앱 모두 입력할 때마다 검색을 보내므로(search-as-you-type, iOS 100ms·Android 150ms 디바운스, 최소 길이 게이트 없음) 사용자가 실제로 마주친다.

폴백 조건은 좁다 — **strip 후 정확히 1코드포인트인 단일어**일 때만 `searchLike`/`countLikeMatches`로 간다(`application/service/ConversationSearchText.toLikeFallbackPattern`). 판정은 `String.length()`가 아니라 `codePointCount`로 한다. 이모지나 보충 평면 문자(`😀`·`𠮷`)는 UTF-16 길이가 2라서, `length()`로 세면 1글자인데 FULLTEXT로 새어 들어가 0건이 된다.

LIKE 패턴의 이스케이프 문자는 **`!`** 다(`ESCAPE '!'`). MySQL 기본값 `\`는 문자열 리터럴 이스케이프와 겹쳐 계층마다 해석이 달라진다. `%`·`_`·`!` 자체를 검색하면 이스케이프된다. 스캔 범위는 `auth_sub` 스코프로 한정된다 — 남의 대화를 훑지 않는다.

**부하 걱정은 실측으로 기각했다**(2026-08-05, 운영계 데이터). 전체 521행·295명·총 1.7MiB, 문서당 평균 3.4KiB, 헤비유저 1위가 대화 10건·0.06MiB다. 그 사용자의 실제 `auth_sub`로 `EXPLAIN ANALYZE`를 돌리면 전량 스캔이 **0.42ms**다(매치 0건이어도 스캔 비용은 전액 지불되는 측정). 키 입력당 검색+COUNT를 곱해도 커넥션 풀(max 30) 압박이 성립하지 않는다. 그래서 `COUNT(*) OVER()` 단일 스캔 통합은 복잡도 손해로 판단해 취소했다.

> **성장 트립와이어**: 사용자당 `SUM(LENGTH(content_text))`가 수 MiB 급인 사용자가 나타나면 재평가한다. 그때 후보는 `MAX_EXECUTION_TIME` 힌트 → `COUNT(*) OVER()` 통합 → title-only 축소 순이다.
>
> 측정 시 주의: `EXPLAIN ANALYZE`에 `auth_sub` 플레이스홀더를 실값으로 바꾸지 않아 `rows=0`인 무효 측정을 한 적이 있다. **시간보다 처리 행 수가 시나리오와 맞는지 먼저 확인할 것.**

---

## 4. 클라이언트 계약 — 테스트가 지키고 있는 것

이 절이 이 리팩토링에서 **가장 중요한 산출물**이다. 아래 계약은 "정리하면 더 깔끔해 보이지만 실제로는 출시된 앱을 깨는" 종류이고, 전부 테스트로 고정해 위반하면 빌드가 깨진다. 각 테스트는 계약을 일부러 위반시켜 실제로 실패하는지 확인했다.

| 계약 | 고정하는 테스트 |
|---|---|
| 대화 `PATCH`·`DELETE`가 envelope와 `data: {}`를 반환 | `ConversationCommandContractTest` |
| `OffsetDateTime`이 밀리초·오프셋을 항상 포함 | `JacksonTimeFormatContractTest` |
| 대화 JSON의 `snake_case` 필수 키가 생략되지 않음 | `ConversationJsonContractTest` |
| 한도 초과는 HTTP `429`, 스트리밍 중 오류는 in-band `ERROR` | `ChatStreamErrorContractTest` |
| 목록 `limit`을 거절하지 않고 500으로 클램프(검색은 100) | `ConversationServiceTest` |
| 폴더 계열 명령이 성공 envelope body를 반환 | `FolderCommandContractTest` |
| 내부 API 응답 형태와 이력 시각 포맷 | `InternalLogListContractTest` |
| 신규 대화 `INFO` 방출 전에 kmeta root 조회 가능 | `ChatServiceTest` |
| 400의 `errorCode`가 원인별로 구분됨 | `InputErrorContractTest` |
| 동시 스트림 상한 초과도 한도와 같은 `429` | `ChatServiceTest` |
| 갱신 쿼리에 `@Modifying`이 빠지지 않음 | `ModifyingQueryContractTest` |
| 로그에 PII가 실리지 않음 | `LogPiiContractTest` |
| 장애 1건이 ERROR 로그 1줄 | `ChatErrorLoggingContractTest` |
| CORS 기본 차단·와일드카드 기동 거부 | `CorsConfigurationTest` |

계약 전문과 각 항목의 근거는 **부록 C**에 있다. 여기서는 배포 판단에 직결되는 네 가지만 짚는다.

1. **명령형 API도 빈 body나 `204`로 바꾸지 않는다.** Android의 `SafeApi`가 `body != null`을 성공 조건으로 쓴다. 성공 상태여도 body가 없으면 실패로 처리한다.
2. **대화 JSON의 wire key는 `snake_case`다.** 응답 정책이 `non_null`이라 값이 null이면 **키 자체가 사라지는데**, iOS는 `strid`·`title`·`creation_time`·`last_used_time`·`display_status`·`display_type`·`num_chats`를 비-옵셔널로 디코딩한다. 서버 매퍼가 nullable 원본에 기본값을 채워 키가 빠지지 않게 한다. **주의: 저장소의 `docs/API-SPEC.md`에 실린 대화 응답 예시는 camelCase로 적혀 있는데 실제 wire는 snake_case다.** 근거는 `domain/model/Conversation`의 `@JsonProperty` 선언이다 — 문서 쪽이 낡았다.
3. **`OffsetDateTime`은 `yyyy-MM-dd'T'HH:mm:ss.SSSXXX`** 로 밀리초와 오프셋을 항상 포함한다. 나노초가 0이라고 밀리초 자리를 생략하면 iOS `ISO8601DateFormatter(withFractionalSeconds)` 파싱이 실패한다.
4. **목록 `limit`은 100 초과를 400으로 거절하지 않고 서버에서 500으로 클램프한다.** iOS가 새로고침할 때 이미 로드된 누적 개수를 그대로 보내기 때문이다.

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다. 나머지는 코드 주석과 테스트가 지킨다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **라우팅 기본값은 `CHEIRON`** (`application-ecs.yml`) | `INTERNAL`로 바꾸면 설정 누락·새 환경의 최악 결과가 "대화 전부 실종"이 된다. 게다가 예외가 아니라 빈 목록이라 로그에 아무 흔적도 남지 않는다. 기본값이 `CHEIRON`이면 최악이 "예전처럼 케이론에서 읽음"이다 |
| 2 | **저장소 태스크 정의 3개는 최종 상태(`INTERNAL`)를 담는다.** 새 환경 최초 배포 때는 변수 3종을 빼고 배포한 뒤 `convertAll` 완료 후 전환한다 | 그대로 배포하면 변환 전에 `kmeta_*`를 읽어 기존 대화가 목록·검색에서 사라진다. 코드가 강제하지 않는다. 더해서 `cloudshell-push.sh`는 AWS의 최신 리비전을 재사용하므로 json 수정만으로는 반영되지 않는다 — 새 리비전 등록이 먼저다 |
| 3 | **CORS 와일드카드를 기동 단계에서 거부한다** (`SecurityConfig`) | 원래 `CORS_ALLOWED_ORIGINS=*`가 롤백 수단이었다. 응답에 PHR·의료 질의가 실리는 서비스에서 **롤백 수단과 사고 경로가 같은 문자열 하나로 묶여 있으면** 오타·복붙 한 번이 전 오리진 개방이 된다. 되돌릴 일이 생기면 코드로 되돌린다 |
| 4 | **내부 API 전용 `SecurityFilterChain`을 `@Order(1)`로 분리하고, `CognitoAuthenticationFilter`를 빈으로 등록하지 않는다** | 그 필터를 빈으로 두면 Spring이 **전역 `WebFilter`로도 등록**해 내부 API 경로까지 Cognito 검증이 걸린다. 실제로 내부 API가 토큰 헤더 때문에 죽은 적이 있다(`cb90280`). 체인 전용 인스턴스로 만드는 것이 그 방어다 |
| 5 | **평문 기동 경로를 두 겹으로 막는다** — ① `application-localhost.yml`의 `spring.config.import: file:.env`가 optional이 아님 ② `ColumnCryptoConfig`가 `mode=off`를 `localhost`/`test` 프로필에서만 허용 | 기본 활성 프로필이 `localhost`라, 컨테이너에서 프로필 지정이 빠지면 로컬 설정으로 뜬다. 그대로 뜨면 개인정보가 평문 저장된다. **둘 중 하나를 손댈 때는 나머지가 여전히 이 경로를 막는지 확인할 것** — `application.yml` 주석에 같은 경고가 있다 |
| 6 | **요청 로그에 헤더를 하나도 남기지 않는다** (`RequestLoggingFilter`) | 블랙리스트로 되돌리면 새 헤더가 추가될 때 기본이 "기록"이라 조용히 샌다. 마스킹 규칙은 `LogMasking` 한 곳에만 있어야 찾을 때 한 곳만 보면 된다 |
| 7 | **`ExternalApiException` 기반 클래스에 cause 생성자·자유형 message 생성자를 두지 않는다** | 외부 응답 body가 JSON이 아니면 Jackson 예외 메시지에 **원문이 담긴다**(실명·생년월일·전화번호·대화 전문). cause로 달거나 로그 인자로 넘기면 스택트레이스로 전부 찍힌다. 생성자를 지운 것이 재도입을 컴파일 단계에서 막는 장치다 |
| 8 | **`@Modifying` 계약 테스트에 "대상이 1개 이상 잡혔는지" 단언을 함께 둔다** | 패키지 스캔이 깨지면 "위반 0건"이 공허하게 통과한다. 같은 이유로 `PendingQueryShapeTest`에도 같은 단언이 있다. 리플렉션 기반 계약 테스트를 새로 만들 때는 이 쌍을 항상 함께 둔다 |
| 9 | **명령형 API도 body를 반환한다.** `204`·빈 body로 바꾸지 않는다 | Android `SafeApi`가 `body != null`을 성공 조건으로 쓴다. 성공 상태여도 body가 없으면 실패 처리한다. 대화 `PATCH`/`DELETE`는 `data: {}`를 반환해 iOS `EmptyData` 디코딩과 Android body 조건을 동시에 만족시킨다 |
| 10 | **목록 `limit` 초과를 400으로 거절하지 않고 500으로 클램프한다** | iOS가 새로고침 때 누적 로드 개수를 그대로 보낸다. 거절하면 대화가 많은 사용자의 새로고침이 전부 실패한다 |
| 11 | **한 글자 LIKE 폴백은 "strip 후 정확히 1코드포인트인 단일어"일 때만.** 길이는 `codePointCount`로 센다 | `length()`로 세면 `😀`·`𠮷` 같은 보충 평면 1글자(UTF-16 길이 2)가 FULLTEXT로 새어 0건이 된다. 조건을 넓히면 긴 검색어까지 인덱스 없는 스캔으로 흘러 부하 판단(0.42ms 실측)의 전제가 깨진다 |
| 12 | **스니펫 매칭은 원문 좌표 `regionMatches`로 한다. `toLowerCase()` 후 `indexOf` 금지** | 소문자 변환이 길이를 보존하지 않는다(`İ` → `i̇`). 변환문 인덱스로 원문을 `substring`하면 범위 초과 500이 난다(실측 재현) |
| 13 | **유지보수 배치는 `BatchExecutionLock` 하나를 공유한다.** 케이론 호출은 사용자 단위 완전 순차 + 백오프 | 동시성 3으로 돌리면 425건 중 97건이 케이론에서 429로 떨어졌다. 잠금이 갈리면 그 순차성이 깨진다. 다만 이 잠금은 **인메모리**라 롤링 배포 중 두 JVM이 동시에 살아 있는 구간은 막지 못한다 — 운영 절차로 수용 중이다(§6-4) |
| 14 | **`Dockerfile`이 `COPY src ./src`로 워킹트리를 복사한다.** 빌드 전에 `git status --short`를 확인한다 | CI가 없어 `build-export.sh`가 로컬 워킹트리를 그대로 이미지로 만든다. 커밋하지 않은 변경이 있으면 **머지한 내용과 배포된 내용이 갈라진다** |
| 15 | **actuator는 `health`·`info`만 무인증 공개한다.** `metrics`·`prometheus`는 공개하지 않는다 | 경로별 호출량 같은 내부 지표다. 수집처(Prometheus·ADOT)가 없어 공개할 실익도 없다 |

---

## 6. 배포 방법

### 6-1. 빌드·배포 경로

CI가 없다. 로컬에서 이미지를 만들어 AWS CloudShell로 옮겨 올린다.

```bash
# 0) 워킹트리 확인 — Dockerfile 이 워킹트리를 복사한다 (§5의 14번)
git -C skix-medcare status --short

# 1) 로컬에서 이미지 빌드 → medcare.tar.bz2 생성
./deploy/build-export.sh            # 운영 이미지(distroless, 셸 없음)
./deploy/build-export.sh debug      # ECS Exec 디버깅용(shell+curl 포함, dev 전용)

# 2) medcare.tar.bz2 를 AWS CloudShell 에 업로드한 뒤, CloudShell 에서
./deploy/cloudshell-push.sh dev     # dev | stg | prd
```

`build-export.sh`는 `Dockerfile`의 ENTRYPOINT 두 곳(`runtime`, `runtime-debug`)에 `-Duser.timezone=UTC`가 있는지 **빌드 전에 검사**하고 없으면 중단한다. JVM 기본 시간대를 UTC로 고정하는 것이 세 저장소 공통 규약이고, Dockerfile은 빌드 컨텍스트로 복사되지 않아 이미지 안의 테스트로는 검사할 수 없기 때문이다.

| 환경 | AWS 계정 | ECS 클러스터 | 서비스 | 태스크 정의 패밀리 | 공개 호스트 |
|---|---|---|---|---|---|
| dev | `010928196421` | `skmg-airbot-dev-ecs-cluster` | `skmg-airbot-dev-medcare-svc` | `skix-medcare-dev-tdef` | `medcare-dev.namuhx.com` |
| stg | `087432099373` | `skmg-airbot-stg-ecs-cluster` | `skmg-airbot-stg-medcare-svc` | `skix-medcare-stg-tdef` | `medcare-stg.namuhx.com` |
| prd | `779846811758` | `skmg-airbot-prd-ecs-cluster` | `skmg-airbot-prd-medcare-svc` | `skix-medcare-prd-tdef` | `medcare.namuhx.com` |

관제 백엔드(`backend-api-main`)는 공개 호스트가 아니라 내부 NLB **8081 포트**로 `/medcare/v1/internal/log/list`를 호출한다.

### 6-2. 배포 순서 (새 환경을 여는 경우)

세 환경은 이미 끝난 절차다. **새 환경을 만들 때만** 이 순서를 따른다.

```
1. DB 변경 적용 (kmeta_* 8종 생성 + 컬럼·인덱스)
2. 라우팅 변수 3종을 제거한 태스크 정의로 배포   ← 기본값 CHEIRON 이라 기존 대화가 그대로 보인다
3. 앱에서 목록·검색·상세·채팅 스모크 + 기준값 캡처(목록 총 개수, 상위 제목, 검색 결과 건수)
4. POST /medcare/v1/internal/conversations/convertAll
5. 완료 로그의 사용자/전체/성공/실패 건수 확인
6. kmeta 건수 대사 (아래 SQL)
7. 저장소 태스크 정의(변수 3종 INTERNAL)로 재배포해 전환
8. 3의 기준값과 비교 + 새 채팅 → 답변 완료 → 목록 재진입으로 실시간 적재 확인
9. backend-api-main 관제 이력 호출 (= INTERNAL_API_KEY 일치 검증)
```

5번에서 **`대화 목록 조회 실패`가 0건인지도 반드시 본다.** 이건 사용자를 통째로 건너뛴 것이라 "실패 건수"에 잡히지 않는다.

6번 대사 SQL:

```sql
SELECT COUNT(*) AS streams,
       SUM(user_strid IS NULL) AS ownerless   -- 0 이어야 한다
  FROM kmeta_streams;
SELECT COUNT(*) AS search_index FROM kmeta_conversation_search;
```

9번은 medcare가 401/5xx를 내도 관제 화면에는 **빈 이력**으로 보이므로, 화면만 보지 말고 양쪽 서버 로그를 함께 본다.

### 6-3. 배포 후 검증

부록 B의 스모크 curl 목록을 순서대로 실행한다. 최소 필수는 아래 넷이다.

```bash
BASE=https://medcare-dev.namuhx.com     # stg: medcare-stg, prd: medcare

# ① 내부 API fail-closed — 키 없이 401 이어야 한다
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/medcare/v1/internal/conversations/drift"

# ② 드리프트 — checkFailedUsers / missingInKmeta / missingSearchIndex 가 0
curl -s -H "X-Internal-Api-Key: $KEY" "$BASE/medcare/v1/internal/conversations/drift"

# ③ 일반 API 무토큰 — 401 + errorCode UNAUTHORIZED
curl -s "$BASE/medcare/v1/conversations"

# ④ 헬스
curl -s "$BASE/actuator/health"
```

### 6-4. 배포와 배치의 중첩 (알아둘 것)

ECS desired count가 1이어도 **롤링 배포 중에는 두 JVM이 동시에 살아 있다** — 드레이닝 중인 기존 태스크와 정상 상태의 신규 태스크. `BatchExecutionLock`은 인메모리(`AtomicBoolean`)라 서로의 실행 여부를 모른다. 배치가 20초 셧다운 드레인보다 오래 걸릴 수 있어 현실적인 상황이다. 겹치면 양쪽이 동시에 케이론을 호출해 429가 늘고 같은 DB 행 변환이 경합한다.

**현재는 운영 절차로 수용한다.**
- 유지보수 배치 실행 중에는 배포하지 않는다
- 일일 점검(04:00 KST)·주간 대사(일 03:00 KST)·identity 재검증(02:30 KST) 시간 전후에는 배포하지 않는다
- 배포 중에는 `convertAll`·`reconcile`·`purge-stale`을 수동 실행하지 않는다
- 배치 시작·완료 로그를 확인한 뒤 다음 작업을 진행한다

**공유 잠금(DB·Redis lease lock)으로 전환해야 하는 조건**: desired count를 2 이상으로 올릴 때, 롤링 배포 중 배치 중첩을 허용해야 할 때, 배포를 자동화할 때. 셋 중 하나라도 해당되면 인메모리 잠금은 더 이상 충분하지 않다.

### 6-5. 롤백

| 대상 | 방법 |
|---|---|
| 애플리케이션 | 이전 이미지로 되돌린다 |
| 라우팅 | **DB를 지우지 않는다.** 태스크 정의에서 변수 3종을 제거(또는 `CHEIRON`)하면 즉시 복구된다. 적재는 라우팅과 무관하게 계속되므로 다시 켤 때 공백이 없다 |
| DB | 테이블을 삭제하지 않는다. `kmeta_*`는 케이론 사본이라 잘못돼도 `convertAll`로 다시 채운다 |

---

## 7. 남은 일

### 7-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **DDL 4건의 세 환경 적용 여부를 직접 조회해 확정한다.** 확인 SQL은 부록 D. 미적용이면 부록 D의 ALTER를 순서대로(중복 정리 → 유니크 → FK) 적용한다 | 백엔드 | 로컬 맥에서는 DB에 닿지 않는다. VDI에서 수행 |
| 2 | **한 글자 검색의 개발계 실호출 확인.** `김`·`%`·`_`·`!` 각 1글자로 검색해 R2DBC 바인딩과 DTO 매핑이 정상인지 본다. 단위 테스트는 통과했지만 실환경 호출로 확인한 기록이 없다 | 백엔드 | 부록 B의 9번 |
| 3 | `GET /medcare/v1/internal/conversations/drift`를 세 환경에서 한 번씩 호출해 `checkFailedUsers`·`missingInKmeta`·`missingSearchIndex`가 0인지 확인 | 백엔드 | 내재화가 지금도 맞게 돌고 있는지 보는 가장 빠른 방법 |

### 7-2. 릴리스 게이트 없음

이 작업 자체는 세 환경에 이미 배포됐다. 별도 게이트가 없다.

### 7-3. 후속 (이 기능을 막지는 않음)

| # | 항목 |
|---|---|
| 4 | **저장소 `README.md`의 "프로젝트 구조" 절이 리팩토링 이전 트리를 그리고 있다**(`domain/entity`, `application/repository`). 코드를 처음 여는 사람이 제일 먼저 읽는 자리다. §3-2의 현재 트리로 교체 |
| 5 | `docs/API-SPEC.md`의 대화 응답 예시가 camelCase로 적혀 있는데 실제 wire는 snake_case다(`domain/model/Conversation`의 `@JsonProperty`가 근거). 앱팀이 문서를 보고 잘못 구현하면 왕복 비용이 크다 |
| 6 | **PHI 평문 저장** — 대화 전문·건강보험 원본. 보존기간·삭제 연쇄·암호화 정책이 아직 없다. 개인정보 안전성 확보조치 기준상 법적 요건 소지가 있다. 선행 조건은 보존기간 확정(개인정보처리방침)과 탈퇴 이벤트 연동 |
| 7 | **PHR 구간 평문 통신** — `PHR_API_URL`이 세 환경 모두 `http://`다. 건강정보뿐 아니라 스크래퍼 access key/secret이 **모든 요청 헤더에** 평문으로 실린다. 선행 조건은 NLB TLS 리스너 + DNS 이름 + 인증서(인프라·스크래퍼 담당) |
| 8 | 과거 커밋 이력에 dev용 케이론 API 키·PHR 시크릿이 평문으로 남아 있다. 히스토리 정리는 하지 않기로 했으므로 해당 키들은 **로테이션 대상**이다 |
| 9 | 인메모리 `BatchExecutionLock`을 공유 잠금으로 전환 (§6-4의 세 조건 중 하나라도 해당될 때) |
| 10 | `backend-api-main`이 medcare 호출 예외·401·5xx를 **빈 배열**로 바꾼다. 키 불일치나 medcare 장애가 "My Health Care 이력 없음"으로 보인다. 상대 저장소 변경이 필요하다 |
| 11 | `X-Acting-Admin` 필수화 — `backend-api-main`이 실제 관리자 ID가 아니라 문자열 `"admin"`을 하드코딩한다. 실제 ID를 싣기 전에는 필수화해도 로그가 달라지지 않는다 |

---

## 8. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| **"대화가 안 보여요"** | 가장 먼저 라우팅을 본다. `INTERNAL`인데 `kmeta_*` 적재가 어긋나면 **예외가 아니라 빈 목록**이 나가고 로그에 아무것도 안 남는다. `GET /internal/conversations/drift`로 확인하고, 어긋났으면 가벼운 것부터 — `POST .../conversations/reconcile`(누락분만 변환) → 안 되면 `POST .../conversations/convertAll`(전체). 급하면 라우팅 3종을 `CHEIRON`으로 되돌려 즉시 복구 |
| **검색이 0건** | 한 글자 검색이면 LIKE 폴백 경로다(§3-7). 두 글자 이상인데 0건이면 `kmeta_conversation_search` 적재를 의심한다 — `drift`의 `missingSearchIndex`를 본다 |
| **"저장은 된 것 같은데 응답이 비어요"** | `@Modifying` 계열 증상을 먼저 의심한다(§3-4의 ①). 새로 추가한 `@Query` 갱신문에 `@Modifying`이 있는지 본다. `./gradlew test --tests '*ModifyingQueryContractTest*'`가 빌드에서 이미 잡아야 정상이다 |
| **로그에 값이 그냥 비어 있다** | 그것이 결함 신호다(§3-5). 정상적인 미기록은 전부 `[ABSENT]`·`[EMPTY]`·`[REDACTED:사유]` 중 하나로 찍힌다. 마커 없이 빈 자리는 포맷 인자가 빠졌거나 마스킹 경로가 깨진 것이다 |
| **알람 대신 쓰는 로그 문구** | 알람은 만들지 않기로 했다(2026-08-03 결정). 문구는 계약으로 유지하고 `ChatErrorLoggingContractTest`가 고정한다. 목록은 부록 E |
| **기동 실패** | ① 프로필 확인 — `localhost`로 떴다면 `.env` 없어서 실패하는 것이 **정상 동작**이다(§5의 5번). ② `cors.allowed-origins`에 `*`가 들어갔는지. ③ 컬럼 암호화 모드가 알 수 없는 값인지. 세 경우 모두 "조용히 안전하지 않게 뜨는 것"보다 기동 실패를 택한 설계다 |
| **내부 API가 401** | `INTERNAL_API_KEY`가 medcare와 `backend-api-main` 양쪽에서 같은 값인지. fail-closed라 미설정도 401이다 |
| **배포했는데 라우팅이 안 바뀜** | `cloudshell-push.sh`는 AWS의 최신 리비전을 재사용한다. 저장소 json 수정은 반영되지 않는다 — 새 리비전을 먼저 등록해야 한다(§5의 2번) |

---

## 부록 A. 핵심 커밋 (시간순)

### A-1. 리팩토링 본체 (`refactor/security-and-structure`, 2026-07-31 ~ 08-05)

| 날짜 | 커밋 | 내용 |
|---|---|---|
| 07-31 | `d29e6ce` | CloudShell 배포 시 최신 태스크 정의 적용 (브랜치 첫 커밋) |
| 07-31 | `25f740b` | 로컬 프로필 시크릿 외부화 (`.env` 주입) |
| 07-31 | `bcdeec6` | 로그에서 PHI·내부 API 키 노출 제거 |
| 07-31 | `9b5a099` | 대화 API 소유권 검증(IDOR 차단), 제목 변경 경로 통일 |
| 07-31 | `f21c103` | 폴더채팅 생성·수정 소유권 검증 |
| 07-31 | `e701389` | `POST /chat/stream` 서버측 사용량 한도 강제 |
| 07-31 | `2a68d31` | `convertAll` 데이터 소실 방지 및 대화 단위 dedupe |
| 07-31 | `515e447` | **테스트 기반 구축** (+907줄) |
| 07-31 | `6c935f1` | **영속성 계층을 `adapter/out/persistence`로 이동** (54파일) |
| 07-31 | `2fca737` / `439dfd2` | 포트/DTO 의존 역전 해소 / 컨트롤러 응답 DTO화 |
| 07-31 | `a7199ca` | 인증 principal 접근 통일 (`AuthenticatedUser` 리졸버) |
| 07-31 | `2bf65f0` | `ChatService` 스트림 파이프라인 재설계 (SSE 의미 보존) |
| 07-31 | `97b09af` | `R2dbcEntityTemplate` 사용을 리포지토리 fragment로 캡슐화 |
| 07-31 | `f542fed` | 데드코드·CORS·품질 정리 (-256줄) |
| 08-01 | `f802391` · `f60eaaf` · `b037602` | 검수 발견사항 반영 3회 — 우회 경로·무음 fail-open·로그 유출·계약 테스트 무효화 |
| 08-01 | `335464e` | **검색이 항상 0건이던 문제** — ngram 구절 검색식 제거 |
| 08-01 | `9640301` · `25d1729` · `456138d` | iOS 비-옵셔널 디코딩 대비 — 필수 키 생략 방지, 시각 포맷 밀리초 고정 |
| 08-01 | `cb90280` | 내부 API가 토큰 헤더로 죽던 문제 |
| 08-01 | `da8473e` · `7b7518e` · `c428154` | SSE 지속시간 상한·유한 버퍼, JWKS 무한 대기 제거, 부하 리뷰 반영 |
| 08-01 | `0467665` | actuator 노출을 `health`/`info`로 되돌림 |
| 08-02 | `b89cc0d` · `a14ecf6` · `d93a5fa` · `ed44d39` | 셧다운 드레인, 커넥션 풀 idle 상한, `common_code` 5분 캐시, 검색 본문 4096자 바운딩 |
| 08-03 | `5a3939a` | **라우팅 기본값을 `CHEIRON`으로** |
| 08-03 | `861d86f` · `4d4960b` | `convertAll`을 사용자 단위 순회로 / 케이론 429 대응(완전 순차 + 백오프 2s→4s→8s) |
| 08-03 | `a7d93df` | `customer_if_log`에서 질의 원문 컬럼 제거 |
| 08-03 | `b2ba137` | 신규 대화 `INFO` 방출 전 kmeta root 선등록 (Android 타이밍 계약) |
| 08-03 | `1c1c8e3` | 전 환경 태스크 정의에 라우팅 `INTERNAL` 명시 (최종 상태 선반영) |
| 08-03 | `68e629a` | CORS 기본값을 '허용 오리진 없음'으로 |
| 08-03 | `e628ec8` · `8a62164` | 폴더채팅 중복을 DB 제약으로 차단 / 400 원인 구분 |
| 08-03 | `fd55715` · `1a68709` · `33c76d2` · `c48e0dd` | 드리프트 점검 API / 로그 중복 계수 제거 / 동시 스트림 상한 / 멱등 조회 연결 재시도 |
| 08-03 | `f4527bf` | **문서로만 지키던 클라이언트 계약 3건을 테스트로 고정** |
| 08-04 | `6698859` · `d7216dd` | 폴더 삭제 원자화 / 부모 지정 검증(없는 폴더·타인 폴더·순환 거절) |
| 08-04 | `45bed06` · `61000cc` | 로그 PHI·PII 차단 / 외부 응답 body·평문 로그인 아이디 제거 |
| 08-04 | `39c7f4d` · `ba48a1b` | 외부 API 예외에 HTTP 상태 보존(5xx 재시도·4xx 즉시 실패) / 재시도 예산 단일화 |
| 08-04 | `965e03a` · `c5fb0d5` · `d396c48` · `bd1bf77` | 잔존 대화 수동 정리 API와 그 안전장치 3회 축소 |
| 08-04 | `5bfad6c` · `2e41fa1` · `64e356f` · `41ed10f` · `f3fb461` | 드리프트 정기 점검 · ID 대사 · 실행 잠금 공유 |
| 08-04 | `70be82b` | **200 역직렬화 실패로 새던 PHI 차단 — cause 를 보존하지 않는다** |
| 08-05 | `a82b14c` | 플랫폼 어댑터의 같은 누출 차단 + **통로를 컴파일 단계에서 봉쇄** |
| 08-05 | `3058b38` | **한 글자 검색 LIKE 폴백** + 스니펫 원문 좌표 매처 |
| 08-05 | `9a64042` / `a557b52` | **`@Modifying` 누락 수정** / 계약 테스트로 빌드 시점 강제 |
| 08-05 | `c05abf3` | `dev` 최종 머지 → 같은 날 `dad4eb7`(stg) → `6453e64`(main) |

### A-2. 설정 하드닝 (`feature/log-masking-and-config-hardening`, 2026-08-13)

| 커밋 | 내용 |
|---|---|
| `87b896f` | 요청 로그에서 헤더를 남기지 않는다 |
| `73e4793` | 내부 API 파라미터에 경계 검증 (`@Min`/`@Max`/`@Size`) |
| `4c7af66` | `.env` import를 non-optional로 — 평문 기동 경로 차단 |
| `e7c2f89` | 평문 컬럼 모드를 `localhost`·`test` 프로필로 제한 |
| `f61123a` | CORS 와일드카드 기동 거부 + 허용 헤더 화이트리스트 |
| `90eeadf` | 내부 API 중복 진입 로그 제거 |
| `96698f6` | **로그 마커 규약 도입** (`[ABSENT]`·`[EMPTY]`·`[REDACTED:사유]`) |

### A-3. 마이그레이션 도구 정리 (`chore/remove-completed-migration-apis`, 2026-08-13)

| 커밋 | 내용 |
|---|---|
| `7ac1a97` | 소진된 레거시 암호화 마이그레이션 도구 제거 (14파일, -424줄) |
| `d04b1a2` | 손으로 부르는 내부 API의 운영 절차를 `docs/operations.md`로 문서화 |

---

## 부록 B. 배포 스모크 curl 목록

개발계 기준이다. 검증계·운영계는 `BASE`만 바꾼다. 앞의 5개는 토큰 없이 되므로 배포 직후 즉시 돌릴 수 있다.

```bash
BASE=https://medcare-dev.namuhx.com          # stg: medcare-stg / prd: medcare
KEY='<INTERNAL_API_KEY — SSM /skix-medcare/<env>/INTERNAL_API_KEY>'
TOKEN='<Cognito IdToken>'
STRID='<본인 계정의 대화 strid>'
```

### 토큰 없이 확인하는 것 (1~6)

```bash
# 1) 헬스 — 200 / {"status":"UP"}
curl -s "$BASE/actuator/health"

# 2) 일반 API 무토큰 — 401, 본문이 공통 envelope 이고 errorCode 가 UNAUTHORIZED
curl -s -w '\n%{http_code}\n' "$BASE/medcare/v1/conversations"
#    기대: {"result":401,...,"errorCode":"UNAUTHORIZED","message":"Authentication required"}
#    스택트레이스·클래스명이 보이면 즉시 중단 (내부 구현 노출)

# 3) 내부 API fail-closed — 키 없이 401
curl -s -o /dev/null -w '%{http_code}\n' \
  "$BASE/medcare/v1/internal/conversations/drift"

# 4) 내부 API 잘못된 키 — 401 (200/403 이면 중단)
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Internal-Api-Key: wrong-key-for-smoke' \
  "$BASE/medcare/v1/internal/conversations/drift"

# 5) actuator 내부 지표 비공개 — metrics 는 401 이어야 한다
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/actuator/metrics"

# 6) CORS 기본 차단 — 응답에 Access-Control-Allow-Origin 이 없어야 한다
curl -s -i -X OPTIONS \
  -H 'Origin: https://evil.example.com' \
  -H 'Access-Control-Request-Method: GET' \
  "$BASE/medcare/v1/conversations" | grep -i 'access-control-allow-origin' \
  && echo '!! CORS 가 열려 있다' || echo 'OK: 허용 오리진 없음'
```

### 내부 API (7~8)

```bash
# 7) 드리프트 — checkFailedUsers / missingInKmeta / missingSearchIndex 가 0
#    사용자 수만큼 케이론을 호출하므로 상시 폴링 금지. 배포 후·정기 점검용이다.
#    다른 유지보수 배치가 돌고 있으면 실행하지 않는다.
curl -s -H "X-Internal-Api-Key: $KEY" \
  "$BASE/medcare/v1/internal/conversations/drift"
#    필드: users checkFailedUsers cheironTotal kmetaTotal missingInKmeta
#          staleInKmeta ownerlessStreams missingSearchIndex orphanFolderChats stalePendingChats

# 8) 관제 연동 계약 — backend-api-main 이 실제로 부르는 경로
#    기대: 정수 result, boolean success, data 배열, 시각은 "yyyy-MM-dd HH:mm:ss"
curl -s -X POST \
  -H "X-Internal-Api-Key: $KEY" -H 'X-Acting-Admin: admin' \
  -H 'Content-Type: application/json' \
  -d '{"loginId":"<전화번호 형태 로그인 아이디>"}' \
  "$BASE/medcare/v1/internal/log/list"
```

### 토큰이 필요한 것 (9~16)

```bash
# 9) 검색 — 한 글자 LIKE 폴백. 네 가지를 각각 돌린다.
#    '김'  : 일반 1글자     → 결과가 나오거나 0건이더라도 500 이 아니어야 한다
#    '%'   : LIKE 와일드카드 → 전체 매치가 아니라 '%' 를 포함한 대화만 나와야 한다
#    '_'   : LIKE 단일문자   → 마찬가지
#    '!'   : 이스케이프 문자  → 마찬가지
for q in '김' '%' '_' '!'; do
  echo "--- searchQuery=$q"
  curl -s -G -H "IdToken: $TOKEN" \
    --data-urlencode "searchQuery=$q" \
    "$BASE/medcare/v1/conversations/search"
done
#    두 글자 검색(FULLTEXT 경로)도 함께 본다 — 회귀 확인
curl -s -G -H "IdToken: $TOKEN" --data-urlencode 'searchQuery=혈압' \
  "$BASE/medcare/v1/conversations/search"

# 10) 대화 목록 — limit 클램프. 400 이 아니라 200 이어야 한다(§5의 10번)
curl -s -o /dev/null -w '%{http_code}\n' -H "IdToken: $TOKEN" \
  "$BASE/medcare/v1/conversations?limit=1000"

# 11) 대화 상세 — snake_case 필수 키 확인
curl -s -H "IdToken: $TOKEN" "$BASE/medcare/v1/conversations/$STRID" \
  | grep -o '"\(strid\|title\|creation_time\|last_used_time\|display_status\|display_type\|num_chats\|input_state\)"' \
  | sort -u
#    기대: 위 키가 모두 나온다. camelCase 가 보이면 계약 위반
#    시각은 .SSS+09:00 형태여야 한다 — 밀리초·오프셋이 빠지면 iOS 파싱이 깨진다

# 12) IDOR — 남의 대화 strid 로 조회하면 내 것이 아니어야 한다(404/403)
curl -s -o /dev/null -w '%{http_code}\n' -H "IdToken: $TOKEN" \
  "$BASE/medcare/v1/conversations/00000000-0000-0000-0000-000000000000"

# 13) 명령형 API 의 body — data: {} 가 반드시 있어야 한다(Android SafeApi)
curl -s -X PATCH -H "IdToken: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"title":"스모크 제목"}' \
  "$BASE/medcare/v1/conversations/$STRID"
#    기대: {"result":200,...,"data":{},"success":true}. 본문이 비면 앱이 실패로 처리한다

# 14) 400 원인 구분 — errorCode 가 셋으로 갈려야 한다
#     path 변환 실패 → INVALID_PARAMETER (파라미터 이름 포함)
curl -s -H "IdToken: $TOKEN" "$BASE/medcare/v1/folders/not-a-number"
#     body 누락 → REQUEST_BODY_MISSING
curl -s -X POST -H "IdToken: $TOKEN" -H 'Content-Type: application/json' \
  "$BASE/medcare/v1/folders"
#     JSON 파싱 실패 → INVALID_REQUEST_BODY
curl -s -X POST -H "IdToken: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"folderName":' "$BASE/medcare/v1/folders"

# 15) 채팅 사용량 조회 — 한도 표시
curl -s -H "IdToken: $TOKEN" "$BASE/medcare/v1/chat/stream"

# 16) 채팅 스트림 — INFO → PROGRESS/GENERATION → STOP → [DONE] 순으로 끝나야 한다
curl -s -N -X POST -H "IdToken: $TOKEN" -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"query":"스모크 테스트입니다"}' \
  "$BASE/medcare/v1/chat/stream"
#    한도 초과는 스트림 시작 전이면 HTTP 429,
#    응답이 시작된 뒤의 오류는 event: error / type: "ERROR" 로 in-band 전달된다
```

### 배포 직후 DB 확인

```sql
-- 라우팅이 INTERNAL 인 환경에서 적재가 정상인지
SELECT COUNT(*) AS streams, SUM(user_strid IS NULL) AS ownerless FROM kmeta_streams;  -- ownerless = 0
SELECT COUNT(*) AS search_index FROM kmeta_conversation_search;
```

> **주의.** 16번에서 스트림이 실제로 답변을 만들므로 운영계에서는 본인 계정으로만 돌린다. 13번은 대화 제목을 실제로 바꾼다 — 스모크용 대화를 하나 만들어 쓰고, 끝나면 원래 제목으로 되돌린다.

---

## 부록 C. 클라이언트 계약 전문

서버가 지켜야 할 Android·iOS·`backend-api-main` 호출 계약이다. **인수인계가 아니라 상시 제약**이다. 대부분 "정리하면 더 깔끔해 보이지만 실제로는 출시된 앱을 깨는" 종류이므로, 바꾸려면 앱 배포 일정과 함께 판단한다.

검토 기준 형상: 서버 `refactor/security-and-structure`, Android `develop` `6d6fc2873`, iOS `develop` `5ffcc612`, `backend-api-main` `dev` `4f0d3f03`.

### C-1. 공통 응답

- 명령형 API도 **빈 HTTP body나 `204`로 바꾸지 않는다.** Android는 성공 상태여도 body가 없으면 성공으로 처리하지 않는다(`SafeApi`의 `body != null` 판정).
- 응답은 기존 JSON envelope를 유지한다: `result`, `data`, `success`, `errorCode`, `message`, `timestamp`.
- 대화 `PATCH`·`DELETE`는 `data: {}`를 반환한다. iOS의 `EmptyData` 디코딩과 Android의 body 존재 조건을 동시에 만족한다.
- 폴더 계열 명령도 성공 envelope body를 반환한다. `data` 생략은 가능하지만 body 자체는 필요하다.

### C-2. 대화 JSON

- wire key는 **`snake_case`** 를 유지한다.
- iOS 목록 항목에는 `strid`, `title`, `creation_time`, `last_used_time`, `display_status`, `display_type`, `num_chats`가 **항상** 있어야 한다.
- iOS 검색 응답에는 `results`, `totalCount`, 각 결과의 `conversation`, `time`, `snippet`이 항상 있어야 한다.
- iOS 상세의 각 chat에는 `strid`, `status`, `input_state.query`가 필요하다. `output_state`를 보낼 경우 `response`, `search_results`도 필요하다.
- 서버 매퍼는 nullable 원본에 **기본값을 채워** 위 필드가 빠지지 않게 한다. 응답 정책이 `non_null`이라 값이 null이면 **키 자체가 사라지는데**, iOS는 이 필드들을 비-옵셔널로 디코딩한다.
- `OffsetDateTime`은 `yyyy-MM-dd'T'HH:mm:ss.SSSXXX` 형식으로 밀리초와 오프셋을 항상 포함한다. 나노초가 0이어도 밀리초 자리를 생략하면 iOS `ISO8601DateFormatter`(`withFractionalSeconds`) 파싱이 실패한다.
- 목록 `limit`은 100 초과 요청을 400으로 거절하지 않고 서버에서 최대 500으로 제한한다. iOS가 새로고침 때 이미 로드된 누적 개수를 그대로 보내기 때문이다.

### C-3. SSE

- 정상 타입은 `INFO`, `PROGRESS`, `GENERATION`, `STOP`이고 종료 표시는 `[DONE]`이다.
- 요청 시작 **전** 사용량 초과는 HTTP `429`로 반환한다. 응답이 아직 커밋되지 않은 시점이라 HTTP 상태로 알릴 수 있다.
- 응답이 **시작된 뒤**의 오류·타임아웃·서버 버퍼 초과는 `event: error`와 `type: "ERROR"`를 쓴다. 이미 커밋된 응답의 상태 코드는 바꿀 수 없다.
- 알 수 없는 이벤트 타입은 클라이언트가 무시해도 되지만 `ERROR`는 사용자 오류로 처리해야 한다.

검증은 두 계층으로 나뉜다. 서버 검증만 통과했다고 사용자에게 오류가 보이는 것은 아니다.

| 계층 | 확인 대상 | 확인 주체 |
|---|---|---|
| 서버 wire | `event: error` / `type: "ERROR"`가 실제로 전송되는지 | 서버 테스트 |
| 앱 UI | 수신한 `ERROR`가 오류 화면으로 이어지는지 | 각 앱의 릴리스 검증 |

### C-4. 타이밍 계약 (Android)

Android는 새 대화의 `INFO` 이벤트를 받는 **즉시** 목록 API를 재조회하고 `Complete`에서는 재조회하지 않는다. `INTERNAL` 라우팅에서 그 재조회에 새 대화가 잡히려면 서버가 `INFO`를 내보내기 **전에** kmeta root를 먼저 넣어야 한다. iOS는 `INFO` 수신 시 로컬 목록에 낙관적으로 추가하므로 이 순서에 의존하지 않는다.

### C-5. 400 오류 코드

`INVALID_PARAMETER`(path 변환 실패, 파라미터 이름 포함) · `REQUEST_BODY_MISSING` · `INVALID_REQUEST_BODY`(JSON 파싱 실패) 셋으로 갈린다. 두 앱 모두 medcare의 `errorCode` 문자열에 분기하지 않으므로(iOS는 403 계열만 해석) 코드 추가는 출시된 앱에 영향이 없다.

동시 스트림 상한(`CHAT_MAX_CONCURRENT_STREAMS`, 기본 3, `0`이면 해제) 초과는 사용량 한도 초과와 **같은 HTTP `429`** 로 나간다 — 앱이 이미 처리하는 경로라 새 분기가 필요 없다. 메시지만 다르다.

### C-6. `backend-api-main` 연동

관제 이력 화면에서 실제로 호출한다.

- `POST /medcare/v1/internal/log/list`
- 요청 body: `loginId`만
- 요청 header: `X-Internal-Api-Key`, `X-Acting-Admin`
- 기대 응답: 정수 `result`, boolean `success`, `data` 배열과 기존 envelope 필드
- 이력 시각: `customerIfCreateDate`를 `yyyy-MM-dd HH:mm:ss`로 디코딩

운영상 주의할 점:
- 호출 예외·401·5xx를 **빈 배열로 바꾸므로** 키 불일치나 medcare 장애가 "My Health Care 이력 없음"으로 보인다.
- 요청 body의 `loginId`와 예외 로그의 `userId`가 그대로 기록된다.
- `X-Acting-Admin`은 실제 관리자 식별자가 아니라 문자열 `admin`으로 고정돼 있다.
- `backend-api-main`은 `convertAll`을 호출하지 않는다.

### C-7. 두 앱이 실제로 쓰는 API

| 기능 | API | Android | iOS |
|---|---|:---:|:---:|
| 채팅 사용량 | `GET /medcare/v1/chat/stream` | O | O |
| 채팅 스트림 | `POST /medcare/v1/chat/stream` | O | O |
| 대화 | `GET /conversations`, `/search`, `/{strid}` | O | O |
| 대화 수정·삭제 | `PATCH`, `DELETE /conversations/{strid}` | O | O |
| 폴더·채팅 목록 | `GET /folders/folderChatlist` | O | O |
| 폴더 생성·수정·삭제 | `POST /folders`, `PUT`, `DELETE /folders/{id}` | O | O |
| 폴더 채팅 추가·수정·삭제 | `POST /folders/{id}/chats`, `PUT`, `DELETE .../{chatId}` | O | O |

- 두 앱 모두 `/internal/**`을 호출하지 않는다.
- Android 인터페이스에는 `GET /folders`, `GET /folders/{id}`가 선언돼 있지만 현재 Repository 호출 경로에서는 쓰지 않는다.

### C-8. 릴리스 회귀 확인 목록

- Android/iOS에서 목록·검색·상세 JSON 디코딩
- 대화 `PATCH`·`DELETE`와 폴더 명령의 성공 처리
- 정상 SSE가 `STOP`과 `[DONE]`으로 끝나는지
- HTTP `429`가 오류 UI로 표시되는지
- 스트리밍 도중 `ERROR`가 wire상 전달되는지(서버) 및 각 앱에서 오류 UI로 표시되는지(앱)
- iOS 검색 결과 선택 시 해당 메시지로 이동하는지
- 관제 이력에 My Health Care 사용량과 시각이 표시되는지
- medcare 연동 장애가 빈 이력으로 보일 수 있으므로 양쪽 서비스 로그에서 성공 여부를 함께 확인했는지

---

## 부록 D. DDL 확인·적용 SQL

§7-1의 1번에서 쓴다. **먼저 확인 쿼리를 세 환경에서 돌리고, 미적용인 것만 적용한다.**

```sql
-- ① 적용 여부 한 번에 확인
SELECT TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS cols, NON_UNIQUE
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME IN ('customer_folder_chat','customer_if_phr')
 GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE;

SELECT CONSTRAINT_NAME, TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_folder_chat'
   AND REFERENCED_TABLE_NAME IS NOT NULL;

SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'customer_if_phr%';

SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_if_phr'
   AND COLUMN_NAME = 'CUSTOMER_IF_PHR_SESSION_HASH';
```

### D-1. `customer_folder_chat` 유니크 제약

코드는 이미 제약 충돌을 `EntityDuplicationException`으로 매핑한다. DDL만 남았다. **중복 정리가 선행되지 않으면 ALTER가 실패한다.**

```sql
-- 1) 중복 확인 (0건이어야 ALTER 가능)
SELECT chat_login_id, chat_str_id, COUNT(*) AS cnt
  FROM customer_folder_chat
 GROUP BY chat_login_id, chat_str_id HAVING cnt > 1;

-- 2) 중복 정리 — 각 그룹에서 가장 오래된 행만 남긴다
DELETE c FROM customer_folder_chat c
  JOIN customer_folder_chat keep
    ON keep.chat_login_id = c.chat_login_id
   AND keep.chat_str_id  = c.chat_str_id
   AND keep.chat_id      < c.chat_id;

-- 3) 제약 추가 (소행 테이블이라 잠금 영향 무시 가능)
ALTER TABLE customer_folder_chat
  ADD UNIQUE KEY uk_folder_chat_login_str (chat_login_id, chat_str_id);
```

### D-2. `customer_folder_chat` 폴더 FK

코드도 폴더 삭제를 한 트랜잭션으로 처리하지만(`FolderService.deleteFolder`), 제약은 **새 삭제 경로가 생겨도 빠뜨릴 수 없다**는 점이 다르다. 존재하지 않는 폴더를 가리키는 행이 있으면 ALTER가 실패한다.

```sql
SELECT COUNT(*) FROM customer_folder_chat c
  LEFT JOIN customer_folders f ON f.folder_id = c.chat_folder_id
 WHERE c.chat_folder_id IS NOT NULL AND f.folder_id IS NULL;   -- 0 이어야 한다

ALTER TABLE customer_folder_chat
  ADD CONSTRAINT fk_folder_chat_folder FOREIGN KEY (chat_folder_id)
  REFERENCES customer_folders (folder_id) ON DELETE CASCADE;
```

### D-3. `customer_if_phr*` collation 통일

이 두 테이블만 `utf8mb4_unicode_ci`고 나머지는 `utf8mb4_0900_ai_ci`다. 지금은 두 테이블에 조인이 없어 무해하지만, 다른 테이블의 문자열 컬럼과 비교하는 쿼리가 생기는 순간 `Illegal mix of collations`로 실패한다.

```sql
ALTER TABLE customer_if_phr      CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
ALTER TABLE customer_if_phr_data CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

`CONVERT TO`는 테이블 기본값과 문자열 컬럼을 함께 바꾼다. charset이 `utf8mb4` 그대로라 컬럼 타입은 변하지 않는다. 다만 **COPY 알고리즘이라 테이블을 재작성**하므로 `customer_if_phr_data`(mediumtext) 행 수를 먼저 보고 시간을 가늠한다.

> **컬럼명 대문자는 바꾸지 않는다.** MySQL은 컬럼 식별자를 대소문자 구분 없이 처리해 실익이 없고, 바꾸면 저장소 DDL과 실 DB가 또 갈라진다. 같은 이유로 `customer_if_phr`의 `CODE`·`AUTH_TYPE`·`USAGE_YN` `MODIFY` 3종도 실행하지 않기로 했다 — 코드가 신규 레코드에서 세 값을 모두 명시적으로 채우고, `AUTH_TYPE`은 `char(2)`에 현재 쓰는 값(`''`, PASS의 `'80'`)이 모두 들어간다.

### D-4. `customer_if_phr` 세션 해시 컬럼

다른 DDL과 성격이 다르다. 없으면 `GET /phr`·`PATCH /phr/usage`까지 **함께 실패한다**(엔티티 조회가 이 컬럼을 포함한다). 그 두 경로가 지금 동작 중이므로 이미 적용돼 있을 가능성이 높지만, 확인은 해야 한다.

```sql
ALTER TABLE customer_if_phr
  ADD COLUMN CUSTOMER_IF_PHR_SESSION_HASH char(64) DEFAULT NULL AFTER CUSTOMER_IF_PHR_USAGE_YN,
  ADD UNIQUE KEY uk_phr_session_hash (CUSTOMER_IF_PHR_SESSION_HASH);
```

**원문이 아니라 SHA-256을 저장한다** — 세션은 그 자체가 bearer 값이라 DB를 읽을 수 있는 경로가 생기면 원문 저장이 곧 탈취 가능한 크레덴셜이 된다. 비교에만 쓰므로 원문이 필요 없다. 유니크 제약은 한 세션이 둘 이상의 행에 묶이지 않게 DB에서 강제한다 — MySQL은 유니크 인덱스에서 NULL을 여럿 허용하므로 미사용 행끼리는 충돌하지 않는다.

---

## 부록 E. 알람 문구 계약

Prometheus·ADOT가 없어 관측 표준은 CloudWatch Logs 지표 필터다. **장애 1건은 로그 1줄**이 되도록 정리했고 `ChatErrorLoggingContractTest`가 고정한다. 알람은 만들지 않기로 했지만(2026-08-03 결정) 문구는 계약으로 유지한다 — 문제가 생기면 아래 문자열로 로그를 직접 검색한다.

| 문자열 | 수준 | 의미 | 조치 |
|---|---|---|---|
| `Chat stream error` | ERROR | 케이론 스트림 실패·타임아웃 | 케이론 상태 확인 |
| `사용량 로그 저장 실패` | ERROR | 토큰은 소비됐는데 기록 실패 → 한도 우회 | DB 확인, 수동 보정 |
| `대화 변환 실패` | ERROR | 해당 대화가 목록·검색에서 사라짐 | 재변환 |
| `convertAll 배치 실패` | ERROR | 배치 전체 중단 | 재실행 |
| `SSE 버퍼 오버플로` | WARN | 느린 클라이언트 — 서버 장애 아님 | 빈발 시 버퍼 상한 재검토 |
| `채팅 한도 조회 실패` | WARN | fail-open으로 채팅 허용 중 | 플랫폼 API 확인 |
| `셧다운 드레인 타임아웃` | WARN | 사용량 로그·변환 유실 가능 | 배포 직후 확인 |
| `kmeta 동기화 드리프트 감지` | WARN | 케이론과 kmeta 불일치 | `/internal/conversations/drift` 확인 |
| `잔존 kmeta 삭제 완료` | INFO | 실제로 지워진 대화 — `customer_folder_chat` 후처리의 유일한 근거 | 이 로그에 찍힌 strid 만 후처리 대상이다. 모의 실행 미리보기 목록을 삭제 근거로 쓰지 않는다 |

`대화 변환 실패`는 실시간(`ChatService`)과 배치(`ConvertService`) 양쪽에 있다. 문구가 같으므로 합쳐서 세되 배치 실행 중에는 건수가 늘 수 있다.
