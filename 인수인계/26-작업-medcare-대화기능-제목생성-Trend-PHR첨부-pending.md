# [작업] medcare 대화 기능 3건 — 제목 자동 요약 · 에이전트 입력(Trend·PHR) · 진행 중 질문 복원

| 항목 | 내용 |
|---|---|
| **상태** | 서버 코드 3건 모두 `dev`·`stg` 반영 완료. **pending turn·Trend·PHR 첨부는 `main`(운영)까지 반영**, **제목 자동 요약만 `main` 미반영** |
| **작업 기간** | 2026-08-06 ~ 2026-09-03 |
| **직접 수정한 저장소** | `skix-medcare` (단일) |
| **요청서를 전달한 대상** | iOS(`benjaminios`), Android(`benjaminandroid`) — `entryType` 필드 추가 요청 |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §2-2 플래그 표를 읽고, §8-1의 1번(Android `entryType` 브랜치 미머지 확인)과 2번(운영계 제목 요약 승격 여부 판단) |

---

## 0. 세 줄 요약

1. skix-medcare(=My Healthcare, AI 건강 상담)의 **대화(chat) 기능**에 세 가지를 넣었다 — ① 대화 제목을 첫 질문 문장 대신 **케이론이 만든 요약**으로 바꾸기, ② 에이전트 입력에 **바이탈 추이(Vital Signs Trend)** 추가와 **저장된 PHR(건강보험 진료·투약 이력) 첨부**, ③ 질문 도중 앱을 나갔다 돌아와도 **"생각 중"이 복원되고 답변을 되찾아오는** 읽기 모델.
2. **메모리·문서에 남은 "미push" 표기는 전부 낡았다.** 기준일 실측으로 세 기능 모두 `origin/dev`·`origin/stg`에 들어가 있고, ②·③은 `origin/main`까지 올라가 있다. `main`에 없는 것은 ①(제목 요약)뿐이다.
3. 남은 것은 **Android가 `entryType`을 `develop`에 머지하는 것**(feature 브랜치에만 있음), **운영계 제목 요약 플래그 ON**(양 플랫폼 릴리스 이후), **운영계 PHR 첨부 플래그 ON**(AI팀 7항목 회신 + 제품·보안 승인이 게이트)이다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **skix-medcare (medcare)** | "My Healthcare / 나만의 주치의" AI 건강 상담 백엔드. Spring WebFlux + R2DBC(MySQL), ECS 배포. 앱이 `POST /medcare/v1/chat/stream` 으로 질문하고 SSE로 답변을 받는다. |
| **케이론 (Cheiron)** | 외부 AI 대화·검색 엔진(`skix.phnyx.ai`). medcare가 HTTP로 호출한다. **대화 원본의 원천(source of truth)** 이며 medcare의 `kmeta_*` 테이블은 그 사본이다. |
| **SKIX_A1** | 케이론 쪽 에이전트 식별자. 앱이 `agentStrId`로 보낸다. |
| **내재화 (INTERNAL 라우팅)** | 대화 목록·상세 조회를 케이론에 매번 묻지 않고 로컬 `kmeta_*` 사본에서 읽는 방식. **세 환경 모두 INTERNAL** 이다. 이 전환이 §3-3 결함의 직접 원인이다. |
| **kmeta_* 테이블** | 케이론 대화를 로컬에 변환·적재한 읽기 모델. `kmeta_streams`(대화 root), `kmeta_chats`(턴), `kmeta_chat_inputs`(질문·에이전트 입력), `kmeta_conversation_search`(검색 인덱스), `kmeta_agent_llm_inputs`. |
| **변환 (convert)** | 케이론 상세 응답을 읽어 `kmeta_*`에 쓰는 동작. 스트림이 정상 종료되면 자동으로 돈다. |
| **strid** | 케이론이 부여하는 문자열 식별자. 대화는 UUID, 턴(chat)은 `{대화UUID}_{턴UUID}` 형태다. |
| **에이전트 입력 (`agentInputFieldToValue`)** | 질문과 함께 케이론에 넘기는 참고 데이터 묶음. 키가 필드 이름인 map 형태이며 값은 앱이 포맷한 문자열이다. |
| **PHR** | Personal Health Record. 여기서는 국민건강보험 자료(국가검진·외래/처방조제)를 스크래퍼로 수집해 medcare DB에 KMS 암호화 저장한 것을 뜻한다. |
| **entryType** | 앱이 대화를 시작한 화면을 알려주는 신규 요청 필드. `"PHR"` / `"VITAL_SIGN"` / 없음. |
| **pending turn (진행 중 턴)** | 질문은 받았지만 답변이 아직 없는 턴. `kmeta_chats`에 `strid="pending-"+UUID`, `status=IN_PROGRESS`, `response=NULL`로 먼저 기록된다. |
| **read-repair** | 상세 조회 중 pending을 발견하면 케이론 원천을 확인해 종료된 턴만 로컬에 적재하는 자기 치유 동작. |
| **다크 런치 (dark launch)** | 코드는 배포하되 기능 플래그를 꺼 둔 상태로 올리는 것. 이 문서의 두 플래그가 그 방식이다. |
| **OwnerContext / ownerScope** | 한 사람을 가리키는 Cognito sub 집합. 소셜 로그인 provider를 갈아타면 sub이 갈리므로, 조회·잠금은 등호가 아니라 이 집합 `IN` 으로 한다. |

### 1-2. 세 기능이 나온 경위

세 건은 독립적으로 착수됐지만 모두 "대화 화면에서 사용자가 겪는 문제"다.

| # | 문제 | 착수 시점 |
|---|---|---|
| ① 제목 | 대화 제목이 **첫 질문 문장 그대로**라 목록에서 대화를 구분할 수 없다. 케이론이 요약 API(`generate_title`)를 새로 열어줬다 | 2026-09-01 |
| ② 입력 | 앱이 보내는 `Vital Signs Trend`(기간별 바이탈 추이)를 **서버가 조용히 버리고 있었다**(record에 컴포넌트가 없어 Jackson이 드랍). 또 PHR을 저장해 두고도 상담에 쓰지 못했다 | 2026-08-12 |
| ③ pending | 내재화 이후 "질문 → 앱 이탈 → 재진입" 시 **답변이 완료돼도 화면이 무반응**. `kmeta_*`에 완료된 대화만 적재돼 진행 중 턴이 상세 응답에 아예 없었고, 두 앱이 그 대화를 '완료'로 보고 폴링을 시작하지 않았다 | 2026-08-21 |

곁가지로 같은 기간에 **답변 신고·평가 API**(위험 답변 신고 / 좋아요·싫어요)도 신설했다(§3-4).

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 브랜치 반영

`skix-medcare` 단일 저장소다. 실측은 `git merge-base --is-ancestor <hash> origin/<branch>` 로 했다.

| 기능 | 대표 커밋 | `dev` | `stg` | `main`(운영) |
|---|---|---|---|---|
| ① 대화 제목 자동 요약 | `1058fc8` · `a6fed67` · `3dfda7d` | 반영 | 반영 | **미반영** |
| ① dev 플래그 ON | `fffb819` | 반영 | 반영 | 미반영 |
| ① stg 플래그 ON | `16eb05b` | 반영 | 반영 | 미반영 |
| ② Vital Signs Trend + 내재화 입력 복원 | `cdbc10b` | 반영 | 반영 | **반영** |
| ② PHR 케이론 첨부(플래그 뒤) | `c960fbc` · `70b816a` | 반영 | 반영 | **반영** |
| ② dev·stg PHR 첨부 ON | `6a2ab43` | 반영 | 반영 | 반영 |
| ③ pending turn 복원 | `cf1b086` ~ `bf7bcfd` (7커밋) | 반영 | 반영 | **반영** |
| ④ 답변 신고·평가 API | `b0e1f20` · `b905390` · `c3b2288` | 반영 | 반영 | **반영** |

브랜치 위치(기준일): `origin/dev` = `75d692e`(09-04), `origin/stg` = `4ffc4e7`(09-04), `origin/main` = `9cb2d05`(08-24).
`dev ⊆ stg` 다(`stg..dev` 0건). `main..dev` 는 30건 — **운영 미승격분이 30커밋 쌓여 있다.**

기능 브랜치는 원격에 그대로 남아 있다: `origin/feature/chat-title-generation`(`3dfda7d`), `origin/feature/agent-input-trend-phr`(`70b816a`), `origin/feature/pending-turn-visibility`(`bf7bcfd`), `origin/feature/chat-report-and-rating`(`c3b2288`). 전부 dev에 머지된 상태라 **새로 작업할 때 이 브랜치들을 다시 쓰지 말고 dev에서 분기한다.**

### 2-2. 기능 플래그 (환경변수 · 태스크 정의 실측)

플래그는 `deploy/task-definition-{dev,stg,prd}.json` 에 환경변수로 적혀 있고, `src/main/resources/application-ecs.yml` 이 `${...:기본값}` 으로 읽는다.

| 플래그 (환경변수) | yml 키 | dev | stg | prd | 코드 기본값 |
|---|---|---|---|---|---|
| `CHAT_TITLE_GENERATION_ENABLED` | `chat.title-generation-enabled` | **true** (09-02) | **true** (09-03) | `false` | `false` |
| `CHAT_PHR_ATTACHMENT_ENABLED` | `chat.phr-attachment-enabled` | **true** (08-19) | **true** (08-19) | `false` | `false` |
| `CHAT_PENDING_RESYNC_COOLDOWN` | `chat.pending-resync-cooldown` | 미기재 → `30s` | 미기재 → `30s` | 미기재 → `30s` | `30s` |
| `CHAT_MAX_CONCURRENT_STREAMS` | `chat.max-concurrent-streams` | (기존) | (기존) | (기존) | `3` |

주의할 점 둘.

- **`origin/main`의 `task-definition-prd.json` 에는 `CHAT_TITLE_GENERATION_ENABLED` 키 자체가 없다.** 제목 요약 커밋이 아직 main에 없기 때문이다. 코드 기본값이 `false`라 운영에 영향은 없지만, main을 승격하면 그때 키가 `false`로 생긴다.
- **`CHAT_PENDING_RESYNC_COOLDOWN`은 어느 환경 태스크 정의에도 없다.** 조정하려면 태스크 정의 리비전에 키를 새로 추가한다(재빌드 불필요).

`docs/operations.md` 의 제목 요약 절에는 아직 **"기본 false, 세 환경 모두 false 로 커밋돼 있다"** 라고 적혀 있다. **이 문장은 낡았다**(dev·stg는 true). §8-3에 정정 항목으로 올려 뒀다.

### 2-3. 배포·검증 상태

