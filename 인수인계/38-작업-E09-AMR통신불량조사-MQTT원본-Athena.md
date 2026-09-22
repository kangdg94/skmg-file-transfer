# [작업] E09(AMR 통신 불량) 조사 — MQTT 원본 데이터 레이크와 Athena

| 항목 | 내용 |
|---|---|
| **상태** | SQL 팩 2종 · 전달용 컬럼 정의서 **작성 완료** · **실행·분석 미완** · AMR팀 회신 대기 |
| **작업 기간** | 2026-08-25 ~ 2026-08-30 |
| **직접 수정한 저장소** | **없음** (조사 전용 작업이다. 조사 대상이 된 서버 동작 변경 커밋 3건은 §2-2에 별도로 적었다) |
| **산출물** | Athena SQL 팩 · MySQL SQL 팩 · AMR팀 전달 컬럼 정의서 — **전문은 부록 B·C·D에 그대로 실었다** |
| **전달한 대상** | AMR팀 (컬럼 정의서 + 확인 요청 5건) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §7-1의 1번 — VDI에서 Athena 사전 확인 3종(파티션 존재·prefix 철자·error 페이로드 구조)을 돌려 레이크가 살아 있는지부터 본다 |

---

## 0. 세 줄 요약

1. 로봇(AMR)의 **E09 = "AMR 통신 불량"** 에러가 2026년 1~8월 운영계에서 얼마나·어떤 상황에서 났는지 전수 조사하는 작업이다. 발생 이력·발생 시점의 로봇 상태는 Athena, 설치일·OTA·재부팅 이력은 MySQL에서 뽑는다.
2. **서버 DB(`history_error`)로 발생 건수를 세면 안 된다.** 조사 기간 한가운데인 2026-07-01에 "미연동 기기의 에러는 적재하지 않는다"로 수집 규칙이 바뀌어(커밋 `9c38d807`) 기간 전후의 모집단이 다르다. MQTT 원본이 쌓이는 S3 데이터 레이크(Athena `iot_messages_raw`)가 전 구간 동일 기준의 유일한 소스다.
3. **SQL 팩 2종은 실행 가능한 상태로 완성돼 있고, 실행·분석만 남았다.** 실행은 회사 VDI에서만 가능하다(실고객 시리얼 엑셀·Athena 콘솔·운영 DB 접근이 전부 거기에만 있다). 이 문서의 §3-3~§3-6은 E09와 무관하게 재사용되는 자산이다 — `robotStatus`처럼 RDS에 시계열이 전혀 없는 값을 조회하는 **유일한 경로**가 이 레이크이기 때문이다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **AMR** | Autonomous Mobile Robot. 로봇 하단의 주행 보드. 위쪽 **SoC**(안드로이드 보드)와 **유선(이더넷) UDP**로 통신한다. |
| **SoC** | 로봇에 올라간 안드로이드 보드. 화면·앱·클라우드 통신을 담당한다. AMR 보드로부터 상태를 받아 종합한다. |
| **E09** | 에러 코드. "AMR 통신 불량". SoC가 AMR 보드와의 UDP 통신이 끊겼다고 판단할 때 올린다. 상세 조건은 §3-7. |
| **IotAgent** | SoC에 올라간 안드로이드 앱. MQTT 발행·구독을 전담한다. `packages-mr7` 저장소의 `apps/IotAgent`. |
| **AMRAgent** | SoC에서 AMR 보드와 UDP로 대화하는 안드로이드 앱. `packages-mr7`의 `apps/AMRAgent`. E09를 최초로 올리는 주체다. |
| **IoT Core** | AWS의 MQTT 브로커. 기기가 여기에 메시지를 발행한다. |
| **Firehose → S3 데이터 레이크** | IoT Core로 들어온 **모든 토픽**의 메시지를 원문 그대로 S3에 적재하는 경로. 가공하지 않는다. |
| **Athena** | S3 파일에 SQL을 던지는 서버리스 쿼리 엔진. 스캔한 바이트 수로 과금(약 $5/TB). 엔진 v3는 Trino 기반이다. |
| **`iot_messages_raw`** | 위 S3 원본을 읽는 Athena 테이블. 컬럼은 `payload` 하나(JSON 원문)뿐이다. §3-3. |
| **prefix** | S3 폴더명이자 파티션 키. MQTT 토픽에서 시리얼을 빼고 `/`를 `_`로 바꾼 값. 예: `skmg/airbot/{serial}/v1/status` → `skmg_airbot_v1_status`. |
| **robotStatus** | 로봇의 동작 상태 문자열(`CLEANING`, `ERROR`, `CHARGING` 등). SoC가 판단해 status 토픽에 싣는다. |
| **`history_error`** | 서버 DB의 오류 이력 테이블. 관제 화면·통계의 원천. |
| **실고객 기기** | 계약이 실제로 존재하는 기기. 시연·테스트·사내 기기를 배제하기 위한 필터. 계약확정·해약확정을 모두 포함한다. |
| **VDI** | 회사 가상 데스크톱. AWS 콘솔·운영 DB·사내 문서에 접근할 수 있는 유일한 환경. |

### 1-2. 문제

E09는 로봇의 주행 보드와 제어 보드 사이 통신이 끊기는 현상이고, 발생하면 로봇의 주요 동작이 전부 정지한다(기기 코드에서 E09는 `CRITICAL` 분류다 — §3-7). AMR팀이 원인을 찾으려면 **언제·어떤 기기에서·무엇을 하던 중에** 났는지가 필요했다.

여기서 세 가지가 걸림돌이었다.

1. **DB로 세면 기간 전후 기준이 다르다.** 2026-07-01 커밋 `9c38d807`이 "미연동 기기(`device.linked_yn <> 'Y'`)의 에러는 `history_error`에 적재하지 않는다"를 넣었다. 1~6월에는 미연동 기기 에러가 들어 있고 7월 이후에는 없다. 그대로 월별 추이를 그리면 **수집 규칙 변경이 "E09 감소"로 보인다.**
2. **에러 시점의 로봇 상태가 DB에 없다.** 조사의 핵심인 "무엇을 하다가 났나"를 알려면 `robotStatus` 시계열이 필요한데, RDS 어디에도 없다(§3-6).
3. **집계 주체마다 숫자가 다르다.** 관제 화면 2,031건 vs BI 2,065건. 원인은 조회 SQL의 `INNER JOIN device ... AND dev.linked_yn = 'Y'`에서 연동 해제 기기가 탈락하는 것이다(적재 누락과는 다른 현상). 품질 담당 조직과의 더 큰 차이는 **실고객 계약 필터링 유무**였다.

### 1-3. 조사 목표 (2026-08-25 착수 시 합의)

- 기간: **2026-01-01 ~ 2026-08-31 (KST)**, 대상: **운영계 실고객 기기 전수**(약 4,900대).
- 뽑을 것 4종:
  1. E09 발생 이력 + **발생 시점의 `robotStatus`**
  2. 해당 기기의 **설치 시기**
  3. 해당 기기의 **OTA(펌웨어 업데이트) 이력**
  4. 해당 기기의 **관제 재부팅 명령 이력**
- 확정 결정: 계약확정·해약확정 **전부 포함**하되 `contract_status` 컬럼으로 구분한다. 실고객 시리얼은 **CSV → S3 → Athena 외부 테이블**로 반입한다. Athena 산출물은 여러 개로 쪼개지 않고 **통합 1개**로 만든다.

---

## 2. 현재 상태 (2026-09-22 기준)

### 2-1. 산출물

| 산출물 | 상태 | 이 문서의 위치 |
|---|---|---|
| Athena SQL 팩 (`E09-athena.sql`, 502줄) | **완성.** 외부 테이블 DDL → CTAS 2종 → 통합 산출물 → 검증 쿼리까지 순서대로 실행 가능 | 부록 B (전문) |
| MySQL SQL 팩 (`E09-investigation.sql`, 156줄) | **완성.** 임시테이블 → 설치/OTA/재부팅 3종 | 부록 C (전문) |
| AMR팀 전달 컬럼 정의서 | **완성·전달 완료** | 부록 D (전문) |
| 실제 쿼리 실행 | **미실행** | – |
| 결과 분석·보고 | **미착수** | – |
| AMR팀 회신 (확인 요청 5건) | **미수신** | 질문은 §5-2 |

> Athena SQL 팩은 2026-08-26에 전면 재작성해 요구 산출물만 남겼다. 그 과정에서 초기 버전의 `[0] 사전 확인` 절이 삭제됐고, 파일 안 주석 한 곳(`3-A`)에 `[0-A] 재확인`이라는 **존재하지 않는 절을 가리키는 문구가 남아 있다.** 그 자리에서 실제로 해야 할 일은 §7-1의 1번에 옮겨 적었다.

### 2-2. 조사 대상이 된 서버 동작 변경 (Git 실측)

조사 설계의 전제가 된 커밋들이다. 세 건 모두 **운영계 포함 전 브랜치에 반영돼 있다** — 즉 지금도 그렇게 동작한다.

| 커밋 | 날짜 | 내용 | dev | stg | main(운영) |
|---|---|---|---|---|---|
| `9c38d807` | 2026-07-01 | `ResponseDeviceService.error()` 맨 앞에 `isLinkedDevice(serial)` 가드 추가. 미연동 기기 에러는 `log.warn` 후 **return**(적재 안 함) | 반영 | 반영 | 반영 |
| `166a3e92` | 2026-06-15 | `amr_alert_error_code` 테이블 도입. 등록된 에러코드는 **그날 첫 발생 시 기기 로그 자동 수집** 요청(`history_device_log` → S3) | 반영 | 반영 | 반영 |
| `6ea90488` | 2026-06-15 | 위 알림의 관리자 SMS 발송을 `"prd".equals(profile)` 조건으로 제한 | 반영 | 반영 | 반영 |

**조사에 미치는 영향**

- `9c38d807` → `history_error`는 1~6월과 7~8월의 모집단이 다르다. **발생 건수 집계에서 배제**한다(§3-2).
- `166a3e92` → E09가 `amr_alert_error_code`에 등록돼 있다면 하루 첫 발생 건에 한해 기기 로그가 S3에 있다. 다만 **운영 반영이 6/15부터라 1~6월 구간엔 없다.** 등록 여부 자체는 운영 DB에서 `SELECT * FROM amr_alert_error_code;`로 확인해야 한다(미확인).

### 2-3. 실행 환경

| 항목 | 상태 |
|---|---|
| 로컬 맥에서 실행 | **불가.** `aws` CLI도 `mysql` 클라이언트도 설치돼 있지 않고, AWS 콘솔 접근 자체가 막혀 있다 |
| VDI에서 실행 | 가능. Athena 콘솔 + DataGrip(운영 DB) |
| 실고객 시리얼 엑셀 | **VDI에만 존재.** 약 4,900행, 컬럼 2개(제품 시리얼 번호 / 계약상태). 2026-08-26 기준본 |
| 운영계 Athena `iot_messages_raw` 테이블 존재 여부 | **미확인.** 2026-07-07 시점 기록으로는 dev·stg만 생성돼 있고 운영계는 "다음 배포 때 일괄 반영" 상태였다. SQL 팩은 운영계 버킷을 대상으로 쓰여 있으므로 그 사이 만들어졌다고 보는 것이 자연스럽지만 실물 확인이 필요하다. 확인 방법은 §7-1의 1번 |
| S3 버킷 | 운영계 `s3://skmg-benjamin-prd-s3-lake/`. 개발계는 이름 체계가 다르다(`airbot-s3-lake-dev`) — 환경 간에 버킷 이름을 유추하지 말고 `aws s3 ls`로 확인할 것 |

### 2-4. AMR팀

| 항목 | 상태 |
|---|---|
| 컬럼 정의서 전달 | 완료 |
| AMR 상태 코드표 수령 | 완료 (2026-08-25). 부록 B의 `amr_state_code` / `amr_action_code` CTE에 그대로 박아 넣었다 |
| 확인 요청 5건 회신 | **미수신.** 다만 그중 4번(E09 판정 조건)은 이후 기기 소스 실측으로 답을 얻었다 — §3-7 |
| 실제 데이터 전달 | **미전달** (쿼리를 아직 안 돌렸다) |

---

## 3. 조사 설계와 데이터 경로

### 3-1. 전체 흐름

```
[로봇]
  AMR 보드 ──UDP(localhost:10000)── SoC(AMRAgent)
                                       │ 20초 무수신 → E09 "occurred"
                                       ↓
                                  SoC(IotAgent)
                                       │ MQTT QoS1 발행
        ┌──────────────────────────────┴──────────────────────────────┐
        │ skmg/airbot/{serial}/v1/error   (에러 발생 시 1건)            │
        │ skmg/airbot/{serial}/v1/status  (60초 주기 + 이벤트 시 즉시)  │
        └──────────────────────────────┬──────────────────────────────┘
                                       ↓
                                  AWS IoT Core
                    ┌──────────────────┴──────────────────┐
                    ↓                                     ↓
        Kinesis → Firehose → S3 레이크            WAS 직접 구독(MQTT)
        s3://skmg-benjamin-prd-s3-lake/            backend-api-main
          {prefix}/year=/month=/day=/hour=/          ResponseDeviceService.error()
          NDJSON, 전 토픽 무손실                       │
                    ↓                                  │ 미연동 기기면 여기서 return
        Athena  iot_messages_raw                       ↓
          payload(JSON 원문) 1컬럼                  MySQL history_error
                    │                                  │
                    │ ① E09 발생시각 + robotStatus      │ ② 발생 건수용으로는 쓰지 않음
                    ↓                                  ↓
        ┌───────────────────────────┐    ┌──────────────────────────────┐
        │ 파일1 E09_발생이력_및_상태 │    │ 파일2 설치시기 (device·contract)│
        └───────────────────────────┘    │ 파일3 OTA (firmware_job*)      │
                    │                     │ 파일4 재부팅 (history_control) │
                    │  시리얼 목록 전달 →  └──────────────────────────────┘
                    └──────────────────────────────┘
                                       ↓
                                  AMR팀 (CSV 4종 + 컬럼 정의서)
```

두 파이프라인이 **같은 MQTT 메시지에서 갈라진다**는 점이 이 조사의 핵심이다. 왼쪽(레이크)은 무조건 전부 쌓고, 오른쪽(WAS→DB)은 업무 규칙으로 걸러진다. 규칙이 바뀌면 오른쪽만 바뀐다.

### 3-2. 왜 DB가 아니라 Athena인가

| 소스 | 1~6월 | 7~8월 | 판정 |
|---|---|---|---|
| `history_error` (MySQL) | 미연동 기기 에러 **포함** | 미연동 기기 에러 **제외** | 기간 안에서 모집단이 바뀜 → **추이 분석 불가** |
| `iot_messages_raw` (Athena) | 전부 | 전부 | 전 구간 동일 → **유일하게 일관** |

