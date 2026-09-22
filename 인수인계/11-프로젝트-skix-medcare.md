# [프로젝트] skix-medcare

| 항목 | 내용 |
|---|---|
| **프로젝트 한 줄 정의** | My Healthcare의 AI 건강 상담, 대화·폴더, PHR 수집, 사용자 데이터 파기를 담당하는 반응형 백엔드 |
| **저장소** | `skix-medcare` |
| **기술 스택** | Java 21, Spring Boot 3.4, WebFlux, R2DBC MySQL, AWS KMS, Cognito JWT, Reactor |
| **배포 형태** | ECS Fargate, Docker 이미지 수동 반입·ECR push·ECS 롤링 배포 |
| **작성자 담당 범위** | 구조 리팩토링, 대화 내재화, 소유자 키 통합, 개인정보·PHR 보안, 비동기 PHR, 대화 제목·Trend·pending turn |
| **기준일** | 2026-09-22 (원격 추적 브랜치를 당일 fetch한 뒤 실측) |
| **인수자가 첫날 할 일** | 세 환경의 DDL·실행 이미지와 자동 파기 플래그를 확인하고, PHR 5년 제한을 개발계 실호출로 검증한다 |

---

## 0. 세 줄 요약

1. 이 서비스는 앱의 AI 건강 상담을 외부 AI 엔진과 연결하고 대화·폴더·평가·신고·PHR 데이터를 자사 DB에 보관한다. 건강정보를 다루므로 소유자 키와 파기·암호화가 기능만큼 중요하다.
2. 최근 6개월에 구조·보안 전면 리팩토링, 대화 내재화, 복수 로그인 소유자 통합, PHR 비동기화·5년 제한·실패 분류, 제목·Trend·pending turn을 반영했다. `dev`와 `stg`는 같고 `main`은 37커밋 뒤다.
3. 운영 전에는 PHR 5년 제한 실검증, identity 플래그 순서, 운영 PHR 테이블 0건, 시크릿·DDL·탈퇴 파기 연계를 확인해야 한다.

### 브랜치 상태

| 측정 | 값 |
|---|---:|
| `origin/main..origin/stg` | **37커밋** |
| `origin/stg..origin/dev` | **0커밋** |
| `origin/main` 최신 | `9cb2d05`, 2026-08-24 |

---

## 1. 이 프로젝트가 하는 일

### 1-1. 목적과 사용자

- 앱 사용자의 AI 건강 상담을 SSE 스트림으로 제공한다.
- 대화·검색 인덱스·폴더·평가·신고를 저장하고 복구·대사한다.
- 건강보험 진료·투약 이력(PHR)의 인증, 비동기 수집, 암호화 저장, 조회·삭제를 담당한다.
- 관제·운영자에게 감사 로그와 파기·대사 도구를 제공한다.
- 외부 AI 엔진은 답변 생성과 제목 생성을 맡지만, 사용자 소유권과 자사 DB 원장은 이 서비스가 통제한다.

플랫폼 회원가입·Cognito 계정 원장·FCM 발송은 `backend-api-main`의 내부 API를 사용한다. PHR 스크래퍼도 별도 서비스다.

### 1-2. 용어

| 용어 | 뜻 |
|---|---|
| **PHR** | Personal Health Record. 건강보험 진료·투약·검진 이력이다. |
| **canonical owner key** | 한 사람이 여러 로그인 sub을 가져도 데이터 소유자로 쓰는 대표 sub다. |
| **alias-read / canonical-write** | 형제 sub의 데이터를 함께 읽는 단계 / 신규 데이터를 대표 키로 쓰는 단계다. |
| **케이론** | 대화 생성·조회와 제목 생성을 제공하는 외부 AI 엔진이다. |
| **kmeta** | 케이론 대화를 자사 DB에 내재화한 테이블 묶음이다. |
| **pending turn** | 앱이 스트림을 놓쳐도 진행 중 질문과 완료 답변을 복원하는 읽기 모델이다. |
| **drift** | 외부 AI 엔진과 자사 DB의 대화 수·검색 인덱스·폴더 연결이 어긋난 상태다. |

---

## 2. 전체 구성

```text
[iOS·Android]
   │ Cognito ID Token
   ▼
[skix-medcare WebFlux]
   ├─ platform 내부 API → 사용자 identity·FCM
   ├─ AI 엔진 API → 대화 스트림·제목·대화 원본
   ├─ PHR 스크래퍼 → 인증·수집
   ├─ AWS KMS → PHR 원문 envelope 암호화
   └─ R2DBC MySQL → 대화·PHR·identity·감사·파기 원장
```