| 항목 | 상태 |
|---|---|
| ③ pending turn | **dev·stg·prd 3환경 배포 완료 (2026-08-24)**. 배포 전 게이트 SQL도 수행했고 prd는 조치 불필요였다(§7-2) |
| ② Trend·PHR 첨부 | 코드는 3환경 배포 완료. PHR 첨부는 dev·stg만 플래그 ON |
| ① 제목 요약 | dev·stg 플래그 ON. **해당 리비전이 실제 ECS에 떠 있는지는 미확인** — 로컬 맥에서 AWS 콘솔·CLI 접근이 불가하다. 회사 VDI에서 ECS 태스크 정의의 이미지 태그와 환경변수를 확인해야 한다 |
| ① dev 검증 curl 2종 | **미확인**. 계획된 절차는 §부록 B에 있다 |
| DDL | **세 기능 모두 DDL 변경 없음.** `kmeta_chat_inputs.agent_input_field_to_value`·`kmeta_streams.last_input_kwargs` 두 JSON 컬럼이 스키마리스라 Trend 필드 추가에도 마이그레이션이 없다 |

### 2-4. 앱 (별도 담당)

`benjaminios`·`benjaminandroid` 저장소를 기준일에 직접 grep해서 확인한 사실이다.

| 항목 | iOS | Android |
|---|---|---|
| `entryType` 전송 | **`origin/develop` 반영 완료** (`e756532f`, 09-02). `AIChatEntryType` enum + `encodeIfPresent`, 세팅 3곳(PHR 검진 목록·바이탈 대시보드·바이탈 추이 상세) 모두 확인 | **`origin/develop` 미반영.** `origin/feature/my-healthcare-phr-integration` 에만 있다(`4ab93f66e`, 09-02). `ReqChatStream`에 `entryType: String? = null` 추가 확인. **develop 머지 안 됨** |
| `Vital Signs Trend` 전송 | **`origin/develop` 반영 완료.** 요청 DTO(`AIChatStreamRequestDTO`)와 응답 DTO(`ConversationListResponseDTO`) 양쪽에 키 존재 | **`origin/develop` 미반영.** `origin/feature/my-healthcare-phr-integration` 과 `origin/feature/MyHealthCare-바이탈사인-데이터-input`(`5c9fe7dfa`, 08-05)에만 있다 |
| pending turn 대응 | **앱 작업 불필요**(기존 코드가 그대로 동작, §3-3) | **앱 작업 불필요** |

**이 비대칭이 지금 가장 중요한 사실이다.** 제목 요약 플래그를 운영에서 켜려면 **양 플랫폼 모두** `entryType`을 실어야 하는데(플래그는 플랫폼별로 갈리지 않는다), Android는 아직 feature 브랜치에만 있다.

---

## 3. 무엇을 만들었나

### 3-1. 대화 제목 자동 요약

#### 무엇

일반 진입으로 시작한 **신규 대화**의 제목을, 답변이 끝난 뒤 케이론 요약으로 바꾼다.

```
지금:  "요즘 혈압이 130/85 정도 나오는데 이 정도면 관리가 필요한 상태인가요?"
변경:  "혈압 관리 상담"
```

#### 왜 앱의 `entryType`이 필요했나

모든 대화를 바꾸면 안 된다. **PHR·바이탈사인 진입은 고정 제목을 유지해야 한다.** 특히 바이탈사인은 iOS가 `[vital sign] … HH:mm` 형식을 전제로 화면 제목을 가공하므로, 서버가 요약으로 덮으면 그 로직이 깨진다.

그래서 "이 대화가 어느 화면에서 시작됐는가"를 서버가 알아야 하는데, **에이전트 입력으로는 판정이 불가능하다.** 앱 코드를 직접 확인한 결과가 근거다.

| 확인한 것 | 근거 |
|---|---|
| iOS는 `vitalSigns`가 옵셔널이 아니라, 측정값이 없어도 `"Vital Signs": ""` 를 **항상** 보낸다 | iOS `AIChatStreamRequestDTO.swift` |
| Android도 "서버 계약상 `Vital Signs` 키는 생략하지 않고 빈 문자열로 포함"이라는 주석과 함께 같은 동작 | Android `VitalAgentInputFactory.kt` |
| 일반 질문에도 최근 바이탈이 **자동 첨부**된다 | Android `MyHealthcareViewModel.loadAndSetRecentVitalData`, iOS도 PHR 연동 사용자면 일반 채팅에서 바이탈 자동 조회 |

즉 **에이전트 입력은 "참고 데이터가 있는가"이지 "어느 화면에서 들어왔는가"가 아니다.** 질문 문구 매칭도 검토했으나 다국어(ko/en)라 서버가 문구를 언어별로 알아야 하고, 앱에서 프롬프트를 한 글자만 다듬어도 조용히 어긋나 접었다.

#### 흐름

```
[앱] POST /medcare/v1/chat/stream  { query, conversationStrid: null, entryType?: "PHR"|"VITAL_SIGN" }
        │
        ├─ ChatService.titleGenerationDue() 판정 — 세 조건 AND
        │     ① chat.title-generation-enabled == true
        │     ② conversationStrid == null        (신규 대화만)
        │     ③ entryType 이 null/blank          (일반 진입만)
        │
        ├─ SSE 스트리밍 (답변 전달) ────────────────► 앱
        │
        └─ 스트림 정상 종료 후 completeStream()
              ① 사용량 로그 저장
              ② convertCompletedConversation()  ← 케이론 상세 재조회 + kmeta 적재
              │     성공해야만 strid 를 방출한다 (실패·대상없음이면 빈 Mono)
              └─ .flatMap(strid -> titleGenerationDue ? generateTitle(strid) : empty)
                    │
                    ├─ POST 케이론 /api/data_management/conversations/{strid}/generate_title
                    │     (헤더 X-API-UID, 20초 timeout, 연결 실패만 1회 재시도)
                    ├─ 빈 제목이면 아무것도 안 함
                    ├─ PATCH 케이론 대화 상세 (제목 = 요약)     ← 원본 갱신
                    └─ 로컬 3테이블 한 트랜잭션 갱신
                          kmeta_streams.title
                          kmeta_conversation_search.title
                          customer_folder_chat.chat_title  (code point 기준 절단)
```

세 가지 순서가 전부 의도적이다.

- **제목 확정은 변환 성공 뒤여야 한다.** 변환은 케이론 상세를 다시 읽어 그 제목을 kmeta에 쓰므로, 먼저 확정하면 방금 만든 요약이 그 자리에서 첫 질문으로 되돌아간다. `convertCompletedConversation`이 **성공 시에만** strid를 방출하고 `flatMap`이 그 게이트다. `.then()`으로 이으면 변환 실패에도 실행된다(변환은 에러를 삼키고 `Mono.empty()`를 낸다).
- **케이론 PATCH를 거쳐야 한다.** 케이론이 원본이라 로컬만 고치면 다음 재변환·read-repair가 되돌린다.
- **비멱등 POST라 읽기 타임아웃을 재시도하지 않는다.** 공용 `idempotentRead()` 정책을 쓰면 같은 대화에 요약을 두 번 돌려 LLM 비용과 제목 흔들림이 생긴다. 연결이 닿지 못한 경우만 1회 재시도한다(`RetryPolicies.connectFailureOnce()`).

제목 생성 실패는 **채팅을 실패로 만들지 않는다.** 이미 답변은 전달됐고, 실패해도 첫 질문으로 만든 제목이 남는다. 그래서 ERROR가 아니라 WARN이고, 지표는 `medcare.chat.title{result=failed}` 하나다.

#### 플래그

`CHAT_TITLE_GENERATION_ENABLED` → `chat.title-generation-enabled`. dev·stg ON, prd OFF(§2-2).

**환경별로 켜는 조건이 다르다.**

| 환경 | 켜는 시점 | 현재 |
|---|---|---|
| dev | 서버 배포 직후. 앱을 기다리지 않는다 — 검증 curl이 신·구앱 요청을 모두 모사한다 | ON (09-02) |
| stg | iOS·Android **둘 다** `entryType`을 실은 QA 빌드가 올라간 뒤 | **ON (09-03) — 조건 미충족 상태로 켰다** |
| prd | 두 플랫폼 **릴리스**가 끝난 뒤 | OFF |

stg를 조건보다 먼저 켠 것은 의도된 결정이다(`application-ecs.yml` 주석에 기록). 그 시점 두 앱 어디에도 `entryType`이 없었으므로 **검증계에서는 PHR·바이탈 진입도 요약 제목을 받는다.** 앱 QA가 이를 결함으로 보고할 수 있음을 감수한 값이다. 인수자는 QA 문의가 오면 이것이 서버 결함이 아니라 플래그 상태임을 설명하면 된다.

되돌리기는 플래그를 끄면 된다. 이미 요약으로 바뀐 제목은 되돌아가지 않지만(케이론에도 그 제목이 저장돼 있다) 사용자가 직접 제목을 고칠 수 있으므로 데이터 복구 절차는 없다.

#### 검증

지표를 배포 게이트로 쓸 수 없다. `medcare.chat.title`은 Micrometer 카운터인데 **Prometheus/ADOT 수집 기반이 없다**(`application.yml`에 수집 설정 없음). 검증은 DB로 한다 — 절차는 §부록 B-1.

#### 남은 것

Android `entryType`의 `develop` 머지(§8-1), 운영계 플래그 ON(§8-2), 착수 전 못 한 사전 확인 2건과 케이론 문의 4건(§8-3).

---

### 3-2. 에이전트 입력 — Vital Signs Trend 추가 · PHR 첨부

브랜치 `feature/agent-input-trend-phr`(dev `520d660`에서 분기). 커밋 2건 + 회귀 테스트 1건.

#### 무엇 — 세 가지가 한 브랜치에 있다

| # | 내용 | 커밋 |
|---|---|---|
| A | 앱이 보내는 `Vital Signs Trend`를 서버가 **받아서** 케이론에 전달 | `cdbc10b` |
| B | 내재화 경로 상세 응답에서 `agent_input_field_to_value` **복원** | `cdbc10b` |
| C | 저장된 PHR을 케이론 요청에 **첨부**(플래그 뒤), 응답으로는 **되받지 않음** | `c960fbc` |

#### 왜

**A.** 앱은 이미 기간별 추이를 보내고 있었는데 서버 `AgentInputFieldToValue` record에 컴포넌트가 없어 **Jackson이 조용히 버렸다.** `fail-on-unknown-properties: false`라 로그에도 지표에도 아무것도 남지 않는 드랍이다.

**B.** `ConversationMapper.toChat`이 `query`만 복원하고 있었다. `agent_input_field_to_value`는 **적재는 하면서 읽지 않아**, 케이론 라우팅과 내재화 라우팅의 상세 응답이 서로 달랐다. 세 환경 모두 INTERNAL이라 이 갭이 곧 앱의 컨텍스트 복원 실패였다.

