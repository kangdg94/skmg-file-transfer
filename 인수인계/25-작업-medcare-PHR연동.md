# [작업] My Healthcare PHR 연동 — 비동기화(202+FCM) · 5년 제한 · 본인인증 실패 분리

| 항목 | 내용 |
|---|---|
| **상태** | 서버 작업 완료 · 개발계 검증 통과 · **dev·stg 브랜치 반영, 운영계(`main`) 미반영** · ECS 배포 상태 일부 미확인 · Android 잔여 2건 |
| **작업 기간** | 2026-08-05 ~ 2026-09-04 |
| **직접 수정한 저장소** | `skix-medcare`, `backend-api-main` |
| **요청서를 전달한 대상** | Android(`benjaminandroid`), iOS(`benjaminios`) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §8-1의 1번(DDL 2건의 환경별 적용 여부 확인)과 2번(dev·stg ECS 배포 상태 확인). 이 둘을 모르면 §2의 나머지 판단이 전부 추정이 된다 |

---

## 0. 세 줄 요약

1. **건강보험공단 스크래핑(PHR) 수집을 "기다리는 API"에서 "접수하고 알림으로 통보하는 API"로 바꿨다.** `POST /phr/response`가 202로 즉시 끝나고, 결과는 FCM 3종(`PHR0001` 성공 / `PHR0002` 일반 실패 / `PHR0003` 본인인증 실패)으로 나간다. 진행 상태는 `GET /phr`의 `scrapeStatus`로 본다.
2. 같은 묶음으로 **수집 범위를 최근 5년으로 제한**하고, **본인인증 실패를 장애와 분리**했다(`POST /phr/request`가 사용자 입력 문제일 때 502가 아니라 400 `PHR_AUTH_REQUEST_FAILED`).
3. 두 저장소 모두 dev·stg 브랜치에 있고 **운영계 `main`에는 비동기화 이후가 하나도 없다.** PHR은 운영계 미출시라 사용자 영향이 없는 상태이며, 운영 오픈은 §8-2의 게이트(identity 플래그 P4, prd PHR 테이블 0건 확인)를 통과한 뒤다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **My Healthcare / medcare** | 앱 안의 AI 건강 상담 서비스. 백엔드 저장소가 `skix-medcare`다. |
| **PHR** | Personal Health Record, 개인건강기록. 여기서는 **건강보험공단(공단)에서 스크래핑으로 가져오는 국가검진·외래/처방조제 이력**을 뜻한다. |
| **스크래퍼 / PHR API** | 공단 스크래핑을 대행하는 별도 서버. medcare가 HTTP로 호출한다(`/api/v2/nhis/start_auth`, `/api/v2/nhis/after_auth`). 환경별 `PHR_API_URL`. |
| **간편인증(start_auth)** | 사용자가 네이버·카카오·PASS로 공단에 본인인증하는 단계. 성공하면 스크래퍼가 `sessionId`를 준다. |
| **수집(after_auth)** | 발급된 세션으로 실제 데이터를 긁어오는 단계. 수십 초 걸린다. |
| **sessionId** | 스크래퍼 세션. UUIDv4이고 **1회용**이다(첫 `after_auth`로 소진, 재사용 401). |
| **PHI** | Protected Health Information. 이 문서에서는 스크래핑 원문 전체를 가리킨다. |
| **`customer_if_phr`** | 인증행 테이블. 소유자 sub, 사용여부, 세션 해시, 수집 상태를 들고 있다. |
| **`customer_if_phr_data`** | 스크래핑 원문 스냅샷 테이블(KMS 암호화 저장). |
| **`OwnerKey` / `OwnerScope` / `loginUid`** | medcare의 소유자 키 추상화. 한 사람이 네이버·카카오 등 여러 provider로 로그인하면 Cognito sub이 여러 개 생기는데, `OwnerKey`는 그중 대표(canonical) 키, `OwnerScope`는 대표 + 형제(sibling) sub 전체, `loginUid`는 이번 요청에 쓴 로그인 sub이다. |
| **identity 플래그** | `IDENTITY_ALIAS_READ_ENABLED` / `IDENTITY_CANONICAL_WRITE_ENABLED`. 위 키 해석을 켜는 ECS 환경변수. dev·stg는 둘 다 `true`, prd는 둘 다 `false`. |
| **FCM 코드** | 푸시 문구를 **`backend-api-main`이 독점**하고, medcare는 코드 문자열(`PHR0001` 등)만 보낸다. |
| **`BackgroundJobs`** | medcare의 백그라운드 실행 헬퍼. 종료 시 최대 20초(`DRAIN_TIMEOUT`) 드레인한다. |
| **스윕(sweep)** | 1분마다 도는 배치. `IN_PROGRESS`로 방치된 행을 회수해 `FAILED`로 전이시킨다. |

### 1-2. 문제

**(1) 수집이 끝날 때까지 HTTP 응답을 붙들고 있었다.** 검증계 2주 실측(2026-08-18~08-31, 성공 121건) 결과는 이렇다.

| 소요시간 | 건수 | 비중 |
|---|---|---|
| < 10초 | 36 | 30% |
| 10~20초 | 30 | 25% |
| 20~30초 | 6 | 5% |
| **30~40초** | **45** | **37%** |
| 40초+ | 4 | 3% |

- **70%가 10초를 넘고 40%가 30초를 넘는다.** 최빈 구간이 30초대다.
- **최대 58.0초 — 당시 서버 타임아웃 `phr.api.timeout: 60s`까지 2초 남아 있었다.**
- 시작(`>>>`) 대비 응답 완료(`<<<`) 결손, 즉 **앱이 응답을 못 받고 끊은 중도절단이 227건 중 16건(7.0%)** 이었다.
- 그 대기를 버티려고 앱에 우회 코드가 쌓여 있었다. Android는 이 경로 하나만 OkHttp readTimeout을 60초로 올리고 포그라운드 알림까지 띄우는 전용 WorkManager Worker를 운영했고, iOS는 응답을 기다리며 502 전용 분기를 따로 뒀다.
- 실사용자는 이보다 느려질 공산이 크다. 5년치가 실제로 쌓인 계정은 QA 계정보다 데이터가 많고, 동시 사용자가 늘면 스크래퍼측 큐잉이 붙는다.

> 실측 데이터 해석 시 주의 — 같은 기간 502가 87건인데 그중 84건은 스크래퍼 접근키가 잘못 배포돼 PHR API 인증에 실패한 사고 흔적이다. **"성공률 57%", "실패는 4.7초 안에 난다"는 이 데이터로 말할 수 없다.** 위 표(성공 121건의 분포)만 유효하다.

**(2) 수집 범위가 무제한이었다.** `after_auth` 요청 body가 `sessionId` 하나뿐이라 전체 기간을 받아 저장하고 있었다. "최근 5년만 조회"가 앱 정책이자 최소수집 조치인데 서버가 지키지 않고 있었다.

**(3) 본인인증 실패가 장애와 뭉개져 있었다.** 입력한 이름·생년월일·전화번호로 간편인증을 **시작조차 못 하는** 경우(스크래퍼 `NHIS_AUTH_REQUEST_FAILED`)를 스크래퍼 장애·타임아웃과 같은 `502 PHR_API_ERROR`로 내보냈다. 앱은 "일시적인 오류입니다. 잠시 후 다시 시도해 주세요"를 띄웠다 — **입력값을 고쳐야 하는 사용자에게 기다리라고 안내**하고 있었고, 기다려도 해결되지 않는다.

**(4) 비동기로 바꾸면 실패를 알릴 방법이 사라진다.** 앞의 시크릿 사고 때 2주간 84건이 실패했지만 동기라서 앱이 즉시 502를 받았고 QA가 바로 알아챘다. 비동기였다면 전부 "접수됨" 뒤 침묵이었다. 그래서 상태 컬럼·실패 통보·유실 회수는 선택이 아니라 전제였다.

### 1-3. 요구사항

- `POST /phr/response`는 **접수만 하고 즉시 202**. 세션 소유권 대조는 **스크래퍼 호출 이전, 동기 구간**에 그대로 둔다.
- 완료·실패를 **FCM으로 통보**하되, 알림을 꺼둔 사용자를 위해 `GET /phr`로도 판정 가능해야 한다.
- 실패 원인 중 **사용자가 할 일이 다른 것만** 분리한다(본인인증 계열 vs 그 외).
- **재수집 실패가 기존 PHR 데이터를 지우거나 숨기면 안 된다.**
- 수집 범위는 **요청일(KST) 기준 최근 5년**. 환경변수로 넓힐 수 없어야 한다.
- 운영계 오픈은 소유자 키(identity) 롤아웃 뒤다 — PHR은 재키잉(re-keying) 대상이 아니라 잘못된 키로 적히면 나중에 고쳐지지 않는다.

**PHR은 운영계·앱 모두 미출시다.** 그래서 신규 엔드포인트도, 앱 버전 하한도, 구 엔드포인트 폐기 기간도 없이 응답 형태를 그냥 바꿀 수 있었다. 이 비용이 0인 시점이 지금뿐이라는 것이 전체 작업의 전제였다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. `skix-medcare`

`origin/dev` = `75d692e`(2026-09-04) / `origin/stg` = `4ffc4e7`(2026-09-04) / `origin/main` = `9cb2d05`(2026-08-24).

| 주제 | 대표 커밋 | dev | stg | main(운영) |
|---|---|---|---|---|
| PHR 삭제를 두 테이블 파기·멱등으로 교정 + `GET /phr/data` 신설 | `8636469` (08-05) | 반영 | 반영 | **반영** |
| PHR 응답 구조 재정리(`phrData` 통일, 수신시각, +09:00 오프셋) | `5d877e7`·`672471f`·`1d70692` (08-05) | 반영 | 반영 | **반영** |
| PHR 수신 완료 푸시(`PHR0001`) + dev·stg 활성화 | `ef97d8e`·`4ef2fa0` (08-19) | 반영 | 반영 | **반영** |
| **수집 범위 5년 제한** | `89b6f95` / 머지 `0f6619d` (08-19) | 반영 | 반영 | **반영** |
| 저장 PHR의 케이론 첨부(기능 플래그 뒤) | `c960fbc` (08-12) | 반영 | 반영 | **반영** |
| **비동기화 202+FCM + 소유자 키 `OwnerContext` 전환** | `4971f08` (08-31) | 반영 | 반영 | **미반영** |
| dev·stg identity 플래그 실상태를 태스크 정의에 반영 | `5e8e205` (08-31) | 반영 | 반영 | 미반영 |
| PHR 파싱 실패 화면 QA 분기 | `2326f38`·`3617a1f` (dev) / `94bf443`·`accb96b` (stg cherry-pick) | 반영 | 반영 | 미반영 |
| **수집 실패 3분류 + `PHR0003` 통보** | `456ea9d`·`bb68b1d`·`a4ca7cf`·`732b6ac` / 머지 `75d692e` (09-03~04) | 반영 | 반영 | 미반영 |
| **`/phr/request` 본인인증 실패를 400으로 분리** | `8b623c8` (09-04) | 반영 | 반영 | 미반영 |

> **메모와 다른 점 1 — 5년 제한은 이미 `main`까지 올라가 있다.** 작업 당시 메모에는 "dev 실호출 대사가 stg/prd 진입 조건"으로 남아 있으나, 실측하면 `0f6619d`가 08-19에 stg(`da600ba`), 08-24에 main(`9cb2d05`)으로 함께 승격됐다. **즉 스크래퍼가 `options`를 실제로 적용하는지 확인되지 않은 채 운영 브랜치에 들어가 있다.** §8-1의 3번이 이것이다.