반대로 **MQTT 페이로드에 아예 없어서 DB로만 알 수 있는 것**도 있다. 그래서 두 팩으로 나뉜 것이다.

| DB로만 알 수 있는 것 | 이유 |
|---|---|
| 설치일 | 사람이 입력하는 값 |
| OTA job 메타(누가·어떤 버전을·언제 걸었나) | 서버가 생성하는 값 |
| 재부팅 명령의 **조작자**(`login_id`) | 기기는 누가 눌렀는지 모른다 |

한 가지 더. 관제 통계 쿼리(`ErrorStatistics.xml`)는 `history_error he`를 12곳에서 조회하는데 **`he.delete_yn`을 단 한 번도 거르지 않는다**(실측: 해당 파일에 `he.delete_yn` 0건). 삭제 표시된 행이 통계에 섞인다는 뜻이다. 이번 조사는 Athena를 쓰므로 영향이 없지만, 관제 숫자와 대조할 때 차이 요인으로 기억해 둘 것. (별건 결함이며 이번 범위 밖이다.)

### 3-3. MQTT 원본 데이터 레이크 — Athena 테이블 정의

**저장소 코드를 아무리 읽어도 이 경로는 나오지 않는다.** `benjamin-lambda`에는 Kinesis→Lambda(Redis 캐시·RDS 적재·SQS 라우팅)만 있고, Firehose→S3는 IoT 룰과 Firehose 설정(AWS 콘솔)에만 존재한다. 코드만 보고 "원본 아카이브가 없다"고 결론 내기 쉬우니 주의할 것.

**테이블 정의**

| 항목 | 값 |
|---|---|
| 테이블명 | `iot_messages_raw` |
| 컬럼 | `payload` (string) — **단 하나.** MQTT 메시지 JSON 원문이 통째로 들어 있다 |
| 파티션 | `prefix` (string, **projection type = injected**), `year`, `month`, `day`, `hour` (int, projection enabled) |
| 파티션 자릿수 | `month`/`day`/`hour`는 **digits=2** (`month=08` 형식) |
| LOCATION | `s3://skmg-benjamin-prd-s3-lake/${prefix}/year=${year}/month=${month}/day=${day}/hour=${hour}/` |
| 파일 형식 | NDJSON (줄 단위 JSON), Firehose가 적재 |

**`prefix` 명명 규칙** — MQTT 토픽에서 세 번째 세그먼트(시리얼)를 빼고 `/`를 `_`로 바꾼다.

| 토픽 | prefix |
|---|---|
| `skmg/airbot/{serial}/v1/status` | `skmg_airbot_v1_status` |
| `skmg/airbot/{serial}/v1/error` | `skmg_airbot_v1_error` |
| `skmg/airbot/{serial}/v1/heartbeat` | `skmg_airbot_v1_heartbeat` |
| `skmg/airbot/{serial}/v1/air_quality` | `skmg_airbot_v1_air_quality` |
| `skmg/security/{serial}/v1/event` | `skmg_security_v1_event` |
| `$aws/events/jobExecution/{jobId}/{status}` | `aws_events_jobexecution` (리터럴 고정) |
| `$aws/things/{thing}/jobs/#` | `aws_things_jobs` (리터럴 고정) |

> `$aws/...` 계열은 토픽 구조가 다르므로 위의 일반 규칙을 쓰지 않고 IoT 룰에서 리터럴로 박았다. jobId를 prefix에 넣으면 폴더가 무한 생성된다.

**★ `prefix`가 injected라는 것의 의미** — 이게 이 테이블의 가장 큰 함정이다.

- `WHERE prefix = '...'`를 **반드시 써야 한다.** 빼면 파티션을 특정할 수 없어 쿼리가 실패한다.
- `SELECT DISTINCT prefix FROM iot_messages_raw`는 **동작하지 않는다.** injected 파티션은 값을 나열할 수 없다.
- 어떤 prefix가 있는지 알려면 S3를 직접 본다: `aws s3 ls s3://skmg-benjamin-prd-s3-lake/`
- 반대로 이 성질 덕분에 안전한 면도 있다. 분석용 파일을 `_analysis/` 아래에 두면 `iot_messages_raw`는 그 폴더를 **절대 읽지 않는다**(WHERE에 명시한 prefix만 스캔). 원본 테이블을 오염시킬 걱정 없이 같은 버킷을 작업 공간으로 쓸 수 있다.

**규모와 비용 감각** (2026-08 실측)

| 항목 | 값 |
|---|---|
| status 토픽 1시간 | 약 12만 행 / 스캔 11.4MB |
| status 토픽 8개월 전량 | 약 66GB → **약 $0.33** |
| error 토픽 | status 대비 무시할 수준. 8개월 풀스캔해도 가볍다 |
| heartbeat 토픽 | 전체 메시지의 약 58%. **실수로 스캔하면 가장 비싸다** |

비용 설계상 부록 B는 error 토픽을 먼저 훑어 **E09가 난 기기 목록을 확정한 뒤**, status는 그 기기로만 좁혀 Parquet 슬림 테이블(`e09_status_slim`)로 떨군다. 이후 조인은 전부 이 슬림 테이블에서 돈다.

**Firehose 버퍼** — 최근 1~5분 데이터는 아직 S3에 없다. 실시간 확인은 IoT Core 콘솔의 MQTT 테스트 클라이언트로 한다.

### 3-4. 시간대 — 한 메시지 안에 두 개가 들어 있다

이 절을 건너뛰면 결과가 **틀린 채로 그럴듯하게** 나온다. 9시간 어긋난 값도 날짜·시간 형식이 멀쩡해서 눈으로는 안 잡힌다.

| 값 | 생성 코드 | 시간대 |
|---|---|---|
| 페이로드 최상위 `timestamp` | `IotAgent`의 `Util.getTime()` = `Instant.now().atZone(ZoneOffset.UTC).truncatedTo(SECONDS)` | **UTC** |
| error 페이로드의 `error.errorDate` | `Util.errorTime()` = `LocalDateTime.now()` (기기 로컬) | **KST** |
| `meta_ingest_ts` | IoT 룰의 `timestamp()` = 브로커 수신 시각 epoch ms | **UTC** |
| MySQL `benjamin` 스키마의 모든 이력 컬럼 | DB `NOW()` / `CURRENT_TIMESTAMP` | **KST** |

**증거 — 실제 페이로드 한 건** (2026-08-26 실측)

```json
{"topic":"skmg/airbot/{serial}/v1/error",
 "prefix":"skmg_airbot_v1_error",
 "meta_ingest_ts":1787609254034,
 "meta_client_id":"...", "meta_source_ip":"...", "meta_principal":"...",
 "serial":"...",
 "timestamp":"2026-08-24T22:07:32Z",
 "error":{"errorCode":"S05-2","errorDate":"2026-08-25 07:07:32"}}
```

같은 메시지 안에서 `22:07:32Z`(UTC)와 `07:07:32`(KST)가 **정확히 +9시간** 차이다. 이 한 건이 두 필드의 시간대를 동시에 증명한다.

MySQL 쪽이 KST라는 것도 코드로 확인된다. RDS 적재 람다(`benjamin-lambda/RDS/historyHandler.mjs`)의 `formatTimestampForMysql`이 페이로드 `timestamp`에 **+9시간을 더해** 저장한다.

```js
const kstOffsetMs = 9 * 60 * 60 * 1000;
const kstDate = new Date(parsed.getTime() + kstOffsetMs);
```

> 참고: `skix-security`·`skix-medcare`·`skix-openapi` 세 저장소는 2026년 8월에 DB 시각을 UTC로 통일했지만, **`backend-api-main`의 `benjamin` 스키마는 그 대상이 아니었다.** 여기 이력 컬럼은 여전히 전부 KST다. "우리 DB는 UTC로 바꿨잖아"라고 기억하고 있으면 정확히 9시간 틀린다.

**보정 규칙 — 조인은 UTC끼리, 변환은 출력에서만**

```sql
-- ① Athena 내부 조인: 양쪽 다 payload.timestamp(UTC) → 보정 없음
LEFT JOIN e09_status_slim s
       ON s.serial = e.serial
      AND s.ts_utc <= e.err_ts_utc
      AND s.ts_utc >  e.err_ts_utc - INTERVAL '15' MINUTE

-- ② 사람이 볼 컬럼으로 내보낼 때만 +9h
SELECT a.err_ts_utc + INTERVAL '9' HOUR AS error_at_kst

-- ③ KST 기준 기간을 UTC 필터로 환산 (2026-01-01 00:00 KST ~ 2026-09-01 00:00 KST)
WHERE ev.err_ts_utc >= TIMESTAMP '2025-12-31 15:00:00'
  AND ev.err_ts_utc <  TIMESTAMP '2026-08-31 15:00:00'

-- ④ MySQL 결과(KST)와 Athena 결과를 붙일 때: Athena 쪽에 +9h 하면 같은 기준이 된다
```

**파티션은 KST 기준이다 (실측 확정)** — 위 샘플의 UTC `22:07`(= KST 익일 `07:07`)이 `day=25` 파티션에 들어 있다. Firehose의 CustomTimeZone이 KST로 설정돼 있다는 뜻이다. 따라서 KST 2026-01-01~08-31은 `year=2026 AND month BETWEEN 1 AND 8`로 **정확히** 대응된다.

> 이 확정 이전에는 "파티션이 UTC인지 KST인지 모르니 넉넉히 잡고 `payload.timestamp`로 정밀 필터링하라"가 방침이었다. 지금도 **새로운 prefix를 처음 다룰 때는 그 방식이 안전하다** — Firehose 설정은 스트림마다 다를 수 있다.

### 3-5. Athena 쿼리 패턴과 함정

실행 중 실제로 부딪혀서 고친 것들이다. 전부 부록 B에 반영돼 있다.

| # | 함정 | 대응 |
|---|---|---|
| 1 | `prefix`를 WHERE에 안 쓰면 쿼리 실패. `DISTINCT prefix` 불가 | 항상 명시. 목록은 `aws s3 ls`로 |
| 2 | `from_iso8601_timestamp()`는 `timestamp with time zone` 타입이라 **CTAS에 못 쓴다** | `date_parse(substr(ts, 1, 19), '%Y-%m-%dT%H:%i:%s')`로 앞 19자만 파싱. `CAST`는 **세션 타임존에 의존하므로 금지** |
| 3 | CTAS에 `external_location` 필수 (Managed Query Results 워크그룹) | 전부 `s3://.../_analysis/<테이블명>/`에 지정 |
| 4 | `DROP TABLE`만으로는 S3 파일이 안 지워져 재실행 시 이전 데이터가 섞인다 | `aws s3 rm s3://.../_analysis/<테이블명>/ --recursive` 병행 |
| 5 | 조인에서 `s.*`를 쓰면 `serial` 중복으로 `AMBIGUOUS_NAME` | 컬럼을 전부 명시 |
| 6 | JSON 필드 접근 | `json_extract_scalar(payload, '$.operation.robotStatus')`. **전문상 대문자인 필드에 주의** (`operation.CPUTemperature`, `operation.WifiRssi`) |
| 7 | error 페이로드의 최상위 키는 `data`가 아니라 **`error`** | `$.error.errorCode`, `$.error.errorDate` |
| 8 | 서버 코드가 읽는 `errorCondition.stationPower`는 **실제 페이로드에 없다** | 사용하지 않는다 |
| 9 | `meta_*` 필드는 파이프라인에 도중 추가된 것이라 **초기 구간(1월 등)에는 없다** | NULL을 다른 의미로 흘려보내지 말 것. 부록 B의 2-A-3은 `ingest_gap_ms IS NULL`을 별도 분류 `X. 판별불가`로 뺀다 |
| 10 | 원본 파일 경로가 필요하면 | Trino 숨김 컬럼 `"$path"` |

**기본 조회 패턴** — 특정 기기의 status 이력을 KST 하루치 보는 최소 예시. 이 문서를 인수한 사람이 E09와 무관하게 가장 자주 쓸 형태다.

```sql
SELECT json_extract_scalar(payload, '$.serial')                            AS serial,
       date_parse(substr(json_extract_scalar(payload,'$.timestamp'),1,19),
                  '%Y-%m-%dT%H:%i:%s') + INTERVAL '9' HOUR                 AS ts_kst,
       json_extract_scalar(payload, '$.operation.robotStatus')             AS robot_status,
       json_extract_scalar(payload, '$.operation.errorCode')               AS error_code,
       json_extract_scalar(payload, '$.battery.chargeRate')                AS battery
FROM iot_messages_raw
WHERE prefix = 'skmg_airbot_v1_status'      -- 필수
  AND year = 2026 AND month = 8 AND day = 24   -- 파티션은 KST
  AND json_extract_scalar(payload, '$.serial') = '<시리얼>'
ORDER BY ts_kst;
```

파티션 3개(`year`/`month`/`day`)를 다 박으면 하루치 한 기기 조회는 스캔이 수백 MB 수준이다. **`day`를 빼면 그 달 전체를 스캔한다** — 12만 행/시간짜리 토픽에서 이건 실수로 하기 쉽고 비싸다.

### 3-6. robotStatus 이력은 여기에만 있다

이 레이크를 써야만 하는 대표 사례다. 조사 착수 전에 RDS를 전수로 뒤졌고, 결론은 **`robotStatus`의 시계열이 어디에도 없다**였다.

| 후보 | 왜 안 되는가 | 근거 |
|---|---|---|
| Redis `operation:{serial}` | 덮어쓰기. 현재값만 | 캐시 람다가 매 status마다 갱신 |
| `statistics_operation` | `UNIQUE KEY (device_id)` — **기기당 1행**, 현재값 | DDL 실측 |
| `history_status` | `status_type` 기준으로 **FOLLOW_ME 시작/종료만** 기록 | DDL 주석 "로봇 동작 시 한 동작의 시작/종료 시간", 운영계 AUTO_INCREMENT 기준 약 8,800행 |
| error 페이로드 | `robotStatus` 필드 자체가 없다 | 페이로드 구조 실측 |
| `history_error` | 에러만 남고 상태는 안 남는다 | DDL |

→ **`skmg_airbot_v1_status` 원본에서 시각 기준 ASOF 조인하는 것이 유일한 실측 경로다.** 같은 이유로 "그때 배터리가 몇 %였나", "그때 CPU 온도가 몇 도였나" 같은 질문도 전부 이 레이크로 간다.

### 3-7. E09는 언제 올라오는가 (기기 소스 실측)

컬럼 정의서에서 AMR팀에 물어본 5개 질문 중 4번("몇 초간 무응답이면 E09로 올리는지")은 **회신을 기다릴 필요 없이 기기 저장소 `packages-mr7`에서 확인됐다.** 아래는 전부 코드 실측이며, 인수자가 데이터를 해석할 때 바로 쓰인다.

**E09를 올리는 곳은 두 군데다.**