**C.** PHR 수집·저장은 이미 있었지만 케이론에 보내는 경로가 없어, 사용자가 건강보험 자료를 연동해도 상담이 그 기록을 몰랐다.

#### 케이론 실측으로 확정한 것 (2026-08-11, dev `https://dev-skix.phnyx.ai` 직접 호출)

- SKIX_A1이 `Vital Signs Trend`·`PHR` **둘 다 수용**하고, `last_input_kwargs`·`chats[].input_state` 양쪽에 보낸 값을 그대로 에코한다.
- `agent_llm_inputs`에 LLM 입력으로 실리고 follow-up 질문이 값을 직접 인용한다 — **에이전트가 실제로 소비한다.** 따라서 AI팀과 필드 정의를 협의할 필요가 없었다.
- 부수 관찰: 백엔드가 보내는 `agent_strId`는 **케이론이 무시하는 죽은 파라미터**다(케이론이 자체 `agent_strid: SKIX_A1`을 쓴다).

#### 흐름 — 보내는 것과 되받아 복제하는 것의 분리

```
[앱]  agentInputFieldToValue: { "Vital Signs": "...", "Air Quality Score": "...", "Vital Signs Trend": "[...]" }
        │   ↑ 인바운드 DTO 타입 = AgentInputFieldToValue  (PHR 컴포넌트 **없음**)
        │     → 앱이 "PHR" 을 보내도 바인딩 자체가 안 된다
        ▼
[ChatService.resolvePhrForChat(ctx)]
        플래그 OFF  → Optional.empty  (DB 조회도 KMS 복호화도 안 함, medcare.chat.phr{result=feature_disabled})
        플래그 ON   → 동의(customer_if_phr.usage_yn='Y') AND 데이터 존재 일 때만 복호화 + 스크래퍼 envelope 제거
                     조회·복호화·파싱 실패는 **첨부만 생략**하고 채팅은 계속 (fail-open)
        ▼
[케이론 요청 타입 = CheironAgentInputFieldToValue  (PHR 컴포넌트 **있음**)]
        POST 케이론 → agent_input_field_to_value.PHR 로 전달
        ▼
[케이론 응답]  입력 필드를 그대로 에코해서 돌려준다
        ▼
[CheironResponseSanitizer]  ← 어댑터 응답 경계, **역직렬화 전 JsonNode 단계**에서 PHR 키를 삭제
        (agent_llm_inputs 는 항목째 삭제. 지울 게 없으면 deepCopy 자체를 건너뛴다)
        ▼
[결과]  앱 응답과 로컬 kmeta 3위치에 PHR 원문이 **저장되지 않는다**
          kmeta_streams.last_input_kwargs
          kmeta_chat_inputs.agent_input_field_to_value
          kmeta_agent_llm_inputs.value
```

정제를 **typed가 아니라 JsonNode 단계**에서 하는 이유: typed 정제라면 도메인 모델이 아는 위치만 지워진다. 실측 응답의 `agent_llm_inputs`는 `input_state` 안에 있고 현재 모델은 그 위치를 모른다. 트리에서 지우면 현재 포맷과 향후 스펙 드리프트가 같은 곳에서 막힌다.

fail-open 범위는 **PHR 조회까지만**이다. 케이론 호출 실패는 이 경계 바깥이라 평소대로 오류로 전파된다(테스트로 고정).

**임의의 크기 상한은 두지 않았다.** 초안에 있던 `@Size(30_000)`·64KB 절단은 폐기했다 — 근거였던 `customer_if_log_text` 원문 저장이 보안 작업에서 이미 제거됐다. 실제 상한은 WebFlux 인바운드 코덱(10MB), 케이론 요청 한도, 모델 컨텍스트 세 곳에 있고, 값은 dev 실측과 케이론 계약으로 확인한다.

#### 플래그

`CHAT_PHR_ATTACHMENT_ENABLED` → `chat.phr-attachment-enabled`. **처음에는 세 환경 모두 `false`로 커밋**했고(`c960fbc`), 2026-08-19에 **dev·stg만 ON**으로 바꿨다(`6a2ab43`). prd는 지금도 OFF다.

**운영계를 켜기 전 게이트 — AI팀 확인 7항목.** 켜는 순간 건강보험 원문이 수탁자에게 나가고, 케이론 내부 DB의 PHR 보존은 medcare가 지울 수 없다.

| # | 확인할 것 |
|---|---|
| ① | 케이론 내부의 PHR **저장 위치** |
| ② | **보존기간** |
| ③ | 대화 삭제 시 **동반 파기** 여부 |
| ④ | PHR 필드만 **비저장** 가능 여부 |
| ⑤ | 기존 대화의 **PHR만 삭제** 가능 여부 |
| ⑥ | **재위탁 LLM 제공자**에 전달·보존되는지 |
| ⑦ | **실크기 PHR 요청 수용** 여부 — 크기 거절은 채팅 실패로 직결되어 fail-open 범위 밖이다 |

케이론이 ④(비저장)를 지원하지 않으면, "PHR을 해당 채팅의 입력 스냅샷으로 보존하되 대화 삭제·탈퇴 파기에 연동한다"는 정책을 **제품·보안 승인**으로 확정해야 한다. 이 경우 `DELETE /phr/delete`의 의미를 "저장 원본과 사용 설정 삭제"로 정정해야 한다(현 보유기간 문서의 '모두 파기' 서술과 충돌).

**개발계·검증계는 이 승인과 무관하게 켜 뒀다(2026-08-19 결정).** 다만 남는 사실이 있다 — 그 환경에서 실계정으로 스크래핑한 PHR 원문은 **케이론 개발 환경에 실제로 전달되어 남아 있다.** ①~⑦이 확정되면 그때까지 쌓인 검증 데이터의 파기 필요 여부도 함께 판단해야 한다. (이 7항목 서술은 2026-08-19 `1ce78be`에서 저장소 문서에서는 제거하고 환경별 상태만 남겼다. 게이트 자체는 유효하므로 여기에 전문을 옮겨 둔다.)

#### 검증

- dev 실호출로 케이론 수용·에코·LLM 소비를 확인 완료(2026-08-11).
- 회귀 테스트 `CheironDetailRealPayloadTest`가 **2026-08-11 dev 실측 응답 원문**을 그대로 쓴다. 필드를 추린 합성 JSON으로는 중첩 검색결과·후속질문·시각 포맷 회귀를 못 잡는다. WebClient 코덱과 **같은 ObjectMapper 빈**으로 검증한다.
- 뮤테이션 검증: `sanitize`를 무변환으로 바꾸면 이 테스트 1건과 sanitizer 테스트 3건이 실패한다.
- 테스트 총 470건 통과(기존 441 + 신규 29).

#### 남은 것

**실크기 합성 데이터로 dev SSE·상세·kmeta 3위치 probe 검사**(PHR 원문이 새지 않는지), AI팀 7항목 회신, 운영계 플래그 ON. Android는 Trend를 develop에 머지해야 실동작한다(§8).

---

### 3-3. 진행 중 질문(pending turn) 읽기 모델 반영

브랜치 `feature/pending-turn-visibility` → dev 머지, 7커밋(`cf1b086` ~ `bf7bcfd`, 모두 2026-08-21). **dev·stg·prd 3환경 배포 완료(2026-08-24).**

#### 무엇 / 왜

내재화(케이론→kmeta) 이후 **"질문 → 앱 이탈 → 재진입" 시 답변이 완료돼도 화면이 무반응**이던 결함을 고쳤다. `kmeta_*`에 완료된 대화만 적재돼 진행 중 턴이 상세 응답에 없었고, 두 앱이 그 대화를 '완료'로 보고 폴링을 시작하지 않았다.

#### 앱 무배포로 고친 원리 — 이 문서에서 가장 중요한 한 줄

내재화 **이전**에 케이론이 쓰던 계약은 `output_state` 키가 **없으면 = 생성 중**이었다. 두 앱은 이미 그 신호로 '생각 중' UI와 폴링(10초 간격, 최대 2분)을 걸고 있다.

- iOS: `guard let outputState else { .thinking }`
- Android: `chat.outputState?.let { ... }`

그래서 **우리 읽기 모델에서 그 계약을 복원**했다. 매퍼가 pending 행의 `output_state`를 비우는 한 줄이 앱 무배포의 발판이다.

덤으로 맞아떨어진 것: **iOS `InputState`는 `query: String` 하나만 비-옵셔널**이다. pending에 건강정보를 빼고 query만 저장한 판단이 앱 계약과 정확히 일치했다.

#### 흐름

```
① 질문 수신
   └─ 한 트랜잭션:
        kmeta_chats     INSERT  strid = "pending-" + UUID, status = IN_PROGRESS, response = NULL
        kmeta_chat_inputs INSERT  query 만 (건강정보 없음)
        같은 트랜잭션이 owner-scoped root 를 FOR UPDATE 로 잠가
          · 소유권 확인 (IDOR 쓰기 차단)
          · 같은 대화 중복 pending 게이트  → 429
        를 겸한다

② 상세 조회 (앱 폴링이 그대로 트리거)
   └─ ConversationMapper 가 pending 행의 output_state 를 비운다 → 앱은 '생각 중' 유지
   └─ repairIfPending()  (read-repair)
        PendingResyncGuard.tryAcquire(대화strid)  ← 대화당 30초 쿨다운, 못 얻으면 그대로 반환
        케이론 상세 조회 (트랜잭션 밖)
        root 잠금 → **처음 본 그 pending strid 가 아직 그대로인지 재확인**
        로컬 terminal strid ⊆ 케이론 스냅샷 확인
        종료된 턴만 전체 교체 — 아무것도 삭제하지 않는다
        결과 4종:
          UPDATED / ALREADY_COMPLETED → 로컬 재조회해서 응답   (앱: 답변 표시)
          NOT_READY / FAILED          → 처음 조회분 그대로     (앱: 생각 중 유지 + 폴링 계속)
        지표: medcare.convert{result=repair_updated|repair_not_ready|repair_failed|repair_already_completed}
```

#### 되돌리면 안 되는 지점 3가지 (상세는 §5)

1. **pending 술어는 `ChatEntity.isPending()` 단일 정의** — `status IN (IN_PROGRESS, IN_QUEUE) AND response IS NULL`. `response IS NULL` 단독 금지.
2. **아무것도 삭제하지 않는다.** pending 행이 답변을 되찾아올 유일한 트리거다.
3. **전체 교체 직전에 pending strid를 재확인**한다. 존재 여부만 보면 그 사이 시작된 새 질문을 자기 것으로 착각해 지운다.