> 로컬 `feature/phr-auth-failure-reason`·`feature/phr-request-auth-error` 브랜치는 원격에 없다(dev로 머지된 뒤 정리됨). 커밋 자체는 dev·stg에 전부 들어 있다.

### 2-2. `backend-api-main`

`origin/dev` = `3fdd8447`(2026-09-18) / `origin/stg` = `ed6dc871`(2026-09-18) / `origin/main` = `7d9ac170`(2026-09-01).

| 주제 | 커밋 | dev | stg | main(운영) |
|---|---|---|---|---|
| medcare 푸시용 내부 API `/internal/medcare/send-fcm` + 공유키 검증 | `e6e726c5`·`7e40fc66`·`91eb9d2f` (08-19) | 반영 | 반영 | **미반영** |
| PHR 완료 알림을 인앱 공통 알림함에 기록 | `0e422ed7` (08-19) | 반영 | 반영 | 미반영 |
| `MedcareFcmCode.PHR0002` | `4e205dfb` (08-31) | 반영 | 반영 | 미반영 |
| `MedcareFcmCode.PHR0003` | `af0045ce` / 머지 `84434da1` / stg `d4e5af52` (09-03~04) | 반영 | 반영 | 미반영 |

> **운영 브랜치의 어긋남을 알고 있을 것.** `skix-medcare` `main`에는 `PhrPushNotifier`(PHR0001 발송)가 들어 있는데, `backend-api-main` `main`에는 그 수신 엔드포인트 `/internal/medcare/send-fcm`이 **아예 없다.** 지금 무해한 이유는 운영계 태스크 정의의 `PHR_PUSH_ENABLED`가 `false`이기 때문뿐이다. 운영계에서 이 플래그를 켜기 전에 반드시 `backend-api-main`을 먼저 올려야 한다(§7-1).

> stg 승격 이력 주의 — `backend-api-main`의 `d4e5af52`는 dev를 통째로 올린 것이라 PHR과 무관한 타 담당자 커밋 `0e1380eb`(공통코드 opt 조건)이 함께 올라갔다. 당시 판단으로 허용한 것이니 stg에서 예상 밖 diff를 보면 이 건을 떠올릴 것.

### 2-3. DDL·배포·검증

DDL은 마이그레이션 도구가 없어 **환경별로 손으로 친다.** 대상 테이블은 `customer_if_phr`이고 두 건은 별개다.

| 항목 | 상태 |
|---|---|
| DDL ① 비동기화 — `SCRAPE_STATUS`·`SCRAPE_START_DATE` + `idx_phr_scrape_status` | 2026-08-31 기준 **dev·stg·prd 3환경 적용 완료**로 기록됨. 로컬 맥에서 DB에 닿을 수 없어 **재확인 못 함** |
| DDL ② 실패 사유 — `SCRAPE_FAILURE_REASON` | 2026-09-04 기준 **어느 환경에도 미적용**. 이후 적용 여부 **미확인** |
| 개발계 배포·검증 (비동기화) | 2026-08-31 **배포 완료 + curl 8단계 검증 전부 통과** (202 접수·`scrapeStatus` 전이·`PHR0001`/`PHR0002` 푸시·중복요청 방어). 스크립트 전문은 부록 B |
| 개발계 배포 (실패 분리, 09-04 커밋) | **미확인** |
| 검증계 배포 | **미확인**. 브랜치는 반영돼 있다 |
| 운영계 | **미배포**(브랜치 미반영). PHR 기능 자체가 미오픈이며 `PHR_PUSH_ENABLED=false` |

**"미확인"을 확인하는 방법**: 로컬 맥에서는 AWS 콘솔·CLI에 닿지 않는다. 회사 VDI에서 (1) ECS 태스크 정의의 이미지 태그와 서비스 배포 시각, (2) 각 환경 DB에서 `SHOW COLUMNS FROM customer_if_phr LIKE 'CUSTOMER_IF_PHR_SCRAPE%'` 로 컬럼 3개(`_STATUS`·`_FAILURE_REASON`·`_START_DATE`)와 인덱스 `idx_phr_scrape_status` 존재를 본다. **②가 없는 환경에 medcare 신버전을 올리면 `POST /phr/request`·`PATCH /phr/usage`가 전건 500이 된다**(§7-1).

### 2-4. 환경별 기능 플래그 (태스크 정의 실측, `origin/dev` 기준)

| 환경변수 | dev | stg | prd |
|---|---|---|---|
| `PHR_PUSH_ENABLED` | `true` | `true` | **`false`** |
| `CHAT_PHR_ATTACHMENT_ENABLED` | `true` | `true` | **`false`** |
| `IDENTITY_ALIAS_READ_ENABLED` | `true` | `true` | **`false`** |
| `IDENTITY_CANONICAL_WRITE_ENABLED` | `true` | `true` | **`false`** |
| `QA_INVALID_PHR_SUBS` | 미설정 | 미설정 | 미설정 |

`PHR_ACCESS_KEY`·`PHR_ACCESS_SECRET`은 세 환경 모두 SSM 참조다(`/skix-medcare/{env}/...`). **운영계 값은 한때 더미(`PENDING_SCRAPPER`)였다** — 운영계에 스크래퍼가 없던 시절 `WebClientConfig`가 세 WebClient를 한 생성자에 묶어 두어 PHR 프로퍼티 하나가 없으면 컨테이너가 기동조차 못 했기 때문이다. 운영 오픈 전에 실값으로 바뀌었는지 확인해야 한다(§8-2).

### 2-5. 앱 (별도 담당자)

| 대상 | 상태 | 확인한 것 |
|---|---|---|
| **Android** (`benjaminandroid`) | **비동기화는 완료, 실패 분리 2건 미착수** | `origin/feature/my-healthcare-phr-integration`(최신 `467b96f11`, 2026-09-14) 기준: 202 접수·`/phr/response` 전용 60초 readTimeout 우회 제거·`PhrDataReceiveWorker` 삭제(파일 없음 확인)·`scrapeStatus`→`PhrScrapeStatus`(미지 값 null 폴백)·`FcmCode.PHR0002` + 라우팅 4곳 전부 반영. **`PHR0003` 문자열이 저장소 어디에도 없다.** **`PHR_AUTH_REQUEST_FAILED` 분기도 없다.** 이 브랜치는 `origin/develop`에 미머지 |
| **iOS** (`benjaminios`) | **전부 반영** | `origin/develop`(`f4e44ec0`, 2026-09-21)에 202 접수(`PHRDataLinkReceiveResult.accepted`), `FCMCode`의 `PHR0002`·`PHR0003`, `POST /phr/request` 400 `PHR_AUTH_REQUEST_FAILED` 분기가 모두 있다(`33e54666` 09-01, `25d58938` 09-04). `scrapeStatus`는 쓰지 않는다 — optional 필드라 무해하다 |

> **메모와 다른 점 2 — iOS는 끝났다.** 작업 당시 메모에는 iOS PHR이 미머지 브랜치에만 있다고 남아 있으나, 지금은 `develop`에 병합돼 있고 `PHR0003`·400 분기까지 들어 있다. 반대로 **Android는 2건이 남아 있다.**

---

## 3. 무엇을 만들었나

### 3-1. 전체 흐름

```
[앱] ① POST /medcare/v1/phr/request  {userAuthType, userName, userBirthDate}   (IdToken 헤더)
        │   전화번호는 body가 아니라 토큰에서 온다 → 스크래퍼 start_auth
        │   실패: 입력 문제면 400 PHR_AUTH_REQUEST_FAILED / 장애면 502 PHR_API_ERROR
        └→ 200 { sessionId }          ← 서버는 sessionId의 SHA-256만 DB에 저장

     ② (사용자가 네이버·카카오·PASS 앱에서 간편인증)

     ③ POST /medcare/v1/phr/response  {sessionId}
        │
        │  [동기 구간 — 여기까지는 반드시 스크래퍼 호출 전]
        │  ㉮ findTopBySessionHashAndOwner(해시, OwnerScope)  → 없으면 404
        │  ㉯ markScrapeStarted : 세션 대조 + SCRAPE_STATUS IS NULL → 'IN_PROGRESS'
        │       1행이면 백그라운드 기동 / 0행이면 재조회해 IN_PROGRESS면 202, 아니면 404
        └→ 202 Accepted (data 키 없음)     ← 앱은 여기서 사용자를 놓아준다

     ④ [백그라운드 · BackgroundJobs]
        스크래퍼 after_auth(options.fromDate = 오늘(KST) - 5년)
          → KMS 암호화 → 한 트랜잭션으로 { 원문 적재 + 과거 스냅샷 파기 + 세션 소비 }
          → 성공: SCRAPE_STATUS = NULL 복귀 + PHR0001 푸시
          → 실패: markScrapeFailed(FAILED, 사유) + 사유별 PHR0002 / PHR0003 푸시
          → 태스크 사망: IN_PROGRESS로 남음 → ⑤가 회수

     ⑤ [스윕 · PhrScrapeSweeper, 1분마다]
        IN_PROGRESS이고 START_DATE가 5분보다 오래된 행 → FAILED(GENERAL_FAILED) + PHR0002

     ⑥ [앱] PHR 화면 진입 시 / foreground 복귀 시 GET /medcare/v1/phr
        scrapeStatus(+ scrapeFailureReason)로 수신중·실패·완료를 확정 판정
        완료면 GET /medcare/v1/phr/data 로 데이터 조회
```

**조회 API를 늘리지 않았다.** 상태 필드는 이미 매 화면 진입마다 불리는 `GET /phr`에 얹었고(같은 행을 읽으므로 추가 쿼리 0), 데이터는 기존 `GET /phr/data`로 가져온다. 별도 상태 조회 API·폴링 API·비동기 작업 테이블·Redis·분산 락은 만들지 않았다.

### 3-2. 저장소별 역할과 주요 파일

경계 원칙: **PHR 도메인과 스크래퍼는 `skix-medcare`, 푸시 문구와 FCM 발송은 `backend-api-main`.** medcare에는 사용자 FCM 토큰도 알림 설정도 없다.

| 책임 | 저장소 | 파일 (`src/main/java/com/skix/medcare/...`) |
|---|---|---|
| PHR API 5종 진입점, 202 응답, QA 분기 | skix-medcare | `adapter/in/web/PhrApiController` |
| 세션 대조·접수·백그라운드 조립·적재 트랜잭션 | skix-medcare | `application/service/PhrService` |
| 백그라운드 기동과 **실패 전이·푸시 선택의 단일 지점** | skix-medcare | `application/service/PhrScrapeRunner` |
| 유실 회수 배치 | skix-medcare | `application/service/PhrScrapeSweeper` |
| 푸시 3종 기동(예외를 밖으로 내보내지 않음) | skix-medcare | `application/service/PhrPushNotifier` |
| 스크래퍼 호출, **5년 옵션**, 오류코드→사유 매핑 | skix-medcare | `adapter/out/phr/PhrApiAdapter` |
| 조건부 UPDATE 전용 리포지토리 | skix-medcare | `adapter/out/persistence/repository/CustomerIfPhrRepository` |
| 앱 공개 실패 분류 enum | skix-medcare | `domain/model/PhrScrapeFailureReason` |
| 실패 사유를 경계 밖으로 나르는 인터페이스 | skix-medcare | `domain/model/PhrScrapeFailure` (`PhrApiException`이 구현) |
| 400 전용 업무 예외 | skix-medcare | `domain/exception/PhrAuthRequestFailedException` |
| 스윕 임계 설정 | skix-medcare | `config/properties/PhrScrapeProperties` |
| 테이블 정의·수동 ALTER 주석 | skix-medcare | `src/main/resources/db/customer_if_phr.sql` |
| **푸시 문구 정의(코드 3종)** | backend-api-main | `internal/model/MedcareFcmCode` |
| 내부 푸시 수신 API | backend-api-main | `internal/controller/InternalMedcareController` |