| 경로 | 파일 | 조건 |
|---|---|---|
| ① UDP 소켓 타임아웃 | `apps/AMRAgent/java/com/sk/airbot/amragent/RecvThread.java` (`reportSocketTimeout`) | AMR 보드로부터의 UDP 수신이 **소켓 타임아웃**에 걸릴 때 |
| ② 부팅 시 이더넷 미동작 | `apps/AMRAgent/java/com/sk/airbot/amragent/receiver/BootReceiver.java` (`reportErrorEthernetIsNotWorking`) | 부팅 후 **330초**(`LIMIT_ETHERNET_CHECKTIME = 330_000`) 안에 `eth0`이 올라오지 않을 때 |

**타임아웃 값** — `SockSender`의 기본 `soTimeout = 20000` (20초). 즉 **AMR 보드로부터 20초간 아무 UDP 패킷도 못 받으면 E09**다. 단 리커버리 모드 진입 알림(notiCode 128)을 받으면 `MsgDef.TWO_MINS`(120초)로 늘렸다가 해제 알림(144)에서 다시 `TWENTY_SECS`로 되돌린다.

**억제 조건** — `AMRState.isOTAUpdating()` 또는 `isRecoveryMode()`인 동안에는 타임아웃이 나도 **E09를 올리지 않는다.** OTA 중 무응답은 정상이기 때문이다.

**래치(1회 발행) 구조** — `mSentSockTimeoutError` 플래그로 눌러둔다.

```java
// 정상 수신 시
if (mSentSockTimeoutError) { reportSocketTimeout(); mSentSockTimeoutError = false; }  // status=2 (resolved)
...
// 타임아웃 시
if (mSentSockTimeoutError == false) { reportSocketTimeout(); mSentSockTimeoutError = true; }  // status=1 (occurred)
```

**★ 해제(resolved)는 MQTT로 발행되지 않는다.** `IotAgent`가 SoC 내부 에러 이벤트를 받아 처리하는 지점에서, 해제 이벤트는 로봇 상태 플래그만 내리고 **`publishError`를 타지 않는다.**

```java
if ("true".equals(isErrorResolved)) {
    mRobotStatus_error = false;
} else {
    mRobotStatus_error = true;
    mEtcTopic.setEtcErrorTopic(code);
    PublishHelper.getInstance().publishError(data);   // ← 발생일 때만
    idleTime();
}
sendStatus();
```

인수자에게 중요한 결론 세 가지.

1. **error 토픽의 E09 1건 = 통신 두절 1회 시작이다.** 해제 메시지가 섞여 들어와 2배로 세지는 일은 없다.
2. **E09 지속 시간은 error 토픽만으로 구할 수 없다.** 필요하면 status 토픽에서 `robotStatus`가 `ERROR`를 벗어나는 시점을 찾아야 한다.
3. **에러 발행 직후 같은 핸들러에서 `sendStatus()`가 호출된다.** 그래서 에러와 **같은 초**의 status 메시지가 자주 존재하고, 그게 `rs_at_error = 'ERROR'`로 나오는 구조적 이유다. 부록 B의 4-A 검증 쿼리에서 `avg_age_sec`이 공칭 주기 60초보다 훨씬 짧은 13초 안팎으로 나오는 것도 같은 원인이다.

**E09의 심각도** — `IotAgent`의 `ErrorCode` enum에서 `E09("E09", 3, true)`이며(우선순위 3, 알림 대상 true), `DeviceAgent`의 `ErrorHandler`에서 `CRITICAL` 분류에 속해 발생 시 모든 동작이 정지한다.

반면 서버 DB `history_error.error_level`에 실제로 적재된 값은 **`Code 2`**였다(운영 실측 기록). 이 값은 계산값이 아니라 적재 시점에 `error_code` 테이블을 `(error_code, model_id)`로 조회한 **스냅샷**이라, **같은 E09라도 모델에 따라 `Code 2`/`Code 4`로 갈린다.** 관제 통계 화면은 여기서 한 번 더 갈라진다 — `ErrorStatistics.xml`은 `history_error`에 저장된 스냅샷을 쓰지 않고 `error_code`를 **조회 시점에 다시 조인**한다. 즉 코드 마스터를 나중에 고치면 과거 통계 숫자가 움직인다.

### 3-8. 산출물 4종

**파일 1 — `E09_발생이력_및_상태.csv` (Athena)**. E09 1건 = 1행.

상태를 **두 벌** 붙이는 것이 이 산출물의 설계 핵심이다.

| 컬럼군 | 창 | 무엇을 답하는가 |
|---|---|---|
| `rs_at_error` 계열 | 에러 직전 **15분** 내 마지막 status | "어떤 에러 상태였나" |
| `rs_before_error` 계열 | 에러 직전 **60분** 내 마지막 **비-ERROR** status | "무엇을 하다가 났나" ← 원인 분석용 |

E09가 나는 순간 `robotStatus`가 `ERROR`로 바뀌기 때문에, 에러 시점 값만 보면 전부 "ERROR였다"밖에 안 나온다. 그래서 `ERROR`가 아니었던 마지막 상태를 따로 뽑는다. 창을 60분으로 넓힌 것은 `ERROR` 상태가 오래 지속될 수 있어서다. 대신 `rs_before_age_sec`이 크면 **이전 에러의 잔상**이므로 해석에서 빼야 한다.

주요 컬럼 (전체 정의는 부록 D):

| 컬럼 | 의미 |
|---|---|
| `serial` / `contract_status` | 시리얼 / 계약확정·해약확정 |
| `error_at_kst` | **E09 발생 시각(KST)**. 페이로드 `timestamp`(UTC) + 9h |
| `device_reported_kst` | 기기가 페이로드에 직접 적어 보낸 시각(`error.errorDate`). `error_at_kst`와 크게 어긋나면 **그 기기 시계가 틀어진 것** |
| `mqtt_msg_cnt` | 같은 `(serial, timestamp)`로 접힌 원본 MQTT 메시지 수. 1이면 정상 |
| `rs_at_error` / `error_code_at_error` / `rs_age_sec` | 에러 시점 상태·에러코드·그 값이 몇 초 전인지 |
| `amr_state_at_error` / `amr_action_at_error` | AMR 보드가 보고한 상태·액션(코드→라벨 변환) |
| `rs_before_error` / `rs_before_age_sec` / `clean_type_before` | ERROR 직전 상태 |
| `amr_state_before` / `amr_action_before` | ERROR 직전 AMR 상태·액션 |
| `is_charging` / `is_docked` / `is_llm` / `is_vital_sign` | 환경 플래그 (가설 검증용) |
| `cpu_temperature` / `wifi_rssi` / `battery_charge_rate` | 환경 수치 |

**빈 값은 "데이터 없음"이 아니라 그 자체가 관측 결과다.** 이걸 결측으로 취급해 버리면 조사의 알맹이가 빠진다.

| 어디가 비었나 | 의미 |
|---|---|
| `rs_at_error` 계열 전체 | E09 직전 15분간 **status가 한 건도 안 왔다** → 기기가 상태 송신까지 멈춘 상태. 그 자체가 분석 대상 |
| `amr_state_*`만 빔 | status는 왔는데 그 안에 `amrStatus`가 없었다 → **SoC가 AMR 값을 못 채운 상태** |
| `rs_before_error` | 직전 60분 내내 `ERROR`였다 → 이전 에러에서 회복되지 않은 상태 |

`wifi_rssi`는 **배제용으로 일부러 넣었다.** AMR–SoC는 유선 통신이므로 Wi-Fi 감도와 무관해야 정상이고, 상관이 보인다면 우리가 모르는 다른 요인이 있다는 신호다.

**파일 2·3·4 (MySQL)** — 설치시기 / OTA 이력 / 재부팅 이력. 컬럼과 주의사항은 부록 D에 그대로 있고, SQL은 부록 C다. 각 파일의 함정 한 줄씩:

- **파일 2 (설치시기)**: 재등록·기기 교체로 **한 시리얼에 `device` 행이 여러 개**일 수 있어 전부 나열한다. `linked_yn`으로 거르지 않는다 — 연동 해제된 기기의 설치 이력도 필요하다.
- **파일 3 (OTA)**: `status = SUCCEEDED`는 **서버가 IoT Job 실행 이벤트를 받아 갱신한 값**이지 기기 반영 완료의 보장이 아니다. 실제 적용은 status 토픽의 펌웨어 버전 변화로 교차검증해야 한다.
- **파일 4 (재부팅)**: `result = 1` / `message = 'reboot successfully'`는 **명령 수신 응답**이지 재기동 성공이 아니다. 또한 앱·운영 경로(`/device/request/operation/reboot`)와 관제 경로(`/control/robot/reboot`)가 **같은 `op_command = 'OPER0002'`를 남기므로** 주체 구분은 `login_id`로만 가능하다. 그래서 부록 C에 계정 분포를 먼저 보는 4-A 쿼리를 넣었다.

**관련 테이블 구조** (DDL 실측)

| 테이블 | 조사에 쓰는 이유 | 주의 |
|---|---|---|
| `history_error` | (건수 집계에는 안 씀) | `error_level`은 적재 시점 스냅샷. `delete_yn` 있음 |
| `device` / `contract` / `model` / `firmware` | 설치일·모델·계약·등록 펌웨어 | `serial`은 `utf8mb4_0900_ai_ci` |
| `firmware_job` / `firmware_job_target` | OTA. `firmware_job.type` `01`=유저 `02`=관제 | `firmware_job.delete_yn` 확인 |
| `history_control` | 재부팅 명령. `op_command='OPER0002'`는 재부팅 전용 | `is_pui`로 기기 화면 조작 구분 |
| `amr_alert_error_code` | 등록 코드는 그날 첫 발생 시 기기 로그 자동 수집 | 2026-06-15 이후만 |
| `history_device_log` | 위에서 수집된 로그의 S3 경로 | 1~6월 구간 없음 |

`firmware_job` 매퍼에는 **호출부가 없는 결함 SQL**이 하나 있다(`updateJob` — `firmware_job_id`(bigint)를 `job_id`(varchar)와 비교해 사실상 매칭되지 않는다). 라이브 갱신 경로는 `updateStatusByJobIdAndTargetId`이고 이쪽은 정상이다. OTA 상태가 이상해 보일 때 엉뚱한 SQL을 의심하지 않도록 적어 둔다.

---

## 4. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 결과가 조용히 틀리는" 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **발생 건수·추이는 Athena로만 센다. `history_error`로 세지 않는다** | 2026-07-01 이후 미연동 기기 에러가 빠져 있어, 수집 규칙 변경이 "7월부터 E09가 줄었다"는 **가짜 개선 신호**로 보고된다. 반대로 설치일·OTA·재부팅은 MQTT에 없으므로 DB가 유일한 소스다. 두 팩을 합치려는 시도가 이 구분을 지운다 |
| 2 | **조인은 UTC끼리, `+9h`는 출력 컬럼에서만** | 한쪽만 보정하면 9시간 어긋난 status가 붙는데, 값이 형식상 멀쩡해 **에러가 안 나고 결과만 틀린다.** 60분 창 안에서 9시간을 틀리면 전부 결측이 되거나 남의 이벤트가 붙는다 |
| 3 | **`CAST`가 아니라 `date_parse(substr(ts,1,19), ...)`로 파싱** | `from_iso8601_timestamp()`는 `timestamp with time zone`이라 CTAS 자체가 실패하고, `CAST`는 **세션 타임존에 의존**해 실행하는 사람에 따라 결과가 달라진다. 재현 불가능한 산출물이 된다 |
| 4 | **error 중복은 `(serial, err_ts_utc)`로 접되 `dup_cnt`를 남긴다** | MQTT는 QoS1(at-least-once)이고 `timestamp`는 초 단위로 잘려 있어(`truncatedTo(SECONDS)`) 재전송분이 원본과 **완전히 같은 값**이 된다. 안 접으면 전송 계층 중복이 에러 2건으로 보고된다. 반대로 접기만 하고 `dup_cnt`를 버리면 다른 집계와 숫자가 안 맞을 때 원인을 못 찾는다 |
| 5 | **상태를 두 벌(`rs_at_error` 15분 / `rs_before_error` 60분 비-ERROR) 붙인다** | 하나로 줄이면 전 행이 `ERROR`로 채워져 "무엇을 하다 났나"를 영영 알 수 없다. 이 조사의 목적 자체가 사라진다 |
| 6 | **실고객 CSV는 헤더 1행을 반드시 넣어 올린다** | 외부 테이블이 `skip.header.line.count = 1`이라 헤더가 없으면 **첫 시리얼 1건이 통째로 사라진다.** 게다가 엑셀 "CSV UTF-8"이 붙이는 BOM이 헤더 행에 딸려가 함께 버려지는데, 헤더가 없으면 BOM이 첫 시리얼 앞에 붙어 **조인이 조용히 실패한다.** 부록 B의 1-A-2가 이 두 가지를 동시에 점검한다 |
| 7 | **MySQL 임시테이블 `serial`에 `CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci`를 명시한다** | 생략하면 DB 기본값 `utf8mb4_general_ci`가 붙는데 `device`·`history_control`·`firmware_job_target`의 `serial`은 `utf8mb4_0900_ai_ci`라 조인에서 `ERROR 1267 (Illegal mix of collations)`이 난다. 이건 시끄럽게 실패하므로 그나마 낫다 |
| 8 | **분석 산출물은 `_analysis/` prefix 아래에만 만든다** | `iot_messages_raw`는 WHERE에 명시한 prefix만 스캔하므로 원본 테이블에 영향이 없다. 원본 토픽 prefix 아래에 CTAS 결과를 쓰면 **원본 조회 결과에 우리가 만든 행이 섞인다** |
| 9 | **파티션이 KST라는 것은 실측으로 확정한 값이고, 새 prefix에는 그대로 가정하지 않는다** | Firehose CustomTimeZone은 스트림마다 설정이 다를 수 있다. 확인 전에는 파티션을 넉넉히 잡고 `payload.timestamp`로 정밀 필터링한다 |

---

## 5. AMR팀에 전달한 내용

컬럼 정의서 전문은 부록 D다. 여기에는 인수자가 문의를 받았을 때 바로 답해야 할 핵심만 옮긴다.

### 5-1. 전달한 것

- CSV 4종 + 컬럼 정의서 1부. (CSV는 쿼리를 아직 안 돌려서 **미전달**, 정의서만 먼저 나갔다.)
- AMR 상태 코드표는 **AMR팀이 준 정의(2026-08-25)를 그대로** 부록 B의 CTE에 박았다. 코드표가 개정되면 그 CTE 한 곳만 고치면 4개 컬럼(`amr_state_at_error`, `amr_action_at_error`, `amr_state_before`, `amr_action_before`)이 함께 갱신된다.