#### 설계 범위 (v1, 의도적으로 좁힘)

자동 회수·스윕·인메모리 레지스트리는 **v1에서 제외**했다. 12분(서버 SSE 상한 10분 + 여유)을 넘긴 pending은 `DriftReport.stalePendingChats`로 **관측만** 한다. 수동 조치 절차는 §9에 전부 적어 뒀다.

알려진 한계(수용한 것):
- 신규 대화 첫 턴이 실패하면 chat 0건짜리 ghost root가 남는다.
- pending 중에는 바이탈 컨텍스트가 nil이거나 이전 값이다(root `last_input_kwargs`에서 읽는데 provisional root는 null).
- `convertAll`의 낡은 스냅샷 경합은 기존 부채 그대로.

#### 플래그

**없다.** 이 기능은 플래그 없이 배포했다. `CHAT_PENDING_RESYNC_COOLDOWN`(기본 30s)은 read-repair 호출량 조절 손잡이일 뿐 기능 ON/OFF가 아니다.

#### dev 실측으로 잡은 것 3건 (전부 목 테스트로는 못 잡는 부류)

1. **`@Query`로 `Mono<Boolean>` 반환 → 런타임 실패**(`"Failed to convert from MySqlDataRow to Boolean"`). MySQL `EXISTS`는 `BIGINT(0|1)`이고 r2dbc-mysql이 변환하지 못한다. **파생 쿼리(`existsBy...`)는 되지만 `@Query`는 안 된다.** COUNT→Long으로 바꿨다(`557f36e`). fail-open이라 채팅은 멀쩡했고 WARN만 남아 '기능이 조용히 꺼진' 형태였다. 리플렉션 가드 `PendingQueryShapeTest`를 추가해 고정했다.
2. **`isTerminal()` 추가로 응답에 `"terminal": false` 키가 새로 생겼다.** Jackson이 boolean getter를 프로퍼티로 만든다. `@JsonIgnore`로 차단(`bf7bcfd`). 직렬화 계약 테스트가 있었으면 커밋 전에 잡혔을 것이다.
3. **시각 대조 마진 5분 → 1분.** 케이론 기록 `07:49:05.516` vs 우리 pending `07:49:05.524` = **8ms** 차이였다.

#### 케이론 실측 계약

- 생성 중 status는 `IN_PROGRESS`. 턴은 질문 ~12초 후 상세에 노출된다.
- `start_time`·`query` 원문을 제공한다.
- **답변을 먼저 싣고 2초 뒤 SUCCESS로 바꾼다.** 반대 순서(SUCCESS 먼저, 답변 나중)도 가능하므로 `isTerminal()`은 SUCCESS에 대해 non-blank response를 요구한다(신고·평가의 완료 판정과 같은 규칙).
- **우리가 SSE를 끊어도 케이론은 생성을 완주한다.** read-repair가 성립하는 근거다.

#### 남은 것

**Phase 2(앱 릴리스 연동)는 `repair_updated` 발생률을 보고 판단**한다. 후보 4가지는 §8-3.

---

### 3-4. (곁가지) 답변 신고·평가 API

같은 기간 `feature/chat-report-and-rating` 브랜치에서 신설했다. 3커밋, **dev·stg·main 전부 반영**.

| 커밋 | 내용 |
|---|---|
| `b0e1f20` (08-06) | 위험 답변 신고·답변 평가 API 신설 |
| `b905390` (08-06) | 명세 추가 — `chatStrId` 확보 계약과 앱측 책임 명시 |
| `c3b2288` (08-06) | 명세에서 과거 답변 지원 명시, 앱 구현 지시를 계약 수준으로 되돌림 |

축은 두 가지다.

1. **무엇을 신고했는지는 서버가 확정한다.** 요청 body는 변조 가능해 조사 근거가 될 수 없으므로, 클라이언트 텍스트를 저장하지 않고 **원천 조회로 얻은 질문·답변**을 저장한다. 답변만으로는 위험성 판정이 어려워 질문 문맥도 함께 남긴다.
2. **쓰기 전에 소유권·chat 소속·완료 상태를 검증한다.** 없으면 임의 문자열로 남의 대화에 대한 신고·평가 행을 만들 수 있다.

구현상 주의점(그대로 두어야 하는 것):

- 검증은 `ConversationService.getOwnedCompletedChat`에 신설했다. **라우팅 분기(CHEIRON/INTERNAL)가 그 클래스에만 있기 때문**이다. 신고·평가 서비스가 케이론·kmeta를 직접 부르면 환경별로 다르게 동작한다. 기존 `getConversationDetail`은 재사용할 수 없었다 — 그쪽 `switchIfEmpty`는 대화 root가 없을 때만 폴백하는데, 대화는 로컬에 있고 요청 chat만 아직 변환되지 않은 경우(스트리밍 직후 변환 지연)에는 걸리지 않아 정상 신고가 404로 거절된다.
- **완료 판정은 status가 아니라 query/response의 blank 여부**가 실질 근거다. `ConversationMapper`가 iOS 비-옵셔널 디코딩 때문에 chat_input이 없으면 query를 `""`로, response null을 `""`로, status null을 SUCCESS로 채운다. 로컬 경로에서 상태값은 신뢰할 수 없다.
- **신고 멱등 확인을 원천 검증보다 먼저** 한다. 순서를 바꾸면 이미 저장된 신고의 재시도가 그 사이의 케이론 장애·대화 삭제 때문에 502/404로 실패해, 접수는 됐는데 앱에는 실패로 표시된다.
- **평가 해제(NONE)는 검증을 타지 않는다.** 자기 행만 지우므로 타인 데이터에 닿을 수 없고, 검증을 걸면 원 대화가 삭제됐거나 케이론 장애일 때 자기 평가를 못 지운다.
- 평가 저장은 **단문 `ON DUPLICATE KEY UPDATE`**. find→save 2문장이면 레이스에서 `DuplicateKeyException`이 나고, 그것을 집 관례대로 400으로 매핑하면 멱등 PUT이 깨진다.
- `reportText` 길이는 **code point 기준 커스텀 검증**(`@MaxCodePoints`)이다. `@Size`는 UTF-16 code unit을 세어 이모지를 2로 계산하므로, MySQL `varchar(200)`이 허용하는 입력을 서버가 거절하고 앱 카운터와도 어긋난다.

테이블은 `customer_chat_report`(신고), `customer_chat_rating`(평가). 소유 키는 **sub 단독**이다.

---

## 4. API 계약

### 4-1. `POST /medcare/v1/chat/stream` — 요청 (변경분만)

| 필드 | 타입 | 필수 | 제약 | 설명 |
|---|---|---|---|---|
| `query` | string | Y | 1~4000자 | 사용자 질문 |
| `conversationStrid` | string | N | UUID 또는 null | null이면 새 대화 |
| `sourceTypes` | string[] | Y | `["PUBMED","WEB"]` | 검색 소스 |
| `agentStrId` | string | N | – | 에이전트 ID (케이론은 실제로는 무시한다) |
| `agentInputFieldToValue` | object | N | – | 아래 3필드 |
| **`entryType`** | string | N | **32자 이하** | **신규.** 대화를 시작한 화면 |

**`agentInputFieldToValue`** — 자유 형식 문자열 3필드. 서버는 가공 없이 케이론에 그대로 넘긴다.

| 키 | 설명 |
|---|---|
| `Vital Signs` | 앱이 포맷한 최근 바이탈 사인 문자열 |
| `Air Quality Score` | 앱이 포맷한 공기질 문자열 |
| **`Vital Signs Trend`** | **신규.** 앱이 포맷한 기간별 바이탈 추세. 예: `[{"type":"spo2","period":"6M",...}]` |

세 필드 모두 서버가 별도 길이 상한을 두지 않는다. 다만 무제한을 보장하지도 않는다.

**`PHR`은 앱 입력 필드가 아니다.** 서버가 동의와 저장 데이터를 확인해 **케이론 요청에만** 채워 넣는 내부 필드다. 앱이 `"PHR"`을 보내도 **바인딩되지 않고 버려지며**, 대화 상세 응답의 `agent_input_field_to_value`에서도 이 키는 제거된다.

**`entryType` 계약**

| 값 | 서버 동작 |
|---|---|
| 없음 / `null` / 빈 문자열 | 일반 진입 — 제목을 요약으로 갱신(플래그 ON 시) |
| `"PHR"` / `"VITAL_SIGN"` | 특수 진입 — **아무것도 안 함**(제목은 현행 유지) |
| 그 외 아무 문자열 | 특수 진입과 동일 취급 |

- **서버는 값을 해석하지 않는다.** 있으면 "요약하지 마라"는 뜻일 뿐이라 모르는 값도 거절하지 않는다. 나중에 새 진입 유형이 생겨도 서버 배포 없이 그 대화의 제목이 보호된다. **모르는 값을 '일반'이 아니라 '특수'로 보는 쪽이 안전하다** — 건너뛰면 현행 제목이 남을 뿐이지만, 반대로 틀리면 고정 제목이 덮인다.
- **32자 초과일 때만 400**이다. 길이 상한만 둔다.
- **대화를 새로 만드는 요청에서만 의미가 있다.** 이어지는 질문에 실려 와도 서버가 무시한다.
- 서버에 enum이 없다. opaque string이다.

**응답 복원**: `GET /medcare/v1/conversations/{strid}`의 `last_input_kwargs.agent_input_field_to_value`와 `chats[].input_state.agent_input_field_to_value`로 위 3필드가 되돌아온다(케이론·내재화 라우팅 동일). PHR은 되돌아오지 않는다.

### 4-2. 진행 중 질문 관련 응답·에러

| 상황 | 응답 |
|---|---|
| 같은 대화에 진행 중인 턴이 이미 있음 | **HTTP 429** (`"이미 처리 중인 채팅이 있습니다. 잠시 후 다시 시도해 주세요."`) |
| 사용량 한도 초과 | HTTP 429 (기존) |
| 진행 중 턴이 있는 대화 상세 조회 | 해당 chat에 **`output_state` 키가 없다.** 앱은 이 신호로 '생각 중' + 폴링 |

429가 두 사유를 공유한다. 구분은 서버 로그와 지표로 한다 — `medcare.chat.pending{result=already_pending}` vs `medcare.chat.limit{result=blocked}`.

**중복 pending을 429로 막는 이유**: 두 pending이 공존하면 먼저 끝난 쪽의 전체 교체가 뒤쪽 pending을 지우는데, **케이론 SSE가 chat strid를 주지 않아** 어느 행이 어느 실행의 것인지 구분할 수 없어 복구가 불가능해진다.

### 4-3. 지표 (Micrometer 카운터)