### 3-3. 데이터 — `customer_if_phr` 상태 컬럼

DDL 2건. **한 문장으로 합쳐 실행하지 말 것** — ①은 이미 적용된 환경이 있어 합치면 duplicate column으로 전체가 실패한다.

```sql
-- ① 비동기화(202+FCM)  — 2026-08-31 기준 dev·stg·prd 적용 완료로 기록됨
ALTER TABLE customer_if_phr
  ADD COLUMN CUSTOMER_IF_PHR_SCRAPE_STATUS     varchar(20) DEFAULT NULL,
  ADD COLUMN CUSTOMER_IF_PHR_SCRAPE_START_DATE timestamp   NULL DEFAULT NULL,
  ADD KEY idx_phr_scrape_status
    (CUSTOMER_IF_PHR_SCRAPE_STATUS, CUSTOMER_IF_PHR_SCRAPE_START_DATE);

-- ② 본인인증 실패 사유 분리 — 2026-09-04 기준 미적용
ALTER TABLE customer_if_phr
  ADD COLUMN CUSTOMER_IF_PHR_SCRAPE_FAILURE_REASON varchar(20) DEFAULT NULL
    AFTER CUSTOMER_IF_PHR_SCRAPE_STATUS;
```

- **`timestamp`에 `NULL DEFAULT NULL`을 반드시 명시한다.** 생략하면 `explicit_defaults_for_timestamp` 설정에 따라 `NOT NULL DEFAULT CURRENT_TIMESTAMP`가 붙어, 수집을 시작한 적 없는 행(사용여부만 바꾼 행 등)에도 시각이 찍히고 스윕이 그 행을 물어간다.
- **`AFTER` 절은 그대로 써도 된다.** 해당 MySQL 버전에서 임의 위치 삽입에도 `ALGORITHM=INSTANT`가 걸리는 것을 확인했다(2026-09-04) — 테이블 리빌드 없이 끝난다.
- 인덱스는 스윕(`WHERE STATUS='IN_PROGRESS' AND START_DATE < ?`)의 전제다. 없으면 1분마다 풀스캔이 돈다.

**상태값**

| 값 | 의미 |
|---|---|
| `NULL` | 최근 수집 시도 없음. **성공해도 여기로 돌아온다** |
| `IN_PROGRESS` | 수집 진행 중 |
| `FAILED` | 수집 실패 확정(스크래퍼 오류·타임아웃, 또는 스윕이 회수한 유실) |

`DONE`을 두지 않은 이유: 완료는 `customer_if_phr_data`의 수신 시각이 이미 말한다. `NULL`로 되돌리면 다음 시도가 같은 출발점에서 시작해 전이가 하나 줄고, 세션 소비 UPDATE에 컬럼 하나를 얹는 것으로 끝난다.

`SCRAPE_FAILURE_REASON`은 `FAILED`일 때만 `AUTH_FAILED` / `SESSION_EXPIRED` / `GENERAL_FAILED` 중 하나를 갖는다.

### 3-4. 설정

`skix-medcare` `src/main/resources/application.yml`:

```yaml
phr:
  api:
    timeout: 120s          # 60s → 120s. 앱이 더 이상 제약조건이 아니게 된 실질 이득
  push:
    enabled: false         # 환경변수 PHR_PUSH_ENABLED 로 덮는다. 성공·실패 푸시가 이 하나를 공유
  scrape:
    sweep-interval: PT1M   # @Scheduled 의 fixedDelayString 이 직접 읽는다
    stale-after: PT5M      # 반드시 phr.api.timeout 보다 길어야 한다
```

`phr.push.enabled`는 성공 푸시와 실패 푸시가 **같은 플래그를 공유한다.** 한쪽만 나가는 상태를 만들지 않으려는 것이다.

### 3-5. 스크래퍼 계약 — 확정값 (2026-09-04 명세 수령)

**수집 옵션 (5년 제한).** `after_auth` body에 `options`를 싣는다. 국가검진과 외래/처방조제는 옵션이 분리돼 있어 **같은 하한을 각각** 보내야 한다.

```json
{ "sessionId": "...",
  "options": {
    "nationalScreenings":   { "fromDate": "2021-09-22" },
    "medicalTreatmentData": { "fromDate": "2021-09-22" } } }
```

- 하한은 `LocalDate.now(KST) - 5년`이다. **컨테이너 기본 시간대가 UTC라 시스템 기본 시간대로 날짜를 구하면 KST 00:00~09:00 요청의 하한이 하루 이르게 나간다.** 공단 데이터가 KST 기준이므로 경계도 KST로 맞춘다.
- `fromDate`는 `String`이다 — 외부 계약이 ISO `YYYY-MM-DD`라 직렬화 설정(날짜 모듈 등록 여부)에 결과가 좌우되지 않게 형식을 코드로 못박았다. **해당 날짜 포함**이다.
- 5년은 프로퍼티가 아니라 어댑터 내부 상수 `SCRAPE_PERIOD_YEARS`다(§5의 7번).
- `skipInvalidPage` 기본값은 **`true`**다(2026-09-04 확인). 그래서 `NHIS_INVALID_DATA_PAGE`는 우리 호출에서 나오지 않는다 — **대신 공단이 이상 페이지를 주면 조용히 건너뛰고 성공으로 끝난다.** 일부 빠진 스냅샷이 '수신 완료'로 저장되고, 저장 시점에 과거 스냅샷은 파기되므로 더 완전했던 직전 데이터를 덮어쓴다. 정책 결정 사안이라 기본값을 유지하고 코드 주석에만 남겼다(§8-3).

**오류 코드 → 처리 (전량).**

`start_auth`(`POST /phr/request`, 동기):

| 코드 | HTTP | 처리 |
|---|---|---|
| `NHIS_AUTH_REQUEST_FAILED` | 401 | **`400 PHR_AUTH_REQUEST_FAILED`** — 사용자가 입력을 고치면 해결되는 유일한 실패 |
| `INVALID_REQUEST_BODY` | 400 | `502 PHR_API_ERROR` |
| `INVALID_APP_CREDENTIALS` | 401 | `502` |
| `NHIS_AUTH_SESSION_CREATE_FAILED` | 500 | `502` |
| `NHIS_UPSTREAM_HTTP_FAILED` | 502 | `502` |
| (명세에 없는 신규 코드) | – | `502` |

`after_auth`(`POST /phr/response` 이후 백그라운드):

| 코드 | HTTP | `scrapeFailureReason` | FCM |
|---|---|---|---|
| `NHIS_AUTH_RESULT_FAILED` (간편인증 결과 검증 실패) | 401 | `AUTH_FAILED` | `PHR0003` |
| `NHIS_AUTH_SESSION_NOT_FOUND` (세션 없음·만료) | 401 | `SESSION_EXPIRED` | `PHR0003` |
| `NHIS_NETFUNNEL_FAILED` (공단 대기열) | 500 | `GENERAL_FAILED` | `PHR0002` |
| `NHIS_CAPTCHA_FAILED` (기본 5회) | 500 | `GENERAL_FAILED` | `PHR0002` |
| `NHIS_LOGIN_FAILED` | 500 | `GENERAL_FAILED` | `PHR0002` |
| `NHIS_UPSTREAM_HTTP_FAILED` | 502 | `GENERAL_FAILED` | `PHR0002` |
| `NHIS_INVALID_DATA_PAGE` | 502 | `GENERAL_FAILED` | `PHR0002` |
| `INVALID_REQUEST_BODY` | 400 | `GENERAL_FAILED` | `PHR0002` |
| `INVALID_APP_CREDENTIALS` | 401 | `GENERAL_FAILED` | `PHR0002` |
| (명세에 없는 신규 코드) | – | `GENERAL_FAILED` | `PHR0002` |

- **아래 일곱 개를 `GENERAL_FAILED`로 둔 것이 판단이다.** 전부 사용자가 재인증해서 해결되지 않는 사건이다(공단 대기열·캡차·공단 로그인·우리 설정 오류). 여기에 "본인인증에 실패했습니다"를 내보내면 사용자는 자기 잘못이 아닌 일로 간편인증을 반복하게 된다.
- `NHIS_AUTH_REQUEST_FAILED`는 **`start_auth` 전용**이라 이 매핑에 오지 않는다.
- 판정은 HTTP 상태가 아니라 **응답 본문 최상위 `errorCode`** 로만 한다. `data.body`는 공단 원문을 포함할 수 있어 읽지도 로깅하지도 않는다.

**스크래퍼 직접 호출 시 알아둘 것** (dev 박스에서 `PHR_API_URL`이 localhost라 컨테이너 안에서만 직접 호출된다):
- `start_auth` 요청 형식은 `birthday`가 `1990-01-01`(하이픈 있음), `phone`이 `01012345678`(하이픈 없음)만 통과한다.
- 자격증명은 반드시 원본(컨테이너 `printenv` 또는 태스크 정의)에서 가져올 것. 화면에서 옮겨 적었다가 `INVALID_APP_CREDENTIALS`로 한 라운드를 날린 적이 있다.

### 3-6. 관측 — 고정 로그 토큰

종료 로그를 새로 정의했다. 예전 `Phr Data Success`는 스크래퍼 응답 직후·**KMS 암호화와 DB 커밋 이전**에 찍혀서, 저장이 실패하면 성공과 실패가 둘 다 남아 `시작 − 성공 − 실패 = 유실` 계산이 깨졌다.

| 토큰 | 찍히는 지점 |
|---|---|
| `PHR_SCRAPE_STARTED` | `markScrapeStarted` 1행 직후, 백그라운드 기동 시 |
| `PHR_SCRAPE_SUCCEEDED` | **DB 커밋 이후** |
| `PHR_SCRAPE_FAILED` | `markScrapeFailed`가 1행일 때만 |
| `PHR_SCRAPE_RECLAIMED` | 스윕이 회수에 성공한 직후 — **이 건수가 유실의 정본이다** |
| `PHR_SCRAPE_PIPELINE_ERROR` | 원인 진단용. **집계에 쓰지 않는다**(전이 승패와 무관하게 남는다) |

상관키는 `phrId`(인증행 PK, PII 아님)다. `sub`도 함께 남긴다 — 이 저장소는 sub을 불투명 가명으로 보고 마스킹 없이 로깅하는 것이 규약이고, 빼면 장애 대응 때 `phrId → sub` 조회가 한 번 더 필요해진다.

**지표 수집기가 없다**(`management.endpoints`에 `health,info`만 노출, Prometheus·ADOT 없음). 그래서 **알람은 `PHR_SCRAPE_RECLAIMED`에 CloudWatch Logs 지표 필터로 건다.** 아직 걸려 있지 않다(§8-3).

---

## 4. API 계약

전부 `IdToken` 헤더(Cognito ID Token)로 인증하며, 모든 PHR API는 요청자 본인 범위(`OwnerScope`)로만 동작한다. 공통 응답은 기존 규격(`result` / `success` / `message` / `data` / `errorCode`)이고 `data`는 `NON_NULL` 직렬화라 **없으면 키 자체가 없다.**

### 4-1. `GET /medcare/v1/phr` — 사용 여부·상태 조회

```json
{ "result": 200, "success": true,
  "data": {
    "phrReceiveDateTime": "2026-07-03T09:00:00.000+09:00",
    "userName": "홍길동", "userBirthDate": "1990-01-01", "usageYn": true,
    "scrapeStatus": "FAILED", "scrapeFailureReason": "AUTH_FAILED" } }
```