| `amrStatus.robotStatus` | 라벨 | | `amrStatus.actionStatus` | 라벨 |
|---|---|---|---|---|
| 0 | IDLE | | 0 | VOID |
| 1 | AUTO_MAPPING | | 1 | READY |
| 2 | MANUAL_MAPPING | | 2 | START (RUN) |
| 3 | NAVIGATION | | 3 | PAUSE |
| 4 | RETURN_CHARGER | | 4 | RESUME |
| 5 | DOCKING | | 5 | COMPLETE |
| 6 | UNDOCKING | | 6 | **FAIL** |
| 7 | ONSTATION | | | |
| 8 | FACTORY_NAVIGATION | | | |
| 9 | ERROR | | | |
| 10 | FOLLOW_ME | | | |
| 11 | MANUAL_CONTROL | | | |

표에 없는 값은 숫자 그대로 출력된다(`COALESCE(label, 원값)`).

**`amr_*`와 `rs_*`는 서로 다른 계층이다.** `rs_*`(= `operation.robotStatus`)는 SoC가 판단한 상태, `amr_*`(= `amrStatus.*`)는 AMR 보드가 UDP로 올려준 값이다. **두 값이 어긋나는 지점**이 이번 조사의 관심사다.

### 5-2. 확인 요청 5건 (회신 미수신)

| # | 질문 | 상태 |
|---|---|---|
| 1 | `amr_state_at_error`가 비었거나 `amr_state_before`와 동일하게 고정된 경우 — AMR 보드가 응답을 멈춘 것으로 봐도 되는지, 아니면 SoC가 값을 못 채우는 다른 경로가 있는지 | 대기 |
| 2 | `amr_action_before`가 `FAIL`인 경우 — AMR이 실패를 *보고한* 것이므로 통신 자체는 살아 있었다고 봐야 하는지 | 대기 |
| 3 | `rs_before_error`가 특정 상태(`CLEANING`/`NAVIGATION` 등)에 쏠릴 경우 — 해당 동작 중 AMR 통신이 취약해지는 알려진 원인이 있는지 | 대기 |
| 4 | E09 판정 조건 — 몇 초 무응답이면 올리는지 | **기기 소스로 자답 완료** (§3-7: UDP 20초, 리커버리 중 120초, 부팅 이더넷 330초, OTA·리커버리 중 억제). 이 값으로 `rs_age_sec` 분포를 검증할 수 있다 |
| 5 | 특정 펌웨어 버전과의 상관 — 버전별 발생 빈도가 갈리는지. 필요하면 우리 쪽에서 파일 1과 파일 3을 결합해 준다 | 대기 |

### 5-3. 전달 시 함께 밝힌 한계

| 항목 | 내용 |
|---|---|
| 시간 해상도 | status가 60초 주기라 에러 **순간**이 아니라 최대 60초 전 상태다. 각 행의 `rs_age_sec`으로 신뢰도를 판단해야 한다 |
| 창 밖은 빈 값 | `rs_at_error` 15분, `rs_before_error` 60분 |
| 중복 | 같은 기기가 시간차를 두고 반복 발생시키면 그대로 여러 행이다(제거하지 않음). **동일 시각(초 단위) 중복 수신분만** 1행으로 접고 `mqtt_msg_cnt`에 남겼다 |
| 대상 | 실고객 계약 기기만. 계약확정·해약확정 모두 포함하며 `contract_status`로 구분 |
| 관제 화면과의 건수 차이 | 이 자료는 MQTT 원본 기준이며, 서버 DB의 수집 범위 변경·조회 조인과 무관하게 1~8월 전 구간 동일 기준으로 뽑았다 |
| 파일 2~4 | 서버 DB 기준이라 연동 해제·계약 종료로 정리된 기기는 일부 항목이 비어 있을 수 있다 |

---

## 6. 실행 방법

로컬 맥에서는 한 줄도 돌릴 수 없다. **전부 VDI에서 한다.**

### 6-1. 순서 (Athena 먼저, MySQL 나중)

```
1. [VDI] 실고객 시리얼 엑셀 → CSV 변환 → S3 업로드        (§6-2)
2. [Athena] 부록 B [1]  외부 테이블 생성 + 검증 1-A / 1-A-2 / 1-B
3. [Athena] 부록 B [2]  e09_events_rc CTAS + 검증 2-A
4. [Athena] 부록 B [3]  e09_status_slim CTAS + 검증 3-A
5. [Athena] 부록 B [4]  통합 산출물 조회 → CSV 다운로드  = 파일 1
                        + 검증 4-A (행수가 2-A의 e09_cnt와 일치해야 함)
6. [Athena] 부록 B [5]  시리얼 목록 문자열 생성 → 복사
7. [DataGrip] 부록 C [1] 붙여넣기 → 1-A 건수 대조 (5-A와 같아야 함)
8. [DataGrip] 부록 C [2][3][4] 실행 → CSV 저장  = 파일 2·3·4
9. AMR팀에 CSV 4종 + 컬럼 정의서 전달
```

**3번이 4번보다 먼저여야 하는 이유**: `e09_status_slim`은 `serial IN (SELECT DISTINCT serial FROM e09_events_rc)`로 대상 기기를 좁혀서 만든다. 순서를 바꾸면 전 기기 8개월 status를 뜨게 되고 스캔이 폭증한다.

**7번이 6번 뒤여야 하는 이유**: MySQL 쪽은 "E09가 난 기기"가 누군지 스스로 알 수 없다. Athena가 정한 목록을 그대로 받는다. MySQL에서 `history_error`로 목록을 다시 만들면 §4의 1번을 어기는 것이다.

### 6-2. 실고객 시리얼 CSV 반입 (가장 자주 깨지는 단계)

0. **원본 엑셀이 사내 DRM 문서면 반드시 복호화 후 작업한다.** 암호화된 채로 저장·업로드하면 Athena가 암호문 바이트를 그대로 텍스트로 읽어 깨진 문자열만 나온다. **에러가 안 나므로 결과를 보기 전에는 모른다.**
1. 새 시트에 A열=시리얼, B열=계약상태 **두 컬럼만** 둔다.
2. **맨 위에 헤더 행 1줄을 반드시 추가한다** (A1=`serial`, B1=`contract_status`. 내용은 무엇이든 무관). 이유는 §4의 6번.
3. "다른 이름으로 저장" → **CSV UTF-8 (쉼표로 분리)**. 일반 "CSV (쉼표로 분리)"는 EUC-KR로 저장돼 계약상태 한글이 깨진다.
4. S3 콘솔에서 `s3://skmg-benjamin-prd-s3-lake/_analysis/real_customer_csv/` 에 업로드. **그 prefix에 다른 파일이 있으면 안 된다**(외부 테이블이 폴더 전체를 읽는다).
5. 부록 B의 1-A / 1-A-2 / 1-B로 검증한다.

| 증상 | 원인 | 조치 |
|---|---|---|
| 한글이 `ï¿½...` | EUC-KR 저장 | CSV UTF-8로 다시 저장 |
| 전체가 깨진 바이트 | DRM 암호문 / 다른 파일 혼입 | 복호화 후 재업로드, prefix 비우기 |
| `first_row_alive = 0` | 헤더 없이 업로드 | 헤더 추가 후 재업로드 |
| `header_leaked = 1` | `skip.header.line.count`가 안 먹음 | `TBLPROPERTIES` 확인 |
| `계약확정`과 `계약확정\r`이 따로 집계 | Windows CRLF | 쿼리에서 `trim()` (부록 B는 이미 적용) |

### 6-3. 재실행

```bash
# 테이블만 지우면 S3 파일이 남아 다음 CTAS 결과와 섞인다. 둘 다 해야 한다.
aws s3 rm s3://skmg-benjamin-prd-s3-lake/_analysis/e09_status_slim/ --recursive
aws s3 rm s3://skmg-benjamin-prd-s3-lake/_analysis/e09_events_rc/   --recursive
```
그다음 Athena에서 `DROP TABLE IF EXISTS ...` → CTAS 재실행. 부록 B의 [6]에 정리용 DROP문이 주석으로 있다.

### 6-4. 검증 체크포인트 (하나라도 어긋나면 진행 금지)

| 대조 | 기대 |
|---|---|
| 부록 B 1-A 합계 | 약 4,900건, 계약상태 한글 정상 |
| 부록 B 2-A `e09_cnt` ↔ 4-A `total_rows` | **정확히 일치.** [2]에서 중복 제거를 명시적으로 했으므로 어긋날 수 없다. 어긋나면 [4] 조인에 중복이 들어간 것 |
| 부록 B 3-A `p_month` 목록 | 1~8월이 다 있어야 한다. 초반 달이 비면 그 구간 Firehose 적재가 없었다는 뜻이며 **결론에 반드시 명시**해야 한다 |
| 부록 B 3-A `amr_filled` | 0이면 `amrStatus` 경로명이 다른 것. 페이로드를 다시 확인하고 [3] CTAS만 재실행 |
| 부록 B 5-A `device_cnt` ↔ 부록 C 1-A `target_device_cnt` | 일치 |
| 부록 C 1-B | 결과가 있으면 그 기기는 파일 2~4에서 빠진다. **미리 목록화해 AMR팀에 함께 알린다** |

---

## 7. 남은 일

### 7-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **VDI에서 레이크 사전 확인 3종.** ① `aws s3 ls s3://skmg-benjamin-prd-s3-lake/`로 운영계 버킷과 prefix 목록이 실제로 있는지 ② Athena에서 `SHOW CREATE TABLE iot_messages_raw`로 운영계 테이블 존재·LOCATION·파티션 프로젝션 확인 ③ `SELECT payload FROM iot_messages_raw WHERE prefix='skmg_airbot_v1_error' AND year=2026 AND month=8 AND day=24 LIMIT 5`로 error 페이로드 구조(최상위 키가 `error`인지)와 `meta_*` 존재 확인 | 백엔드 | 부록 B에서 삭제된 `[0] 사전확인`이 하던 일. 운영계 테이블 존재가 **미확인**이라 여기가 첫 관문이다 |
| 2 | 부록 B·C를 §6-1 순서대로 실행해 CSV 4종 생성 | 백엔드 | 오류가 나면 §6-2·6-4 표로 자가 진단 |
| 3 | `SELECT * FROM amr_alert_error_code;` — E09가 등록돼 있는지 확인 | 백엔드 | 등록돼 있으면 6/15 이후 구간에 한해 기기 로그(S3)가 추가 증거로 쓰인다 |
| 4 | 부록 B 2-A-3 / 2-A-5로 **중복의 성격 판정** | 백엔드 | status와 error의 `dup_pct`가 비슷하면 QoS1 재전송(전송 계층), error만 높으면 발행 경로의 앱 레벨 중복이다. 후자면 기기 담당에게 넘길 결함이다 |
| 5 | CSV 4종 + 컬럼 정의서를 AMR팀에 전달하고 §5-2 질문 5건 회신 독촉 | 백엔드 | 4번 질문은 §3-7로 자답했으니 "이 이해가 맞는지"로 바꿔 묻는 것이 낫다 |

### 7-2. 분석 단계에서 확인할 것

| # | 항목 |
|---|---|
| 6 | **미연동 기기 에러 누락의 정량화.** 이번 조사의 커버리지 한계다. Athena의 E09 건수와 `history_error`의 E09 건수를 월별로 대조하면 "7월 이후 DB에서 빠진 양"이 나온다. 전수라고 주장하려면 이 숫자가 있어야 하고, 동시에 §4의 1번 결정을 뒷받침하는 근거가 된다 |
| 7 | **`status_missing` 비율** (부록 B 4-A). E09 직전 15분간 status가 아예 없던 건으로, AMR 통신 두절이 클라우드 송신까지 끊은 사례다. 높으면 그 자체가 결론의 일부다 |
| 8 | **`rs_age_sec` 분포 vs 20초 판정 기준**(§3-7) 대조. 에러 직전 status 간격이 20초보다 길게 벌어지는 패턴이 선행 신호인지 |
| 9 | **`rs_before_error` 쏠림**과 `amr_action_before = FAIL` 비율 |
| 10 | **OTA 직후 발생 여부** — 파일 3의 `ota_completed_at_kst`와 파일 1의 `error_at_kst` 간격 |
| 11 | **재부팅 명령 직후 발생 여부** — 파일 4의 `commanded_at_kst`와의 간격. 부팅 이더넷 330초 경로(§3-7 ②)에 해당하는 건이 있는지 |

### 7-3. 후속 (이 조사를 막지는 않음)

| # | 항목 |
|---|---|
| 12 | **경계 케이스 — KST 1/1 00:00~01:00 발생 건.** 직전 60분 status가 2025년 12월 파티션에 있어 `rs_before_error`가 빌 수 있다. 건수가 미미해 12월을 스캔 범위에 넣지 않았다. 해당 구간이 결론에 중요해지면 `month`를 12월까지 넓혀 [3]만 재실행하면 된다 |
| 13 | **E09 지속 시간 산출.** 해제가 MQTT로 발행되지 않으므로(§3-7) status 토픽에서 `robotStatus`가 `ERROR`를 벗어나는 시점을 찾는 별도 쿼리가 필요하다. 이번 산출물에는 없다 |
| 14 | **관제 통계 SQL의 `he.delete_yn` 미필터** (`ErrorStatistics.xml`, 12개 쿼리 전부). 삭제 표시된 행이 관제 숫자에 섞인다. 별건 결함 |
| 15 | **`firmware_job` 매퍼의 `updateJob`** — `firmware_job_id`(bigint)를 `job_id`(varchar)와 비교하는 잘못된 조건. 현재 호출부가 없어 무해하지만 누군가 쓰면 조용히 0건 갱신된다 |
| 16 | **개발계·검증계 Athena 테이블 상태 확인.** 2026-07-07 기록으로는 dev·stg에 생성 완료, 운영계는 미반영이었다. 지금 각 환경에 무엇이 있는지 실물 확인이 필요하다 |

---

## 8. 운영 중 자주 만날 상황