| 지표 | 태그 |
|---|---|
| `medcare.chat.title` | `result=failed` |
| `medcare.chat.phr` | `result=feature_disabled` 등 |
| `medcare.chat.pending` | `result=already_pending` / `left_after_error` |
| `medcare.convert` | `result=repair_updated` / `repair_not_ready` / `repair_failed` / `repair_already_completed` |
| `medcare.chat.limit` | `result=blocked` / `fail_open` / `concurrent_blocked` |
| `medcare.chat.sse` | `result=overflow` |

**수집 기반이 없다.** Prometheus/ADOT 설정이 `application.yml`에 없어 이 카운터들을 대시보드·알람·배포 게이트로 쓸 수 없다. 현재로선 **DB 조회와 로그가 유일한 검증 수단**이다(§부록 B).

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **진입 판정은 앱의 `entryType` 한 필드로만.** 에이전트 입력(바이탈·공기질·추이)으로 판정하지 않는다 | iOS는 `vitalSigns`가 non-optional이라 항상 `""`를 보내고, Android도 "키를 생략하지 않는다"가 명시된 계약이며, 일반 질문에도 바이탈이 자동 첨부된다. 입력값으로 판정하면 **모든 일반 대화가 '바이탈 진입'으로 분류**돼 제목 요약이 통째로 죽는다 |
| 2 | **모르는 `entryType`은 '일반'이 아니라 '특수'로 취급**(요약 skip). 서버에 enum을 두지 않는다 | enum을 두면 앱이 새 진입 유형을 추가할 때마다 서버 배포가 필요하고, 그 사이 신규 진입의 고정 제목이 요약으로 덮인다. 지금 구조에서는 서버가 아무것도 몰라도 제목이 보호된다 |
| 3 | **제목 확정은 변환 성공 뒤.** `convertCompletedConversation`이 성공 시에만 strid를 방출하고 `flatMap`이 게이트다 | `.then()`으로 이으면 **변환 실패에도 제목 생성이 실행된다**(변환은 에러를 삼키고 `Mono.empty()`를 낸다). 반대로 제목을 변환보다 먼저 확정하면 변환이 케이론 상세를 다시 읽어 **방금 만든 요약을 첫 질문으로 되돌린다** |
| 4 | **생성 제목은 케이론 PATCH를 반드시 거친다** | 케이론이 원본이라 로컬 3테이블만 고치면 다음 재변환·read-repair가 되돌린다 |
| 5 | **제목 생성 POST는 읽기 타임아웃을 재시도하지 않는다**(`connectFailureOnce()`, 공용 `idempotentRead()` 금지) | 비멱등 POST다. 읽기 타임아웃까지 재시도하면 같은 대화에 요약을 두 번 돌려 LLM 비용과 제목 흔들림이 생긴다 |
| 6 | **임시 제목·중복 가드 코드를 두지 않는다** | `ConvertService.ensureOwnedRoot`가 이미 `title = query`를 삽입한다. 추가 가드는 중복이고, 두 곳이 제목을 정하면 어느 쪽이 이겼는지 추적이 안 된다 |
| 7 | **앱 입력 타입과 케이론 출력 타입을 분리한다** (`AgentInputFieldToValue`에 PHR 없음 / `CheironAgentInputFieldToValue`에만 있음) | 한 타입으로 합치면 **클라이언트가 임의의 건강보험 데이터를 에이전트에 주입**할 수 있다. 지금은 앱이 `"PHR"`을 보내도 바인딩 자체가 안 된다 |
| 8 | **PHR 정제는 역직렬화 *전* JsonNode 단계에서** 한다(`CheironResponseSanitizer`) | typed 정제는 도메인 모델이 아는 위치만 지운다. 실측 응답의 `agent_llm_inputs`는 `input_state` 안에 있고 현재 모델은 그 위치를 모른다 — **PHI 사본이 kmeta에 한 벌 더 남는다.** 트리에서 지우면 향후 스펙 드리프트도 같은 곳에서 막힌다 |
| 9 | **fail-open은 PHR 조회에만.** 케이론 호출 실패는 정상 전파 | PHR 조회 Mono가 `empty`나 `error`를 내면 하위 `flatMapMany`가 구독되지 않아 **케이론 호출 자체가 사라진다** — PHR 서브시스템이 채팅을 통째로 삼키는 최악의 형태다. '없음'은 `Optional.empty`로 표현한다. 반대로 케이론 실패까지 fail-open하면 사용자가 답변 없이 성공 응답을 받는다. 이 경계가 테스트로 고정돼 있다 |
| 10 | **pending 술어는 `ChatEntity.isPending()` 단일 정의** = `status IN (IN_PROGRESS, IN_QUEUE) AND response IS NULL` | `response IS NULL` 단독으로 판정하면 **케이론 FAILURE chat도 response NULL**이라 종료된 턴이 영원히 '생각 중'이 되고 조회마다 케이론을 다시 부른다. `IN_QUEUE`를 빼면 진행 중 턴이 '빈 답변 완료'로 보여 앱 폴링이 멈춘다(원래 고친 결함의 재생산). 넣어서 틀리면 조회가 한 번 더 돌 뿐이라 **비용이 비대칭**이다 |
| 11 | **pending 행을 절대 삭제하지 않는다.** 스트림 오류 종료에서도 지우지 않는다 | 케이론은 우리가 SSE를 끊어도 생성을 완주한다(실측). pending 행이 그 답변을 되찾아올 **유일한 트리거**다. 지우면 질문과 복구 트리거가 함께 사라져 케이론에 답변이 살아 있어도 영영 못 가져온다. **운영계에서 SQL로 지우는 것도 금지**(§9) |
| 12 | **read-repair는 종료된 턴만 적재하고, 전체 교체 직전에 pending strid를 재확인한다** | 진행 중 스냅샷을 쓰면 완료된 답변이 '생각 중'으로 퇴행한다. 존재 여부만 보면, 조회와 쓰기 사이에 정상 완료가 커밋되고 사용자가 다음 질문을 시작한 경우 **낡은 스냅샷 교체가 방금 만든 질문을 지운다** |
| 13 | **`isTerminal()`은 SUCCESS에 대해 non-blank response를 요구한다** | 케이론은 **답변을 먼저 싣고 2초 뒤 SUCCESS로 바꾼다**(실측). 반대 순서도 가능하므로 status만 보면 빈 답변을 종료로 확정한다 |
| 14 | **`@Query`로 `Mono<Boolean>`을 반환하지 않는다** (COUNT→Long 사용) | MySQL `EXISTS`는 `BIGINT(0|1)`이고 r2dbc-mysql이 Boolean으로 변환하지 못해 런타임 실패한다. **파생 쿼리(`existsBy...`)는 되지만 `@Query`는 안 된다.** fail-open이라 조용히 꺼진 형태로 나타난다. `PendingQueryShapeTest`가 리플렉션으로 고정 |
| 15 | **도메인 모델에 boolean getter를 추가할 때 `@JsonIgnore`를 확인한다** | Jackson이 boolean getter를 프로퍼티로 만들어 **응답에 새 키가 생긴다**(`"terminal": false`가 실제로 샜다). 앱 계약이 깨진다 |
| 16 | **read-repair 쿨다운(`PendingResyncGuard`)은 정확성 장치가 아니라 호출량 조절 장치다** | 앱이 10초마다 폴링하므로 가드가 없으면 그대로 케이론 호출 루프가 된다. 반대로 "가드가 붐빈다"는 이유로 복구를 막으면 안 되므로, 항목 수 상한(10,000)을 넘으면 만료분을 정리하고 그래도 남으면 **통과시킨다** |

---

## 6. 앱에 요청한 내용 — `entryType` 필드 추가

앱 코드는 직접 수정하지 않았다. 아래가 iOS·Android 담당에게 전달한 요청의 핵심이다. 인수자는 **이 내용이 실제 구현과 맞는지 확인하는 역할**이다(현재 반영 상태는 §2-4).

### 6-0. 요청의 범위

요청은 **요청 바디에 `entryType` 한 필드를 추가하는 것**뿐이다. 화면·UI 변경은 없고, 제목을 만들거나 가공하는 로직도 앱에는 생기지 않는다.

다만 필드 하나치고 **값이 지나가야 할 경로가 길다.** 특히 PHR 진입은 대화방이 자기 출처를 모르는 구조라 진입 화면부터 값을 들고 내려와야 한다.

**공통 규칙은 하나다.**

> **새 대화를 만드는 요청**이면서 **PHR·바이탈 화면에서 시작한 경우**에만 값을 넣는다. 그 외에는 넣지 않는다.

새 대화 판정은 두 플랫폼 모두 이미 가진 값으로 된다 — iOS `conversationId == nil`, Android `currentConversationStrid == null`.

**앱이 제목을 받아 처리하는 방식은 지금과 똑같다.** 목록 API가 주는 `title`을 그대로 쓰면 되고, 이미 임시 제목(첫 질문 30자)을 만들었다가 서버 제목으로 대체하는 흐름이 양쪽 다 있다.

**서버를 먼저 배포해도 앱에는 아무 영향이 없다.** 플래그로 막혀 있기 때문이다.

### 6-1. 미리 말해 둔 두 가지

1. **`"entryType": null`로 나가도 괜찮다.** Android Gson 설정이 `serializeNulls()`라 필드가 null이어도 바디에 키가 실리는데, 서버는 **키 없음·null·빈 문자열을 모두 같게** 본다. 이것 때문에 Gson 설정을 바꿀 필요가 없다 — medcare API 전체에 영향이 가는 변경이라 서버 쪽도 원치 않는다.
2. **대화를 새로 만드는 요청에서만 의미가 있다.** 이어지는 질문에 실려 와도 서버가 무시한다(제목을 매 턴 바꾸면 목록에서 대화를 잃는다). 그래서 **"첫 요청에만 정확히 넣기"보다 "새 대화일 때 넣기"만 지켜지면 충분**하고, 실수로 이어지는 턴에 남아 있어도 사고가 나지 않는다.

### 6-2. iOS — 진입 출처를 대화방까지 내려줘야 한다

**PHR 진입이 구조상 판정 불가였다.** `PHRCheckupListView`가 `di.makeAIChatRoomViewModel(initialQuery:)`로 **질문 문구만** 넘겨서, 대화방에 들어온 뒤에는 그 질문이 PHR에서 시작됐는지 알 수 없다. 반면 **바이탈은 이미 알고 있다** — `AIChatRoomViewModelV2`의 `isFromVitalSign`이 `init`에서 스냅샷 유무로 정해진다.

한쪽만 새로 만들면 비대칭이 생기므로 **진입 출처를 명시 파라미터 하나로 통일**하는 편을 제안했다.