| 필드 | 설명 |
|---|---|
| `phrReceiveDateTime` | 저장된 최신 PHR 데이터의 **실제 수신 시각**. 저장 데이터가 없으면 키 생략. 필드 존재 = 데이터 이력 있음. 형식 `yyyy-MM-dd'T'HH:mm:ss.SSS+09:00` |
| `usageYn` | AI 상담 활용 동의. 레코드 없으면 `false` |
| `scrapeStatus` | `IN_PROGRESS` / `FAILED`. 시도한 적 없거나 마지막 시도가 성공이면 **키 생략** |
| `scrapeFailureReason` | `AUTH_FAILED` / `SESSION_EXPIRED` / `GENERAL_FAILED`. 실패가 아니면 키 생략 |

**`scrapeStatus`는 "데이터 존재 여부"가 아니다.** `phrReceiveDateTime`과 조합해야 화면이 결정된다.

| `phrReceiveDateTime` | `scrapeStatus` | 화면 |
|---|---|---|
| 없음 | 없음 | 미연동 |
| 없음 | `IN_PROGRESS` | 최초 수신 중 |
| 없음 | `FAILED` | 최초 연동 실패 → 재연동 |
| **있음** | 없음 | 정상(연동 완료) |
| **있음** | `IN_PROGRESS` | **기존 데이터 유효** + 갱신 중 |
| **있음** | `FAILED` | **기존 데이터 유효** + 갱신 실패 |

### 4-2. `PATCH /medcare/v1/phr/usage?usageYn={boolean}`

`data`는 4-1과 같은 구조. 레코드가 없으면 새로 만든다.

### 4-3. `POST /medcare/v1/phr/request` — 간편인증 요청

```json
{ "userAuthType": "80", "userName": "홍길동", "userBirthDate": "1990-01-01" }
```

`userAuthType`은 `1`(네이버) / `2`(카카오) / `80`(PASS). 셋 다 필수이고 `userBirthDate`는 형식을 강제하지 않는다(스크래퍼에 그대로 전달).

> **인증 대상 전화번호는 body가 아니라 토큰에서 나온다** — 타인 명의 인증 차단. 그래서 Postman에서 이름·생년월일만 바꿔봐야 소용없고, **토큰 계정과 실제 인증 가입번호가 같아야** 성공한다. 다른 계정 토큰으로 호출하면 `NHIS_AUTH_REQUEST_FAILED`가 난다.

성공 `200`: `{ "data": { "sessionId": "..." } }`. `sessionId`는 1회용이다.

| HTTP | errorCode | 의미 |
|---|---|---|
| **400** | `PHR_AUTH_REQUEST_FAILED` | 입력한 이름·생년월일·전화번호로 간편인증을 **시작할 수 없다.** 메시지 "본인인증에 실패했습니다. 다시 시도해 주세요." 사용자가 입력을 고치면 해결된다 |
| 502 | `PHR_API_ERROR` | 스크래퍼 장애·설정 오류·공단측 오류. 기다려야 하는 실패 |

분기는 `errorCode`로만 한다. `message`는 고정 계약이 아니다.

### 4-4. `POST /medcare/v1/phr/response` — 수집 요청 (202)

```json
{ "sessionId": "..." }
```

접수 응답 `202`:

```json
{ "result": 202, "success": true,
  "message": "PHR 수신 요청을 접수했습니다.",
  "timestamp": "2026-07-03T12:34:56.789Z" }
```

`data` 키는 **없다** — PHI는 이 응답에 실리지 않고 `GET /phr/data`로만 나간다. **본문 `result`와 HTTP 상태를 일치시킨다**(둘이 어긋나면 `result`로 분기하는 앱이 '완료'로 오해한다). `ApiResponse.accepted(...)` 팩토리가 이 일을 한다 — 기존 `successMethod`는 `result`를 200으로 못박아 그대로 쓰면 본문 200 / HTTP 202가 된다.

**중복 요청**: 같은 세션이 아직 수집 중이면 202를 그대로 반환하고 **스크래퍼를 다시 호출하지 않는다.** 다만 그 사이 수집이 끝나면 세션이 소비된 뒤라 404가 나올 수 있다. **이 API가 보장하는 것은 "항상 같은 응답"이 아니라 "외부 호출 중복 방지"다.**

| 상황 | 결과 |
|---|---|
| 세션이 요청자 것이 아님 / 존재하지 않음 | `404 ENTITY_NOT_FOUND` (동기, 즉시. 스크래퍼 호출 없음) |
| 이미 성공·실패로 종료된 세션의 재요청 | `404` (동기, 즉시) |
| 간편인증 결과 검증 실패 / 세션 만료 | 202 뒤 **`PHR0003` 푸시**, `scrapeStatus=FAILED`, `scrapeFailureReason`은 `AUTH_FAILED` 또는 `SESSION_EXPIRED` |
| 스크래퍼 오류·타임아웃·저장 실패 | 202 뒤 **`PHR0002` 푸시**, `GENERAL_FAILED` |
| 배포 중 태스크 종료로 수집 유실 | 최대 5분 뒤 스윕이 회수 → `PHR0002`(`GENERAL_FAILED`) |

### 4-5. `GET /medcare/v1/phr/data` — 저장 데이터 조회

`usageYn`과 무관하게 저장된 최신 원문을 반환한다. 재스크래핑 없음, 즉시 응답, `Cache-Control: no-store`.

```json
{ "result": 200, "data": { "phrData": { "gender": 1, "...": "..." } }, "success": true }
```

`phrData`는 저장된 스크래퍼 응답에서 내부 `data`만 추출한 JSON 객체다. 스크래퍼 `code`·loginId·내부 PK는 포함하지 않는다. 저장 데이터가 없으면 `404 ENTITY_NOT_FOUND`.

### 4-6. `DELETE /medcare/v1/phr/delete`

`customer_if_phr`와 `customer_if_phr_data`를 **한 트랜잭션으로 모두 파기**하고 **멱등 200**이다(지울 데이터가 없어도 성공). 과거에는 데이터 행이 없으면 404였고 인증행은 지우지 않아 리셋이 불가능했다.

### 4-7. FCM 코드 3종

| 코드 | 문구 | 앱 동선 |
|---|---|---|
| `PHR0001` | 개인건강기록(PHR) 수신이 완료되었어요!\n건강상담을 시작해 보세요. | 알림 목록 |
| `PHR0002` | 개인건강기록(PHR) 수신에 실패했어요.\n다시 연동해 주세요. | PHR 연동 화면 |
| `PHR0003` | 본인인증에 실패했습니다.\n다시 시도해 주세요. | PHR 연동 화면 |

문구는 전적으로 `backend-api-main`이 정한다. medcare는 코드만 보낸다.

> ⚠️ **`PHR0003`의 "다시 시도"가 가리키는 곳은 간편인증이다.** 실패 시점에 세션 해시가 파기돼 `POST /phr/response` 재호출은 404다. 이 알림에서 잇는 화면은 반드시 `POST /phr/request`여야 한다. `PHR0002`도 재시작 지점은 같다.

### 4-8. 내부 푸시 API (medcare → backend-api-main)

```http
POST {backend}/internal/medcare/send-fcm
X-Internal-Api-Key: <공유키>
{ "sub": "<Cognito sub>", "code": "PHR0002" }
```