인증 필터는 Cognito ID Token의 `sub`만 신뢰한다. 데이터 접근 전 `OwnerContext`가 raw sub, canonical key, 접근 가능한 sibling 범위를 만든다. identity 권위 조회가 실패하면 raw sub 범위로 축소하지만, `CONFLICT`는 fail-closed다.

---

## 3. 코드 구조

| 경로 | 책임 |
|---|---|
| `src/main/java/com/skix/medcare/adapter/in/web` | Chat·Conversation·Folder·PHR·운영 REST 진입점 |
| `.../application/service/chat` | 스트림, 변환, pending turn, 제목·첨부 조립 |
| `.../application/service/identity` | canonical owner 해석, backfill, 충돌 처리 |
| `.../application/port/out` | DB·AI 엔진·PHR·플랫폼·암호화 출력 포트 |
| `.../adapter/out/cheiron` | AI 엔진 호출과 민감 응답 정제 |
| `.../adapter/out/phr` | PHR 스크래퍼 호출과 실패 분류 |
| `.../adapter/out/crypto` | KMS envelope 암복호화 |
| `.../adapter/out/persistence` | R2DBC 저장소 구현 |
| `.../security` | Cognito 인증과 `AuthenticatedUser` |
| `docs/operations.md` | drift·convert·purge 운영 도구의 목적과 실행 규칙 |

리팩토링 이후 의존 방향은 adapter → application → domain이다. 외부 응답 모델을 도메인에 그대로 흘리지 않고 포트에서 변환한다. PHR은 fail-open이 아니라 저장·암호화·소유자 키가 모두 맞을 때만 성공으로 확정한다.

---

## 4. 데이터

| 테이블·설정 | 용도·주의 |
|---|---|
| `kmeta_streams`, `kmeta_chats`, `kmeta_conversation_search` | 대화 root·turn·검색 인덱스. `cheiron_sub`는 외부 파티션 출처다. |
| `customer_folders`, 폴더-대화 연결 | 사용자 폴더. 재키잉 시 최신 연결의 행 정합성을 지킨다. |
| `medcare_user_identity` | raw sub → canonical owner key 레지스트리와 `NORMAL`/`CONFLICT` 상태 |
| `rekey_journal` | 재키잉 전 값을 기록해 정밀 롤백한다. |
| `customer_if_phr`, `customer_if_phr_data` | PHR 인증 상태와 암호화된 수집 데이터 |
| 감사·신고·평가 테이블 | 운영자 접근과 사용자 피드백 기록 |

### 주요 설정

| 키 | 의미 |
|---|---|
| `IDENTITY_ALIAS_READ_ENABLED` | 형제 sub 조회 범위 확장 |
| `IDENTITY_CANONICAL_WRITE_ENABLED` | 신규 쓰기를 대표 키로 통일. alias-read보다 먼저 켜면 안 된다 |
| `PHR_PUSH_ENABLED` | 비동기 PHR 결과 FCM 전송 |
| `PHR_ACCESS_KEY`, `PHR_ACCESS_SECRET` | 스크래퍼 자격증명. SSM 실값이 없으면 기동 실패 가능 |
| `CHAT_TITLE_GENERATION_ENABLED` | AI 제목 요약 |
| `CHAT_PHR_ATTACHMENT_ENABLED` | 저장된 PHR을 AI 입력에 첨부 |
| `RETENTION_*` | 자동 파기 보유기간. 정책 확정 전 활성화 금지 |

DB 세션은 UTC로 강제하고 업무 경계일만 KST로 계산한다. 연결은 `sslMode=REQUIRED`이며 서버 인증서 검증은 후속 과제다.

---

## 5. 빌드·테스트·로컬 실행·배포

```bash
./gradlew clean test
./gradlew clean bootJar
./deploy/build-export.sh
```

로컬 `.env`는 필수다. `spring.config.import`에서 `optional:`을 제거해 파일 누락 시 기동 실패하도록 했다. 실값 대신 `.env.example`의 자리표시자를 사용한다.

배포는 태스크 정의 등록 → 이미지 push → ECS 서비스 갱신 순서다. `cloudshell-push.sh`는 저장소 JSON을 자동 등록하지 않는다. identity 플래그는 P3 alias-read를 전 태스크에 배포한 뒤 P4 canonical-write를 별도 리비전으로 올린다. 재키잉 뒤에는 alias-read를 끄지 않는다.