```swift
enum AIChatEntryType: String, Encodable { case phr = "PHR"; case vitalSign = "VITAL_SIGN" }
```

**값을 세팅하는 곳 (3곳)**

| 진입 | 파일 | 값 |
|---|---|---|
| PHR 검진 상담 | `PHRCheckupListView.swift` | `.phr` |
| 바이탈 대시보드 | `VitalSignsView.swift` | `.vitalSign` |
| 바이탈 추이 상세 | `VitalWellnessDetailView.swift` | `.vitalSign` |

**값이 지나가는 경로 (6파일)** — `DIContainer.makeAIChatRoomViewModel` → `AIChatRoomViewModelV2.init` → `sendMessage` → `AIChatServiceV2Protocol.sendMessage` → `AIChatServiceV2.sendMessage`(+`AIChatServiceV2Mock`) → `streamSSE`/`buildBody` → `AIChatStreamRequestDTO`.

**DTO에 프로퍼티만 추가하면 안 나간다.** `AIChatStreamRequestDTO`가 커스텀 `encode(to:)`라 `encodeIfPresent` 한 줄을 같이 넣어야 한다(nil이면 키가 빠지는 건 `conversationStrid`와 같은 방식).

**오탐 지점 두 군데**

- `handleNewConversation()`은 **같은 ViewModel 안에서** `conversationId`를 nil로 되돌린다. 출처를 `let`으로 두면 바이탈에서 들어와 "새로운 대화"를 누른 사용자가 계속 `VITAL_SIGN`을 보내게 된다. `var`로 두고 스냅샷 정리 옆에서 함께 비우면 된다.
- `retryLastMessage()`는 `sendMessage`를 다시 탄다. 첫 메시지 실패 후 재시도는 **여전히 새 대화 생성**이라 값이 남아 있어야 한다. 전송 시점이 아니라 성공 시점에 비우거나, `conversationId == nil` 조건만으로 판정하면 자연히 해결된다.

**값을 넣지 않는 진입**: 홈 입력창, "새로운 대화", 후속질문 칩. 공기질은 전용 진입 화면이 없어 해당 없음.

### 6-3. Android — 경로 중간에 WorkManager가 있다

값이 지나갈 곳이 iOS보다 한 겹 많다. ViewModel이 use case를 직접 부르지 않고 `workDataOf`로 넘기는 구조라, **`androidx.work.Data`는 String만 실을 수 있어** 그 경계에서는 `String?`(또는 enum `.name`)으로 건너가야 한다.

**값을 세팅하는 곳 (5곳)**

| 진입 | 파일 | 값 |
|---|---|---|
| PHR 구강 검진 | `MyHealthcarePhrScreeningOralDetailScreen.kt` | `PHR` |
| PHR 일반 검진 | `MyHealthcarePhrScreeningOralDetailScreen.kt` | `PHR` |
| PHR 암 검진 | `MyHealthcarePhrCancerScreeningDetailScreen.kt` | `PHR` |
| 바이탈 메인(최근 측정) | `VitalMainScreen.kt` | `VITAL_SIGN` |
| 바이탈 리포트(1주/1개월/6개월) | `VitalReportScreen.kt` | `VITAL_SIGN` |

바이탈 추이는 **기간 3종이 모두 같은 호출부**를 타므로 기간별 분기가 필요 없고, 최근 측정과 추이도 서버 입장에서는 같은 `VITAL_SIGN`이다.

**값이 지나가는 경로 (6파일)**

```
진입 화면 → nav route 쿼리 파라미터
  → MyHealthcareChatScreen (nav arg 읽기)
      → MyHealthcareViewModel.sendMessage 계열
          → workDataOf                      ← String 경계
              → ChatStreamWorker.doWork
                  → ChatStreamUseCase.invoke
                      → ChatStreamRequest (domain)
                          → toReqChatStream (MyHealthcareMapper)
                              → ReqChatStream
```

Repository 인터페이스·구현과 `MedcareAPI`는 요청 객체를 통째로 받으므로 **수정 없이 따라온다.**

**nav 경로의 함정**: `MyHealthcareNavigationExt.toConcreteMyHealthcareRoute`가 백스택 복원 시 route를 다시 조립하는데, 여기에 `entryType`을 안 넣으면 **단일 인스턴스 정리가 일어날 때 값이 조용히 사라진다.** route 템플릿(`MyHealthcareGraph.kt`)과 함께 봐야 한다.

**`initialMessage`는 PHR 표식이 될 수 없다.** 홈 입력창도 같은 파라미터를 쓰므로 별도 값이 필요하다.

**재시도**: `retryMessage()`가 `sendMessage(isRetry = true)`로 되돌아오므로, 첫 메시지 실패 후 재시도에서도 값이 남아 있어야 한다(iOS와 같은 사정).

**값을 넣지 않는 진입**: 홈 입력창, 헤더 새 채팅, 목록 새 채팅, 폴더 내 새 채팅. 공기질은 `FEATURE_AIR_QUALITY_ENABLED = false`라 해당 없음.

### 6-4. `Vital Signs Trend` (별도 요청)

제목 요약과는 별개 건이다. 서버가 필드를 받게 됐으므로 앱이 실제로 보내야 동작한다.

- **iOS**: `AgentInputFieldValues`(`AIChatStreamRequestDTO.swift`)와 `AgentInputFieldValuesResponseDTO`(`ConversationListResponseDTO.swift`) **양쪽**에 `"Vital Signs Trend"` 키 추가. → **`develop` 반영 확인됨**(§2-4).
- **Android**: 요청·응답 매핑 양쪽. → **`develop` 미반영**, feature 브랜치에만 있다.
- **PHR은 앱이 모델링하지 않는다.** 서버 내부 필드다.

---

## 7. 배포 방법

### 7-1. 순서

세 기능 모두 **`skix-medcare` 단일 저장소**이고 **DDL 변경이 없다.** 다른 저장소와의 배포 순서 제약도 없다.

```
1. (선행) 배포 전 게이트 SQL  ← §7-2. pending turn 최초 배포 때만 의미가 있었으나 재배포 시에도 무해
2. 애플리케이션 이미지 빌드·푸시
3. ECS 태스크 정의 리비전 등록  ← 플래그 값이 여기 들어간다. **리비전 등록 누락이 흔한 함정이다**
4. 서비스 업데이트
5. 배포 후 검증  ← §부록 B
```

**플래그를 바꾸는 것만으로도 태스크 정의 리비전 등록이 필요하다.** 코드 재빌드는 필요 없지만, 리비전을 새로 등록하지 않으면 `deploy/task-definition-*.json` 을 고쳐도 실행 중인 태스크에는 반영되지 않는다.

### 7-2. 배포 전 게이트 (pending turn 관련, 최초 배포 때 수행함)

```sql
SELECT status, COUNT(*) FROM kmeta_chats WHERE response IS NULL GROUP BY status;
```

`IN_PROGRESS` 또는 `IN_QUEUE`가 있으면 **그 대화들이 배포 순간 '생각 중'으로 바뀐다.** 운영계는 8건이 전부 다른 status라 조치가 불필요했다. 개발계·검증계에서는 배포 전 정리로 20건씩 지웠는데 **그건 검증계라 허용한 것**이다(§9 참조 — 운영계에서는 금지).

### 7-3. 플래그를 켜는 순서

| 플래그 | dev | stg | prd |
|---|---|---|---|
| `CHAT_TITLE_GENERATION_ENABLED` | 배포 직후 (완료) | 양 플랫폼 QA 빌드 후 (**조건 미충족 상태로 켬**) | 양 플랫폼 **릴리스** 후 |
| `CHAT_PHR_ATTACHMENT_ENABLED` | 기능 검증 목적 (완료) | 기능 검증 목적 (완료) | AI팀 7항목 회신 + 제품·보안 승인 후 |

**한쪽 플랫폼만 배포된 상태로 제목 요약을 켜면 안 된다.** 플래그는 플랫폼별로 갈리지 않는다.

### 7-4. 롤백

| 대상 | 방법 |
|---|---|
| 제목 요약 | **플래그를 끄면 끝.** 이미 요약으로 바뀐 제목은 되돌아가지 않지만(케이론에도 저장됨) 사용자가 직접 고칠 수 있어 데이터 복구 절차는 없다 |
| PHR 첨부 | **플래그를 끄면 끝.** OFF면 PHR DB 조회도 KMS 복호화도 하지 않는다(코드 경로 자체를 타지 않음). 단 **이미 케이론에 전달된 원문은 회수 불가**다 |
| pending turn | 플래그가 없으므로 **이전 이미지로 되돌리는 것뿐**이다. DDL이 없어 스키마 롤백은 필요 없다. 되돌리면 pending 행은 남고 앱이 다시 '완료'로 오인하게 된다 — **pending 행을 지우지 말고** 다음 배포까지 둔다 |

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **Android `entryType`·`Vital Signs Trend`의 `develop` 머지 확인.** 현재 `feature/my-healthcare-phr-integration`에만 있다(§2-4). 머지 전에는 운영계 제목 요약을 켤 수 없고, Trend도 Android에서 실동작하지 않는다 | Android 담당 + 백엔드 | 커밋 `4ab93f66e`(entryType), `5c9fe7dfa`(Trend) |
| 2 | **dev·stg ECS에 제목 요약 리비전이 실제로 떠 있는지 확인.** 태스크 정의의 이미지 태그와 `CHAT_TITLE_GENERATION_ENABLED` 값 | 백엔드 | 로컬 맥에서 불가. VDI에서 확인 |
| 3 | **dev 제목 요약 검증 curl 2종 실행**(§부록 B-1). 아직 미수행 | 백엔드 | DB 접근이 필요해 VDI에서 |
| 4 | **착수 전 못 한 사전 확인 2건**(로컬 맥은 DB 미도달): ① 바이탈 진입 제목이 실제로 `[vital sign] ... HH:mm` 형식인지 ② 일반 대화 제목이 실제로 첫 질문과 같은지 | 백엔드 | 둘 다 §3-1 전제의 근거 |
| 5 | **dev PHR 첨부 probe 검사.** 실크기 합성 데이터로 SSE·상세 응답·kmeta 3위치에 PHR 원문이 새지 않는지(§부록 B-2) | 백엔드 | `CheironResponseSanitizer`가 실제로 작동하는지 확인하는 유일한 방법 |

### 8-2. 릴리스 게이트 (전부 확정 전 진행 금지)