- 해석은 동기, 발송만 비동기다. **사용자를 못 찾으면 404**(시스템 이상 신호), 알림이 꺼져 있거나 토큰이 없으면 **202 뒤 조용히 skip**(정상 업무 결과). 둘을 뭉개면 백필 결손으로 전원이 푸시를 못 받는 상태와 정상 상태를 구분할 수 없다.
- **미지의 코드에는 기본값 폴백이 없다 → `400 INVALID_CODE`.** 이것이 배포 순서를 강제하는 근거다(§7-1).
- 다른 `/internal/**`과 달리 **공유키를 검증한다.** 임의 sub에 임의 시점 푸시를 쏠 수 있는 엔드포인트라서다.

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다. 나머지는 코드 주석과 테스트가 지킨다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **세션 소유권 대조는 202 이전, 스크래퍼 호출 이전에 한다.** `markScrapeStarted`가 세션 대조 + `IS NULL` 전이를 한 문장으로 처리한다 | 뒤로 밀면 남의 세션으로 온 요청에도 202를 주고 나서 **외부에서 타인의 건강정보를 끌어와 자기 계정에 적재**하게 된다. 스크래퍼는 세션↔요청자 바인딩을 하지 않으므로(2026-08-04 실측) 이 대조가 유일한 인가 지점이다 |
| 2 | **실패 전이를 `BackgroundJobs.launch`의 두 번째 인자에서 하지 않는다.** 실패 전이는 job 체인 안(`onErrorResume`)에 있다 | 그 인자는 `Consumer<Throwable>`이라 안에서 `markScrapeFailed(...)`를 불러도 **Mono가 구독되지 않아 아무 일도 일어나지 않는다.** 거기서 `subscribe()`하면 `doFinally`가 이미 `inFlight`를 감소시킨 뒤라 셧다운 드레인 추적에서도 빠져 전이 자체가 유실된다 |
| 3 | **`markScrapeFailed`가 1행을 반환한 쪽만** 정본 로그를 남기고 푸시한다. 이 계약이 `PhrScrapeRunner.failAndNotify` 한 곳에만 있다 | 백그라운드 실패 경로와 스윕이 같은 행을 동시에 잡을 수 있다. 조건을 두 곳에 복사하면 한쪽만 고쳐져 **실패 알림이 두 번 간다.** 분산 락 대신 조건부 UPDATE의 영향 행 수로 승자를 가리는 것이 이 저장소의 관용구다 |
| 4 | **`markScrapeStarted`/`markScrapeFailed`/`clearSessionHash`는 빈 Mono를 오류로 취급한다**(`switchIfEmpty`) | Spring Data R2DBC는 `@Query`로 쓴 UPDATE에 `@Modifying`이 없으면 **문장은 실행하면서 영향 행 수를 emit하지 않고 빈 Mono로 완료한다.** 2026-08-05에 `/phr/response`가 저장·세션소비를 다 하고도 200 + 빈 body를 반환한 실제 사고가 있었다. '0행'(경합의 정상 결과)과 '신호 없음'(설정 오류)을 갈라 둔 방어다. `ModifyingQueryContractTest`가 빌드 시점에 강제한다 |
| 5 | **`phr.scrape.stale-after`(5분)는 `phr.api.timeout`(120초)보다 항상 길어야 한다** | 짧으면 정상적으로 오래 걸리는 수집을 유실로 보고 `FAILED`로 전이시킨다. 그러면 실패 푸시를 보낸 직후 성공 경로의 `clearSessionHash`가 0행이 되어 **그 수집 자체가 롤백된다** — 멀쩡한 수집을 우리가 죽이는 셈이다 |
| 6 | **`requestPhrAuth`(인증행 생성)에서 `SCRAPE_STATUS`를 채우지 않는다** | `markScrapeStarted`의 `IS NULL` 조건이 여기 기댄다. 여기서 값을 채우면 `/phr/response`가 **영원히 0행**이 되어 전건 404다 |
| 7 | **수집 5년은 프로퍼티가 아니라 어댑터 내부 상수(`SCRAPE_PERIOD_YEARS`)다** | 이 값은 운영 튜닝값이 아니라 수집 정책이다. 프로퍼티로 두면 환경변수 하나로 수집 범위가 정책보다 넓어질 수 있고, 그건 최소수집 원칙 위반이 된다 |
| 8 | **수집 하한 날짜는 KST로 계산한다**(`LocalDate.now(clock.withZone(KST))`) | 컨테이너 JVM 기본 시간대가 UTC라 시스템 기본으로 구하면 **KST 00:00~09:00 요청의 하한이 하루 이르게** 나간다. 공단 데이터가 KST 기준이라 경계도 KST여야 한다 |
| 9 | **`scrapeAndPersist`를 `Mono.defer`로 감싼다** | 이 메서드는 `PhrScrapeRunner.launch`의 **인자로 평가**되므로, defer가 없으면 스크래퍼 호출 조립이 202를 돌려주는 요청 스레드에서 일어난다. 지금은 WebClient가 콜드라 실제 HTTP가 나가지 않지만, 어댑터가 조금이라도 즉시 작업을 하게 바뀌는 순간 그 일이 동기 구간으로 샌다 |
| 10 | **`recoverMono`에 `PhrAuthRequestFailedException` 통과 조건을 유지한다** | 그 아래 줄이 모든 예외를 `PhrApiException`으로 되감싼다. 통과 조건을 빼면 사용자에게 재입력을 안내하려고 만든 400이 **다시 502 '외부 서비스 실패'로 되돌아가** 분류가 통째로 무의미해진다. 테스트로 고정했다 |
| 11 | **`PhrPushNotifier`의 세 메서드는 어떤 경우에도 예외를 밖으로 던지지 않는다**(자체 try/catch) | 성공 푸시 호출 지점이 `doOnNext`라, 예외가 새면 성공 시그널이 오류로 바뀌어 **이미 커밋된 PHR이 500으로 끝난다.** 게다가 그 예외가 `doOnError`를 지나 'Phr Data Save Fail'로 오기록된다 — 저장은 멀쩡히 됐는데 저장 실패로 남는다. 실패 푸시 쪽도 같다: 예외가 새면 이미 기록된 `FAILED` 전이가 파이프라인 오류로 뒤집혀 보인다 |
| 12 | **성공·실패 푸시가 `phr.push.enabled` 하나를 공유한다** | 나누면 완료만 나가고 실패는 안 나가는(혹은 그 반대) 상태를 만들 수 있다. 비동기 전환 이후 실패 푸시는 사용자가 실패를 아는 **유일한 즉시 경로**다 |
| 13 | **푸시는 문구가 아니라 코드를 보낸다.** 문구는 `MedcareFcmCode`가 독점한다 | 문구를 파라미터로 열면 `/internal/**`에 닿는 누구나 임의 텍스트를 사용자 단말에 띄울 수 있다. 문구를 늘릴 때도 파라미터가 아니라 코드를 늘린다 |
| 14 | **`MedcareFcmCode.of()`에 미지 코드 기본값 폴백을 두지 않는다** | 폴백은 오타 하나로 엉뚱한 문구를 사용자에게 보내고도 아무 신호를 남기지 않는다. 대신 이 성질이 배포 순서를 강제한다(§7-1) — 순서를 어기면 **시끄럽게** 400으로 실패한다 |
| 15 | **`AUTH_FAILED`와 `SESSION_EXPIRED`를 한 FCM 코드(`PHR0003`)로 묶는다.** `PhrScrapeRunner.notify`의 switch에 `default`를 두지 않는다 | 사용자가 할 일이 '간편인증부터 다시'로 같아서, 가르면 만료를 본인 잘못으로 오해하게 만드는 문구 차이만 남는다. 세부 구분이 필요한 화면은 `scrapeFailureReason`을 읽는다. `default`가 없어야 사유가 늘 때 컴파일이 깨져 이 분기를 반드시 보게 된다 |
| 16 | **`scrapeStatus == FAILED`라고 기존 PHR 데이터를 숨기거나 지우지 않는다** | 재수집 실패일 뿐 이전 데이터는 서버에 그대로 있고 `GET /phr/data`로 정상 조회된다. `FAILED` + `phrReceiveDateTime` 존재는 **정상 조합**이다 |
| 17 | **`deleteSnapshotsOlderThan`은 단일 키가 아니라 `OwnerScope`로 지운다** | 단일 키면 다른 provider로 로그인해 만들어진 sibling sub 소유의 PHI 사본이 **영구히 남는다.** 신규 INSERT는 `OwnerKey`, 조회·삭제·잠금은 `OwnerScope`, 기존 행 UPDATE는 소유자 컬럼을 되쓰지 않는다 — 이 경계를 섞지 말 것 |
| 18 | **`/phr/response` 응답에서 QA 분기(`qaInvalidPhrSubs`)를 뺐다.** `GET /phr/data` 쪽에만 남겼다 | 202 응답에 payload가 없어 대체할 자리가 없다. 적재는 평소대로 되므로 `GET /phr/data` 분기만으로 앱의 "유효하지 않은 데이터" 화면 QA가 성립한다. 이 분기는 `QA_INVALID_PHR_SUBS` 환경변수에 sub을 넣어야만 동작하고 **세 환경 모두 미설정**이다 — 운영계에는 넣지 않는다 |
| 19 | **엔티티 `save()`로 인증행을 갱신하지 않는다.** 모든 갱신이 컬럼 단위 부분 UPDATE다 | `save()`는 읽어둔 컬럼을 되써서 **소비된 세션을 되살린다.** 스크래퍼 호출이 수십 초라 경합 창이 넓다 |

---

## 6. 앱에 요청한 내용

앱 코드는 직접 수정하지 않았다. 아래가 두 요청서의 핵심 전부다. 인수자는 이 내용이 실제 구현과 맞는지 확인하는 역할이다. **iOS는 전부 반영됐고, Android는 6-2의 2건이 남아 있다**(§2-5).

### 6-1. 요청서 ① — PHR 연동 비동기화 (2026-08-31 전달)

**바뀌는 흐름**

```
[지금]  POST /phr/response ─── 최대 60초 대기 ───► 200 { data: { phrData } }
                                                     └─ 이 시점에 앱이 "연동 완료" 처리
[변경]  POST /phr/response ──► 202 즉시 (접수됨, data 키 없음)
                                    └─ 여기서는 "완료" 처리하지 않음
                      서버가 백그라운드로 수집 → 성공 PHR0001 / 실패 PHR0002·PHR0003 푸시
                      푸시 수신 후에야 GET /phr + GET /phr/data 로 완료 처리
```

**(A) 대기 코드 제거 — 순감이다**

- Android: 전용 `PhrDataReceiveWorker`와 결과 홀더 **삭제**(WorkManager 기동 경로 포함), `/phr/response` 전용 60초 readTimeout 분기 제거(스트리밍 `text/event-stream` 분기는 유지), 응답 타입을 본문 데이터 없는 형태로.
- iOS: `receiveData()`의 502 전용 분기와 `authResponseServerError` 경로 정리. **요청 성공 = 접수됨.**

**(B) 🚨 "접수됨"과 "연동 완료"를 분리해야 한다 (가장 중요)**

202를 단순 성공으로만 바꾸면 스크래핑이 끝나기도 전에 "불러왔습니다"가 뜬다. **202 수신 시에는 일회성 세션 정리와 요청 화면 종료까지만** 하고, 아래는 실행하지 않는다.

- 연동 완료 상태로 전환 / PHR 데이터 로컬 저장 / "신규 기록을 불러왔습니다" 안내 / `SUCCESS`·`NO_RECORD` 이벤트 발생

**완료 판정은 `PHR0001`을 받은 뒤 `GET /phr` · `GET /phr/data` 재조회 결과로만** 한다.

- iOS는 응답 콜백에서 로컬 저장·상태 전환·토스트를 한꺼번에 하고 있었으므로 실질적인 분리 작업이 필요했다.
- Android는 이미 `fetchPhrInfo()` 재조회를 한 번 더 거친 뒤에야 적용하고 있어, **그 재조회 트리거를 "응답 성공"에서 "`PHR0001` 수신"으로 옮기면 되는** 구조였다.

**(C) 실패 푸시 처리 + FCM 진입 경로**

- Android는 코드별 개별 배선이라 세 곳을 각각 손봐야 한다: 홈의 실시간 수신 콜백, **앱 종료 상태에서 받은 FCM을 처리하는 하드코딩 목록**, 알림 이력 클릭 분기.
- iOS는 실시간·콜드스타트·알림 이력이 한 경로로 수렴해 코드 맵 등록만으로 세 경로가 함께 커버된다.
- `PHR0001`은 알림 목록으로, **`PHR0002`·`PHR0003`은 PHR 연동 화면으로**(재연동이 목적). iOS는 foreground 수신 시 즉시 화면 이동을 하지 말고 상태만 갱신한다.
- 🚨 **실패 푸시를 받아도 기존에 성공한 PHR 데이터와 연동 상태는 지우지 않는다.**

**(D) 알림을 꺼둔 사용자 대비 — `GET /phr` 재조회 (필수)**

서버 발송 로직은 알림 마스터 스위치 OFF·FCM 토큰 없음이면 **조용히 건너뛴다.** 드문 예외가 아니라 정상 경로이고, 그 사용자는 FCM만으로는 결과를 영영 알 수 없다. 새 화면이나 폴링은 필요 없고 기존 재조회 흐름에 아래만 얹으면 된다.

- PHR 화면 **진입 시** / 앱 **foreground 복귀 시** `GET /phr`
- `IN_PROGRESS` → 기존 데이터 유지, **완료 처리하지 않음**
- `FAILED` → 기존 데이터 유지 + 실패 안내
- `scrapeStatus` 키 없음 + `phrReceiveDateTime` 존재 → 필요하면 `GET /phr/data`

**(E) 전경(foreground) 동작 확인 요청** — 토스트를 없애고 FCM만 남기면 앱이 전경일 때 사용자가 아무것도 못 볼 수 있다. 서버는 `notification` 페이로드로 보내는데 iOS는 전경 배너를 기본으로 안 띄우고 Android도 트레이 대신 `onMessageReceived`로만 들어온다. PHR 수집 실패는 사용자가 방금 간편인증을 마치고 앱을 보고 있을 확률이 가장 높은 시나리오라 이 조합이 특히 불리하다. → **2026-09-04 확인 결과 앱 쪽에서 이미 처리돼 있어 우려는 해소됐다.**

### 6-2. 요청서 ② — 본인인증 실패 분리 (2026-09-04 전달) · **Android 잔여 2건**

**(1) `PHR0003` FCM 코드 추가**

`FcmCode`에 `PHR0003`을 추가하고 FCM 라우팅 분기를 `PHR0001 || PHR0002 || PHR0003`으로 넓힌다. 서버가 코드를 고르는 규칙은 `AUTH_FAILED·SESSION_EXPIRED → PHR0003`, `GENERAL_FAILED → PHR0002`이고 **앱이 판단할 것은 없다.**

`GET /phr`에 같은 분류를 담은 `scrapeFailureReason`도 함께 넣어 뒀지만, 현재 화면 설계에서는 실패 안내를 알림 문구가 전담하므로 **앱 작업 대상에서 뺐다.** optional 필드라 무시해도 문제없다.

**(2) `POST /phr/request`의 `400 PHR_AUTH_REQUEST_FAILED` 분기**

지금 앱은 이 실패를 `else -> onFailure()`로 뭉개 "일시적인 오류입니다. 잠시 후 다시 시도해 주세요"를 띄운다. **입력값을 고쳐야 하는 사용자에게 기다리라고 안내하고 있고, 기다려도 해결되지 않는다.** (최초 연동이면 스위치까지 꺼져서 사용자는 원인을 모른 채 처음부터 다시 하게 된다.)

```json
HTTP 400
{ "result": 400, "success": false,
  "errorCode": "PHR_AUTH_REQUEST_FAILED",
  "message": "본인인증에 실패했습니다. 다시 시도해 주세요." }
```

- **앱 공통 계층은 손댈 필요가 없다.** Android의 `safeApi`가 400을 `ApiResult.ApiError(code, resErrorData=파싱된 errorCode)`로 정상 전달하는 것을 확인했다(가로채지도, 크래시로 보내지도 않는다). ViewModel 분기만 추가하면 된다.
- **문구는 이미 앱에 있다.** `mh_phr_authentication_failed`("본인인증에 실패했습니다. 다시 시도해 주세요")가 기획 확정 문구와 같은데, 비동기화 MR에서 기존 분기가 빠지면서 미사용 상태가 됐다. 그대로 재사용하면 된다.