공개 호스트는 `medcare-dev.namuhx.com`, `medcare-stg.namuhx.com`, `medcare.namuhx.com`으로 사용되지만 실제 API Gateway·ALB 경로는 VDI에서 확인해야 한다.

---

## 6. 최근 6개월 작업 이력

| 시기 | 무엇을 | 왜 | 결과 | 핵심 커밋 |
|---|---|---|---|---|
| 3~5월 | 초기 ECS·DB 연결, 폴더 수정, 토큰 오류, 사용자 조회, UID를 Cognito sub으로 전환했다. | 서비스 초기 기능과 사용자 식별을 안정화해야 했다. | 대화·폴더·사용자 API 기반이 형성됐다. | `49ed3cc`, `f0a6e75`, `5f5882e` |
| 6월 | 문자셋·인덱스, 내부 관리자 인증, PHR 초안을 보강했다. | 한글 폴더명과 운영 호출 인증, 건강데이터 연동 기반이 필요했다. | utf8mb4와 내부 API 분리가 들어갔다. | `473617f`, `e6c9051`, `d6b48b9` |
| 7월~8월 초 | 패키지·보안·정합성 전면 리팩토링과 대화 내재화를 완료했다. | 외부 AI 엔진만 믿으면 조용한 데이터 유실을 복구할 수 없고 시크릿·로그·인증 구멍이 있었다. | 3환경 배포 완료, drift·convert·purge 운영 경로와 대규모 테스트가 생겼다. | 리팩토링 머지 `520d660`, 운영 정리 `7327a39` |
| 8월 | PHR 암호화, 원문 사본 제거, 전화번호 컬럼 제거, 사용자·보유기간 파기와 감사 로그를 반영했다. | 건강정보 최소 보관과 탈퇴 파기 의무를 충족하기 위해서다. | 코드는 `main`까지 반영됐고 실행 기록이 있으나 일부 DDL·이미지는 미확인이다. | 보안 티켓 묶음, `d6705bc` |
| 8월 말 | 복수 로그인 소유자 키를 canonical로 통합했다. | 같은 사람이 로그인 수단을 바꾸면 대화가 사라져 보였다. | dev·stg P6 완주와 drift 0을 확인했고 운영은 미착수다. | `a82b603`, `e6bf502`, `d600dd5` |
| 8~9월 | pending turn, Trend·PHR 첨부, 제목 생성, 비동기 PHR 202+FCM, 5년 범위, 인증 실패 분리를 구현했다. | 장시간 수집·스트림 이탈을 복구하고 최소수집과 사용자 안내를 강화했다. | `dev`·`stg` 반영. pending·Trend·PHR 첨부는 `main`, 제목·비동기 후속은 운영 미반영이다. | `4971f08`, `1058fc8`, `a6fed67`, `75d692e` |
| 8월 | DB 세션 UTC와 TLS 강제를 적용했다. | 같은 DB 안의 시각 기준과 평문 폴백을 없애기 위해서다. | 3환경 반영 완료로 기록됐고 기동 로그 재확인이 필요하다. | UTC·TLS 변경 묶음 |

---

## 7. 알려진 이슈와 기술 부채

| 우선순위 | 이슈 | 근거 |
|---|---|---|
| P0 | PHR 5년 제한이 실호출 검증 없이 운영 브랜치까지 들어갔다. | 구버전 스크래퍼가 `options`를 무시하면 성공 응답 속에서 5년 초과 PHI가 저장된다. |
| P0 | 운영 PHR은 identity P3→P4 전에 열면 raw sub로 영구 저장된다. | PHR 테이블은 재키잉 대상이 아니다. |
| P1 | 보유기간 수치가 확정되지 않아 자동 파기가 꺼져 있다. | 기획·법무 확정 없이 임의 TTL을 적용할 수 없다. |
| P1 | PHR 내부 구간이 HTTP이고 access key·secret이 헤더에 실린다. | NLB TLS 리스너·DNS·인증서가 선행돼야 한다. |
| P1 | PHR·대화 원문 중 일부는 평문이며 정책이 미완이다. | 검색 요구와 암호화가 충돌하므로 보유기간·삭제 연쇄와 함께 결정해야 한다. |
| P1 | 접속기록 S3 Object Lock·월 1회 점검·RDS 감사 로깅이 미완이다. | 애플리케이션 로그만으로 DB 직접 접근을 잡을 수 없다. |
| P2 | `README.md` 구조 설명과 API 예시 casing이 현재 코드와 다르다. | 신규 인수자가 첫 진입점에서 잘못된 구조·계약을 읽는다. |
| P2 | 메트릭은 있으나 수집 기반이 없어 운영 게이트로 쓸 수 없다. | CloudWatch 로그 지표나 별도 수집 구성이 필요하다. |