| # | 확인할 것 | 상대 |
|---|---|---|
| 6 | **운영계 제목 요약 ON** — iOS·Android **둘 다 릴리스 완료**가 조건. 한쪽만이면 그 플랫폼의 PHR·바이탈 진입 제목이 요약으로 덮인다 | iOS·Android |
| 7 | **운영계 PHR 첨부 ON** — AI팀 7항목(§3-2) 전부 회신 + 제품·보안 승인 | AI팀 · 제품 · 보안 |
| 8 | 케이론이 PHR 비저장(④)을 지원하지 않을 경우, "입력 스냅샷으로 보존하되 대화 삭제·탈퇴 파기에 연동" 정책 확정과 `DELETE /phr/delete` 의미 정정 | 제품 · 보안 · 법무 |
| 9 | dev·stg에서 실계정 PHR이 케이론 개발 환경에 이미 전달돼 남아 있다. 7항목 확정 시 **그 검증 데이터의 파기 필요 여부** 함께 판단 | AI팀 · 보안 |
| 10 | **`main` 승격** — `main..dev` 30커밋. 제목 요약이 여기 포함된다. 승격하면 `task-definition-prd.json`에 `CHAT_TITLE_GENERATION_ENABLED=false` 키가 생긴다(값이 false라 동작 변화는 없다) | 백엔드 |

### 8-3. 후속 (이 기능들을 막지는 않음)

| # | 항목 |
|---|---|
| 11 | **`docs/operations.md` 제목 요약 절 정정.** "기본 false, 세 환경 모두 false 로 커밋돼 있다"가 낡았다(dev·stg는 true, 각각 09-02·09-03) |
| 12 | **케이론 문의 4건**(블로커 아님): ① `generate_title` 지연 실측 → 현재 20초 timeout 조정 근거 ② `generate_title`이 제목을 케이론에 **자체 저장**하는지 → 그렇다면 PATCH 제거 가능 ③ 멱등성 ④ 실패 응답 형태 |
| 13 | **지표 수집 기반 부재.** `medcare.chat.*`·`medcare.convert` 카운터가 있으나 Prometheus/ADOT 수집 설정이 없어 알람·대시보드·배포 게이트로 쓸 수 없다. 별도 인프라 티켓 |
| 14 | **pending Phase 2(앱 릴리스 연동)** — `repair_updated` 발생률을 보고 판단한다. 후보 넷: ① 앱 폴링 2분 상한을 서버 SSE 상한(10분)에 정렬 + 타임아웃 시 전체화면 에러 대신 **턴 단위 실패** ② **스트림 완료 시 목록 무효화**(실측: 서버 `last_used_time`은 정상 갱신되는데 앱이 세션 중 목록을 재조회하지 않아 순서가 그대로다. 앱을 재시작하면 맨 위로 올라온다 — 데이터가 아니라 표시 문제) ③ **재개형 SSE**(현재 sink가 요청당 unicast라 스트림 허브 재설계가 선행) ④ **완료 FCM** |
| 15 | **직렬화 계약 테스트 부재.** `"terminal": false` 키 누출(§3-3)은 계약 테스트가 있었으면 커밋 전에 잡혔다. `ConversationJsonContractTest` 같은 것을 만들면 앱 무배포 전략의 발판이 된다 |
| 16 | pending v1의 알려진 한계 3건(ghost root / pending 중 바이탈 컨텍스트 nil / `convertAll` 낡은 스냅샷 경합). 수용한 상태이고 각각 별도 티켓 후보다 |
| 17 | `agent_strId` 파라미터가 **케이론에서 무시되는 죽은 값**임이 실측됐다(케이론이 자체 `SKIX_A1`을 쓴다). 앱·서버 양쪽에서 걷어낼지 결정 |
| 18 | 12분 초과 pending의 **자동 회수** 도입 여부. 판단 근거는 `stalePendingChats` 수치의 빈도다(§9) |

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인·조치 |
|---|---|
| **⚠️ 운영계에서 pending 행을 SQL로 지우라는 요청** | **절대 지우지 않는다.** 그 행이 곧 그 턴이고, 지우면 질문과 복구 트리거가 함께 사라져 **케이론에 답변이 살아 있어도 영영 못 가져온다.** 개발계·검증계에서 배포 전 정리로 지운 적은 있으나 그건 검증계라 허용한 것이다 |
| "검증계에서 PHR·바이탈 진입 대화도 제목이 요약으로 바뀐다" (QA 보고) | **서버 결함이 아니다.** stg 플래그를 앱 대응 전에 켠 의도된 상태다(§3-1). 앱이 `entryType`을 싣기 시작하면 자연히 해소된다 |
| "질문했는데 계속 생각 중" | ① `GET /conversations/{strid}` 응답의 해당 chat에 `output_state`가 없으면 서버가 pending으로 보고 있는 것이다. ② 케이론에 답변이 있으면 사용자가 대화를 다시 열 때 자동 복구된다(대화당 30초 간격). ③ 급하면 `convertAll` |
| "이미 처리 중인 채팅이 있습니다" (429) | 같은 대화에 pending이 이미 있다. 의도된 거절이다. 서버 로그에 `같은 대화에 진행 중인 턴이 있어 차단 - user: {sub}` 가 남는다 |
| 미완료 턴이 쌓인다 | `DriftReport.stalePendingChats`가 **12분(서버 SSE 상한 10분 + 여유)** 초과 pending 수다. 배포·재시작·변환 실패로 스트림이 죽은 경우에 생긴다. 조치는 아래 표 |
| 대화 순서가 안 바뀐다 | 서버 `last_used_time`은 정상 갱신된다. 앱이 세션 중 목록을 재조회하지 않는 **표시 문제**다. 앱을 재시작하면 맨 위로 올라온다(§8-3의 14번 ②) |
| 제목이 안 바뀐다 | ① 플래그 확인 ② `entryType`이 실려 오지 않았는지 ③ 변환이 실패했는지(변환 실패면 제목 생성 자체가 스킵된다). 서버 로그: `대화 제목 생성 실패 - user: {}, strid: {}` (WARN), `대화 변환 실패 - user: {}, strid: {}` (ERROR) |
| PHR이 상담에 반영 안 된다 | ① 플래그 확인 ② `customer_if_phr.usage_yn = 'Y'` 인지 ③ 저장된 데이터가 있는지. 조회·복호화·파싱 실패는 **채팅을 막지 않고 첨부만 생략**한다 |
| 대화 삭제 순서 | 케이론에 삭제 요청이 **먼저** 나가고 그 다음 로컬을 지운다. 이 순서가 계약이다 |

**`stalePendingChats > 0` 일 때의 순서**

| 상황 | 조치 |
|---|---|
| 케이론에 답변이 있음 | **아무것도 하지 않는다.** 사용자가 대화를 열면 자동 복구된다. 급하면 `convertAll` |
| 케이론에도 대응 턴이 없음 | 해당 `strid`의 **pending chat 한 행만** 수동 삭제. root와 다른 chat은 건드리지 않는다 |
| 반복해서 쌓임 | 자동 회수 도입을 별도 티켓으로 검토(빈도 근거가 이 수치다) |

`reconcile`은 여기 쓰지 않는다. 대화 **strid 집합**을 비교하는 절차라, 대화 자체는 양쪽에 있고 턴 하나만 미완료인 경우는 누락으로 잡히지 않아 아무 일도 하지 않는다.

**대상 식별 SQL**

```sql
SELECT c.strid, c.stream_strid, s.user_strid, c.start_time
FROM kmeta_chats c JOIN kmeta_streams s ON s.strid = c.stream_strid
WHERE c.response IS NULL AND c.status IN ('IN_PROGRESS','IN_QUEUE')
  AND c.start_time < UTC_TIMESTAMP() - INTERVAL 12 MINUTE;
```

**로그 키워드**

| 키워드 | 의미 |
|---|---|
| `title_generation feature enabled=` | 기동 시 1회. 플래그 실제값 확인용 |
| `generateConversationTitle() - started: conversationStrid=` | 제목 생성 시작(제목 본문은 PHI라 로그에 남기지 않는다) |
| `대화 제목 생성 실패` | WARN. 채팅은 성공했고 제목만 첫 질문으로 남았다 |
| `대화 변환 실패` | ERROR. 이쪽이 실패하면 제목 생성은 실행되지 않는다 |
| `같은 대화에 진행 중인 턴이 있어 차단` | INFO. 429의 원인 |

---

## 부록 A. 핵심 커밋 (시간순)

전부 `skix-medcare` 저장소다. `dev`/`stg`/`main` 열은 2026-09-22 실측이다.

| 날짜 | 커밋 | 내용 | dev | stg | main |
|---|---|---|---|---|---|
| 08-06 | `b0e1f20` | 위험 답변 신고·답변 평가 API 신설 — 신고 대상은 서버가 원천에서 확정 | ✓ | ✓ | ✓ |
| 08-06 | `b905390` | 신고·평가 명세 추가 (`chatStrId` 확보 계약, 앱측 책임) | ✓ | ✓ | ✓ |
| 08-06 | `c3b2288` | 명세에 과거 답변 지원 명시, 앱 구현 지시를 계약 수준으로 | ✓ | ✓ | ✓ |
| 08-12 | `cdbc10b` | **Vital Signs Trend 추가 + 내재화 경로 입력 필드 복원** | ✓ | ✓ | ✓ |
| 08-12 | `c960fbc` | **PHR 케이론 첨부 — 기능 플래그 뒤, 응답으로는 되받지 않음** | ✓ | ✓ | ✓ |
| 08-12 | `70b816a` | 케이론 상세 응답 실측 원문 회귀 테스트 | ✓ | ✓ | ✓ |
| 08-19 | `6a2ab43` | PHR 첨부를 개발계·검증계에서 활성화 | ✓ | ✓ | ✓ |
| 08-19 | `1ce78be` | PHR 첨부의 승인 대기 서술 정리(확인 7항목을 문서에서 제거) | ✓ | ✓ | ✓ |
| 08-21 | `cf1b086` | **진행 중 질문을 읽기 모델에 남겨 재진입 시 답변 표시** (핵심) | ✓ | ✓ | ✓ |
| 08-21 | `39aa535` | 미완료 턴을 드리프트 점검 관측 축(`stalePendingChats`)에 추가 | ✓ | ✓ | ✓ |
| 08-21 | `b7818a0` | 답변이 비어 있는 SUCCESS는 종료로 보지 않음 | ✓ | ✓ | ✓ |
| 08-21 | `abebcb5` | 폴더 개수 상한 도입으로 깨진 부모 검증 테스트 복구 | ✓ | ✓ | ✓ |
| 08-21 | `ddcd0f8` | read-repair 쿨다운을 태스크 정의로 조정 가능하게 노출 | ✓ | ✓ | ✓ |
| 08-21 | `557f36e` | 중복 게이트 쿼리 런타임 실패를 COUNT로 교체 | ✓ | ✓ | ✓ |
| 08-21 | `bf7bcfd` | 내부 판정(`terminal`) 응답 누출 차단 + 대조 마진 1분 | ✓ | ✓ | ✓ |
| 09-01 | `1058fc8` | **케이론 제목 생성 API(`generate_title`) 어댑터 추가** | ✓ | ✓ | ✗ |
| 09-01 | `a6fed67` | **일반 진입 신규 대화의 제목을 답변 완료 후 요약으로 확정** (+`entryType` 수신) | ✓ | ✓ | ✗ |
| 09-01 | `3dfda7d` | 제목 요약 플래그를 ECS 환경변수로 노출 (3환경 전부 false) | ✓ | ✓ | ✗ |
| 09-02 | `fffb819` | 개발계에서 제목 요약 ON | ✓ | ✓ | ✗ |
| 09-03 | `16eb05b` | 검증계에서 제목 요약 ON | ✓ | ✓ | ✗ |