**"다시 시도"의 목적지가 `PHR0003`과 다르다** — 이 표를 앱에 함께 전달했다.

| | `400 PHR_AUTH_REQUEST_FAILED` | `PHR0003` (FCM) |
|---|---|---|
| 시점 | `/phr/request` 즉시 실패 | 수집 중 백그라운드 실패 |
| 세션 | 아직 만들어진 적 없음 | 만들어졌다가 파기됨 |
| 원인 | 입력 정보가 인증에 맞지 않음 | 간편인증 자체가 실패·만료 |
| 적절한 동선 | **입력 바텀시트 유지 + 오류 표시** | `/phr/request`부터 다시 |

`400`에서 같은 값으로 자동 재요청하면 반드시 다시 실패한다. 최초 연동 실패 시 스위치를 끄는 처리도 이 경우에는 입력만 고치면 되므로 유지하는 쪽이 나을 수 있다 — 화면 흐름은 앱 담당 판단에 맡겼다.

**(3) 참고로 전달한 것** — 앱의 `scrapeStatus == FAILED` 사용처 두 곳(기존 데이터 유지 판정, 202 직후 재조회)은 둘 다 사유를 보지 않으므로 `scrapeFailureReason`은 요청 대상에서 뺐다. 또 Android의 `PHR_AUTHENTICATION_ERROR_RESULT=502` / `PHR_AUTHENTICATION_ERROR_CODE="PHR_API_ERROR"` 상수는 **죽은 코드**다 — `/phr/response`가 스크래퍼 호출 전에 202를 반환하게 되면서 그 경로에서 502가 나올 수 없다.

---

## 7. 배포 방법

### 7-1. 순서 (환경별로 반복, 역순 금지)

```
1. backend-api-main   ← PHR0002·PHR0003 코드. medcare 보다 반드시 먼저
2. customer_if_phr DDL (①②, 그 환경에 없는 것만)   ← 1과 독립, 3보다 먼저
3. skix-medcare
4. 앱
```

| 어긴 경우 | 결과 |
|---|---|
| medcare를 backend보다 먼저 | `MedcareFcmCode.of()`가 미지 코드에 폴백을 주지 않아 **실패 푸시가 전건 400.** 사용자는 실패를 영영 모른다 |
| DDL 없이 medcare 배포 | `save()`가 없는 컬럼을 INSERT해 **`POST /phr/request`·`PATCH /phr/usage`가 전건 500.** 읽기는 `SELECT *`라 멀쩡해 증상이 헷갈린다 |
| DDL ①을 이미 적용된 환경에 다시 | duplicate column으로 **ALTER 전체 실패.** ①②를 한 문장으로 합치지 말 것 |

**운영계 첫 배포에서 `PHR_PUSH_ENABLED`를 `true`로 올릴 때**는 `backend-api-main` 운영 배포가 선행돼야 한다. §2-2에서 확인했듯 운영 `main`에는 `/internal/medcare/send-fcm`이 아직 없다.

### 7-2. 배포 절차 (skix-medcare)

ECS 배포는 CloudShell 수동 방식이다.

```bash
./deploy/build-export.sh            # medcare.tar.bz2 생성 (운영 이미지, distroless)
./deploy/build-export.sh debug      # ECS Exec 디버깅용 (shell+curl 포함, dev 전용)
# medcare.tar.bz2 를 AWS CloudShell 에 업로드한 뒤
./deploy/cloudshell-push.sh dev|stg|prd
```

- 클러스터 `skmg-airbot-{env}-ecs-cluster`, 서비스 `skmg-airbot-{env}-medcare-svc`, 태스크 패밀리 `skix-medcare-{env}-tdef`.
- **함정: `cloudshell-push.sh`는 AWS에 등록된 최신 태스크 정의 리비전을 그대로 재사용한다.** 저장소의 `deploy/task-definition-*.json`을 고쳤다고 반영되지 않는다. 환경변수를 바꾸려면 콘솔이나 `register-task-definition`으로 **새 리비전을 먼저 등록**해야 한다.
- `build-export.sh`는 Dockerfile 두 스테이지 모두에 `-Duser.timezone=UTC`가 있는지 확인하고 없으면 빌드를 막는다. 이 저장소는 JVM 기본 시간대를 UTC로 고정하는 것이 전제다.

### 7-3. 배포 후 검증

1. **기동 확인** — 로그에 `QA 분기 활성` 경고가 뜨면 그 환경에 `QA_INVALID_PHR_SUBS`가 들어 있다는 뜻이다(운영계에서는 나오면 안 된다).
2. **전체 흐름** — 부록 B의 8단계 curl. 1단계만 따로 돌려도 backend의 코드 반영 여부를 가를 수 있다(미정의 코드면 400, 반영됐으면 202).
3. **DDL 확인** — `SHOW COLUMNS FROM customer_if_phr LIKE 'CUSTOMER_IF_PHR_SCRAPE%'` 로 3개 컬럼, `SHOW INDEX FROM customer_if_phr` 로 `idx_phr_scrape_status`.
4. **스윕 동작** — 수집 중 태스크를 재시작한 뒤 5분 안에 `PHR_SCRAPE_RECLAIMED`가 1건 남고 실패 푸시가 **한 번만** 가는지.
5. **기존 데이터 유지** — 데이터가 있는 계정에서 재연동을 실패시켜 `scrapeStatus=FAILED`와 `phrReceiveDateTime`이 **둘 다** 내려오는지.
6. **복수 provider** — sibling sub 두 계정으로 로그인해 같은 PHR이 조회·갱신되는지(dev·stg는 identity 플래그가 켜져 있어 실제로 갈린다).

### 7-4. 롤백

- **애플리케이션**: 이전 이미지로 되돌린다. 단, 롤링 중 구 태스크는 `FAILED`만 쓰고 사유 컬럼을 비워둘 수 있다 — 서버가 그 경우를 `GENERAL_FAILED`로 닫아 앱 계약을 배포 순서에 노출하지 않는다(`PhrService.responseFailureReason`).
- **DB**: 컬럼을 **되돌리지 않는다.** 구버전은 이 컬럼들을 모르고, 지우면 신버전 재배포 때 다시 전건 500이 된다.
- **푸시**: 급하면 `PHR_PUSH_ENABLED=false`로 끌 수 있다(새 태스크 정의 리비전 필요). 다만 비동기 전환 이후 이 플래그가 꺼지면 사용자가 결과를 아는 즉시 경로가 사라진다 — `GET /phr` 재조회만 남는다.

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **DDL 2건의 환경별 적용 여부 확인.** ①은 3환경 적용 완료로 기록돼 있고 ②는 미적용으로 기록돼 있으나 둘 다 재확인 못 했다. §7-3의 3번 쿼리로 dev·stg·prd를 각각 본다 | 백엔드 | VDI에서. **이걸 모르면 아래가 전부 추정** |
| 2 | **dev·stg ECS 배포 상태 확인.** 특히 09-04 실패 분리(`75d692e` / `84434da1`)가 배포됐는지. 안 됐으면 §7-1 순서로 배포 | 백엔드 | VDI에서 태스크 정의 이미지 태그·배포 시각 확인 |
| 3 | **5년 옵션이 실제로 적용되는지 dev 실호출 대사.** 스크래퍼가 `options`를 적용한다는 것은 **명세 기반 가정**이고 검증되지 않았다. 구버전이면 요청은 200으로 성공하면서 옵션만 무시돼 **5년 초과 PHI가 계속 저장되는 침묵 실패**가 가능하다. 5년보다 오래된 기록이 있는 계정으로 (1) 국가검진과 (2) 외래/처방조제 **각각**의 최고(最古) 날짜가 `fromDate` 이상인지 대사하고, 스크래퍼 요청 캡처에 `options`가 실제 수신됐는지 확인한다 | 백엔드 + 스크래퍼 담당 | **이미 `main`까지 올라가 있어 원래 계획한 게이트가 이미 지나갔다**(§2-1). 지금은 "운영 오픈 전 확인"으로 성격이 바뀌었다 |
| 4 | **Android 잔여 2건 착수 확인** — `PHR0003` 코드 추가, `/phr/request` 400 분기. 요청서는 전달됐으나 2026-09-14 브랜치 기준 미반영 | Android 담당 | 내용은 §6-2 |
| 5 | Android PHR 브랜치(`feature/my-healthcare-phr-integration`)의 `develop` 머지 일정 확인 | Android 담당 | 서버가 앞서 있는 상태 |
| 6 | 스크래퍼 담당에게 **두 `fromDate`가 수집 단계에 적용되는지, 날짜 비교가 해당 날짜 포함인지**를 문서로 받아 둔다 | 백엔드 | 3번의 근거 보강 |

### 8-2. 운영계 오픈 게이트 (전부 확정 전 prd 배포·PHR 오픈 금지)

| # | 확인할 것 | 이유 |
|---|---|---|
| 7 | **identity 플래그 P3(`alias-read`) → P4(`canonical-write`) 완료.** prd는 현재 둘 다 `false` | 그 상태로 PHR을 열면 사용자가 만드는 PHR 행이 canonical이 아닌 로그인 sub으로 적힌다. **PHR은 재키잉 대상이 아니라 나중에 고쳐지지 않는다.** 롤아웃 순서가 강제다 — `canonical-write`만 단독으로 켜면 코드가 무시하고 기동 경고만 남긴다 |
| 8 | **prd `customer_if_phr`·`customer_if_phr_data` 0건 확인** | 재키잉 대상이 아니라 로그인 sub으로 적힌 테스트 행이 남으면 정식 서비스 데이터와 섞인다. dev·stg는 `alias-read`로 계속 접근해도 되지만 prd는 안 된다 |
| 9 | **운영계 `PHR_ACCESS_KEY`·`PHR_ACCESS_SECRET`이 실값인지** | 한때 더미(`PENDING_SCRAPPER`)였다. `WebClientConfig`가 세 WebClient를 한 생성자에 묶어 두어 이 프로퍼티가 없으면 컨테이너가 기동조차 못 한다 |
| 10 | **`backend-api-main` 운영 배포가 medcare보다 먼저** | 운영 `main`에 `/internal/medcare/send-fcm`이 없다(§2-2) |
| 11 | 3번(5년 옵션 실적용) 확정 | 최소수집 조치가 실제로 걸려 있는지 |
| 12 | 오픈 후 **1건 실측** — 신규 인증행·데이터 행이 `OwnerKey`(canonical)로 저장되는지 DB에서 직접 확인 | |
| 13 | `main` 승격 및 운영 배포. `PHR_PUSH_ENABLED`는 **운영 첫 배포부터 `true`** | 릴리스 전이라 dark 롤아웃이 필요 없고, 이제 FCM이 결과 통보의 유일한 즉시 경로다 |

### 8-3. 후속 (이 기능을 막지는 않음)