| 상황 | 대응 |
|---|---|
| "이 기기 그때 뭐 하고 있었어요?" | Athena `skmg_airbot_v1_status`. §3-5의 기본 조회 패턴에 시리얼·날짜만 바꾼다. **RDS에는 없다** |
| "관제 화면 건수랑 왜 달라요?" | 세 가지 요인을 순서대로 확인한다. ① 7/1 이후 미연동 기기 에러 미적재 ② 조회 SQL의 `INNER JOIN device ... linked_yn='Y'`에서 연동 해제 기기 탈락(과거 실측 34건 차이의 원인) ③ 실고객 계약 필터링 유무 |
| Athena 쿼리가 "no partitions" / 결과 0건 | `WHERE prefix = '...'`를 빠뜨렸거나 prefix 철자가 틀렸다. `aws s3 ls`로 실제 폴더명을 확인. 토픽에서 시리얼을 빼고 `/`→`_`다 |
| 시각이 9시간 어긋나 보임 | Athena는 UTC, `benjamin` 스키마 MySQL은 KST. §3-4. 조인은 UTC끼리, 출력에서만 `+ INTERVAL '9' HOUR` |
| 기기가 보고한 시각과 서버 시각이 다름 | `device_reported_kst`(기기 자가보고, KST)와 `error_at_kst`(브로커 기준, UTC+9)를 비교한다. 크게 벌어지면 **그 기기 시계가 틀어진 것**이다. `meta_ingest_ts`(브로커 수신 epoch ms)를 기준자로 쓰면 확실하다 |
| 같은 초에 같은 에러가 2건 | QoS1 재전송일 가능성이 높다. `mqtt_msg_cnt` / `dup_cnt`로 확인하고, 성격 판정은 부록 B의 2-A-3·2-A-5 |
| CTAS 결과가 이상함 (이전 실행분이 섞임) | `DROP TABLE`만으로는 S3 파일이 안 지워진다. §6-3 |
| 스캔 비용이 갑자기 큼 | `day` 파티션을 빠뜨렸거나 `skmg_airbot_v1_heartbeat`(전체의 58%)를 건드렸다 |
| E09가 언제부터 언제까지였는지 | error 토픽으로는 **시작만** 알 수 있다. 해제는 발행되지 않는다(§3-7). status의 `robotStatus` 변화로 봐야 한다 |
| 서버 로그 키워드 | 미연동 기기 에러 폐기: `미연동 기기 에러 무시`. 알림 대상 에러 발생: `기기 에러 발생: errorCode=` |

---

## 부록 A. 핵심 커밋·산출물 (시간순)

조사 자체는 코드 변경이 없다. 아래 커밋 3건은 **조사 설계의 전제가 된 서버 동작 변경**이며, 2026-09-22 기준 전부 `origin/dev`·`origin/stg`·`origin/main` 반영 상태로 실측했다.

| 날짜 | 저장소 | 커밋 | 내용 | 조사에 준 영향 |
|---|---|---|---|---|
| 2026-06-15 | backend-api-main | `166a3e92` | `amr_alert_error_code` + 알림 대상 에러 당일 첫 발생 시 기기 로그 자동 수집 | 6/15 이후 구간에만 추가 증거 존재 |
| 2026-06-15 | backend-api-main | `6ea90488` | 위 알림 SMS를 `prd` 프로필에서만 발송 | – |
| 2026-07-01 | backend-api-main | `9c38d807` | 미연동 기기 에러 `history_error` 적재 중단 | **모집단 변경. Athena 단독 방침의 근거** |

산출물 작성 이력 (파일 기준. 별도 저장소에 커밋되지 않은 작업 산출물이다).

| 날짜 | 산출물 | 내용 |
|---|---|---|
| 2026-08-25 | MySQL SQL 팩 | 설치·OTA·재부팅 3종 + 임시테이블 COLLATE 대응 |
| 2026-08-26 | Athena SQL 팩 | 전면 재작성. 요구 산출물만 남기고 통합 1개로 정리. `[0] 사전확인` 절 삭제 |
| 2026-08-26 | AMR팀 컬럼 정의서 | 파일 4종 컬럼 정의 + 한계 + 확인 요청 5건 |
| 2026-08-30 | Athena SQL 팩 | 중복 성격 판별 쿼리(2-A-3 / 2-A-4 / 2-A-5) 추가 |

---

## 부록 B. Athena SQL 팩 전문 (`E09-athena.sql`)

아래 그대로 Athena 콘솔에 붙여 절 번호 순으로 실행한다. 파일 안 주석이 `E09-investigation.sql`을 가리키는 곳은 **이 문서의 부록 C**를 뜻한다. 주석의 `[0-A] 재확인`은 삭제된 절을 가리키므로 §7-1의 1번으로 대체한다.