---

## 8. 남은 일

### 8-1. 즉시

1. 세 환경에서 PHR 신규 컬럼, identity·대화 DDL, 삭제된 원문 테이블의 실제 상태를 확인한다.
2. 실행 중 ECS 이미지가 8월 하드닝과 9월 PHR 실패 분리를 포함하는지 확인한다.
3. 5년보다 오래된 이력이 있는 개발 계정으로 검진과 외래·처방 각각의 최장 과거 날짜를 대사한다.
4. 개발계·검증계 drift를 실행해 `missingInKmeta`, `missingSearchIndex`, `checkFailedUsers`가 0인지 확인한다.
5. 보유기간 확정 요청과 접속기록 인프라 작업을 재개한다.

### 8-2. 운영 릴리스 게이트

- token PII 전환의 운영 배포와 `user_accounts` 백필을 먼저 완료한다.
- identity 플래그를 alias-read → canonical-write 순서로 올리고 충돌 0, `cheiron_sub` NULL 0을 확인한다.
- 운영 `customer_if_phr`·`customer_if_phr_data`가 0건인지 확인하고 PHR 시크릿 실값을 대조한다.
- `backend-api-main`을 먼저 배포해 FCM·identity·탈퇴 연계 API를 제공한다.
- PHR 5년 제한 실검증과 신규 행의 canonical key 저장을 확인한다.
- 제목 요약은 양 앱의 `entryType` 릴리스 뒤, PHR 첨부는 AI 엔진 보존·파기 7항목과 제품·보안 승인을 받은 뒤 켠다.

### 8-3. 후속

- PHR 회수 알람과 drain timeout을 실측 기반으로 조정한다.
- PHR 스크래퍼를 콜백형으로 바꾸는 방안을 검토한다.
- 부분 수집(`skipInvalidPage=true`)을 검출할 메타데이터를 스크래퍼와 합의한다.
- 대화·PHR 보존 정책, 내부 TLS, 키 로테이션을 하나의 개인정보 후속 트랙으로 관리한다.

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| 특정 로그인만 대화가 안 보임 | `medcare_user_identity`에서 raw sub·owner key·상태를 본다. `CONFLICT`면 자동 교정하지 않는다. |
| 대화 404 | `kmeta_streams.user_strid`와 `cheiron_sub`를 확인한다. `cheiron_sub` NULL이면 출처 백필 누락이다. |
| 외부 AI 엔진과 건수가 다름 | drift의 `missingInKmeta`와 `staleInKmeta`를 구분한다. 운영의 대량 stale은 삭제가 아니라 UID·테넌트 조사 대상이다. |
| PHR이 계속 진행 중 | `scrapeStatus`, 인증 세션 해시, 백그라운드 작업 로그와 `PHR_SCRAPE_RECLAIMED`를 확인한다. |
| PHR 실패 안내가 다름 | `PHR0002` 일반 실패와 `PHR0003` 인증 실패를 구분한다. 앱의 전경 FCM 처리도 확인한다. |
| 재키잉 뒤 일부 대화가 사라짐 | alias-read가 꺼졌는지 확인한다. canonical-write만 끄는 것은 가능하지만 둘을 함께 끄면 안 된다. |
| backfill timeout | 플랫폼 API를 직접 호출해 응답 시간을 본 뒤, 순차 재귀가 너비 우선 `Flux.expand`로 되돌아갔는지 확인한다. |

---

## 부록 A. 주요 API

| 영역 | API |
|---|---|
| 상담 | `GET/POST /medcare/v1/chat/stream` |
| 대화 | `/medcare/v1/conversations`, 상세·검색·수정·삭제·평가·신고 |
| 폴더 | `/medcare/v1/folders`, 폴더-대화 연결 |
| PHR | `POST /medcare/v1/phr/request`, `POST /response`, `GET /data`, `DELETE /delete` |
| 운영 | identity backfill, convertAll, drift, reconcile, retention purge, user purge |

## 부록 B. 핵심 커밋

`a82b603` identity 배관, `e6bf502` 소유자 키 전환, `4971f08` PHR 202+FCM, `1058fc8` 제목 생성, `a6fed67` 제목 확정, `75d692e` PHR 실패 분류를 우선 확인한다.