참고 — 같은 pending 브랜치에 섞여 들어간 `f1b025e`(사용자당 폴더 50개 제한, 08-20)는 이 문서의 범위 밖 별개 기능이다.

**앱 저장소 참고 커밋** (직접 수정하지 않았고 기준일 실측으로 확인만 했다)

| 저장소 | 브랜치 | 커밋 | 내용 |
|---|---|---|---|
| `benjaminios` | `develop` | `e756532f` (09-02) | AI 상담 요청에 `entryType` 전달 |
| `benjaminandroid` | `feature/my-healthcare-phr-integration` (**develop 미머지**) | `4ab93f66e` (09-02) | 채팅 요청에 `entryType` 추가 + 목록·상세에서 서버 생성 제목 반영 |
| `benjaminandroid` | `feature/MyHealthCare-바이탈사인-데이터-input` (**develop 미머지**) | `5c9fe7dfa` (08-05) | 바이탈 홈에서 1주/1개월/6개월 Data Input |

**주요 파일** (`src/main/java/com/skix/medcare/` 하위)

| 책임 | 파일 |
|---|---|
| `entryType` 수신 | `adapter/in/web/dto/ChatRequest.java` |
| 제목 판정·게이트·오류 흡수 | `application/service/ChatService.java` (`titleGenerationDue`, `generateTitle`, `completeStream`) |
| 제목 생성·PATCH·로컬 3테이블 동기화 | `application/service/ConversationService.java` (`generateAndApplyTitle`, `syncLocalTitle`) |
| 케이론 `generate_title` 호출 | `adapter/out/cheiron/CheironApiAdapter.java` |
| 플래그 정의 | `config/properties/ChatProperties.java` |
| 앱 입력 타입(PHR 없음) | `domain/model/AgentInputFieldToValue.java` |
| 케이론 출력 타입(PHR 있음) | `domain/model/CheironAgentInputFieldToValue.java` |
| PHR 응답 정제 | `adapter/out/cheiron/CheironResponseSanitizer.java` |
| PHR 조회·복호화 | `application/service/PhrService.java` (`findPhrForChat`) |
| pending 술어 | `adapter/out/persistence/entity/kmeta/ChatEntity.java` (`isPending`) |
| pending 등록·read-repair | `application/service/ConvertService.java` (`registerPendingTurn`, `repairPendingConversation`, `RepairResult`) |
| read-repair 호출부 | `application/service/ConversationService.java` (`repairIfPending`) |
| read-repair 쿨다운 | `application/service/chat/PendingResyncGuard.java` |
| 내재화 입력 복원 | `application/mapper/ConversationMapper.java` (`toChat`) |

**테스트** (회귀를 지키는 자리)

| 테스트 | 고정하는 것 |
|---|---|
| `ChatTitleGenerationTest` | 판정 3조건, 변환 성공 게이트, 실패 시 채팅 성공 유지 |
| `CheironGenerateTitleAdapterTest` | 404/에러 매핑, timeout, 재시도 정책 |
| `ChatPhrAttachmentTest` | 플래그 OFF 시 조회 안 함, fail-open 경계 |
| `CheironDetailRealPayloadTest` | 2026-08-11 dev 실측 응답 원문 회귀 |
| `AgentInputRestoreTest` | 내재화 경로 입력 필드 복원 |
| `PendingQueryShapeTest` | `@Query`가 Boolean을 반환하지 않도록 리플렉션 가드 |
| `ConvertServiceTest` / `ConversationServiceTest` | read-repair 4결과 분기, 재확인 로직 |

---

## 부록 B. 검증 절차

로컬 맥에서는 개발계 DB·ECS에 도달할 수 없다. 아래는 **VDI에서** 수행한다.

### B-1. 제목 요약 검증 (미수행 — §8-1의 3번)

플래그가 ON인 환경에서 curl 2종을 돌린다. 호스트와 `IdToken`은 각 환경 값으로 바꾼다.

```bash
MEDCARE="https://<medcare 호스트>"
TOKEN="<Cognito IdToken>"

# ① 일반 신규 대화 — entryType 없음 → 제목이 요약으로 바뀌어야 한다
curl -N -X POST "${MEDCARE}/medcare/v1/chat/stream" \
  -H "IdToken: ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{
    "query": "요즘 혈압이 130/85 정도 나오는데 이 정도면 관리가 필요한 상태인가요?",
    "conversationStrid": null,
    "sourceTypes": ["WEB","PUBMED"],
    "agentStrId": "SKIX_A1"
  }'
# SSE STOP 이벤트의 conversation_strid 를 기록한다.

# ② 특수 진입 — entryType: "PHR" → 제목이 query 그대로여야 한다
curl -N -X POST "${MEDCARE}/medcare/v1/chat/stream" \
  -H "IdToken: ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{
    "query": "제 검진 결과를 설명해 주세요",
    "conversationStrid": null,
    "sourceTypes": ["WEB","PUBMED"],
    "agentStrId": "SKIX_A1",
    "entryType": "PHR"
  }'

# ③ 길이 상한 — 33자 이상이면 400
curl -s -o /dev/null -w '%{http_code}\n' -X POST "${MEDCARE}/medcare/v1/chat/stream" \
  -H "IdToken: ${TOKEN}" -H 'Content-Type: application/json' \
  -d '{"query":"x","sourceTypes":["WEB"],"entryType":"AAAAAAAAAABBBBBBBBBBCCCCCCCCCCDDD"}'
# 기대: 400
```

**DB 확인 — 세 테이블의 제목이 모두 같아야 한다.**

```sql
-- <STRID> 는 ①에서 기록한 conversation_strid
SELECT 'kmeta_streams'            AS src, s.title
  FROM kmeta_streams s              WHERE s.strid = '<STRID>'
UNION ALL
SELECT 'kmeta_conversation_search', c.title
  FROM kmeta_conversation_search c  WHERE c.stream_strid = '<STRID>'
UNION ALL
SELECT 'customer_folder_chat',      f.chat_title
  FROM customer_folder_chat f       WHERE f.chat_str_id = '<STRID>';
```

- ①의 기대: 세 행 모두 **요약 제목**(첫 질문 문장이 아님). `customer_folder_chat`은 해당 대화를 폴더에 담았을 때만 행이 있다(없으면 정상).
- ②의 기대: 세 행 모두 **query 원문 그대로**.

**케이론 원본도 같아야 한다** — 대화 상세를 다시 조회해 `title`이 위와 일치하는지 본다. 다르면 PATCH가 실패한 것이다.

```bash
curl -s "${MEDCARE}/medcare/v1/conversations/<STRID>" -H "IdToken: ${TOKEN}"
```

### B-2. PHR 첨부 누출 probe (미수행 — §8-1의 5번)

플래그가 ON인 환경에서 PHR이 저장된 계정으로 질문한 뒤, **PHR 원문이 나가면 안 되는 4곳**을 확인한다.

1. **SSE 응답 본문** — B-1의 ① curl 출력에 PHR 문자열이 없는지.
2. **대화 상세 응답** — `GET /medcare/v1/conversations/{strid}` 의 `last_input_kwargs.agent_input_field_to_value`와 `chats[].input_state.agent_input_field_to_value`에 `PHR` 키가 없는지.
3. **kmeta 3위치**:

```sql
SELECT 'streams' AS src, s.last_input_kwargs                AS payload
  FROM kmeta_streams s        WHERE s.strid = '<STRID>'
UNION ALL
SELECT 'chat_inputs', ci.agent_input_field_to_value
  FROM kmeta_chat_inputs ci
  JOIN kmeta_chats c ON c.strid = ci.chat_strid
 WHERE c.stream_strid = '<STRID>'
UNION ALL
SELECT 'agent_llm_inputs', a.value
  FROM kmeta_agent_llm_inputs a
  JOIN kmeta_chats c2 ON c2.strid = a.chat_strid
 WHERE c2.stream_strid = '<STRID>';
```

(조인 키에 주의한다. `kmeta_chat_inputs`와 `kmeta_agent_llm_inputs`는 둘 다 **턴 단위**라 `chat_strid`로 묶이고, 대화 단위인 `stream_strid` 컬럼을 갖고 있지 않다.)

세 행 어디에도 `"PHR"` 키나 건강보험 데이터 본문이 없어야 한다. **하나라도 있으면 `CheironResponseSanitizer`가 작동하지 않은 것이므로 즉시 플래그를 끈다.**

4. **실크기 데이터 수용 여부** — 합성이 아니라 실제 크기의 PHR로 한 번은 돌려야 한다. 크기 거절은 fail-open 범위 밖이라 **채팅 실패로 직결**된다(§3-2의 7항목 중 ⑦).

### B-3. pending turn read-repair 시뮬레이션

**태스크를 죽이는 것보다 SQL 시뮬레이션이 낫다** — 실패 시 원인이 하나로 좁혀진다.

1. 정상 완료된 대화 하나를 고른다.
2. 그 대화의 **완료 턴 하나를 삭제**한다(`kmeta_chats` + 대응 `kmeta_chat_inputs`).
3. 같은 query로 **합성 pending을 삽입**한다 — `strid = 'pending-' || UUID()`, `status='IN_PROGRESS'`, `response=NULL`, `stream_strid`는 그 대화.
4. 앱 또는 curl로 `GET /medcare/v1/conversations/{strid}` 를 호출한다.
5. 기대: 첫 호출에서 read-repair가 돌아 케이론 원본으로 복구되고, 응답에 그 턴의 `output_state`가 실린다. 로그·지표는 `medcare.convert{result=repair_updated}`.
6. 30초 안에 다시 부르면 쿨다운에 막혀 케이론 호출이 나가지 않는다(정상).

**주의**: 3번의 `stream_strid`를 틀리면 남의 대화에 pending을 심는 셈이 된다. 반드시 같은 대화 root를 쓴다. 그리고 **운영계에서는 하지 않는다.**