```sql
-- =====================================================================
-- E09 (AMR 통신 불량) 조사 — Athena 파트
-- 대상: 실고객 기기 / 기간: 2026-01-01 ~ 2026-08-31 (KST)
-- 테이블: iot_messages_raw (s3://skmg-benjamin-prd-s3-lake/)
-- 엔진: Athena engine v3 (Trino)
--
-- 산출물: [4] 통합 결과 1개  — E09 1건 = 1행 (발생시각 + 그 시점 로봇 상태)
--         [5] DB 조회용 시리얼 목록 (E09-investigation.sql 로 넘김)
--
-- ─────────────────────────────────────────────────────────────────────
-- ★ 왜 DB(history_error) 가 아니라 여기서 뽑는가
--   커밋 9c38d807 `[fix] 미연동 기기 에러 적재하지 않음` (2026-07-01) 이후
--   미연동 기기(device.linked_yn <> 'Y')의 에러는 history_error 에 **적재되지 않는다**.
--   → 1~6월엔 포함, 그 이후엔 제외 = 조사 기간 안에서 모집단이 바뀐다.
--     그 상태로 월별 추이를 그리면 수집 규칙 변경이 "E09 감소"로 보인다.
--   MQTT 원본(S3)은 연동 여부와 무관하게 전 구간 동일하게 쌓이므로
--   기간 전체를 같은 기준으로 보려면 여기가 유일한 소스다.
--
-- ★ 페이로드 구조 (2026-08-26 실측 확정)
--   error 토픽의 최상위 키는 `data` 가 아니라 **`error`** 다.
--     {"topic":"skmg/airbot/{serial}/v1/error", "prefix":"skmg_airbot_v1_error",
--      "meta_ingest_ts":1787609254034, "meta_client_id":"...", "meta_source_ip":"...",
--      "meta_principal":"...", "serial":"...", "timestamp":"2026-08-24T22:07:32Z",
--      "error":{"errorCode":"S05-2","errorDate":"2026-08-25 07:07:32"}}
--   · errorCondition.stationPower 는 실제 페이로드에 없어 사용하지 않는다.
--   · meta_ingest_ts = 수집 시각(epoch ms, UTC). 위 샘플에서 payload timestamp +2초.
--     기기 시계가 의심될 때 기준자로 쓸 수 있다.
--
-- ★ 시간대 — 전부 실측 확정
--   payload.timestamp       = Instant.now().atZone(UTC)  → UTC   ← 조인 기준
--   payload.error.errorDate = LocalDateTime.now()        → KST   ← 기기 자가보고
--   MySQL 이력 컬럼 전부                                  → KST
--   위 샘플이 그대로 증거: 22:07:32Z(UTC) ↔ 07:07:32(KST), 정확히 +9h.
--   내부 조인은 UTC 끼리, 출력에서만 +9h.
--
-- ★ 파티션 = KST 기준 (확정)
--   위 샘플의 UTC 22:07(=KST 익일 07:07)이 day=25 파티션에 들어 있다.
--   따라서 KST 2026-01-01~08-31 = year=2026 AND month BETWEEN 1 AND 8 로 정확히 대응된다.
--   (month=1 파티션의 UTC 범위는 2025-12-31T15:00Z~ 이므로 아래 UTC 필터와 일치)
--
-- ★ Athena 제약 (실행 중 실제로 부딪힌 것들)
--   · from_iso8601_timestamp() 는 'timestamp with time zone' 이라 CTAS 불가
--     → date_parse 로 앞 19자만 파싱. CAST 는 세션 타임존에 의존하므로 금지.
--   · CTAS 에 external_location 필수 (Managed Query Results 워크그룹)
--   · 재실행 시 DROP TABLE 만으로는 S3 파일이 안 지워짐 → aws s3 rm 병행
--   · 조인에서 s.* 금지 (serial 중복 → AMBIGUOUS_NAME). 컬럼 명시.
--   · prefix 는 projection type=injected → WHERE 에 반드시 명시. DISTINCT 조회 불가.
-- =====================================================================


-- =====================================================================
-- [1] 실고객 시리얼 목록 — S3 CSV → 외부 테이블
--
-- 준비 (VDI):
--   0) ★ 원본 엑셀이 사내 DRM 문서면 **복호화 후** 작업할 것.
--      암호화된 채로 저장·업로드하면 Athena 가 암호문 바이트를 그대로 텍스트로 읽어
--      깨진 문자열만 쏟아진다 (에러는 안 나므로 결과를 보기 전엔 모른다).
--   1) 새 시트에 A열=시리얼, B열=계약상태 두 컬럼만 둔다
--   2) ★ 맨 위에 **헤더 행 1줄을 반드시 추가**한다
--        A1 = serial          B1 = contract_status
--        (내용은 무엇이든 상관없다. 아래 skip.header.line.count=1 이 1행을 버리므로
--         헤더가 없으면 **첫 시리얼 1건이 통째로 누락**된다)
--   3) "다른 이름으로 저장" → **CSV UTF-8 (쉼표로 분리)** 선택
--        · 일반 "CSV (쉼표로 분리)" 는 EUC-KR 로 저장돼 계약상태 한글이 깨진다
--   4) S3 콘솔에서 아래 경로에 업로드 (해당 prefix 에 다른 파일이 없어야 함)
--        s3://skmg-benjamin-prd-s3-lake/_analysis/real_customer_csv/
--   5) 아래 DDL 실행 → 1-A, 1-B 로 검증
--
-- ※ 헤더 행이 BOM 도 함께 처리한다: 엑셀 "CSV UTF-8" 은 파일 맨 앞에 BOM 을 붙이는데,
--   BOM 은 1행 첫 칸에 딸려가고 그 행이 통째로 버려지므로 시리얼 값이 오염되지 않는다.
--   (헤더 없이 올리면 BOM 이 첫 시리얼 앞에 붙어 조인이 조용히 실패한다)
-- ※ _analysis 는 iot_messages_raw 가 읽지 않는다 (prefix 가 injected 라
--   WHERE 에 명시한 prefix 만 스캔) → 원본 테이블에 영향 없음.
-- =====================================================================
DROP TABLE IF EXISTS real_customer_serial;
CREATE EXTERNAL TABLE real_customer_serial (
    serial          string,
    contract_status string
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',', 'quoteChar' = '"', 'escapeChar' = '\\')
LOCATION 's3://skmg-benjamin-prd-s3-lake/_analysis/real_customer_csv/'
TBLPROPERTIES ('skip.header.line.count' = '1');

-- 1-A. 적재 확인 — 합계가 약 4,900건 / 계약상태 한글이 안 깨졌는지
--   ※ 파일이 Windows(CRLF) 로 저장되면 마지막 컬럼 끝에 \r 이 붙는다.
--     trim() 으로 제거해야 '계약확정' 과 '계약확정\r' 이 따로 집계되지 않는다.
SELECT trim(contract_status) AS contract_status, COUNT(*) AS cnt
FROM real_customer_serial
GROUP BY trim(contract_status)
ORDER BY cnt DESC;
-- 깨진 문자열이 잔뜩 나오면  → 1-Z 로 파일 확인 (다른 파일이 섞인 것)
-- 'ï¿½...' 처럼 한글만 깨지면 → EUC-KR 로 저장된 것. CSV UTF-8 로 다시 저장

-- 1-A-2. ★ 헤더 누락 점검 — 엑셀 1행에 있던 시리얼이 살아있는지 직접 확인
--   헤더 행 없이 올리면 skip.header.line.count=1 이 이 행을 버려 조용히 사라진다.
SELECT
    (SELECT COUNT(*) FROM real_customer_serial
      WHERE upper(trim(serial)) = 'WRBA1M10KRDWI0425J00207')          AS first_row_alive,  -- 1 이어야 정상
    (SELECT COUNT(*) FROM real_customer_serial
      WHERE serial LIKE '%serial%' OR contract_status LIKE '%status%') AS header_leaked,   -- 0 이어야 정상
    (SELECT COUNT(*) FROM real_customer_serial
      WHERE serial IS NULL OR trim(serial) = '')                       AS blank_rows;
-- first_row_alive = 0 → 헤더를 안 넣고 올린 것. 엑셀에 헤더 추가 후 재업로드.
-- header_leaked  = 1 → 헤더는 넣었는데 skip 설정이 안 먹은 것. TBLPROPERTIES 확인.

-- 1-B. 중복 시리얼 점검 (계약상태가 갈리는 건이 있는지)
SELECT upper(trim(serial)) AS serial, COUNT(*) AS rows,
       array_join(array_sort(array_agg(DISTINCT trim(contract_status))), ' / ') AS statuses
FROM real_customer_serial
WHERE serial IS NOT NULL AND trim(serial) <> ''
GROUP BY upper(trim(serial))
HAVING COUNT(*) > 1
ORDER BY rows DESC;
-- 결과가 있으면 아래 [2] 의 contract_status 는 '계약확정 / 해약확정' 처럼 병기된다.


-- =====================================================================
-- [2] 실고객 E09 발생 이력  (error 토픽 × 실고객 목록)
--     error 토픽은 status 대비 양이 미미해 스캔이 가볍다.
-- =====================================================================
DROP TABLE IF EXISTS e09_events_rc;
CREATE TABLE e09_events_rc
WITH (
    format              = 'PARQUET',
    parquet_compression = 'SNAPPY',
    external_location   = 's3://skmg-benjamin-prd-s3-lake/_analysis/e09_events_rc/'
) AS
WITH rc AS (   -- 실고객 목록 정규화 (엑셀 유래라 공백·대소문자 방어)
    SELECT upper(trim(serial)) AS serial,
           array_join(array_sort(array_agg(DISTINCT trim(contract_status))), ' / ') AS contract_status
    FROM real_customer_serial
    WHERE serial IS NOT NULL AND trim(serial) <> ''
    GROUP BY upper(trim(serial))
),
ev AS (
    SELECT
        json_extract_scalar(payload, '$.serial')                              AS serial,
        try(date_parse(substr(json_extract_scalar(payload, '$.timestamp'), 1, 19),
                       '%Y-%m-%dT%H:%i:%s'))                                  AS err_ts_utc,
        json_extract_scalar(payload, '$.error.errorDate')                      AS err_date_kst_raw
    FROM iot_messages_raw
    WHERE prefix = 'skmg_airbot_v1_error'
      AND year  = 2026
      AND month BETWEEN 1 AND 8
      AND json_extract_scalar(payload, '$.error.errorCode') = 'E09'
)
-- ★ (serial, err_ts_utc) 로 중복 제거한다.
--   MQTT 는 QoS 1(at-least-once) 이라 브로커 재전송 시 같은 메시지가 두 번 저장되고,
--   timestamp 는 초 단위로 잘려 있어(Util.getTime 의 truncatedTo(SECONDS))
--   재전송분이 원본과 **완전히 동일한 값**이 된다. 즉 전송 계층의 중복이지 에러 2건이 아니다.
--   dup_cnt 로 몇 개가 접혔는지 남겨 다른 집계와 대조할 수 있게 한다.
SELECT ev.serial,
       ev.err_ts_utc,
       MAX(ev.err_date_kst_raw) AS err_date_kst_raw,
       MAX(rc.contract_status)  AS contract_status,
       COUNT(*)                 AS dup_cnt          -- 1 이면 중복 없음
FROM ev
JOIN rc ON rc.serial = upper(trim(ev.serial))
WHERE ev.err_ts_utc >= TIMESTAMP '2025-12-31 15:00:00'   -- 2026-01-01 00:00 KST
  AND ev.err_ts_utc <  TIMESTAMP '2026-08-31 15:00:00'   -- 2026-09-01 00:00 KST
GROUP BY ev.serial, ev.err_ts_utc;

-- 2-A. 결과 확인 — e09_cnt 가 이후 [4] 산출물의 행수와 같아야 한다
SELECT COUNT(*)               AS e09_cnt,          -- 중복 제거 후 (= 보고 기준)
       SUM(dup_cnt)           AS raw_msg_cnt,      -- 원본 MQTT 메시지 수
       SUM(dup_cnt) - COUNT(*) AS collapsed_dups,  -- 재전송으로 접힌 건수
       COUNT(DISTINCT serial) AS device_cnt,
       MIN(err_ts_utc + INTERVAL '9' HOUR) AS first_kst,
       MAX(err_ts_utc + INTERVAL '9' HOUR) AS last_kst
FROM e09_events_rc;

-- 2-A-2. 중복이 실제로 어떤 건지 확인 (필요 시)
SELECT serial, err_ts_utc + INTERVAL '9' HOUR AS error_at_kst, dup_cnt
FROM e09_events_rc
WHERE dup_cnt > 1
ORDER BY dup_cnt DESC, serial, err_ts_utc;

-- 2-A-3. ★ 중복이 왜 생겼는지 판별 — meta_ingest_ts(수집 시각, epoch ms) 간격으로 구분한다
--   payload timestamp 는 초 단위로 잘려 있어 중복끼리 동일하지만,
--   meta_ingest_ts 는 수집 순간을 ms 로 기록하므로 원인이 갈린다.
WITH raw AS (
    SELECT json_extract_scalar(payload, '$.serial')                      AS serial,
           substr(json_extract_scalar(payload, '$.timestamp'), 1, 19)    AS ts_raw,
           CAST(json_extract_scalar(payload, '$.meta_ingest_ts') AS BIGINT) AS ingest_ms,
           "$path"                                                       AS s3_file
    FROM iot_messages_raw
    WHERE prefix = 'skmg_airbot_v1_error'
      AND year = 2026 AND month BETWEEN 1 AND 8
      AND json_extract_scalar(payload, '$.error.errorCode') = 'E09'
),
dup AS (
    SELECT serial, ts_raw,
           COUNT(*)                            AS msg_cnt,
           COUNT(DISTINCT ingest_ms)           AS distinct_ingest,
           MAX(ingest_ms) - MIN(ingest_ms)     AS ingest_gap_ms,
           COUNT(DISTINCT s3_file)             AS distinct_files
    FROM raw
    GROUP BY serial, ts_raw
    HAVING COUNT(*) > 1
)
SELECT
    CASE
        -- meta_* 필드는 수집 파이프라인에 도중 추가된 것이라 초기 구간(1월 등)에는 없다.
        -- NULL 을 C 로 흘려보내면 원인을 잘못 단정하게 되므로 별도 분류한다.
        WHEN ingest_gap_ms IS NULL   THEN 'X. 판별불가 (meta_ingest_ts 미주입 구간)'
        WHEN distinct_ingest = 1     THEN 'A. 수집 파이프라인 중복 (동일 레코드가 두 번 기록)'
        WHEN ingest_gap_ms   < 1000  THEN 'B. 기기가 같은 초에 두 번 발행 (앱 레벨 중복)'
        ELSE                              'C. MQTT QoS1 재전송 (브로커 ACK 타임아웃 후 재전송)'
    END                          AS 추정원인,
    COUNT(*)                     AS 건수,
    MIN(ingest_gap_ms)           AS min_gap_ms,
    ROUND(AVG(ingest_gap_ms), 0) AS avg_gap_ms,
    MAX(ingest_gap_ms)           AS max_gap_ms
FROM dup
GROUP BY 1
ORDER BY 건수 DESC;

-- 2-A-5. ★ 결정적 판별 — 다른 토픽에서도 같은 중복이 나오는가
--
--   논리: 중복이 MQTT 전송 계층(QoS1 재전송) 때문이라면
--         **같은 QoS1 로 발행되는 모든 토픽에서 비슷한 비율**로 나타나야 한다.
--         error 토픽에서만 나온다면 전송 문제가 아니라 error 발행 경로 고유의
--         앱 레벨 중복(같은 이벤트로 publishError 가 두 번 호출)이다.
--   ※ status 도 QOS1 로 발행된다 (PublishHelper.publish → publishString(..., QOS1)).
--   ※ meta_ingest_ts 없이도 판별되므로 초기 구간까지 커버된다.
WITH status_dup AS (
    SELECT serial, ts_utc, COUNT(*) AS c
    FROM e09_status_slim
    GROUP BY serial, ts_utc
),
error_dup AS (
    SELECT serial, err_ts_utc, dup_cnt AS c
    FROM e09_events_rc
)
SELECT 'status' AS topic,
       SUM(c)                                        AS raw_msgs,
       COUNT(*)                                      AS distinct_events,
       SUM(c) - COUNT(*)                             AS dup_msgs,
       ROUND(100.0 * (SUM(c) - COUNT(*)) / SUM(c), 3) AS dup_pct
FROM status_dup
UNION ALL
SELECT 'error(E09)',
       SUM(c), COUNT(*), SUM(c) - COUNT(*),
       ROUND(100.0 * (SUM(c) - COUNT(*)) / SUM(c), 3)
FROM error_dup;
-- 판정:
--   · 두 dup_pct 가 비슷 → 전송 계층 공통 현상 = QoS1 재전송으로 설명됨
--   · status 는 0 에 가깝고 error 만 높음 → error 발행 경로의 앱 레벨 중복
--     (IotAgent.java:2005 publishError 호출부에 중복 억제 로직이 없다)

-- 2-A-4. 개별 중복 건 상세 (위 분류의 근거 확인용)
WITH raw AS (
    SELECT json_extract_scalar(payload, '$.serial')                      AS serial,
           substr(json_extract_scalar(payload, '$.timestamp'), 1, 19)    AS ts_raw,
           CAST(json_extract_scalar(payload, '$.meta_ingest_ts') AS BIGINT) AS ingest_ms,
           "$path"                                                       AS s3_file
    FROM iot_messages_raw
    WHERE prefix = 'skmg_airbot_v1_error'
      AND year = 2026 AND month BETWEEN 1 AND 8
      AND json_extract_scalar(payload, '$.error.errorCode') = 'E09'
)
SELECT serial, ts_raw, COUNT(*) AS msg_cnt,
       array_join(array_sort(array_agg(ingest_ms)), ' , ')          AS ingest_ms_list,
       MAX(ingest_ms) - MIN(ingest_ms)                              AS ingest_gap_ms,
       COUNT(DISTINCT s3_file)                                      AS distinct_files
FROM raw
GROUP BY serial, ts_raw
HAVING COUNT(*) > 1
ORDER BY ingest_gap_ms DESC, serial;

-- 2-B. 계약상태별 분포
SELECT contract_status, COUNT(*) AS e09_cnt, COUNT(DISTINCT serial) AS device_cnt
FROM e09_events_rc
GROUP BY contract_status ORDER BY e09_cnt DESC;

-- 2-C. 실고객 목록에 없어서 제외된 기기 (목록 오탈자·누락 점검)
--      여기 나온 시리얼이 정말 비실고객인지 확인. 실고객인데 빠졌으면 CSV 를 보강한다.
WITH rc AS (
    SELECT upper(trim(serial)) AS serial FROM real_customer_serial
    WHERE serial IS NOT NULL AND trim(serial) <> '' GROUP BY 1
),
ev AS (
    SELECT json_extract_scalar(payload, '$.serial') AS serial,
           try(date_parse(substr(json_extract_scalar(payload,'$.timestamp'),1,19),'%Y-%m-%dT%H:%i:%s')) AS err_ts_utc
    FROM iot_messages_raw
    WHERE prefix = 'skmg_airbot_v1_error' AND year = 2026 AND month BETWEEN 1 AND 8
      AND json_extract_scalar(payload, '$.error.errorCode') = 'E09'
)
SELECT ev.serial, COUNT(*) AS e09_cnt
FROM ev
LEFT JOIN rc ON rc.serial = upper(trim(ev.serial))
WHERE rc.serial IS NULL
  AND ev.err_ts_utc >= TIMESTAMP '2025-12-31 15:00:00'
  AND ev.err_ts_utc <  TIMESTAMP '2026-08-31 15:00:00'
GROUP BY ev.serial
ORDER BY e09_cnt DESC;


-- =====================================================================
-- [3] status 슬림 테이블 — E09 발생 기기로만 제한
--     8개월 status 원본을 한 번 스캔해 필요한 필드만 Parquet 으로 떨군다.
--     대상 기기를 [2] 결과로 좁혀 저장·후속조인 비용을 크게 줄인다.
--
-- 재실행 시:
--   DROP TABLE IF EXISTS e09_status_slim;
--   aws s3 rm s3://skmg-benjamin-prd-s3-lake/_analysis/e09_status_slim/ --recursive
-- =====================================================================
DROP TABLE IF EXISTS e09_status_slim;
CREATE TABLE e09_status_slim
WITH (
    format              = 'PARQUET',
    parquet_compression = 'SNAPPY',
    external_location   = 's3://skmg-benjamin-prd-s3-lake/_analysis/e09_status_slim/',
    partitioned_by      = ARRAY['p_month']
) AS
SELECT serial, ts_utc,
       robot_status, op_error_code,
       is_charging, is_docked, is_llm, is_vital_sign,
       cpu_temperature, wifi_rssi, battery_charge_rate,
       amr_robot_state, amr_action_status,
       clean_type,
       p_month
FROM (
    SELECT
        json_extract_scalar(payload, '$.serial')                    AS serial,
        try(date_parse(substr(json_extract_scalar(payload, '$.timestamp'), 1, 19),
                       '%Y-%m-%dT%H:%i:%s'))                        AS ts_utc,
        json_extract_scalar(payload, '$.operation.robotStatus')      AS robot_status,
        json_extract_scalar(payload, '$.operation.errorCode')        AS op_error_code,
        json_extract_scalar(payload, '$.operation.isCharging')       AS is_charging,
        json_extract_scalar(payload, '$.operation.isDocked')         AS is_docked,
        json_extract_scalar(payload, '$.operation.isLLM')            AS is_llm,
        json_extract_scalar(payload, '$.operation.isVitalSign')      AS is_vital_sign,
        json_extract_scalar(payload, '$.operation.CPUTemperature')   AS cpu_temperature,
        json_extract_scalar(payload, '$.operation.WifiRssi')         AS wifi_rssi,   -- 전문상 대문자 W
        json_extract_scalar(payload, '$.battery.chargeRate')         AS battery_charge_rate,
        json_extract_scalar(payload, '$.amrStatus.robotStatus')      AS amr_robot_state,
        json_extract_scalar(payload, '$.amrStatus.actionStatus')     AS amr_action_status,
        json_extract_scalar(payload, '$.operation.cleanType')        AS clean_type,
        month                                                        AS p_month
    FROM iot_messages_raw
    WHERE prefix = 'skmg_airbot_v1_status'
      AND year  = 2026
      AND month BETWEEN 1 AND 8      -- 파티션 KST 확정 → KST 1/1~8/31 과 정확히 대응
)
WHERE serial IS NOT NULL
  AND ts_utc IS NOT NULL
  AND serial IN (SELECT DISTINCT serial FROM e09_events_rc);
-- 경계 케이스: KST 1/1 00:00~01:00 에 발생한 E09 는 직전 60분 status 가
--   2025년 12월 파티션에 있어 rs_before_error 가 빌 수 있다.
--   해당 구간 건수가 미미해 12월(약 한 달치 추가 스캔)은 포함하지 않았다.

-- 3-A. 적재 확인 — 초반 달이 비면 Firehose 적재 시작이 1월보다 늦다는 뜻
SELECT p_month,
       COUNT(*)                   AS rows,
       COUNT(DISTINCT serial)     AS devices,
       COUNT(amr_robot_state)     AS amr_filled,
       COUNT(wifi_rssi)           AS wifi_filled,
       COUNT(battery_charge_rate) AS battery_filled,
       MIN(ts_utc) AS min_ts, MAX(ts_utc) AS max_ts
FROM e09_status_slim
GROUP BY p_month ORDER BY p_month;
-- amr_filled 가 0 이면 amrStatus 경로명이 다른 것 → [0-A] 재확인 후 이 CTAS 만 재실행


-- =====================================================================
-- [4] ★ 최종 통합 산출물 — CSV 다운로드 대상
--     E09 1건 = 1행. 발생시각 + 그 시점 로봇 상태.
--
--   상태를 두 벌 붙이는 이유:
--     E09 발생과 동시에 robotStatus 가 'ERROR' 로 바뀌므로,
--     에러 시점 값만 보면 "ERROR 였다"밖에 안 나온다.
--       rs_at_error     = 직전 15분 내 마지막 status → 어떤 에러였나
--                         (operation.errorCode 는 robotStatus='ERROR' 일 때만 유효)
--       rs_before_error = 직전 60분 내 마지막 **비ERROR** status → 무엇을 하다 났나
--     창을 60분으로 넓힌 건 ERROR 상태가 오래 지속될 수 있어서다.
--     rs_before_age_sec 이 크면 이전 에러의 잔상이므로 해석에서 제외할 것.
-- =====================================================================
WITH
-- AMR 코드표 (AMR팀 제공, 2026-08-25). 한 곳에서만 정의해 4개 컬럼이 함께 갱신되게 한다.
amr_state_code AS (
    SELECT * FROM (VALUES
        ('0','IDLE'), ('1','AUTO_MAPPING'), ('2','MANUAL_MAPPING'), ('3','NAVIGATION'),
        ('4','RETURN_CHARGER'), ('5','DOCKING'), ('6','UNDOCKING'), ('7','ONSTATION'),
        ('8','FACTORY_NAVIGATION'), ('9','ERROR'), ('10','FOLLOW_ME'), ('11','MANUAL_CONTROL')
    ) AS t(code, label)
),
amr_action_code AS (
    SELECT * FROM (VALUES
        ('0','VOID'), ('1','READY'), ('2','START'), ('3','PAUSE'),
        ('4','RESUME'), ('5','COMPLETE'), ('6','FAIL')
    ) AS t(code, label)
),
at_err AS (   -- 에러 직전 15분 내 마지막 status
    SELECT e.serial, e.err_ts_utc, e.err_date_kst_raw, e.contract_status, e.dup_cnt,
           s.robot_status, s.op_error_code,
           s.is_charging, s.is_docked, s.is_llm, s.is_vital_sign,
           s.cpu_temperature, s.wifi_rssi, s.battery_charge_rate,
           s.amr_robot_state, s.amr_action_status,
           date_diff('second', s.ts_utc, e.err_ts_utc) AS age_sec,
           row_number() OVER (PARTITION BY e.serial, e.err_ts_utc ORDER BY s.ts_utc DESC) AS rn
    FROM e09_events_rc e
    LEFT JOIN e09_status_slim s
           ON s.serial = e.serial
          AND s.ts_utc <= e.err_ts_utc
          AND s.ts_utc >  e.err_ts_utc - INTERVAL '15' MINUTE
),
pre_err AS (  -- 에러 직전 60분 내 마지막 비ERROR status
    SELECT e.serial, e.err_ts_utc,
           s.robot_status      AS pre_robot_status,
           s.clean_type        AS pre_clean_type,
           s.amr_robot_state   AS pre_amr_state,
           s.amr_action_status AS pre_amr_action,
           date_diff('second', s.ts_utc, e.err_ts_utc) AS pre_age_sec,
           row_number() OVER (PARTITION BY e.serial, e.err_ts_utc ORDER BY s.ts_utc DESC) AS rn
    FROM e09_events_rc e
    LEFT JOIN e09_status_slim s
           ON s.serial = e.serial
          AND s.ts_utc <= e.err_ts_utc
          AND s.ts_utc >  e.err_ts_utc - INTERVAL '60' MINUTE
          AND s.robot_status <> 'ERROR'
)
SELECT
    a.serial,
    a.contract_status,
    a.err_ts_utc + INTERVAL '9' HOUR AS error_at_kst,
    a.err_date_kst_raw               AS device_reported_kst,
    a.dup_cnt                        AS mqtt_msg_cnt,   -- 1 = 정상, 2 이상 = 재전송 중복
    -- 에러 시점
    a.robot_status                   AS rs_at_error,
    a.op_error_code                  AS error_code_at_error,
    a.age_sec                        AS rs_age_sec,
    COALESCE(sa.label, a.amr_robot_state)    AS amr_state_at_error,
    COALESCE(aa.label, a.amr_action_status)  AS amr_action_at_error,
    -- ERROR 직전 (원인 분석용)
    p.pre_robot_status               AS rs_before_error,
    p.pre_age_sec                    AS rs_before_age_sec,
    p.pre_clean_type                 AS clean_type_before,
    COALESCE(sb.label, p.pre_amr_state)      AS amr_state_before,
    COALESCE(ab.label, p.pre_amr_action)     AS amr_action_before,
    -- 환경
    a.is_charging, a.is_docked, a.is_llm, a.is_vital_sign,
    a.cpu_temperature, a.wifi_rssi, a.battery_charge_rate
FROM at_err a
LEFT JOIN pre_err p  ON p.serial = a.serial AND p.err_ts_utc = a.err_ts_utc AND p.rn = 1
LEFT JOIN amr_state_code  sa ON sa.code = a.amr_robot_state
LEFT JOIN amr_action_code aa ON aa.code = a.amr_action_status
LEFT JOIN amr_state_code  sb ON sb.code = p.pre_amr_state
LEFT JOIN amr_action_code ab ON ab.code = p.pre_amr_action
WHERE a.rn = 1
ORDER BY a.serial, a.err_ts_utc;

-- 4-A. 검증 — 결과 행수가 [2-A] 의 e09_cnt 와 같아야 한다 (LEFT JOIN 이라 상태가 없어도 행은 유지)
--      status 결측률도 함께 본다. 결측이 많으면 AMR 통신 두절이 status 송신까지 끊은 것.
WITH at_err AS (
    SELECT e.serial, e.err_ts_utc, s.robot_status,
           date_diff('second', s.ts_utc, e.err_ts_utc) AS age_sec,
           row_number() OVER (PARTITION BY e.serial, e.err_ts_utc ORDER BY s.ts_utc DESC) AS rn
    FROM e09_events_rc e
    LEFT JOIN e09_status_slim s
           ON s.serial = e.serial
          AND s.ts_utc <= e.err_ts_utc
          AND s.ts_utc >  e.err_ts_utc - INTERVAL '15' MINUTE
)
SELECT COUNT(*)                                    AS total_rows,
       COUNT(robot_status)                         AS status_matched,
       COUNT(*) - COUNT(robot_status)              AS status_missing,
       ROUND(AVG(age_sec), 1)                      AS avg_age_sec,
       MAX(age_sec)                                AS max_age_sec
FROM at_err WHERE rn = 1;
-- 판정:
--   · total_rows 는 [2-A] 의 e09_cnt 와 **정확히 일치**해야 한다.
--     ([2] 에서 중복 제거를 명시적으로 했으므로 이제 어긋날 수 없다.
--      예전엔 e09_events_rc 에 (serial,ts) 중복이 남아 있어 여기서 조용히 접혔다)
--   · avg_age_sec 이 13초 안팎으로 나온다. 공칭 주기 60초보다 훨씬 짧은데,
--     에러 발생이 status 즉시 발행을 유발하기 때문으로 보인다. 조사엔 유리한 조건.
--   · status_missing = E09 직전 15분간 status 가 아예 없던 건.
--     AMR 통신 두절이 status 송신까지 끊은 사례이므로 그 자체가 분석 대상이다.


-- =====================================================================
-- [5] DB 조회용 시리얼 목록 생성
--     결과 한 칸을 통째로 복사해 E09-investigation.sql [1] 의 VALUES 에 붙여넣는다.
--     (칸이 잘려 보이면 "결과 CSV 다운로드" 로 받을 것)
-- =====================================================================
SELECT array_join(array_agg(line), ',' || chr(10)) AS paste_into_mysql
FROM (
    SELECT DISTINCT '(''' || serial || ''')' AS line
    FROM e09_events_rc
) t;

-- 5-A. 기기 수 확인 (MySQL 쪽 임시테이블 건수와 일치해야 함)
SELECT COUNT(DISTINCT serial) AS device_cnt FROM e09_events_rc;


-- =====================================================================
-- [6] 정리 — 재분석 예정이면 남겨둘 것
-- =====================================================================
-- DROP TABLE e09_status_slim;   -- S3 파일은 aws s3 rm 으로 별도 삭제
-- DROP TABLE e09_events_rc;
-- DROP TABLE real_customer_serial;
```