| # | 항목 |
|---|---|
| 14 | **`PHR_SCRAPE_RECLAIMED`에 CloudWatch Logs 지표 필터 + 알람.** 지표 수집기가 없어 이것이 유일한 유실 관측 경로다. 아직 걸려 있지 않다 |
| 15 | **`BackgroundJobs.DRAIN_TIMEOUT`(20초) 상향 검토.** 스크래핑 최빈값이 30초대라 배포마다 진행 중인 건이 죽는다. **`PHR_SCRAPE_RECLAIMED` 건수를 본 뒤** 판단한다 — 배포 시간이 그만큼 길어지므로 데이터 없이 올리지 않는다 |
| 16 | **스크래퍼 콜백 전환.** PHR API가 사내 관리라면 "완료 시 medcare로 콜백"을 협의할 수 있다. 그러면 "수십 초짜리 외부 호출을 우리 프로세스가 들고 있는" 문제가 원천적으로 사라진다. 이번에 만든 상태머신은 그대로 두고 수신부만 바꾸면 된다 |
| 17 | **`skipInvalidPage=true`로 인한 조용한 부분 수집.** 일부 빠진 스냅샷이 완전했던 직전 스냅샷을 덮어쓴다. 스크래퍼가 건너뛴 페이지 수를 응답에 주는지 확인하면 부분 수집 판별이 가능해진다. 정책 결정 사안이라 기본값을 유지하고 주석에만 남겼다 |
| 18 | **저장된 PHR의 케이론(AI) 첨부**는 별건이다. `CHAT_PHR_ATTACHMENT_ENABLED` 플래그 뒤에 있고 dev·stg `true` / prd `false`다. 이 작업 묶음과 배포 순서를 공유하지 않는다 |
| 19 | 기존 전체 기간으로 저장된 dev·stg 스냅샷은 테스트 데이터라 정리 대상이 아니다(당시 확정). 운영계는 애초에 0건이어야 한다(8번) |
| 20 | `POST /phr/request`(start_auth)는 p95 1.2초로 비동기 전환 대상이 아니다. 판단 근거를 남겨 둔다 |

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| "연동했는데 아무 반응이 없어요" | 먼저 `GET /phr`의 `scrapeStatus`. `IN_PROGRESS`면 정상 대기 중이다(최빈 30초대). 5분 넘게 `IN_PROGRESS`면 스윕이 못 돌고 있는 것 — 로그에서 `PHR 수집 유실 회수 실패` 확인 |
| "본인인증 실패 푸시를 받았어요" | `scrapeFailureReason`을 본다. `AUTH_FAILED`(인증 결과 검증 실패) / `SESSION_EXPIRED`(세션 만료). **둘 다 `/phr/request`(간편인증)부터 다시** 해야 한다. `/phr/response` 재호출은 404다 |
| "이름을 정확히 넣었는데 인증이 안 돼요" | `POST /phr/request`가 400 `PHR_AUTH_REQUEST_FAILED`인지 502인지 확인. **400이면 입력값 문제**(토큰 계정의 전화번호와 실제 인증 가입번호가 달라도 여기로 온다), 502면 스크래퍼·공단 장애라 기다려야 한다 |
| 실패 푸시가 두 번 왔다 | 설계상 불가능하다(§5의 3번). 났다면 `failAndNotify`의 `updated == 1` 조건이 깨졌거나 같은 계약이 두 곳으로 복사된 것이다 |
| 상태 조회 | `SELECT CUSTOMER_IF_PHR_ID, CUSTOMER_IF_PHR_AUTH_SUB, CUSTOMER_IF_PHR_SCRAPE_STATUS, CUSTOMER_IF_PHR_SCRAPE_FAILURE_REASON, CUSTOMER_IF_PHR_SCRAPE_START_DATE, CUSTOMER_IF_PHR_SESSION_HASH IS NOT NULL AS session_alive FROM customer_if_phr WHERE CUSTOMER_IF_PHR_AUTH_SUB IN (...) ORDER BY CUSTOMER_IF_PHR_ID DESC LIMIT 5;` |
| `IN_PROGRESS`가 오래 남아 있음 | 스윕이 5분 뒤 회수한다. 회수되지 않으면 임계 미달이거나 스윕이 안 도는 것 |
| `FAILED`인데 데이터는 보인다 | **정상 조합이다.** 재수집 실패일 뿐 이전 스냅샷은 그대로 있다 |
| 수집 소요시간·유실 관측 | 부록 C의 Q10 |
| 앱이 "유효하지 않은 데이터" 화면을 QA하고 싶다 | 태스크 정의 `QA_INVALID_PHR_SUBS`에 대상 sub을 쉼표로 넣고 새 리비전 등록 → `GET /phr/data`가 파싱 실패용 payload를 돌려준다. 끝나면 뺀다. 기동 로그에 `QA 분기 활성` 경고가 뜬다 |
| 서버 로그 키워드 | `PHR_SCRAPE_STARTED` / `PHR_SCRAPE_SUCCEEDED` / `PHR_SCRAPE_FAILED` / `PHR_SCRAPE_RECLAIMED` / `PHR_SCRAPE_PIPELINE_ERROR`, 푸시는 `sendPhrReceivedPush()` · `PHR ... 푸시 실패` |
| 스크래퍼 건별 추적 | medcare 로그의 `traceId`를 스크래퍼 로그에서 grep한다. `X-Request-Id`로 보낸 호출당 랜덤 UUID다(예전에는 전화번호를 실어 보내 외부 로그로 PII가 샜다) |

---

## 부록 A. 핵심 커밋 (시간순)

| 날짜 | 저장소 | 커밋 | 내용 |
|---|---|---|---|
| 08-05 | skix-medcare | `8636469` | 삭제를 두 테이블 파기·멱등으로 교정 + `GET /phr/data` 신설 |
| 08-05 | skix-medcare | `5d877e7` / `672471f` | PHR 응답 구조 최소 계약으로 재정리, `phrData` 개명 |
| 08-05 | skix-medcare | `1d70692` | `phrReceiveDateTime`을 +09:00 오프셋 명시로 |
| 08-05 | skix-medcare | `3757ed9` | PHR 스펙을 `docs/API-SPEC.md`로 통합 |
| 08-12 | skix-medcare | `c960fbc` | 저장된 PHR의 채팅 첨부(기능 플래그 뒤) — **별건** |
| 08-19 | skix-medcare | `ef97d8e` / `4ef2fa0` | 수신 완료 푸시 `PHR0001` + dev·stg 활성화 |
| 08-19 | backend-api-main | `e6e726c5` / `7e40fc66` / `91eb9d2f` | medcare 푸시 내부 API + 공유키 검증 |
| 08-19 | backend-api-main | `0e422ed7` | PHR 완료 알림을 인앱 공통 알림함에 기록 |
| **08-19** | skix-medcare | **`89b6f95`** (머지 `0f6619d`) | **수집 범위 5년 제한** |
| 08-25 | skix-medcare | `2326f38` / `3617a1f` | PHR 파싱 실패 화면 QA 분기 (stg cherry-pick `94bf443`·`accb96b`) |
| **08-31** | skix-medcare | **`4971f08`** | **202 접수 + FCM 통보 + 상태 컬럼 + 스윕 + 소유자 키 `OwnerContext` 전환** |
| 08-31 | backend-api-main | `4e205dfb` (stg 머지 `2c21801e`) | `MedcareFcmCode.PHR0002` |
| 08-31 | skix-medcare | `5e8e205` (stg `2ee1091`) | dev·stg identity 플래그 실상태를 태스크 정의에 반영 |
| 09-03 | skix-medcare | `456ea9d` | 수집 실패를 `AUTH_FAILED`·`SESSION_EXPIRED`·`GENERAL_FAILED`로 분류 |
| 09-03 | skix-medcare | `bb68b1d` | 본인인증 실패를 전용 푸시 `PHR0003`으로 통보 |
| 09-03 | backend-api-main | `af0045ce` (머지 `84434da1`, stg `d4e5af52`) | `MedcareFcmCode.PHR0003` |
| 09-04 | skix-medcare | `a4ca7cf` | 스크래퍼 오류 명세를 받아 실패 분류 확정(파라미터 테스트) |
| 09-04 | skix-medcare | `732b6ac` | 리뷰 잔여 지적 정리 |
| **09-04** | skix-medcare | **`8b623c8`** (머지 `75d692e`, stg `4ffc4e7`) | **간편인증 요청 실패를 장애가 아니라 사용자 실패(400)로 분리** |
| 09-04 | skix-medcare | `7a66434` | 스크래퍼 옵션 기본값(`skipInvalidPage`) 확인 결과 기록 |

**커밋을 둘로 나눈 것은 의도다.** 실패 분류(`456ea9d`)는 앱 영향이 0이라(FCM 코드 추가는 가산적) 바로 배포할 수 있고, `PHR0003`(`bb68b1d`)과 400 분리(`8b623c8`)는 UI 결정·응답 계약에 걸려 단독 revert가 가능해야 했다. **끝난 작업을 안 끝난 작업의 인질로 만들지 않으려는 분리다.**

테스트: 2026-08-31 비동기화 시점에 전체 610건 + 신규 12건 통과. PHR 관련 테스트 클래스는 `PhrDataContractTest`·`PhrDataLifecycleTest`·`PhrSessionBindingTest`·`PhrPushNotifyTest`·`PhrScrapeFailureTest`·`PhrOutboundRequestTest`·`PhrQaInvalidPayloadContractTest`·`PhrPruneQueryTest`·`PhrSnapshotPruneTest` 등이다. 고정한 계약은 (1) 세션 대조 실패는 404이고 백그라운드 기동·스크래퍼 호출 이전, (2) `markScrapeStarted` 0행이 중복 요청을 막음, (3) `markScrapeFailed` 1행일 때만 푸시, (4) **실패 전이가 job 체인 안에서 실행됨**(회귀 방지의 핵심), (5) sibling sub 로그인으로도 자기 PHR 행을 찾고 갱신, (6) 스윕을 두 번 돌려도 푸시가 한 번만 나감.

---

## 부록 B. 개발계 검증 curl (8단계)

`CURL-verify-dev.sh`로 저장해 실행한다. **상단 변수를 직접 채워야 한다** — 로컬 맥에서는 AWS CLI·SSM에 닿지 않아 값이 비어 있는 상태로 남아 있다. `INTERNAL_API_KEY`는 SSM 또는 `backend-api-main` 쪽 medcare 내부 API 키 설정에서, `ID_TOKEN`은 dev Cognito 로그인으로 얻는다.

```bash
#!/usr/bin/env bash
# PHR 비동기화(202+FCM) dev 검증용 curl 모음.
# 값을 채워야 하는 변수는 아래에서 직접 채운다.

set -euo pipefail

# ── 채워야 할 값 ──────────────────────────────────────────────
MEDCARE_URL=""          # medcare dev 공개 URL (또는 ALB 주소)
ID_TOKEN=""             # Cognito IdToken (dev 사용자 풀에서 로그인 후 발급)
BACKEND_URL=""          # backend-api-main dev URL
INTERNAL_API_KEY=""     # medcare 내부 API 공유키
TEST_SUB=""             # 테스트 계정 Cognito sub (푸시 대상)
# ─────────────────────────────────────────────────────────────

echo "=== 1) backend-api-main 단독 확인 — PHR0002 가 반영됐는지 ==="
echo "    (미정의 코드였다면 400, 반영됐으면 202)"
curl -sS -i -X POST "${BACKEND_URL}/internal/medcare/send-fcm" \
  -H "X-Internal-Api-Key: ${INTERNAL_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"sub\":\"${TEST_SUB}\",\"code\":\"PHR0002\"}"
echo

echo "=== 2) 정상 연동 — 세션 발급 ==="
echo "    userAuthType: 1=네이버 2=카카오 80=PASS. 응답의 sessionId 를 다음 단계에 쓴다."
SESSION_RESP=$(curl -sS -X POST "${MEDCARE_URL}/medcare/v1/phr/request" \
  -H "IdToken: ${ID_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"userAuthType":"80","userName":"홍길동","userBirthDate":"1990-01-01"}')
echo "$SESSION_RESP"
SESSION_ID=$(echo "$SESSION_RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["data"]["sessionId"])')
echo "sessionId=${SESSION_ID}"
echo

echo "=== 3) 수집 요청 — 202 즉시 응답, data 없어야 함 ==="
curl -sS -i -X POST "${MEDCARE_URL}/medcare/v1/phr/response" \
  -H "IdToken: ${ID_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"sessionId\":\"${SESSION_ID}\"}"
echo
echo "    ↑ HTTP/... 202 확인. body 는 { result:202, data 없음 } 이어야 한다."
echo

echo "=== 4) 진행 상태 확인 — scrapeStatus: IN_PROGRESS ==="
curl -sS "${MEDCARE_URL}/medcare/v1/phr" -H "IdToken: ${ID_TOKEN}"
echo
echo

echo "=== 5) 30초 대기 후 재확인 — scrapeStatus 사라지고 phrReceiveDateTime 채워짐 ==="
sleep 30
curl -sS "${MEDCARE_URL}/medcare/v1/phr" -H "IdToken: ${ID_TOKEN}"
echo
echo "    ↑ 단말에 PHR0001 푸시도 함께 도달했는지 확인"
echo

echo "=== 6) 저장된 데이터 조회 ==="
curl -sS "${MEDCARE_URL}/medcare/v1/phr/data" -H "IdToken: ${ID_TOKEN}"
echo
echo

echo "=== 7) 중복 요청 방어 — 이미 소비된 세션 재호출, 404 여야 함 ==="
curl -sS -i -X POST "${MEDCARE_URL}/medcare/v1/phr/response" \
  -H "IdToken: ${ID_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"sessionId\":\"${SESSION_ID}\"}"
echo
echo "    ↑ HTTP/... 404, errorCode: ENTITY_NOT_FOUND 여야 한다."
echo

echo "=== 8) 남의 세션으로 시도 — 404 (다른 계정의 IdToken 필요) ==="
echo '    OTHER_ID_TOKEN="..." 을 채우고 아래 주석 해제'
: '
curl -sS -i -X POST "${MEDCARE_URL}/medcare/v1/phr/response" \
  -H "IdToken: ${OTHER_ID_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"sessionId\":\"${SESSION_ID}\"}"
'
```