---

## 부록 C. MySQL SQL 팩 전문 (`E09-investigation.sql`)

운영계 `benjamin` 스키마에 DataGrip으로 접속해 **세션이 유지되는 한 콘솔에서** [1]부터 순서대로 실행한다(임시테이블이라 세션이 끊기면 사라진다). 파일 안 주석이 `E09-athena.sql`을 가리키는 곳은 **이 문서의 부록 B**를 뜻한다. [1]의 예시 시리얼 두 줄은 지우고 부록 B [5]의 결과를 붙여넣는다.

```sql
-- =====================================================================
-- E09 (AMR 통신 불량) 조사 — MySQL 파트
-- 대상 DB: backend-api-main `benjamin` 스키마 (운영계) / DataGrip 실행
-- 대상 기기: E09-athena.sql [5] 에서 넘어온 "실고객 × E09 발생" 기기
-- 기간: 2026-01-01 ~ 2026-08-31 (KST)
--
-- 산출물
--   [2] 설치 시기
--   [3] OTA 진행 이력
--   [4] 관제 재부팅 명령 이력
--
-- ─────────────────────────────────────────────────────────────────────
-- ★ 이 파일로 "E09 발생 건수"를 세지 말 것
--   커밋 9c38d807 `[fix] 미연동 기기 에러 적재하지 않음` (2026-07-01) 이후
--   미연동 기기의 에러는 history_error 에 적재되지 않는다.
--   → 1~6월엔 포함, 이후엔 제외 = 기간 안에서 모집단이 바뀐 테이블이다.
--   발생 이력·상태는 Athena(MQTT 원본)에서 뽑는다. E09-athena.sql 참조.
--
--   여기서 뽑는 3종은 MQTT 페이로드에 아예 없는 정보라 DB 가 유일한 소스다:
--     설치일(사람이 입력) / OTA job 메타(서버가 생성) / 재부팅 조작자(login_id)
--
-- ★ 시간대 — 이 파일의 모든 시각 컬럼은 KST
--   history_control.request_date     = DB NOW()  → KST
--   firmware_job_target.create_date  = DB NOW()  → KST
--   Athena 결과(UTC)와 붙일 때는 Athena 쪽에 +9h 하면 같은 기준이 된다.
--
-- ★ 실행 주의
--   임시테이블이라 **세션이 유지되는 한 콘솔**에서 [1]부터 순서대로 실행할 것.
-- =====================================================================


-- =====================================================================
-- [1] 대상 기기 목록 — Athena [5] 결과 붙여넣기
--
-- ※ serial 의 CHARACTER SET/COLLATE 를 반드시 명시할 것.
--   생략하면 DB 기본값(utf8mb4_general_ci)이 붙는데 device/history_control/
--   firmware_job_target 의 serial 은 utf8mb4_0900_ai_ci 라
--   조인 시 ERROR 1267 (Illegal mix of collations) 이 난다.
-- =====================================================================
DROP TEMPORARY TABLE IF EXISTS tmp_e09_dev;
CREATE TEMPORARY TABLE tmp_e09_dev (
    serial VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL PRIMARY KEY
) ENGINE=InnoDB;

INSERT INTO tmp_e09_dev (serial) VALUES
('WRBA1M10KRNGJ2925K02663'),
('WRBA1M10KRDWJ1425L02188')
-- ↑ 이 두 줄을 지우고 Athena [5] 의 paste_into_mysql 결과를 통째로 붙여넣을 것
;

-- 1-A. 건수 확인 — Athena [5-A] 의 device_cnt 와 같아야 한다
SELECT COUNT(*) AS target_device_cnt FROM tmp_e09_dev;

-- 1-B. DB 에 device 행이 아예 없는 기기 (있으면 [2]~[4] 에서 빠지므로 미리 파악)
SELECT t.serial
FROM tmp_e09_dev t
LEFT JOIN device d ON d.serial = t.serial
WHERE d.id IS NULL;


-- =====================================================================
-- [2] 설치 시기
--     ※ 재등록·기기 교체로 serial 당 device 행이 여러 개일 수 있어 전부 나열한다.
--       linked_yn 으로 거르지 않는다 — 연동 해제된 기기의 설치 이력도 필요하다.
-- =====================================================================
SELECT
    t.serial,
    d.id                AS device_id,
    d.install_date,
    d.create_date       AS device_row_created,
    d.linked_yn,
    d.auto_update_yn,
    m.model_name,
    f.version           AS registered_firmware_version,
    c.contract_no,
    c.contract_date,
    c.rd_nm_addr        AS install_address
FROM tmp_e09_dev t
JOIN device d ON d.serial = t.serial
LEFT JOIN model    m ON m.id = d.model_id
LEFT JOIN contract c ON c.id = d.contract_id
LEFT JOIN firmware f ON f.id = d.firmware_id
ORDER BY t.serial, d.id;


-- =====================================================================
-- [3] OTA 진행 이력
--     firmware_job.type : '01' 유저, '02' 관제
--     status            : PENDING / IN_PROGRESS / SUCCEEDED / FAILED
--
--     ※ status·completed_date 는 IoT Job 실행 이벤트를 받아 갱신된다
--       (IotJobService.updateStatusByJobIdAndTargetId).
--       "SUCCEEDED 인데 기기 펌웨어가 안 바뀐" 경우가 있을 수 있으므로,
--       실제 적용 여부는 Athena status 토픽의 firmware 값 변화로 교차검증할 것.
-- =====================================================================
SELECT
    fjt.serial,
    fj.job_id,
    f.version              AS target_version,
    f.major_version,
    f.note                 AS firmware_note,
    CASE fj.type WHEN '01' THEN '유저' WHEN '02' THEN '관제' ELSE fj.type END AS job_type,
    fjt.status,
    fjt.create_date        AS ota_started_at_kst,
    fjt.completed_date     AS ota_completed_at_kst,
    fjt.version            AS reported_version,
    fjt.fail_reason_device,
    fj.create_login_id     AS job_creator
FROM tmp_e09_dev t
JOIN firmware_job_target fjt ON fjt.serial = t.serial
JOIN firmware_job        fj  ON fj.id = fjt.firmware_job_id
LEFT JOIN firmware       f   ON f.id = fj.firmware_id
WHERE fjt.create_date >= '2026-01-01' AND fjt.create_date < '2026-09-01'
  AND IFNULL(fj.delete_yn, 'N') = 'N'
ORDER BY fjt.serial, fjt.create_date;


-- =====================================================================
-- [4] 관제 재부팅 명령 이력  (op_command = 'OPER0002' — 재부팅 전용)
--
--     ※ 앱/운영 경로(/device/request/operation/reboot)와
--       관제 경로(/control/robot/reboot)가 **같은 op_command** 를 남긴다.
--       주체 구분은 login_id 로 해야 한다 → 4-A 로 계정 분포를 먼저 파악할 것.
--
--     ※ result=1 / message='reboot successfully' 는 **명령 수신 응답**이지
--       재기동 성공이 아니다. 실제 재부팅 여부는 명령 직후 status 송신이
--       끊겼다가 재개되는 패턴으로 Athena 에서 확인해야 한다.
-- =====================================================================

-- 4-A. 명령 주체 분포 (관제 계정 식별용) — 대상 기기 한정
SELECT hc.login_id, COUNT(*) AS cnt,
       MIN(hc.request_date) AS first_at, MAX(hc.request_date) AS last_at
FROM tmp_e09_dev t
JOIN history_control hc ON hc.serial = t.serial
WHERE hc.op_command = 'OPER0002'
  AND hc.request_date >= '2026-01-01' AND hc.request_date < '2026-09-01'
GROUP BY hc.login_id
ORDER BY cnt DESC;

-- 4-B. 재부팅 명령 전체
SELECT
    hc.serial,
    hc.request_date  AS commanded_at_kst,
    hc.response_date AS responded_at_kst,
    hc.login_id      AS commanded_by,
    hc.result        AS ack_success,
    hc.message       AS ack_message,
    hc.is_pui,
    hc.correlation_id,
    TIMESTAMPDIFF(SECOND, hc.request_date, hc.response_date) AS ack_sec
FROM tmp_e09_dev t
JOIN history_control hc ON hc.serial = t.serial
WHERE hc.op_command = 'OPER0002'
  AND hc.request_date >= '2026-01-01' AND hc.request_date < '2026-09-01'
  AND IFNULL(hc.delete_yn, 'N') = 'N'
ORDER BY hc.serial, hc.request_date;
```

---

## 부록 D. AMR팀 전달 컬럼 정의서 전문

AMR팀에 실제로 전달한 문서를 그대로 옮겼다(제목 수준만 이 문서에 맞춰 낮췄다). 문의 대응 시 AMR팀이 보고 있는 문서와 같은 문장으로 답하기 위해 전문을 둔다.

### E09(AMR 통신 불량) 조사 데이터 — 컬럼 정의서

작성: 데이터플랫폼팀
대상 기간: 2026-01-01 ~ 2026-08-31 (KST)
대상 기기: 실고객 계약 기기 (계약확정 + 해약확정 전체)

---

#### 1. 이 데이터는 어디서 나온 것인가

**파일 1**은 기기가 MQTT로 올린 **원본 메시지**에서 추출했습니다. 서버가 가공·요약한 값이 아니라 기기가 실제로 보낸 값입니다.

| 사용한 토픽 | 발행 주기 | 역할 |
|---|---|---|
| `skmg/airbot/{serial}/v1/error` | 에러 발생 시 1건 | **E09 발생 시각**의 기준점 |
| `skmg/airbot/{serial}/v1/status` | **60초 주기** + on demand | 그 시각의 **기기 상태** |

두 토픽은 서로 다른 메시지라 한 건으로 묶여 오지 않습니다. **E09 발생 시각을 기준으로 그 직전에 도착한 status 메시지를 찾아 붙이는** 방식으로 만들었습니다.

> 주의: **해상도 한계**: status는 60초 주기이므로 "E09 발생 순간"이 아니라 **"최대 60초 전의 상태"** 입니다. 각 행의 `rs_age_sec`(몇 초 전 값인지)으로 신뢰도를 판단해 주세요.

**파일 2~4**(설치·OTA·재부팅)는 MQTT에 없는 정보라 서버 DB에서 추출했습니다.

---

#### 2. 공통 규약

##### 시간대
**모든 시각 컬럼은 KST(한국 시간)입니다.** MQTT 원본 `timestamp`는 UTC라 +9시간 변환해 내보냈습니다.

##### 빈 값(NULL·공백)의 의미
빈 값은 "데이터 없음"이 아니라 **그 자체가 관측 결과**입니다.

| 어디가 비었나 | 의미 |
|---|---|
| `rs_at_error` 계열이 빔 | E09 직전 15분간 **status 메시지가 한 건도 안 왔음** → 기기가 상태 송신을 멈춘 상태 |
| `amr_state_*` 계열만 빔 | status는 왔는데 그 안에 `amrStatus`가 없었음 → **SoC가 AMR 값을 못 채운 상태** |
| `rs_before_error`가 빔 | 직전 60분 내내 `ERROR` 상태였음 → 이전 에러에서 회복되지 않은 상태 |

##### AMR 상태 코드표

원본은 숫자(`amrStatus.robotStatus`, `amrStatus.actionStatus`)이며 읽기 쉽도록 라벨로 변환했습니다. AMR팀이 제공한 정의(2026-08-25)를 그대로 반영했으며, 표에 없는 값은 숫자 그대로 출력됩니다.

**`amr_state_*` — AMR 로봇 상태 (`amrStatus.robotStatus`)**

| 코드 | 라벨 | 코드 | 라벨 |
|---|---|---|---|
| 0 | IDLE | 6 | UNDOCKING |
| 1 | AUTO_MAPPING | 7 | ONSTATION |
| 2 | MANUAL_MAPPING | 8 | FACTORY_NAVIGATION |
| 3 | NAVIGATION | 9 | ERROR |
| 4 | RETURN_CHARGER | 10 | FOLLOW_ME |
| 5 | DOCKING | 11 | MANUAL_CONTROL |

**`amr_action_*` — AMR 액션 상태 (`amrStatus.actionStatus`)**

| 코드 | 라벨 | 코드 | 라벨 |
|---|---|---|---|
| 0 | VOID | 4 | RESUME |
| 1 | READY | 5 | COMPLETE |
| 2 | START (RUN) | 6 | **FAIL** |
| 3 | PAUSE | | |

##### 플랫폼 로봇 상태 (`rs_*` 컬럼)

`operation.robotStatus`로, **SoC(Android) 쪽이 판단한 상태**입니다.

`READY` · `MOVE_START` · `MOVING` · `MOVE_COMPLETE` · `CLEAN_START` · `CLEANING` · `CLEAN_COMPLETE` · `CHARGING` · `RETURN_START` · `RETURNING` · `RETURN_COMPLETE` · `DOCKING` · `ERROR` · `AQSCAN_START` · `AQSCANING` · `AQSCAN_COMPLETE` · `AQMAPSCAN_START` · `AQMAPSCANING` · `AQMAPSCAN_COMPLETE` · `UPDATING` · `MAPPING` · `EDIT_MAP` · `EDIT_AP` · `FOLLOW_ME` · `FIXED_SECURITY` · `PATROL_SECURITY` · `STREAMING` · `UNKNOWN`

> 참고: **`amr_*`와 `rs_*`는 서로 다른 계층입니다.** `rs_*`는 SoC의 판단, `amr_*`는 AMR 보드가 UDP로 올려준 값입니다. **두 값이 어긋나는 지점**이 이번 조사의 관심사입니다.

---

#### 3. 파일 1 — `E09_발생이력_및_상태.csv`

**E09 에러 1건 = 1행.** 발생 시각과 그 시점의 기기 상태를 한 행에 담았습니다.

##### 식별 · 시각

| 컬럼 | 의미 |
|---|---|
| `serial` | 기기 시리얼 번호 |
| `contract_status` | 계약 상태 (`계약확정` / `해약확정`) |
| `error_at_kst` | **E09 발생 시각 (KST)** — 기기가 에러 메시지를 발행한 시각 |
| `device_reported_kst` | 기기가 에러 메시지 안에 직접 적어 보낸 발생 시각. `error_at_kst`와 크게 어긋나면 해당 기기의 시계가 틀어진 것 |
| `mqtt_msg_cnt` | 이 에러에 해당하는 원본 MQTT 메시지 수. **보통 1**이며, 2 이상이면 전송 재시도로 같은 메시지가 중복 수신된 것입니다(에러가 여러 번 난 것이 아님). 다른 집계와 건수를 맞출 때 참고용 |

##### 에러 시점 — "어떤 에러 상태였나"

| 컬럼 | 의미 | 읽는 법 |
|---|---|---|
| `rs_at_error` | E09 직전 마지막 status의 로봇 상태 | 대부분 `ERROR`로 나옵니다(E09 발생과 동시에 전환). **비어 있으면 status 송신 자체가 끊긴 것** |
| `error_code_at_error` | 그 status에 실려 있던 에러 코드 | `E09`로 나와야 두 토픽이 일치. **다른 코드면 같은 시점에 다른 에러도 함께 발생**한 것 |
| `rs_age_sec` | 그 status가 에러보다 **몇 초 전** 값인지 | 주기가 60초이므로 **0~60이 정상**. 수백 초면 에러 이전부터 이미 송신이 지연되고 있었다는 신호 |
| `amr_state_at_error` | 에러 시점의 AMR 로봇 상태 | |
| `amr_action_at_error` | 에러 시점의 AMR 액션 상태 | |

##### ERROR 직전 — "무엇을 하던 중이었나" ★ 원인 분석용

| 컬럼 | 의미 | 읽는 법 |
|---|---|---|
| `rs_before_error` | `ERROR`가 **아니었던** 마지막 로봇 상태 | `CLEANING`/`MOVING`이면 주행 중 발생, `CHARGING`/`READY`면 정지 중 발생 |
| `rs_before_age_sec` | 그 값이 몇 초 전인지 | **60~120초면 방금 전환된 것** = 이번 에러의 직접 선행 상태로 신뢰 가능. **수천 초면 이전 에러의 잔상**이므로 해석에서 제외해 주세요 |
| `clean_type_before` | 그때의 공기청정 모드 코드 | |
| `amr_state_before` | ERROR 직전의 AMR 로봇 상태 | |
| `amr_action_before` | ERROR 직전의 AMR 액션 상태 | |

##### 환경 값 (상관 확인용)

| 컬럼 | 의미 | 왜 넣었나 |
|---|---|---|
| `is_charging` | 충전 중 여부 (true/false) | 충전 중 전원 노이즈 가설 |
| `is_docked` | 도킹 여부 (true/false) | 스테이션 접점 관련 가설 |
| `is_llm` | LLM 동작 중 여부 | 부가 기능 부하가 통신에 영향을 주는지 |
| `is_vital_sign` | 바이탈사인 기능 동작 여부 | 상동 |
| `cpu_temperature` | CPU 보드 온도 (°C) | 과열로 인한 처리 지연 가설 |
| `wifi_rssi` | Wi-Fi 수신 감도 | **배제용**. AMR-SoC는 유선 통신이므로 원래 무관해야 정상. 상관이 보이면 다른 요인이 있다는 뜻 |
| `battery_charge_rate` | 배터리 잔량 (%, 0~100) | 저전압 상황 상관 |

---

#### 4. 파일 2 — `E09_기기_설치시기.csv`

E09가 발생한 기기의 설치 정보입니다. **기기 1대 = 1행**이 원칙이나, 재등록·기기 교체 이력이 있으면 여러 행이 나올 수 있어 전부 포함했습니다.

| 컬럼 | 의미 |
|---|---|
| `serial` | 기기 시리얼 |
| `device_id` | 시스템 내부 기기 ID (같은 시리얼에 여러 행이 있을 때 구분용) |
| `install_date` | **설치일** |
| `device_row_created` | 시스템에 기기가 등록된 일시 |
| `linked_yn` | 현재 연동 상태 (Y/N) |
| `auto_update_yn` | 펌웨어 자동 업데이트 설정 여부 |
| `model_name` | 모델명 |
| `registered_firmware_version` | 시스템에 등록된 펌웨어 버전 |
| `contract_no` | 계약번호 |
| `contract_date` | 계약일자 |
| `install_address` | 설치 주소 (도로명) |

---

#### 5. 파일 3 — `E09_OTA이력.csv`

E09가 발생한 기기의 펌웨어 업데이트 이력입니다. **OTA 작업 1건 = 1행.**

| 컬럼 | 의미 |
|---|---|
| `serial` | 기기 시리얼 |
| `job_id` | OTA 작업 ID |
| `target_version` | 배포하려던 펌웨어 버전 |
| `major_version` | 대분류 버전 |
| `firmware_note` | 해당 펌웨어 설명 (변경 내역) |
| `job_type` | 작업 주체 — `유저` / `관제` |
| `status` | 진행 상태 — `PENDING`(대기) / `IN_PROGRESS`(진행중) / `SUCCEEDED`(성공) / `FAILED`(실패) |
| `ota_started_at_kst` | 작업 생성 일시 |
| `ota_completed_at_kst` | 완료 일시 |
| `reported_version` | 기기가 회신한 버전 정보 |
| `fail_reason_device` | 실패 사유 (기기 보고) |
| `job_creator` | 작업 생성자 계정 |

> 주의: `status`는 서버가 IoT Job 실행 이벤트를 받아 갱신한 값입니다. **`SUCCEEDED`가 곧 기기 반영 완료를 보장하지는 않습니다.** 실제 적용 여부가 중요한 건이면 파일 1의 발생 시각과 대조해 확인해 주세요.

---

#### 6. 파일 4 — `E09_재부팅이력.csv`

E09가 발생한 기기에 내려진 **재부팅 명령** 이력입니다. **명령 1건 = 1행.**

| 컬럼 | 의미 |
|---|---|
| `serial` | 기기 시리얼 |
| `commanded_at_kst` | 재부팅 **명령 시각** |
| `responded_at_kst` | 기기 응답 시각 |
| `commanded_by` | 명령을 내린 계정 |
| `ack_success` | 기기의 명령 수신 성공 여부 (1=성공) |
| `ack_message` | 수신 응답 메시지 |
| `ack_sec` | 명령 후 응답까지 걸린 시간(초) |
| `is_pui` | 기기 화면(PUI)에서 조작했는지 여부 |
| `correlation_id` | 명령 추적용 ID |

> 주의: **`ack_success`는 "명령을 받았다"는 뜻이지 "재기동에 성공했다"는 뜻이 아닙니다.** 실제 재부팅 여부는 명령 직후 상태 송신이 끊겼다가 재개되는지로 판단해야 합니다.

---

#### 7. 저희가 확인하고 싶은 것

데이터를 보실 때 아래 판단을 함께 주시면 감사하겠습니다.

1. **`amr_state_at_error`가 비어 있거나 `amr_state_before`와 동일하게 고정된 경우** — AMR 보드가 응답을 멈춘 것으로 봐도 되는지, 아니면 SoC가 값을 못 채운 다른 경로가 있는지
2. **`amr_action_before`가 `FAIL`인 경우** — AMR이 실패를 *보고한* 것이므로 통신 자체는 살아 있었다고 봐야 하는지
3. **`rs_before_error` 분포가 특정 상태(`CLEANING`/`NAVIGATION` 등)에 쏠릴 경우** — 해당 동작 중 AMR 통신이 취약해지는 알려진 원인이 있는지
4. **E09 판정 조건** — 몇 초간 무응답이면 E09로 올리는지. 이 값을 알면 `rs_age_sec` 분포와 대조해 검증할 수 있습니다
5. **특정 펌웨어 버전과의 상관** — 파일 3의 버전별로 발생 빈도가 갈리는지 확인이 필요하면 저희 쪽에서 결합해 드리겠습니다

---

#### 8. 알려진 한계 (해석 시 감안해 주세요)

| 항목 | 내용 |
|---|---|
| **시간 해상도** | status가 60초 주기라 에러 순간이 아닌 최대 60초 전 상태입니다. `rs_age_sec`으로 확인해 주세요 |
| **에러 직전 창** | `rs_at_error`는 직전 **15분**, `rs_before_error`는 직전 **60분** 내에서 찾았습니다. 그 밖이면 빈 값 |
| **중복 발생** | 같은 기기가 시간차를 두고 E09를 반복해 올리면 그대로 여러 행이 됩니다(중복 제거하지 않음). 다만 **동일 시각(초 단위)에 중복 수신된 메시지**는 전송 재시도로 판단해 1행으로 합쳤고, 그 사실을 `mqtt_msg_cnt`에 남겼습니다 |
| **대상 기기** | 실고객 계약 기기만 필터링했습니다. 계약확정·해약확정을 모두 포함하며 `contract_status`로 구분할 수 있습니다 |
| **관제 화면과의 건수 차이** | 이 자료는 MQTT 원본 기준입니다. 서버 DB는 2026년 7월경부터 **연동 해제 상태 기기의 에러를 적재하지 않도록** 변경되어 기간 전후로 수집 범위가 달라졌고, 조회 시에도 계약·고객 정보가 연결된 기기만 집계합니다. **이 자료는 그런 변경과 무관하게 1~8월 전 구간 동일한 기준으로 추출**했습니다 |
| **파일 2~4** | 서버 DB 기준이라 연동 해제·계약 종료로 정보가 정리된 기기는 일부 항목이 비어 있을 수 있습니다 |

---

문의: 데이터플랫폼팀