**실패 분리(09-04분)까지 검증하려면 위에 더해** 다음 두 가지를 확인한다.

```bash
# 9) PHR0003 이 backend 에 반영됐는지 (미정의면 400, 반영됐으면 202)
curl -sS -i -X POST "${BACKEND_URL}/internal/medcare/send-fcm" \
  -H "X-Internal-Api-Key: ${INTERNAL_API_KEY}" -H "Content-Type: application/json" \
  -d "{\"sub\":\"${TEST_SUB}\",\"code\":\"PHR0003\"}"

# 10) /phr/request 400 분리 — 토큰 계정의 전화번호와 맞지 않는 이름·생년월일로 호출
#     기대: HTTP 400, errorCode: PHR_AUTH_REQUEST_FAILED (502 PHR_API_ERROR 가 아니어야 한다)
curl -sS -i -X POST "${MEDCARE_URL}/medcare/v1/phr/request" \
  -H "IdToken: ${ID_TOKEN}" -H "Content-Type: application/json" \
  -d '{"userAuthType":"80","userName":"없는사람","userBirthDate":"1900-01-01"}'
```

`2026-08-31 dev 검증에서는 1~8단계가 전부 통과했다`(202 접수, `scrapeStatus` 전이, `PHR0001`·`PHR0002` 푸시 도달, 중복요청 방어).

---

## 부록 C. 수집 지연·유실 관측 쿼리 팩

실행 위치: CloudWatch Logs Insights (`ap-northeast-2`). 로그 그룹은 `/ecs/skix-medcare-{dev|stg|prd}`.

로그 패턴은 `%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n` 이다.

### C-1. 전환 이후 — 이 쿼리들을 쓴다

`/phr/response`가 202로 즉시 끝나므로 **`<<<` 응답 라인은 모든 요청에 즉시 찍힌다.** 더 이상 수집 시간을 말해주지 않는다. 전환 후에는 고정 토큰으로 본다.

**Q10. 유실 관측 (정본)**

```
fields @timestamp
| filter @message like /PHR_SCRAPE_(STARTED|SUCCEEDED|FAILED|RECLAIMED)/
| parse @message "PHR_SCRAPE_STARTED phrId=* sub=*"   as s1, s2
| parse @message "PHR_SCRAPE_SUCCEEDED phrId=* sub=*" as o1, o2
| parse @message "PHR_SCRAPE_FAILED phrId=* sub=*"    as f1, f2
| parse @message "PHR_SCRAPE_RECLAIMED phrId=* sub=*" as r1, r2
| stats count(s1) as 시작, count(o1) as 성공, count(f1) as 실패, count(r1) as 유실회수,
        count(s1) - count(o1) - count(f1) as 미종결
        by bin(1d)
| sort @timestamp asc
```

- `유실회수 > 0` → 배포·재시작이 진행 중 수집을 죽이고 있다. `BackgroundJobs.DRAIN_TIMEOUT`(20초) 상향을 검토할 근거다.
- `미종결`은 `유실회수`와 대체로 같아야 한다. 크게 벌어지면 스윕이 못 잡는 경로가 있다는 뜻이다(임계 미달이거나 스윕이 안 도는 것 — `PHR 수집 유실 회수 실패` 로그 확인).
- `PHR_SCRAPE_PIPELINE_ERROR`는 **집계에 쓰지 않는다.** 전이 승패와 무관하게 남는다.

**Q11. 수집 소요시간 (전환 후)**

`phrId`는 인증행 PK라 재사용되지 않으므로 sub과 달리 정확히 짝지어진다.

```
fields @timestamp
| filter @message like /PHR_SCRAPE_(STARTED|SUCCEEDED)/
| parse @message "PHR_SCRAPE_STARTED phrId=* sub=*"   as startedId, s2
| parse @message "PHR_SCRAPE_SUCCEEDED phrId=* sub=*" as doneId, o2
| stats earliest(@timestamp) as 시작, latest(@timestamp) as 종료 by coalesce(startedId, doneId) as phrId
| filter 시작 != 종료
| fields (종료 - 시작) / 1000 as 소요초
| stats count(*) as 건수, pct(소요초, 50) as p50, pct(소요초, 95) as p95, max(소요초) as 최대
```

**Q12. 푸시 발송 확인**

```
fields @timestamp, @message
| filter @message like /sendPhrReceivedPush\(\)|PHR .* 푸시 실패|PHR .* 푸시 기동 실패/
| sort @timestamp desc
| limit 100
```

발송 시도 자체가 0건이면 `PHR_PUSH_ENABLED`가 안 먹은 것이다(기본값 `false`).

**Q13. 스크래퍼 실패 원인 타입**

```
fields @timestamp
| filter @message like "PHR API call failed"
| parse @message "op=*, traceId=*, causeType=*" as op, traceId, causeType
| filter op = "AFTER_AUTH"
| stats count(*) as 건수 by causeType
| sort 건수 desc
```

`ReadTimeoutException`이 잡히면 `phr.api.timeout`(120초) 상한에 걸린 것이다. `InvalidPayload_*`는 스크래퍼 응답 구조 문제라 별개다. `traceId`는 스크래퍼측 로그와 대조할 열쇠다.

### C-2. 전환 이전 쿼리 (참고 — 동기 시절 판단 근거)

아래는 비동기 전환의 근거를 만들 때 쓴 쿼리다. **전환 후에는 응답시간을 말해주지 않지만**, 만약 어떤 환경이 아직 구버전이라면 그대로 쓸 수 있다.

```
# Q1. 응답시간 분포
fields @timestamp
| filter @message like "<<< /medcare/v1/phr/response"
| parse @message "<<< /medcare/v1/phr/response | Status: * | *ms" as status, elapsedMs
| stats count(*) as 건수, pct(elapsedMs,50) as p50, pct(elapsedMs,90) as p90,
        pct(elapsedMs,95) as p95, pct(elapsedMs,99) as p99, max(elapsedMs) as 최대

# Q2. 5초 구간 히스토그램 — 타임아웃 절벽이 실재하는가
fields @timestamp
| filter @message like "<<< /medcare/v1/phr/response"
| parse @message "<<< /medcare/v1/phr/response | Status: * | *ms" as status, elapsedMs
| fields floor(elapsedMs / 5000) * 5 as 구간시작초
| stats count(*) as 건수 by 구간시작초
| sort 구간시작초 asc

# Q4. 중도 절단 — 시작했으나 끝난 기록이 없는 요청
fields @timestamp
| filter @message like "/medcare/v1/phr/response"
| parse @message ">>> * /medcare/v1/phr/response" as 요청메서드
| parse @message "<<< /medcare/v1/phr/response | Status: * | *ms" as status, elapsedMs
| stats count(요청메서드) as 요청, count(status) as 응답완료,
        count(요청메서드) - count(status) as 중도절단 by bin(1d)
| sort @timestamp asc
```

> **`<<<`는 body를 쓸 때만 찍힌다**(`writeWith` 훅). 앱이 먼저 커넥션을 끊은 요청에는 남지 않는다. 그래서 `>>>` 대비 `<<<`의 결손이 곧 중도절단 건수다(Q4).

### C-3. 2026-08-18~08-31 검증계 실측 원자료

| 구간 | 건수 | 비고 |
|---|---|---|
| 세션 발급 | 246 | |
| `/phr/response` 시도(`>>>`) | 227 | 19건은 간편인증 화면에서 이탈 |
| 응답 완료(`<<<`) | 211 | **중도절단 16건 = 7.0%** |
| 성공(200) | 121 | 평균 21.2초, max 58.0초 |
| 실패(502) | 87 | 평균 0.27초, max 4.7초 |
| 404 / 401 | 2 / 1 | 11.5ms / 5ms |

**해석에서 제외해야 하는 부분** — 502 87건 중 84건(upstream 401)은 스크래퍼 접근키 배포 문제였다. 스크래핑 실패도 사용자 실패도 아니다. 같은 원인으로 `/phr/request`도 383건 중 137건이 실패했다(두 호출이 같은 키를 쓴다). 따라서 **"성공률 57%"와 "실패는 4.7초 안에 난다"는 근거로 쓸 수 없다.** 유효한 결론은 §1-2의 성공 121건 분포뿐이다.

중도절단 16건은 401로 설명되지 않는다(401은 502 body를 내려주므로 `<<<`가 남는다). 커넥션이 죽은 별개 건이며 원인 미상이다. 08-21 하루에 9건이 몰렸는데, 그날 stg 배포·태스크 재시작이 있었다면 "배포가 진행 중인 스크래핑을 죽인다"는 가설이 동기 방식에서도 이미 성립했다는 증거가 된다(미확인).

### C-4. 남는 한계

- **스크래퍼 순수 소요시간은 분리 측정되지 않는다.** KMS 1회 + DB 트랜잭션이 1초 안쪽이라 총 소요시간으로 대신한다.
- **중도절단의 사유는 구분되지 않는다.** 앱 백그라운드 전환인지, 네트워크 끊김인지, 태스크 재시작인지, QA가 화면을 던져두고 이탈한 것인지는 서버 로그로 알 수 없다.
- **모집단이 QA·개발자 트래픽이다.** 운영계 미출시라 대안이 없다. 소요시간 분포는 공단 스크래퍼의 실응답이라 대표성이 있지만 **이탈률·재시도 빈도·실패 유형 분포는 대표성이 없다.**
- **ALB 단에서 잘린 요청은 아예 안 보인다.** ALB 액세스 로그가 켜져 있다면 `target_processing_time`과 `elb_status_code`(460/504)로 교차 확인할 수 있다. 켜져 있는지 미확인.
- **한 가지 주의 — 비동기로 바꾼다고 스크래핑이 빨라지지 않는다.** 30초 걸리는 건은 그대로 30초다. 진짜 이득은 *앱이 더 이상 제약조건이 아니게 되어* `phr.api.timeout`을 120초로 올릴 수 있게 된 것이다.
