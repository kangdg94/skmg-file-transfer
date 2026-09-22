# [작업] 맵 변환·공기질 히트맵 SQS 전환과 SQS 소비 DLQ 수정

| 항목 | 내용 |
|---|---|
| **상태** | (1) 맵 변환 SQS 전환: 코드 완료 · 원격 브랜치 반영 · **어느 환경에도 미배포 · 미머지** / (2) 히트맵 SQS 전환: **설계만, 코드 0줄** / (3) SQS 소비 DLQ 수정: **dev·stg·main 전부 반영 완료**, 배포 여부 미확인 |
| **작업 기간** | 2026-07-27 ~ 2026-08-10 (맵 변환 7/27~8/3, DLQ 8/6~8/10, 히트맵 설계 8/10) |
| **직접 수정한 저장소** | `backend-map-converter`, `backend-api-main`, `db-schema` |
| **요청서를 전달한 대상** | 관제 프론트(`frontend-web-backoffice`) — shapeList 폴백 (§6) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §8-1의 1번(`backend-api-main` `refactor/mapconvert` 리베이스 — 425커밋 뒤처짐)과 2번(db-schema 저장소와 실제 dev/stg DB의 불일치 확인) |

---

## 0. 세 줄 요약

1. 로봇 지도(맵) 변환 파이프라인을 **동기 HTTP 호출에서 SQS 비동기로 재설계**했다. 코드는 세 저장소의 `refactor/mapconvert` 브랜치에 전부 들어가 있고 원격에도 올라가 있지만, **머지도 배포도 하지 않았다.** 신·구 경로가 플래그로 공존하므로 배포 자체는 안전하나, **DDL이 반드시 먼저** 들어가야 한다.
2. 공기질 히트맵을 같은 방식으로 SQS 전환하는 건은 **설계 결정만 남아 있고 코드는 한 줄도 없다.** 별건이던 "aq_map 전용 큐 분리"가 이 설계에 흡수됐다.
3. 별개로, 2026-08 운영계 IoT 메시지 SQS 전환 직후 DLQ가 쌓인 사고를 수정했다. 이쪽은 **dev·stg·main 세 브랜치 모두 내용이 들어가 있다**(브랜치 커밋 해시는 다르다 — §2-3). 배포 후 실측할 항목 3건이 남았다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **맵(지도) 변환** | 로봇이 SLAM으로 만든 원시 지도(`.pgm` 흑백 이미지 + `.yaml` 메타)를 서비스가 쓸 수 있는 형태(`.png` 이미지 + `.json` 공간정보 + `.yaml`)로 가공하는 처리. CPU를 오래 쓰며 실측 대부분 40초 이내, 최악 12분. |
| **pgm / yaml** | SLAM 산출물. `.pgm`은 픽셀 흑백 이미지, `.yaml`은 해상도(resolution)와 원점(origin)을 담은 메타. 둘을 합쳐야 픽셀 좌표를 로봇 좌표로 바꿀 수 있다. |
| **refiner (`sk_map_refiner`)** | 실제 변환을 수행하는 **외부 네이티브 바이너리**. 저장소에 없고 배포 산출물로 워커 호스트 `/app/refiner/`에 놓인다. 소스도 우리 소유가 아니다. |
| **`backend-map-converter`** | 변환 워커 서버. Maven, Spring Boot 3.2.10, 패키지 `com.skix.mapconverter`(리팩토링 전에는 이전 개발사 이름의 패키지였고, `origin/dev`에는 아직 그 이름이 남아 있다). EC2에서 돌고 map NLB 뒤 포트 `28089`로 노출된다. |
| **`backend-api-main`** | 앱·관제의 주 백엔드. Gradle, Spring Boot 3.2.4, 패키지 `com.skmagic`. 맵 변환의 **오케스트레이터**이자 상태의 유일한 진실 원천. |
| **conv json / edit json** | 맵 json은 두 종류다. **conv**는 변환 산출물로 `shapeList[{id,name,position[{x,y}]}]` 스키마. **edit**는 앱·관제에서 편집을 저장할 때 만들어지는 `room_list` 풀 스키마. DB `user_map.map_data`에는 **edit만** 저장된다. 이 이중 포맷이 §6 문제의 원인이다. |
| **공기질 히트맵** | 로봇이 집 안을 돌며 측정한 공기질 점 데이터를 IDW(역거리가중) 보간으로 면(面)으로 펼쳐 지도 위에 색으로 그리는 것. |
| **`backend-hitmap`** | 히트맵 연산 전용 서버. Maven, Spring Boot 3.5.3 / Java 17, 패키지 `com.skmagic.hitmap`. EC2 + NLB로 떠 있고 엔드포인트는 `POST /hitmap` 하나뿐이다. |
| **IDW** | Inverse Distance Weighting. 히트맵 보간 알고리즘. 라이브러리가 `idw_1.5.0.jar`로 `backend-api-main/libs/`에 들어 있다(`build.gradle:78`). |
| **`aqi_map_refiner`** | 공기질 지도 후처리용 외부 네이티브 바이너리. `backend-api-main` 호스트의 `/aqMap/aqi_map_refiner` 경로에서 `ProcessBuilder`로 실행된다(`ResponseDeviceService:4327`). |
| **outbox** | "DB 커밋과 메시지 발행을 원자적으로 묶는" 패턴. 발행할 메시지를 같은 트랜잭션에서 DB에 써 두고, 커밋 후에 실제로 발행한다. 여기서는 **별도 테이블 없이 `user_map_convert`에 컬럼을 추가**해 구현했다. |
| **reconciliation(수렴)** | 메시지가 유실되거나 DLQ로 빠져도 DB 상태가 영원히 중간 상태로 남지 않도록, 주기적으로 오래된 미완료 행을 강제 종결하는 스케줄러. |
| **DLQ (Dead Letter Queue)** | 지정 횟수만큼 처리에 실패한 SQS 메시지가 옮겨지는 큐. 여기 쌓이면 그 작업은 자동으로는 더 진행되지 않는다. |
| **visibility timeout** | SQS가 메시지를 소비자에게 넘긴 뒤 다른 소비자에게 보이지 않게 숨겨 두는 시간. 이 시간 안에 삭제(ack)하지 못하면 다시 배달된다. |
| **FIFO 큐 / MessageGroupId** | 순서를 보장하는 SQS 큐. 같은 `MessageGroupId`의 메시지는 앞의 것이 처리될 때까지 뒤의 것이 처리되지 않는다. §1-2의 연쇄 실패 원인. |
| **op_param** | `history_control` 테이블의 제어 파라미터 컬럼. `TEXT`(65,535바이트). |

### 1-2. 문제

세 갈래 작업이지만 뿌리는 같다 — **CPU가 오래 걸리는 처리를 동기 HTTP로 붙들고 있으면 구조적으로 깨진다**는 것.

**(1) 맵 변환 — 구조적 결함 8가지**

| # | 문제 | 실제로 일어난 일 |
|---|---|---|
| 1 | 워커가 `LinkedBlockingQueue` 인메모리 큐 + 단일 스레드 | 서버 재시작·크래시 시 대기·처리 중 작업이 **전부 유실되고 아무에게도 통보되지 않았다** |
| 2 | 워커 예외 시 완료 콜백(`callResultAPI`) 미호출 | 오케스트레이터가 **영원히 대기**. 설치 현장에서 기사가 계속 기다렸다 |
| 3 | `waitFor()` 무한 블록 + stdout을 전부 읽은 뒤 stderr를 읽는 파이프 처리 | refiner **교착 → 워커 영구 정지** 가능 |
| 4 | 전 엔드포인트 `isAuthorized = true` 하드코딩 | 인증이 사실상 비활성 |
| 5 | AWS 키·장기 JWT 평문 커밋 | 시크릿 노출(git 이력 포함) |
| 6 | `/test/map`, `/mock/**` 무방비 노출 | 공격 표면 |
| 7 | 동일 응답 중복 처리 방어 부재 | `user_map_convert`에 **중복 22그룹/3,718행**, 최악은 동일 job이 5초 간격 **3,598회** |
| 8 | 이전 개발사 이름이 박힌 패키지·클래스명 + 대량 죽은 코드 | main 소스 47개 중 다수가 미사용 |

여기에 별개 사건이 하나 더 있었다. 과거 커밋 `bc0fef7f`/`0f2ab978`가 pgm 업로드를 **기기가 사용 중인 맵의 in-place 교체**로 바꿨다: `map_data=NULL` UPDATE + conv 이미지 DELETE + pgm 참조 덮어쓰기를 **변환을 시도하기도 전에 커밋**했다. 변환이 비동기로 실패하면 되돌릴 원본이 없어 "기기 먹통"이 됐다. 이후 `25569f21`로 원복됐지만 **파괴적 쿼리 3종이 죽은 코드로 남아** 재도입 위험이 있었다.

**(2) 히트맵 — 동기 호출이 SQS 소비 스레드를 점유한다**

기기가 공기질 스캔을 마치면 `aq_map` MQTT 메시지가 오고, `backend-api-main`이 그 처리 중에 `backend-hitmap`을 **동기 HTTP로 호출한다.** 이 핸들러는 IoT 메시지를 소비하는 SQS 리스너 스레드 위에서 돈다. 즉 히트맵 연산이 늘어지면 **IoT 메시지 큐의 visibility timeout(60초)을 넘겨 메시지가 재배달되고, FIFO라 같은 기기의 뒤 메시지까지 함께 막힌다.**

**(3) SQS 소비 DLQ 적재 — 2026-08-06 운영계 사고**

운영계를 MQTT 직접 구독에서 SQS(FIFO, visibility 60초) 소비로 전환한 직후 DLQ가 쌓였다. 원인이 두 층으로 겹쳐 있었다.

- **핸들러 버그**: `getSchedule` 응답 전문에 스케줄별 `scheduleTaskList`가 중첩돼 `op_param` `TEXT`(65,535바이트)를 실제로 넘겼다(`reserve_clean` 48건). `getHoliday` 전문은 `message` `VARCHAR(400)`을 넘겼다. 둘 다 `DataTruncation`.
- **FIFO 증폭**: awspring 3.1.1의 `OrderedMessageSink`는 그룹 앞 메시지가 실패하면 **뒤 메시지를 실행조차 하지 않고 ack하지 않는다.** 기기 1대의 8개 토픽이 통째로 DLQ로 갔다.

### 1-3. 목표

- 맵 변환을 **at-least-once + visibility + DLQ**로 옮겨 유실·무한대기·중복을 구조로 해결한다.
- 기존 HTTP 경로를 **살려 둔 채 플래그로 공존**시켜, 롤백이 코드 되돌리기가 아니라 플래그 끄기가 되게 한다.
- 히트맵도 같은 하네스로 옮겨 SQS 소비 스레드에서 떼어낸다(설계만).
- DLQ 사고는 **영구 실패와 일시 실패를 구분**해서, 재실행해도 결과가 같은 실패만 ack하고 나머지는 재시도하게 한다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 맵 변환 SQS 전환 — 저장소별

| 저장소 | 브랜치 | HEAD | 원격 반영 | dev 대비 | 비고 |
|---|---|---|---|---|---|
| `backend-map-converter` | `refactor/mapconvert` | `2d4f02e` (2026-07-27) | 반영(local = origin) | **0커밋 뒤, 1커밋 앞** | `origin/dev` HEAD가 `841f7a9`(2026-07-10)에서 멈춰 있다. **fast-forward 머지가 그대로 된다** |
| `backend-api-main` | `refactor/mapconvert` | `2642bc33` (2026-07-31) | 반영(local = origin) | **425커밋 뒤, 1커밋 앞** | 분기점은 `4f0d3f03`(2026-07-30). 해소 판단은 §8-1의 1번과 부록 D |
| `db-schema` | `refactor/mapconvert` | `e8f745f` (2026-07-30) | 반영(local = origin) | **`main` 대비 27커밋 뒤, 1커밋 앞** | 이 저장소는 브랜치가 `main` 하나뿐이다(dev/stg 브랜치 없음) |

세 커밋 모두 **어느 통합 브랜치에도 머지되지 않았다** (`merge-base --is-ancestor` 전부 NO).

### 2-2. 맵 변환 — 배포·DB·인프라

| 항목 | dev | stg | prd | 근거 |
|---|---|---|---|---|
| 요청/결과 큐 및 DLQ 생성 | 완료 | 완료 | 완료 | 2026-08-03 작업 기록. 로컬에서 AWS 콘솔 확인 불가 → **재확인 필요** |
| visibility(요청 2400 / 결과 900 / DLQ 30), maxReceive 3 | 완료 | 완료 | 완료 | 위와 같음 |
| WAS·MAP IAM 정책 연결, EC2에서 큐 접근 확인 | 완료 | 완료 | 완료 | 위와 같음 |
| `sqs:CreateQueue` 미허용 확인 | 완료 | 완료 | 완료 | 위와 같음 |
| S3 `result/` lifecycle 미설정 확인 | 완료 | 완료 | 완료 | 위와 같음 |
| DB 백업·중복 정리·outbox 컬럼·backfill | 완료 | 완료 | **미실행** | 위와 같음 |
| `UNIQUE(job_id)`, 상태 인덱스 2종 | 완료 | 완료 | **미실행** | 위와 같음 |
| **신규 애플리케이션 배포** | **미실행** | **미실행** | **미실행** | 브랜치가 머지되지 않았으므로 확정 |
| SQS 컷오버 | 미실행 | 미실행 | 미실행 | 위와 같음 |
| DLQ/timeout/side-effect 알람 | 미실행 | 미실행 | 미실행 | 위와 같음 |

**주의 — 실측으로 드러난 불일치 1건.** 위 표의 "dev/stg DB outbox 컬럼 완료"는 2026-08-03에 실제 DB에 직접 적용한 기록이다. 그런데 **`db-schema` 저장소의 `stg.sql`(`origin/main`)에는 outbox 컬럼이 없다.** `user_map_convert` 정의에 `status`·`request_payload`·`enqueue_attempts`·`last_enqueue_error`·`last_enqueue_at`·`ux_user_map_convert_job_id`·인덱스 2종이 전부 없다(grep 0건). 즉 **저장소의 스키마 정의와 실제 dev/stg DB가 어긋나 있다.** 어느 쪽이 맞는지는 실제 DB에 `SHOW CREATE TABLE user_map_convert`를 쳐 봐야 확정된다(§8-1의 2번).

### 2-3. SQS 소비 DLQ 수정 — 반영 상태

작업 브랜치는 `backend-api-main` `bugfix/sqs-dlq-op-param-truncation`(원격 반영, HEAD `292b1248`). **여기서 함정이 하나 있다.**

| 커밋 | origin/dev | origin/stg | origin/main |
|---|---|---|---|
| `249892b1` op_param 클램프 + FIFO 연쇄 차단 | 반영 | 반영 | **미반영** |
| `22aaf21d` 히트맵 전용 RestTemplate 분리 | 반영 | 반영 | **미반영** |
| `24b56052` 예외 분류 보완 | 반영 | 반영 | **미반영** |
| `5b4b1180` `SdkException.retryable()` 오분류 제거 | 반영 | 반영 | **미반영** |
| `292b1248` 타임아웃·커넥션 풀 조정 | 반영 | 반영 | **미반영** |

**커밋 해시로만 보면 운영계에 없는 것처럼 보이지만, 내용은 들어가 있다.** 같은 5개 변경이 `stg` 브랜치 위에 별도 커밋으로 다시 올라갔고(`857f0830`, `fbe68933`, `b981fc21`, `075b4e0a`, `60f0643e` — 전부 2026-08-06), 그것이 `1ae05f13` "Merge branch 'stg' into 'main'"으로 `main`에 들어갔다. 코드로 확인했다.

- `origin/main`의 `api/device/control/model/HistoryControl.java`에 `OP_PARAM_MAX_BYTES`·`clampUtf8Bytes`가 **있다**.
- `core/configuration/IotInboundMessageHandler.java`는 `origin/main`과 `origin/dev`가 **바이트 단위로 동일**하다.
- `SystemConstants`의 `API_RESPONSE_TIMEOUT=10000`, `API_POOL_MAX_TOTAL=100`, `API_POOL_MAX_PER_ROUTE=20`이 dev·main 양쪽에 있다.

**따라서 `merge-base --is-ancestor`만으로 판정하면 틀린다.** 이 저장소는 dev와 stg가 같은 변경을 서로 다른 커밋으로 갖는 경우가 있으니 **내용으로 확인**해야 한다.

추가로 알아 둘 것: **`origin/main`의 트리 해시가 `60f0643e`(2026-08-06)와 바이트 단위로 같다.** 2026-09-01 `7d9ac170` [revert]가 8월 하순 핫픽스를 전부 되돌렸기 때문이다. 즉 운영계 코드는 사실상 **이 DLQ 수정이 마지막 상태**다.

| 항목 | 상태 |
|---|---|
| 애플리케이션 배포(dev/stg/prd) | **미확인.** 로컬에서 ECS·EC2를 볼 수 없다. VDI에서 태스크 정의 이미지 태그 또는 서버의 빌드 시각으로 확인 |
| 배포 후 실측 3건 | **미실행** (§8-1의 5·6·7번) |

### 2-4. 히트맵 SQS 전환

| 항목 | 상태 | 근거 |
|---|---|---|
| 코드 | **0줄.** `refactor/heatmap-sqs` 브랜치는 어느 저장소에도 없다 | `git branch -a` 실측 |
| 설계 | 결정 5건 확정(§3-5) | 2026-08-10 기록 |
| `backend-hitmap` 저장소 | 사실상 **정지 상태.** 마지막 커밋이 2025-09-22, `origin/dev`·`origin/stg`는 2025-08-19(`276d5c6`)에서 멈춰 있고 `origin/main`만 `9e39fc7`로 한 발 앞서 있다. SQS 코드는 없다(`sqs` grep 0건) | `git log --all` 실측 |
| 작성자 기여 | 이 저장소에는 **작성자 커밋이 1건뿐이며(`8bd42a0`, 2025-08-11) 인수인계 대상 6개월 범위 밖**이다. 즉 이 저장소 자체는 작성자 담당이 아니고, 이번 설계는 "이 서버를 폐기하고 워커로 흡수한다"는 계획이다 | `git log --format=%an --all` 실측 |

### 2-5. 관제 프론트 (별도 담당자)

| 대상 | 상태 | 확인한 것 |
|---|---|---|
| `frontend-web-backoffice` shapeList 폴백 | **미구현** | `origin/dev`·`origin/stg`·`origin/main` 전부에서 `shapeList` grep **0건**. `src/components/widget/MapEdit.vue`는 여전히 `roomList.value = setPoints(data.area?.room_list ?? [])`만 읽는다. 요청 내용은 §6 |

---

## 3. 무엇을 만들었나

### 3-1. 맵 변환 파이프라인 전체 흐름

먼저 **현행(HTTP)** 이다. 인수자가 지금 운영 중인 것을 이해해야 신규 경로의 의미가 보인다.

```
[로봇 기기]
  SLAM 주행 완료 → pgm/yaml 을 S3(맵 버킷)에 올리고
  MQTT 발행: skmg/airbot/{deviceId}/v1/response/mapping      (신규 맵 생성)
             skmg/airbot/{deviceId}/v1/response/mapUpload     (기존 맵에 pgm 재업로드)
        │
        ▼
[AWS IoT Core] → IoT rule → SQS FIFO 큐 benjamin-iot-event-{env}.fifo (visibility 60초)
        │
        ▼
[backend-api-main]  SqsIotEventListener → IotInboundMessageHandler
        │            → ResponseDeviceService.mapping / mapUpload
        │            → MapConvertService.convertMapRequest
        │            user_map_convert 에 행 INSERT (code=0)
        ▼
   HTTP POST {map.convert.url}/...       ← 동기. 여기서 붙들려 있는다
        │
        ▼
[backend-map-converter]  (EC2, map NLB 뒤 :28089)
        │  pgm 전처리(PgmPixelRewriter) → sk_map_refiner 외부 바이너리 실행
        │  → png / json(conv) / yaml 생성 → S3 업로드
        ▼
   HTTP POST {api}/api/map/convert/response   ← 콜백
        │
        ▼
[backend-api-main]  MapConvertController → MapConvertService
        │  user_map_image 에 png/json/yaml 3행, device_map 갱신, 기본 스케줄 생성,
        │  공기질 캐시 클리어, 기기에 setMap MQTT 발송, 앱에 FCM
        ▼
[소비자]  Android/iOS 앱(json 직접 파싱), 관제 웹, 통계·리포트·OpenAPI(DB map_data)
```

다음이 **신규(SQS)** 다. 두 경로는 플래그로 공존한다.

```
[요청 경로 — 간이 outbox]
  ResponseDeviceService.mapping / mapUpload
    (입구 중복 방어: DB 쓰기 전에 existsConvertRequest)
   → MapConvertService.convertMapRequest(MapConvertCommand)   ← 2차 방어 + lang 결정
   → MapConvertEnqueueService.enqueue
        같은 트랜잭션에서  status=ENQUEUE_PENDING + request_payload(JSON 원본) 저장
   → @TransactionalEventListener(AFTER_COMMIT) 로 SQS 발행
        성공 → markQueued (REQUIRES_NEW, terminal 회귀 방지 가드)
        실패 → status=ENQUEUE_FAILED → MapConvertRepublishScheduler(60초)가 재발행
   → (HTTP 레거시 경로를 쓰면 기존 동작 그대로, status 는 QUEUED 로 기록)

[변환]
  SqsMapConvertListener (String JSON 수신, receiveCount 로깅)
   → ConversionWorker   attempt 단위 디렉토리 격리 + path traversal 방어
   → RefinerProcessExecutor  stdout/stderr 를 파일로 redirect(교착 구조적 제거),
                             프로세스 트리 kill, KILL_GRACE 5초, 64KB tail 캡처
   → AWSS3Util  결과 3파일을 result/{jobID}/attempts/{attemptId}/map.{png,json,yaml} 로 업로드
   → ResultPublisher  발행 전 불변식 검증 후 결과 큐로 발행

[결과 경로 — 멱등 + outcome + post-commit]
  SqsMapConvertResultListener (락 없는 사전검사)
   → MapConvertService.handleResult(MapConvertResultMessage)  @Transactional
       ① lockConvertStatus (SELECT ... FOR UPDATE)            ← 멱등의 핵심
       ② isTerminalForResult: SUCCEEDED 면 종료 /
          FAILED 인데 늦게 성공 결과가 오면 재수용 (성공 우선 정책)
       ③ 실패 → FAILED 확정 → HANDLED_FAILURE (ack)
          성공 → verify-before-write (S3 3파일 존재 + JSON 검증을 통과한 뒤에만 DB 쓰기)
                → user_map_image / device_map / 스케줄 / status 갱신 → HANDLED_SUCCESS
       ④ 외부 효과(캐시 클리어·기기 setMap·FCM)는 MapConvertResultSideEffects 이벤트로
          모아 AFTER_COMMIT 핸들러에서 실행 (롤백 시 자동 폐기)
   → outcome 에 따라: HANDLED_SUCCESS / HANDLED_FAILURE / ALREADY_PROCESSED → ack
                      RETRYABLE_FAILURE 만 재시도 → DLQ

[수렴]
  MapConvertReconcileScheduler (10분 주기)
   QUEUED / ENQUEUE_* 상태로 create_date 기준 3시간 경과한 잡
   → 가드 UPDATE 로 FAILED(504) 확정 + 실패 FCM + MAP_CONVERT_TIMED_OUT 마커 로그
```

### 3-2. 상태 기계 (`user_map_convert.status`)

```
SQS:  ENQUEUE_PENDING ──AFTER_COMMIT 발행──► QUEUED ──결과 소비──► SUCCEEDED / FAILED
            │ 발행 실패                         ▲                      ▲  │
            ▼                                   │                      │  │ 성공 우선
      ENQUEUE_FAILED ──republisher(60초, update_date 기준)─┘   FAILED ──늦은 성공─┘
            └──── reconciler(create_date + 3시간): 어디서 멈췄든 FAILED(504) + FCM ────┘

HTTP: (요청 성공 시) QUEUED ──콜백──► SUCCEEDED / FAILED

불변식: markQueued / markEnqueueFailed / markConvertTimedOut 전부 상태 가드를 건다.
        terminal(SUCCEEDED/FAILED) 을 덮어쓸 수 없다.
```

### 3-3. 보장 범위 — 여기까지만 보장한다

| 구간 | 보장 |
|---|---|
| DB 상태 | 요청·결과 메시지가 유실되거나 DLQ로 가도 reconciliation이 `SUCCEEDED` 또는 `FAILED`로 **반드시 수렴시킨다** |
| FCM / 기기 setMap / 캐시 클리어 | **커밋 후 best-effort 외부 효과. 전달 보장 없다** |
| 외부 효과 실패 | 고정 마커 로그 → 알람 → 수동 재처리. 자동 재시도도 전용 outbox도 없다 |

- 마커: `MAP_CONVERT_SIDE_EFFECT_FAILED jobID=... effect=CACHE_CLEAR|SET_MAP|FCM` (스택 트레이스 포함). reconciliation의 타임아웃 FCM 등록 실패도 같은 마커를 쓴다.
- **FCM은 `@Async`라 등록 단계 예외만 잡힌다.** 실제 Firebase 전송 실패는 이 마커에 나타나지 않는다. **마커가 없다는 것이 알림 도달을 뜻하지 않는다.**
- side-effect 실패를 결과 SQS 재시도로 풀지 않는다. DB가 이미 terminal이라 재배달은 멱등 검사에서 스킵되고, 억지로 재처리하면 DB·FCM·setMap 중복만 생긴다.
- **최종 상태의 진실 원천은 DB다.**

### 3-4. 저장소별 역할과 주요 파일

`backend-map-converter` (`refactor/mapconvert`, 87파일 +1,966/−3,719)

| 책임 | 파일 (`src/main/java/com/skix/mapconverter/` 하위) |
|---|---|
| 요청 큐 수신·결함 메시지 분기 | `sqs/SqsMapConvertListener.java` |
| 변환 실행(attempt 격리, 분류 로그) | `sqs/ConversionWorker.java` |
| 결과 발행(불변식 검증, message 100자 절단) | `sqs/ResultPublisher.java` |
| wire DTO | `sqs/dto/MapConvertRequest.java`, `sqs/dto/MapConvertResult.java` |
| refiner 실행(교착 제거·프로세스 트리 kill) | `common/util/RefinerProcessExecutor.java` |
| pgm 전처리·포맷 결함 판정 | `common/util/PgmPixelRewriter.java`, `common/util/InvalidPgmException.java` |
| S3 입출력·오류 영구/일시 분류 | `common/util/AWSS3Util.java`, `common/util/S3PermanentException.java`, `common/util/S3TransientException.java` |
| SQS 메시지에 Java 타입 헤더를 싣지 않게 하는 설정 | `common/config/MapConvertSqsTemplateConfig.java` |
| 설정 fail-fast(큐 이름 blank 시 기동 실패) | `common/config/ConverterProperties.java` |
| 부팅 시 잔여 attempt 디렉토리 청소 | `common/config/WorkRootStartupCleaner.java` |
| 레거시 HTTP 경로(그대로 유지) | `controller/MapConvertController.java`, `service/MapConvertService.java` |

정리한 것: 이전 개발사 이름의 패키지 → `com.skix.mapconverter`, 이전 개발사 접두사가 붙은 클래스 → `MapConvert*`로 리네임, Maven 좌표 `com.skix:map-converter`. JNI MapOptimization·모니터링 서브시스템·목/테스트 컨트롤러·유물 API(`/ReqMapOptimizationComplet`, `/segFileDownload`)·springdoc·`static/`·레거시 의존성(log4j 1.x, jackson 1.x) 제거. HTTP 경로에도 적용된 라이브 버그 수정: 예외 시에도 콜백 발송, 음수 소요시간·무효 SLF4J 포맷 로그 버그, Boot 3.2에서 무효가 된 로깅 키 교정, MDC에 jobID 심기.

`backend-api-main` (`refactor/mapconvert`, 39파일 +1,391/−628)

| 책임 | 파일 (`src/main/java/com/skmagic/` 하위) |
|---|---|
| 요청 outbox 적재 | `api/map/service/MapConvertEnqueueService.java` |
| 발행 후 상태 갱신(REQUIRES_NEW) | `api/map/service/MapConvertEnqueueStatusUpdater.java` |
| 결과 처리 본체(멱등·성공 우선·verify-before-write) | `api/map/service/MapConvertService.java` |
| 커밋 후 외부 효과 실행 | `api/map/service/MapConvertResultSideEffectHandler.java`, `api/map/event/MapConvertResultSideEffects.java`, `api/map/event/MapConvertEnqueuedEvent.java` |
| 요청 발행 | `core/configuration/MapConvertRequestPublisher.java` |
| 결과 큐 소비 | `core/configuration/SqsMapConvertResultListener.java` |
| 발행 실패 재발행(60초) | `core/configuration/MapConvertRepublishScheduler.java` |
| 상태 수렴(10분) | `core/configuration/MapConvertReconcileScheduler.java` |
| Java 타입 헤더 제거 | `core/configuration/MapConvertSqsTemplateConfig.java` |
| 입구(기기 응답 수신) | `api/device/control/service/ResponseDeviceService.java` |
| SQL | `src/main/resources/mapper/map/MapConvert.xml` |

DTO 전면화로 `Map<String,Object>` 흐름을 없앴다. wire는 record(`MapConvertRequestMessage`/`MapConvertResultMessage`), 내부는 `MapConvertCommand`·`MapConvertHandlingResult`+`MapConvertOutcome` enum, HTTP 레거시 경계는 `MapConvertRequestBody`/`MapConvertCallbackRequest`/`MapConvertHttpRequest`. **MyBatis 바인딩용만 Lombok 클래스**(`MapConvertRequestRow`, `MapConvertJobInfo`, `PendingMapConvertEnqueue`, `HistoryControlSerialInfo`) — MyBatis 3.0.3 + 컴파일 `-parameters` 부재라 record를 못 쓴다. wire JSON과 레거시 HTTP 계약은 바이트 단위로 바꾸지 않았다.

그 밖에: 파괴적 쿼리 3종(`updatePgmImage` 등)과 죽은 쿼리 `getAllAreasBySerial` 삭제, `Thread.sleep(1000)` 제거(S3는 strong consistency), 결과 JSON 10MB 상한.

`db-schema` (`refactor/mapconvert`, `e8f745f`) — `prod.sql`·`stg.sql`의 `user_map_convert` 목표 상태 정의. 전문은 부록 C.

### 3-5. 히트맵 SQS 전환 — 확정된 설계 (코드 없음)

현행은 이렇다.

```
[로봇] 공기질 스캔 완료 → MQTT aq_map → IoT → benjamin-iot-event-{env}.fifo (visibility 60초)
   ▼
[backend-api-main] SqsIotEventListener → IotInboundMessageHandler
   → ResponseDeviceService.aq_map(topic, payload)
      · createHistoryAqMapScan(...) — history_airbot_air_quality_scan 적재
      · /aqMap/aqi_map_refiner 외부 바이너리를 ProcessBuilder 로 실행 (같은 호스트)
      · airSensorRestTemplate 로 backend-hitmap 동기 POST (응답 타임아웃 45초)
        dev/local: http://internal-skmg-airbot-d-alb-map-....elb.amazonaws.com/hitmap
        stg:       http://skmg-benjamin-stg-map-nlb-....elb.amazonaws.com/hitmap
        prd:       http://skmg-benjamin-prd-map-nlb-....elb.amazonaws.com/hitmap
      · createAirSensorRecommendLibrary(IdwProcessRequest) — 에어센서 위치 추천
      · 완료 FCM (alarmCode ALWK0009 / code SCAN1003)
   ▲ 이 전부가 SQS 소비 스레드 위에서 동기로 돈다
```

확정한 결정은 다음 5가지다.

| # | 결정 | 근거 |
|---|---|---|
| 1 | 워커를 **`backend-map-converter`에 전용 큐 쌍으로 추가**한다. 새 서버를 만들지 않는다 | 맵 변환에서 만든 하네스(리스너·멱등·outcome·reconciliation·프로세스 실행기)를 그대로 재사용한다 |
| 2 | IDW를 **`idw_1.5.0.jar` 인프로세스로** 돌린다 | 별도 HTTP 홉을 없앤다. converter pom에 `includeSystemScope=true`가 **이미 있다**(`pom.xml:102`). 현재 jar는 `backend-api-main/libs/idw_1.5.0.jar`에 있고 `build.gradle:78`이 참조하므로 **이관 대상**이다 |
| 3 | `aqi_map_refiner` 바이너리를 **워커 호스트로 이관**한다 | 지금은 `backend-api-main` 호스트의 `/aqMap/`에 있다. 변환 워커와 같은 곳에 두면 실행 환경이 하나로 모인다 |
| 4 | **`backend-hitmap` EC2 + NLB를 폐기**한다 | 엔드포인트가 `POST /hitmap` 하나뿐이고 호출자도 하나뿐이다. 인프로세스로 옮기면 서버 자체가 불필요하다 |
| 5 | 브랜치는 `refactor/mapconvert` **위에** `refactor/heatmap-sqs`로 쌓는다 | 하네스에 의존하므로 |

**반드시 알아야 할 두 가지 주의.**

- `idw_1.5.0.jar`는 벽 감지 버그(`Point`의 `equals`/`hashCode` 부재)를 고친 판이다. **히트맵 출력이 현행과 달라지는 것이 정상이다.** 검증 전에 현행 출력으로 골든 베이스라인을 떠 두지 않으면 "달라졌다"와 "틀렸다"를 구분할 수 없다.
- **신·구 경로 병행 실행 금지.** 같은 DB 테이블에 이중 기록된다. 맵 변환과 달리 히트맵은 플래그 공존이 안전하지 않다.

이 설계가 별건이던 **"`aq_map` 전용 Standard 큐 분리"를 흡수**한다. 전용 큐 쌍 + 워커 이관 + `FOR UPDATE` 멱등으로 같은 문제가 해결되므로 따로 추진하지 않는다.

### 3-6. SQS 소비 DLQ 수정 — 무엇을 고쳤나

| # | 커밋 | 내용 |
|---|---|---|
| 1 | `249892b1` | `getSchedule` 응답을 전문 통째가 아니라 **스케줄 건수 요약**으로 적재. `getHoliday`도 **공휴일 건수 요약**으로. `HistoryControl.setOpParamStr`에 **UTF-8 바이트 기준 클램프**를 넣어 호출부 60곳을 일괄 방어. 재실행해도 결과가 같은 영구 실패는 **로그만 남기고 ack**해 FIFO 그룹 연쇄 실패를 끊음 |
| 2 | `22aaf21d` | 히트맵 전용 `airSensorRestTemplate` 빈 분리. 공용 `restTemplate`에 `@Primary` |
| 3 | `24b56052` | 예외 분류를 **화이트리스트(명확한 영구 실패만 ack)** 로 변경. 미분류 예외는 재시도 |
| 4 | `5b4b1180` | `SdkException.retryable()` 분기 **삭제**. 이 메서드는 `RetryableException` 외에는 전부 `false`를 반환해서 5xx·스로틀링·타임아웃까지 영구 실패로 ack해 **메시지를 유실시킨다.** AWS 실패는 재시도가 기본값이어야 한다. `InvalidDataAccessResourceUsageException`(없는 컬럼·테이블 등 SQL 오류)은 영구 실패로 추가 |
| 5 | `292b1248` | 히트맵 응답 타임아웃 60초 → **45초**(visibility 60초 안에 DB 적재·푸시까지 끝나도록). 공용 응답 타임아웃 30초 → **10초**(16개 서비스가 공유하는 `RestTemplate`이라 느린 업스트림 하나가 톰캣 스레드를 오래 점유). 커넥션 풀 총 25/라우트 5 → **총 100/라우트 20** |

현재 값(`origin/dev`·`origin/main` 동일):

```
SystemConstants.API_CONNECT_TIMEOUT             = 5000
SystemConstants.API_CONNECTION_REQUEST_TIMEOUT  = 5000
SystemConstants.API_RESPONSE_TIMEOUT            = 10000
SystemConstants.API_POOL_MAX_TOTAL              = 100
SystemConstants.API_POOL_MAX_PER_ROUTE          = 20
app.airsensor.api.response-timeout-ms           = 45000   (local/dev/stg/prd 4개 전부)
```

**히트맵 비동기 분리는 한 번 구현했다가 철회했다.** 최대 1분이라는 가정 아래 동기 유지로 결정했다. 또 하나 알아 둘 것: **awspring 3.1.1에는 처리 중인 메시지의 visibility 하트비트가 없다.** 어댑터가 갱신해 주는 것은 배치의 후속 메시지뿐이다. 그래서 "처리 시간 < visibility"를 코드 쪽 타임아웃으로 지키는 수밖에 없다.

---

## 4. SQS 메시지·큐 계약

### 4-1. 큐

| 용도 | 이름 | visibility | 비고 |
|---|---|---|---|
| IoT 이벤트(기존, 운영 중) | `benjamin-iot-event-{dev,stg,prd}.fifo` | 60초 | **FIFO.** `aws.sqs.enabled=true`, `aws.iot.subscribe-enabled=false`로 MQTT 직접 구독과 배타 운영 |
| 맵 변환 요청(신규) | `benjamin-map-convert-request-{dev,stg,prd}` | 2400초 | Standard + DLQ, maxReceive 3 |
| 맵 변환 결과(신규) | `benjamin-map-convert-result-{dev,stg,prd}` | 900초 | Standard + DLQ, maxReceive 3 |

맵 변환에 FIFO를 쓰지 않은 이유: 기기당 1잡이고 잡 사이 순서가 무관하다. FIFO는 §1-2의 연쇄 실패라는 비용만 낳는다.

### 4-2. 메시지 본문

**String JSON이다. Java 타입 헤더(`JavaType`)를 싣지 않는다.** 양쪽 저장소가 각각 `MapConvertSqsTemplateConfig`에서 `doNotSendPayloadTypeHeader()`를 건다. Java FQCN이 wire에 실리면 패키지 리네임만으로 계약이 깨지기 때문이다(이번 리팩토링이 정확히 패키지를 바꿨다).

요청(`MapConvertRequestMessage` → `MapConvertRequest`): `jobID`, pgm/yaml의 S3 키, `lang` 등.
결과(`MapConvertResult` → `MapConvertResultMessage`): `jobID`, `code`, `message`(100자 절단), 산출물 3키.

`ResultPublisher`는 발행 전에 불변식을 검증한다 — `SUCCEEDED`면 `code=200`이고 산출물 3키가 모두 있어야 한다. `message`를 100자로 자르는 것은 DB `varchar(100)` 계약을 맞추기 위해서다.

### 4-3. 결함 메시지 처리 (`SqsMapConvertListener`)

두 갈래다.

- **역직렬화는 됐으나 필수 필드·형식 검증에 실패**: `jobID`가 있으면 `FAILED` 결과를 발행해 즉시 종결한다. 없으면 예외를 전파해 재시도 후 DLQ.
- **JSON 문법 자체가 깨져 역직렬화 불가**: `jobID`를 신뢰할 수 없으므로 예외 전파 → 재시도 소진 → request DLQ. 이 경우 DB 행은 `create_date` 기준 reconciliation 기한이 지나면 `FAILED`로 종결된다. **깨진 JSON에서 정규식 등으로 jobID를 뽑아내지 않는다.**

`ConversionWorker`의 refiner 종료코드 처리: **non-zero exit는 "분류 불가"로 보고 전파 → 재시도 → DLQ**. 타임아웃만 `FAILED`로 확정한다. refiner의 exit-code 계약을 우리가 모르기 때문이다.

`AWSS3Util`의 오류 분류(다운로드·업로드 대칭):

| 분류 | 조건 | 처리 |
|---|---|---|
| 영구 | 404 / 400, 크기 초과, 결과 파일 부재 | `FAILED` 확정 |
| 일시 | **401 / 403**, 429, 5xx, 네트워크 | 재시도 |

401/403을 일시로 둔 것이 의도적이다. IAM 오배포는 코드 결함이 아니라 환경 문제이고, 고치면 그대로 성공해야 한다.

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 실제로 사고가 나는" 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **reconciliation 기준 시각은 `create_date`다.** 재발행 스캔(`findPendingEnqueues`)만 `update_date`를 쓴다 | `update_date`는 재발행 실패 때마다 갱신된다. 이 기준으로 종결을 판정하면 **발행이 계속 실패하거나 `request_payload`가 손상된 행은 매 분 기한이 밀려 영원히 종결되지 않는다.** 정확히 그 행이 종결돼야 하는 행이다 |
| 2 | **성공 우선 정책** — `FAILED`로 확정된 뒤에 늦게 도착한 `SUCCEEDED` 결과를 **받아들인다** | 거부하면 실제로 만들어진 변환 결과를 버린다. 사용자에게는 재스캔을 시키는 일이 된다. reconciliation의 오탐도 이 정책이 복구한다. `FOR UPDATE` 행 락으로 진행 중 결과 처리와 직렬화되므로 경합은 없다 |
| 3 | **verify-before-write** — 성공 결과라도 S3 3파일 존재와 JSON 검증을 통과한 뒤에만 DB를 쓴다 | 부분 커밋이 구조적으로 사라진다. 이걸 빼면 "DB는 성공인데 이미지가 없는 맵"이 생기고, 그 상태는 reconciliation도 고칠 수 없다(이미 terminal이므로) |
| 4 | **외부 효과(FCM·기기 setMap·캐시 클리어)는 `AFTER_COMMIT`에서만 실행한다** | 트랜잭션 안에서 보내면 롤백 시 **유령 FCM**이 나가고, 기기에 없는 맵을 setMap 하고, 캐시를 커밋 전에 클리어해 옛 값을 다시 캐싱한다. 캐시 순서 버그는 실제로 이 변경으로 함께 고쳐졌다 |
| 5 | **side-effect 실패를 결과 SQS 재시도로 풀지 않는다** | DB가 이미 terminal이라 재배달은 멱등 검사에서 스킵된다. 억지로 재처리 경로를 열면 DB·FCM·setMap 중복만 생긴다. 대응은 마커 로그 → 알람 → 수동 |
| 6 | **파괴 후 검증 금지** — pgm 업로드는 기존 맵을 in-place 교체하지 않는다. 파괴적 쿼리 3종을 코드에서 완전히 삭제했다 | §1-2의 "기기 먹통" 사건 재현. 비동기 검증(변환) 전에 원본을 커밋으로 파괴하면 재시도·복원·복사의 원천 데이터가 전부 사라진다. 죽은 코드로 남겨 두면 누군가 다시 부른다 |
| 7 | **converter의 SQS 스위치는 `spring.cloud.aws.sqs.enabled` 단 하나다.** 모든 SQS 빈이 같은 조건을 쓴다 | 빈별로 조건을 나누면 "리스너는 떴는데 발행자는 없는" 반쪽 상태가 생긴다. 큐 이름이 비면 `ConverterProperties`가 **기동을 실패시킨다**(fail-fast). 조용히 기본 큐로 붙는 것보다 안 뜨는 편이 낫다 |
| 8 | **S3 결과 키 `result/{jobID}/attempts/{attemptId}/`에 lifecycle을 걸지 않는다** | attempt 단위 격리는 동시 재처리 충돌을 막기 위한 것이고, 결과 키가 `user_map_image.map_path`로 **장기 참조**된다. 자동 만료를 걸면 과거 맵 이미지가 조용히 사라진다 |
| 9 | **`AWSS3Util`에서 401/403을 일시 오류로 분류한다** | 영구로 바꾸면 IAM 정책 오배포 한 번에 그 시간대 변환이 전부 `FAILED` 확정된다. 권한은 고치면 그대로 성공하는 환경 문제다 |
| 10 | **`SdkException.retryable()`로 재시도 여부를 판정하지 않는다** (DLQ 수정 `5b4b1180`) | 이 메서드는 `RetryableException` 서브타입 외에는 전부 `false`를 반환한다. 5xx·스로틀링·타임아웃이 영구 실패로 ack돼 **메시지가 유실된다.** 이름 때문에 "AWS SDK가 알려 주는 정답"처럼 보이는 것이 함정이다 |
| 11 | **예외 분류는 화이트리스트다.** 명확한 영구 실패만 ack하고 미분류는 재시도 | 블랙리스트로 뒤집으면 새로 생긴 예외 타입이 전부 조용히 ack돼 유실된다. 유실보다 DLQ가 낫다 |
| 12 | **FIFO 큐에서는 영구 실패를 반드시 ack해야 한다** | 같은 `MessageGroupId`의 후속 메시지가 통째로 막혀 **정상 메시지까지 DLQ로 밀려난다.** 기기 1대의 8개 토픽이 통째로 넘어간 것이 이 사고였다 |
| 13 | **히트맵 타임아웃(45초) < IoT 큐 visibility(60초)** 관계를 깨지 않는다 | `aq_map` 핸들러가 SQS 소비 스레드 위에서 동기 호출하므로, 45초를 넘기면 DB 적재·푸시를 마치기 전에 메시지가 재배달된다. **awspring 3.1.1에는 처리 중 메시지 하트비트가 없어** 이 부등식이 유일한 방어다. 타임아웃을 올리려면 visibility를 먼저 올려야 한다 |
| 14 | **refiner 타임아웃(1800초) < 요청 큐 visibility(2400초)** — 같은 원리 | 실측 최악 12분의 2.5배 여유다. refiner 타임아웃을 올릴 때 visibility를 같이 올리지 않으면 변환이 끝나기 전에 같은 잡이 재배달돼 중복 실행된다 |
| 15 | **정리(cleanup)를 `finally`에만 맡기지 않는다.** `WorkRootStartupCleaner`가 부팅 시 잔여 attempt 디렉토리를 청소한다 | `finally`는 `kill -9`에 무력하다. 크래시 정합성은 종료 시점이 아니라 **기동 시점**에 확보해야 한다 |
| 16 | **`lang`은 `^[a-z]{2}(-[A-Z]{2})?$` 화이트리스트를 통과해야 한다** | 값이 셸 스크립트 argv로 들어간다. 정규식이 argv 경계 방어다 |
| 17 | **`lang` 조회를 SQS 분기보다 앞에 둔다** | 뒤에 두면 SQS 경로에서 다국어가 유실된다. 초기 구현의 버그였고 수정해서 들어갔다 |
| 18 | **`createConvertRequest`가 `update_date=NOW()`를 명시적으로 넣는다** | NULL로 두면 재발행 스캔(`update_date` 기준)이 **3값 논리 때문에 그 행을 영영 못 잡는다** |
| 19 | **`user_map_convert`를 outbox로 재사용하고 별도 outbox 테이블을 만들지 않는다** | 별도 테이블이면 "요청 행 INSERT"와 "outbox INSERT"가 두 번의 쓰기가 된다. 같은 테이블에 컬럼을 얹으면 하나의 원자적 쓰기로 끝난다 |
| 20 | **`getMapConvertCode`(앱 폴링)는 손대지 않는다** | 이 쿼리의 의도는 "현재 맵"이 아니라 **"최근 변환 작업의 결과"** 다. 실패를 보고하는 것이 정당하다. "FAILED를 제외하자"고 필터를 걸면 **앱이 실패를 영영 못 받아** 무한 대기한다. 같은 `max(id)` 계열이라도 `getBuildingChromsMapList`의 `is_use`(관제 화면)는 진짜 표시 결함이다(§8-3) |

---

## 6. 관제 프론트에 요청한 내용 — shapeList 폴백

프론트 코드는 직접 수정하지 않았다. 아래가 전달한 요청의 전문에 해당한다. 인수자는 **아직 구현되지 않았다는 것**(§2-5 실측)을 알고 재요청 여부를 판단하면 된다.

### 6-1. 증상

관제 → Map관리 → pgm 업로드로 새 맵 생성(변환 성공) → 그 맵에서 "맵 편집 시작하기" 클릭 → **분할·병합·공간할당·금지구역 메뉴는 활성화되는데 실제 편집 동작이 전혀 안 된다.** 지도 이미지(png)는 정상으로 보인다. 앱에서 한 번이라도 편집한 적 있는 맵은 관제 편집이 정상 동작한다. 우선순위는 높다 — 설치 현장 VOC에 직결된다.

### 6-2. 원인 (코드로 확정)

| 포맷 | 생성 시점 | 스키마 |
|---|---|---|
| **conv json** | 맵 변환 성공 시(변환 서버 산출물) | `{ "shapeList": [ { "id", "name", "position": [ {"x":..,"y":..}, … ] } ] }` — `position`은 `{x,y}` 객체 배열, **픽셀 좌표** |
| **edit json** | 앱·관제에서 편집을 **저장**할 때 | `{ "uid", "info", "room_list": [...], "block_area", "block_wall", "charging_station", "assign_info", "user_angle" }` |

서버 `user_map.map_data`(편집 화면의 `area`)에는 **edit json만** 저장된다. 쓰는 곳은 편집 저장(`createEditJson`) 한 곳뿐이다. 따라서 **한 번도 편집하지 않은 맵**(pgm 업로드 직후, SLAM 변환 직후)은 `map_data`가 NULL이다.

관제 편집 위젯은 `area.room_list`만 읽는다. `src/components/widget/MapEdit.vue`의 `roomList.value = setPoints(data.area?.room_list ?? [])` — **`shapeList` 분기가 없다.** 미편집 맵에서 `roomList`가 빈 배열이 되고, 조작 대상이 없으니 아무 동작도 안 한다.

### 6-3. 왜 지금까지 안 드러났나

Android·iOS 앱은 **둘 다 이 폴백을 이미 갖고 있다.**

- Android(`benjaminandroid`): `data/src/main/java/com/magic/android/iot/data/mapper/map/MapJsonMapper.kt` — `when { has("shapeList") → …; has("room_list") → … }`
- iOS(`benjaminios`): `MagicIot/Networking/Domain/Schedule/DeviceSectionGetReq.swift` — `room_list` 디코드 실패 시 `shapeList` 폴백(주석 `//old`)

**신규 SLAM 맵의 최초 열람은 항상 이 폴백을 탄다**(edit json이 아직 없으므로). 즉 모든 신규 설치에서 매번 실행돼 온 검증된 경로다. 기존 운영에서는 맵이 관제 편집에 도달할 때쯤이면 앱이 이미 첫 편집을 마친 뒤였고, "관제발 pgm 업로드 → 곧장 관제 편집"이라는 경로가 이번에 처음 그 구멍을 밟았다.

### 6-4. 요청 사항

**편집 화면 로드 시 `area.room_list`가 비어 있으면 conv json(`shapeList`)을 폴백으로 파싱해 `roomList`를 구성해 달라.**

데이터는 이미 내려가고 있다. `getBuildingMapInfo` 응답의 `mapInfo.jsonUrl`이 json 파일 경로이고, 서버가 이미 **edit json 우선, 없으면 conv json**으로 골라 준다(`Operation.xml`의 `getMapImageInfo`가 `COALESCE`로 처리). 미편집 맵에서는 `jsonUrl`이 자동으로 conv json을 가리킨다. **추가 API가 필요 없다.**

```
로드 시:
  if (area?.room_list 가 비어있지 않음) → 기존 로직 그대로
  else:
    jsonUrl 로드 → JSON 파싱
    if (json.shapeList 존재):
      roomList = shapeList.map(shape => ({
        id:     shape.id,            // 없으면 신규 id 부여
        name:   shape.name ?? '',
        points: shape.position.flatMap(p => [p.x, p.y]),  // [{x,y},…] → 캔버스 points
        color:  랜덤/기본 색상,       // conv json 에는 색상이 없다
      }))
    // block_wall / block_area / charging_station / user_angle 은 conv json 에 없다 → 빈 값으로 시작
```

- Android 참조 구현: `MapJsonMapper.kt`의 `parseShapeList` (id/name/position만 읽어 폴리곤 구성, 중심점 계산)
- `position` 좌표는 **픽셀 좌표 확정**이다. 앱이 변환 없이 그대로 폴리곤으로 쓰고 있다.

**저장은 수정할 것이 없다.** 기존 `src/common/utils/MapDataUtil.ts`의 `convertRoomList`가 저장 시 image 좌표 → robot 좌표를 직접 계산한다(`src/common/utils/CoordinateUtil.ts`의 `imageToRobot`, yaml의 resolution·origin 기반). shapeList에서 시작한 편집본도 저장하면 완전한 edit json이 만들어지고 서버가 `map_data`에 넣는다. **한 번 저장하면 그 맵은 이후 앱·관제 모두 정상 경로(`room_list`)로 동작한다.**

### 6-5. 검증 시나리오

1. 관제에서 pgm 업로드 → 변환 성공 → **편집 저장 없이** 편집 화면 진입 → 방 목록이 뜨고 분할·병합·공간할당·금지구역이 동작
2. 편집 저장 → `SELECT map_data IS NOT NULL FROM user_map WHERE id = ?`로 생성 확인
3. 저장 후 재진입 → `room_list` 경로로 정상(폴백 미사용)
4. 같은 맵을 **앱**에서 열어 정상 표시(앱 회귀 없음)
5. 기존(이미 편집된) 맵 편집 → 기존 동작 그대로

### 6-6. 주의

- 설치 현장 VOC 직결이다. **폴백 추가 외에 저장·분할·병합 로직 자체는 건드리지 말 것.**
- conv json에는 방 색상·robot 좌표·스테이션 정보가 없다. 프론트에서 기본값으로 시작하는 것이 맞고, 저장 시 좌표는 자동 계산되므로 별도 처리가 필요 없다.
- 백엔드에서 "변환 성공 시 `map_data` 초기 시딩"을 하는 안은 **Full 시딩(robot 좌표 포함)이 가능한 경우에만** 검토할 후속 과제다. 그게 적용되더라도 **이 폴백은 기존 미편집 맵 호환을 위해 여전히 필요하다.**

**`map_data` 소비자 전수 조사 결과(부분 시딩이 왜 안 되는지)**: 통계·리포트·FCM은 `id`/`name`만 쓰지만 **스케줄·AQM 동기화·OpenAPI는 `robot_position`을 쓴다.** 따라서 "robot 좌표 없는 최소 시딩"은 스케줄 회귀를 일으킨다. "`map_data`가 NULL"에 익숙한 소비자에게 "필드가 빠진 문서"를 주면 폴백 가드를 그냥 통과해 버린다. **2단계 서버 시딩은 Full 시딩만 유효하며 보류 상태다.**

---

## 7. 배포 방법

### 7-1. 맵 변환 — 순서 (역순 금지)

```
0. backend-api-main refactor/mapconvert 리베이스 (§8-1의 1번, 부록 D)   ← 지금 이게 먼저다
1. DDL 적용                       ← 반드시 애플리케이션보다 먼저 (아래 이유)
2. 1차 배포: 양쪽 서버, SQS 플래그 전부 OFF
3. 배포 직후 QUEUED + code=200 보정 1회
4. 2차 배포: api-main processing 활성화
5. 3차 배포: converter SQS 활성화
6. 4차 배포: api-main 요청 발행 활성화
환경 순서는 항상 dev → stg → prd
```

**DDL이 먼저여야 하는 이유, 그리고 개발계의 함정.**

`refactor/mapconvert`의 `MapConvert.xml` `createConvertRequest`가 `status`·`request_payload` 등 outbox 컬럼을 요구한다. 컬럼이 없는 상태에서 플래그를 켜면 **첫 맵 변환 요청부터 즉시 실패**한다.

그런데 소스의 플래그 기본값이 환경마다 다르다.

| 저장소 | local | dev | stg | prd |
|---|---|---|---|---|
| `backend-api-main` `aws.sqs.map-convert-processing-enabled` | false | **true** | false | false |
| `backend-api-main` `aws.sqs.map-convert-request-publish-enabled` | false | **true** | false | false |
| `backend-map-converter` `spring.cloud.aws.sqs.enabled` | false | **true** | false | false |

**즉 개발계는 "배포 = 즉시 활성화"다.** "일단 배포하고 나중에 켜자"가 불가능하므로 dev에서는 DDL이 반드시 선행돼야 한다. stg·prd는 false라 배포와 활성화를 분리할 수 있다.

단계적 컷오버를 하려면 **1차 배포 artifact에서 세 플래그를 전부 `false`로 맞춰라.** 리베이스하면서 설정 파일을 어차피 옮겨야 하므로(부록 D), **그때 dev 기본값을 false로 바꾸는 것이 이 함정을 없애는 가장 깔끔한 방법이다.**

한 가지 더. `backend-map-converter`의 `application.yml`은 최상단에 `spring.profiles.active: dev`가 **하드코딩**돼 있다. 기동 시 프로필을 명시하지 않으면 dev로 뜬다. stg·prd 배포에서 프로필 주입을 반드시 확인하라.

### 7-2. 배포 전 확인

```bash
git -C <repo> status --short
git -C <repo> rev-parse --short HEAD
```

- 양쪽 서버가 **String JSON**으로 요청·결과를 주고받는지 (`doNotSendPayloadTypeHeader`)
- converter와 api-main의 요청 큐명·결과 큐명이 **대상 환경과 일치**하는지
- refiner timeout `1800`초, 요청 visibility `2400`초인지
- api-main reconciliation이 `create_date`, 재발행이 `update_date` 기준인지
- `MAP_CONVERT_SIDE_EFFECT_FAILED` 로그 마커가 들어 있는지

DB는 **문서의 완료 표시를 믿지 말고 배포일에 실제로 확인**한다(§2-2의 불일치 참고).

```sql
SHOW CREATE TABLE user_map_convert;
SHOW INDEX FROM user_map_convert;
```

```sql
SELECT
    SUM(status IS NULL)                      AS null_status,
    SUM(update_date IS NULL)                 AS null_update_date,
    SUM(status = 'FAILED' AND code = 0)      AS wrong_failed,
    SUM(status = 'QUEUED' AND code = 200)    AS completed_but_queued
FROM user_map_convert;
```

```sql
SELECT job_id, COUNT(*) AS cnt
FROM user_map_convert
WHERE job_id IS NOT NULL AND TRIM(job_id) <> ''
GROUP BY job_id HAVING COUNT(*) > 1;
```

기대값: `null_status=0`, `null_update_date=0`, `wrong_failed=0`, 중복 `job_id` 0건, 인덱스 `ux_user_map_convert_job_id`·`ix_user_map_convert_status_update_date`·`ix_user_map_convert_status_create_date` 존재.

**reconciliation 대상 사전 확인(필수).** processing 플래그를 켜면 오래된 미완료 행이 `FAILED(504)`로 종결되고 사용자에게 실패 FCM이 나간다.

```sql
SELECT status, COUNT(*) AS cnt
FROM user_map_convert
WHERE status IN ('QUEUED', 'ENQUEUE_PENDING', 'ENQUEUE_FAILED')
  AND create_date < DATE_SUB(NOW(), INTERVAL 3 HOUR)
GROUP BY status;
```

0건이 아니면 상세 행과 request·result DLQ를 함께 조사한다. 의미 없는 과거 행은 삭제하지 말고 별도 백업 후 `FAILED(504)`로 종결한다. **SQS payload가 있는 행은 임의로 종결하지 않는다.**

### 7-3. 단계별 활성화

**1차 — 플래그 OFF 배포.** 확인: 애플리케이션 정상 기동 / 기존 HTTP 요청 → converter 처리 → HTTP 콜백 성공 / `status`가 성공 시 `SUCCEEDED`·실패 시 `FAILED` / 동일 `job_id` 중복 응답이 추가 행을 만들지 않음 / SQS 리스너가 아직 뜨지 않음.

배포 직후 1회 보정(구버전 HTTP가 완료한 행은 `QUEUED + code=200`으로 남아 있을 수 있다):

```sql
SELECT COUNT(*) AS correction_targets
FROM user_map_convert
WHERE status = 'QUEUED' AND code = 200 AND request_payload IS NULL;

UPDATE user_map_convert
   SET status = 'SUCCEEDED', update_date = NOW()
 WHERE status = 'QUEUED' AND code = 200 AND request_payload IS NULL;

SELECT COUNT(*) AS remaining_targets
FROM user_map_convert
WHERE status = 'QUEUED' AND code = 200 AND request_payload IS NULL;   -- 0 이어야 한다
```

**2차 — api-main processing 활성화.**

```yaml
aws:
  sqs:
    map-convert-processing-enabled: true
    map-convert-request-publish-enabled: false
```

확인: 결과 큐 리스너 기동 로그 / outbox republisher 기동 / reconciliation 사전 조회 결과와 실제 종결 대상이 일치 / 기존 HTTP 경로 계속 정상.

**3차 — converter SQS 활성화.**

```yaml
spring:
  cloud:
    aws:
      sqs:
        enabled: true
```

확인: 요청 리스너 기동 / 대상 환경 요청·결과 큐명 로그 / 권한·`QueueNotFound` 오류 없음. 아직 api-main은 HTTP로 발행하므로 **converter가 대기 상태인 것이 정상**이다.

**4차 — api-main 요청 발행 활성화.** `map-convert-request-publish-enabled: true`. 이 시점부터 신규 요청이 SQS 전체 경로를 탄다.

### 7-4. 컷오버 확인 (환경별로 실제 맵 변환 1건)

1. `user_map_convert`: `ENQUEUE_PENDING → QUEUED → SUCCEEDED` 전이
2. 요청 큐: Sent/Received/Deleted 증가, 잔여 Visible/NotVisible 정상 감소
3. converter 로그: 요청 수신 → PGM 검증 → refiner 성공 → S3 업로드 → 결과 발행
4. S3: `result/{jobID}/attempts/{attemptId}/map.png|json|yaml` 존재
5. 결과 큐: Sent/Received/Deleted 증가
6. `user_map_image`: 같은 `jobID`의 conv png·json·yaml 각 1건
7. `device_map`, 스케줄, 캐시 후처리 정상
8. 앱 성공 FCM 수신 및 화면 전이
9. 요청·결과 DLQ 0건
10. `MAP_CONVERT_TIMED_OUT`, `MAP_CONVERT_SIDE_EFFECT_FAILED` 0건

추가로 dev에서 **동일 결과 메시지를 한 번 재전달**해 DB 후처리가 중복되지 않는지 확인한다(멱등 검증).

### 7-5. 알람 (prd 컷오버 전 필수)

| 대상 | 조건 |
|---|---|
| request DLQ | Visible 메시지 1건 이상 |
| result DLQ | Visible 메시지 1건 이상 |
| 요청 큐 적체 | `AgeOfOldestMessage` 운영 기준 초과 |
| 결과 큐 적체 | `AgeOfOldestMessage` 운영 기준 초과 |
| reconciliation 종결 | `MAP_CONVERT_TIMED_OUT` 1건 이상 |
| 외부 효과 실패 | `MAP_CONVERT_SIDE_EFFECT_FAILED` 1건 이상 |

FCM은 `@Async`이므로 `effect=FCM` 마커가 없다고 단말 전달까지 성공한 것은 아니다. Firebase 비동기 실패 로그도 함께 감시한다.

### 7-6. 롤백

**코드와 DB를 되돌리지 않고 SQS 경로만 단계적으로 끈다.**

1. api-main `map-convert-request-publish-enabled=false`로 배포 → 신규 요청이 HTTP로 복귀
2. 요청 큐의 Visible/NotVisible이 0인지 확인
3. 결과 큐가 drain될 때까지 api-main processing과 converter를 **유지**
4. converter `spring.cloud.aws.sqs.enabled=false`로 배포
5. 결과 큐의 Visible/NotVisible이 0인지 확인
6. api-main `map-convert-processing-enabled=false`로 배포
7. 기존 HTTP 왕복 재검증

**DB 컬럼·인덱스·UNIQUE와 SQS 큐는 롤백 시 제거하지 않는다.** 구버전 코드가 참조하지 않고, 남겨 두어야 다시 켤 수 있다. 백업 테이블 복원은 데이터 손실이 확인되고 복구 대상을 특정한 경우에만 별도 승인 후 수행한다.

### 7-7. DLQ 수정 배포

이미 dev·stg·main에 내용이 들어가 있으므로(§2-3) **추가 브랜치 작업이 없다.** 남은 것은 배포 여부 확인과 배포 후 실측뿐이다(§8-1의 5·6·7번). 별도 순서 제약도 없다.

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **`backend-api-main` `refactor/mapconvert` 리베이스.** 2026-09-22 실측으로 `origin/dev` 대비 **425커밋 뒤처져 있다**(2026-09-14 시점 411에서 증가). 판단 결론은 **재작업이 아니라 리베이스**다. 근거와 절차는 부록 D | 백엔드 | 나머지 두 저장소는 리베이스가 거의 필요 없다(3번, 부록 D-4) |
| 2 | **`db-schema` 저장소와 실제 dev/stg DB의 불일치 확인.** 저장소 `origin/main`의 `stg.sql`에는 outbox 컬럼이 없는데, 작업 기록상 dev/stg 실제 DB에는 2026-08-03에 적용했다. **실제 DB에 `SHOW CREATE TABLE user_map_convert`를 쳐서 어느 쪽이 사실인지 확정**하고, DB가 맞으면 저장소를 맞춘다 | 백엔드 | 이게 틀리면 §7-1의 DDL 선행 판단 자체가 흔들린다 |
| 3 | `backend-map-converter` `refactor/mapconvert` 머지 준비. **`origin/dev`가 `841f7a9`(2026-07-10)에서 멈춰 있어 0커밋 뒤처짐 — fast-forward가 그대로 된다.** 리베이스 불필요 | 백엔드 | 단, 머지 전에 이 저장소의 평문 시크릿 처리 방침을 정할 것(8번) |
| 4 | dev DDL 확인 후 **dev 1차 배포(플래그 OFF)** → §7-3·§7-4 | 백엔드 | 개발계는 배포=활성화라 플래그를 명시적으로 내려야 한다 |
| 5 | **DLQ 전체 topic/serial 분포 확인 — redrive 전에.** 무엇이 왜 쌓였는지 모른 채 일괄 redrive 금지 | 백엔드 | VDI에서 SQS 콘솔 |
| 6 | **`SHOW COLUMNS FROM device_config LIKE 'streaming_restrictions_yn'`.** 매퍼가 이 컬럼을 4곳에서 참조하는데(`mapper/device/DeviceConfig.xml` 49·138·225·564행) **`db-schema`의 `prod.sql`·`stg.sql`에는 없다**(grep 0건). 실제 운영 DB에 있는지 확인. 없으면 `getConfig` 계열이 `InvalidDataAccessResourceUsageException`으로 떨어지고, 이건 §3-6에서 **영구 실패로 분류해 ack**하도록 만든 예외라 조용히 버려진다 | 백엔드 | 2026-09-22 실측으로 여전히 미해소 |
| 7 | **IoT rule의 `MessageGroupId`가 serial 단위인지 고정값인지 확인.** 고정값이면 전 기기가 하나의 FIFO 그룹이 되어 기기 1대의 실패가 전체를 막는다. §5의 12번이 완화책일 뿐 근본 해결이 아니다 | 백엔드 | VDI에서 IoT Core 콘솔 |
| 8 | `backend-map-converter` **평문 시크릿 처리 방침 결정.** `refactor/mapconvert`의 `application.yml`에 AWS 액세스 키 1쌍과 장기 JWT 4개가 평문으로 있다. 그 AWS 액세스 키 자체는 이미 폐기가 확인됐으나, `backend-api-main`은 그 사이 SSM 런타임 로딩으로 넘어갔다(2026-08-11 `6b2a4526`, 08-13 `b8c0f28e`). **이 저장소만 옛 방식으로 남는다** | 백엔드 | 리팩토링이 만든 문제가 아니라 원래 있던 것을 그대로 옮긴 것이다. 머지 전에 같이 정리할지 별건으로 뺄지 결정 |

### 8-2. 운영계 릴리스 게이트 (전부 통과 전 prd 배포 금지)

| # | 확인할 것 |
|---|---|
| 9 | prd DB 1차 작업(백업 → `update_date` NULL 제거 → outbox 컬럼 → status backfill → 제약 → 인덱스 2종). 절차와 SQL 전문은 부록 B-5 |
| 10 | prd 플래그 OFF 신규 코드 배포 및 HTTP 회귀 |
| 11 | prd 2차 DB 작업 및 `UNIQUE(job_id)` 적용. **1차에서 UNIQUE를 만들면 안 된다** — 구버전 코드에는 공통 중복 방어가 없어 즉시 위반이 난다. 부록 B-6 |
| 12 | prd 알람 6종 구성 및 **발화 테스트**(§7-5) |
| 13 | prd SQS 단계적 컷오버 및 왕복 확인(§7-4) |
| 14 | **dev·stg에서 실제 맵 변환 왕복 검증.** 이 리팩토링에는 **자동 테스트가 없다**(사용자 결정으로 전부 삭제했다). 왕복 시나리오가 유일한 회귀 검증이고 VOC 직결 기능이다 |

### 8-3. 후속 (이 기능을 막지는 않음)

| # | 항목 |
|---|---|
| 15 | **히트맵 SQS 전환 착수.** 설계는 §3-5에 확정. 착수 전 Phase 0으로 실물 검증 3건이 필요하다 — `aqi_map_refiner` 바이너리의 워커 호스트 이식성, 워커 EC2의 IAM 권한, 신규 큐 쌍 생성. 그리고 **골든 베이스라인 확보**(jar 1.5.0으로 출력이 달라지는 것이 정상이므로) |
| 16 | **관제 표시 결함 백엔드 수정** — `getBuildingChromsMapList`의 `is_use`를 "최신 비-FAILED 맵" 기준으로, `/app/map/list`에서 최신-FAILED 숨김. 실패한 반쪽 맵이 관제 CS 화면과 앱 목록에 보이는 문제다. **설계는 확정, 규모도 작고, 미구현** |
| 17 | **2단계 `map_data` 서버 시딩** — Full 시딩(robot 좌표 포함, yaml 변환 수식 이식)만 유효. 부수 가치로 디폴트 스케줄의 픽셀-좌표 잠복 버그가 개선된다. §6-6 참고 |
| 18 | 요청 경로 FCM 3곳이 아직 트랜잭션 안에서 직접 호출된다(결과 경로만 이번에 이벤트화). FCM이 `@Async`라 위험은 낮다 |
| 19 | `pgmUpload`의 S3 업로드가 트랜잭션 안에 있다. 실패 시 고아 파일이 남는다(경미) |
| 20 | **폴링 정밀화** — `jobId` 계약. 같은 건물에서 여러 기기가 동시에 변환할 때를 대비. 앱 계약 변경이 필요하다 |
| 21 | side-effect 실패 대응 자동화 — 전용 replay 도구. 현재는 기존 운영 수단으로 수동 재발송한다. **side-effect outbox·상태 컬럼·자동 재처리는 이번 범위 밖으로 명시적으로 보류**했다 |
| 22 | **앱의 상태 재조회 또는 타임아웃 fallback** — FCM이 도달하지 않을 때 사용자 대기를 앱 스스로 끝내는 수단. 서버 보장이 DB까지인 이상 이게 있어야 사용자 경험이 닫힌다 |
| 23 | 테스트 재구축. 그리고 컷오버 안정화 후 **HTTP 레거시 경로 폐기** 여부 결정 |
| 24 | 워커 장애 후 2400초 재배달 지연이 실제로 문제가 될 때만 heartbeat 도입과 visibility 단축을 검토 |
| 25 | refiner의 exit-code 계약을 확보하면, 결정적 입력 오류 코드만 즉시 `FAILED`로 분류하도록 좁힌다 |
| 26 | 결과 처리 구간을 계측한 뒤 필요할 때만 결과 큐 visibility 단축과 전용 S3Client를 검토 |
| 27 | refiner 로그 파일 성장, converter 재변환 낭비 — 적대적 리뷰에서 나온 P3 2건. 근거와 함께 수용한 상태 |

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| "맵 변환이 안 끝나요" | `SELECT id, job_id, serial, status, code, message, create_date, update_date FROM user_map_convert WHERE serial = '<serial>' ORDER BY id DESC LIMIT 5;` — `status`가 진실이다 |
| `ENQUEUE_PENDING`이 오래 남아 있음 | `AFTER_COMMIT` 발행이 실패했거나 아직 안 됐다. `MapConvertRepublishScheduler`가 60초마다 재시도한다. `enqueue_attempts`·`last_enqueue_error`·`last_enqueue_at` 컬럼을 본다 |
| `ENQUEUE_FAILED` | 발행이 반복 실패 중. `last_enqueue_error`에 사유가 있다. IAM·큐명·네트워크를 의심 |
| `QUEUED`인데 3시간 넘음 | reconciliation이 `FAILED(504)`로 종결하고 실패 FCM을 보낸다. 로그 마커 `MAP_CONVERT_TIMED_OUT` |
| `FAILED`였다가 `SUCCEEDED`로 바뀜 | **정상이다.** 성공 우선 정책(§5의 2번). 늦게 온 성공 결과를 받아들인 것 |
| FCM이 안 왔는데 DB는 `SUCCEEDED` | 외부 효과는 best-effort다(§3-3). 로그 마커 `MAP_CONVERT_SIDE_EFFECT_FAILED effect=FCM`을 찾되, **FCM은 `@Async`라 마커가 없어도 미도달일 수 있다.** Firebase 비동기 로그를 함께 본다 |
| 기기에 맵이 안 들어감 | `effect=SET_MAP` 마커. DB `SUCCEEDED`와 S3 결과 3파일을 확인한 뒤 기기 setMap을 재요청한다 |
| 공기질 화면이 옛 맵을 보여줌 | `effect=CACHE_CLEAR` 마커. DB는 terminal 유지. 캐시 TTL이나 다음 갱신을 확인 |
| 관제에서 맵 편집이 안 됨 | §6. 미편집 맵의 `shapeList` 폴백 부재. **아직 미구현이다.** 임시 회피는 앱에서 한 번 편집·저장하는 것 |
| DLQ에 메시지가 쌓임 | redrive **전에** 백업하고 `jobID` / DB `status`·`code`·`message` / `request_payload` / S3 입력·결과 객체 존재 여부 / 양쪽 로그의 같은 `jobID`를 함께 확인한다. 원칙: IAM·네트워크·배포 오류가 해소된 경우에만 redrive / 입력 없음·손상 같은 영구 오류는 redrive하지 않음 / **원인 파악 전 일괄 redrive 금지** / **DB `status`를 직접 `SUCCEEDED`로 바꾸지 말 것** |
| DB는 `FAILED`인데 DLQ 메시지는 `SUCCEEDED` | S3 결과 3파일이 있으면 **result 메시지를 redrive해도 된다.** 성공 우선 정책이 받아 준다 |
| IoT 메시지가 DLQ로 감 | `IoT handler permanent failure` 로그를 찾는다. 이 로그가 찍힌 건은 **재실행해도 결과가 같다고 판정해 ack한 것**이라 redrive 대상이 아니다. 원인을 고쳐야 한다 |
| 히트맵 생성이 자주 45초를 넘음 | `airSensorRestTemplate` 타임아웃 로그 빈도를 본다. 잦으면 **전용 큐 분리 시점**이다(= §8-3의 15번을 당길 근거). 타임아웃만 올리면 안 된다(§5의 13번) |
| OASYS·LLM 등 다른 외부 호출이 10초에 끊김 | `API_RESPONSE_TIMEOUT=10000`은 16개 서비스가 공유하는 값이다. 특정 업스트림만 길게 필요하면 `airSensorRestTemplate`처럼 **전용 빈을 분리**하고 공용 값은 건드리지 않는다 |
| 로그 키워드 | `MAP_CONVERT_TIMED_OUT`, `MAP_CONVERT_SIDE_EFFECT_FAILED`, `IoT handler permanent failure`, converter의 `classification=... retryable=...`, MDC `jobID` |

---

## 부록 A. 핵심 커밋 (시간순)

| 날짜 | 저장소 | 커밋 | 내용 | 반영 |
|---|---|---|---|---|
| 07-27 | `backend-map-converter` | `2d4f02e` | 맵 변환 파이프라인 SQS 기반 재설계 (87파일 +1,966/−3,719) | `refactor/mapconvert`만 |
| 07-30 | `db-schema` | `e8f745f` | `user_map_convert` SQS 전환 목표 상태 정의 (`prod.sql`·`stg.sql`) | `refactor/mapconvert`만 |
| 07-31 | `backend-api-main` | `2642bc33` | 맵 변환 파이프라인 SQS 기반 재설계 (39파일 +1,391/−628) | `refactor/mapconvert`만 |
| 08-06 | `backend-api-main` | `249892b1` / `857f0830` | SQS DLQ 적재 원인 수정 — `op_param` 클램프, FIFO 그룹 연쇄 실패 차단 | dev·stg / main |
| 08-06 | `backend-api-main` | `22aaf21d` / `fbe68933` | 히트맵 전용 RestTemplate 분리 | dev·stg / main |
| 08-06 | `backend-api-main` | `24b56052` / `b981fc21` | 예외 재시도 분류 보완(화이트리스트 전환) | dev·stg / main |
| 08-06 | `backend-api-main` | `5b4b1180` / `075b4e0a` | `SdkException.retryable()` 오분류 제거 | dev·stg / main |
| 08-06 | `backend-api-main` | `292b1248` / `60f0643e` | HTTP 타임아웃·커넥션 풀 조정 (45초/10초/100·20) | dev·stg / main |
| 08-06 | `backend-api-main` | `1ae05f13` | Merge branch 'stg' into 'main' — 위 5건이 운영 브랜치에 들어간 지점 | main |

**커밋 해시가 두 벌인 이유**: 작업 브랜치 `bugfix/sqs-dlq-op-param-truncation`의 커밋이 dev로 들어갔고, 같은 변경이 stg 위에 별도 커밋으로 다시 올라가 main으로 머지됐다. `merge-base --is-ancestor`로 왼쪽 해시를 main에 물으면 전부 `NO`가 나오지만 **내용은 들어가 있다**(§2-3).

**이력 재구성 메모**: 2026-08-03 후속 수정(reconciliation 기준 시각, side-effect 마커, `(status, create_date)` 인덱스)을 기존 커밋에 amend해서 저장소별로 여전히 단일 리팩토링 커밋이다. amend로 SHA가 바뀌었다 — api-main `5f666428` → `2642bc33`, db-schema `8953ee5` → `e8f745f`. api-main은 머지 커밋 위에 amend가 불가해 `git commit-tree`(현재 트리 + dev 부모)로 재구성했고 트리 바이트 동일을 검증했다.

**검증 수준**: 빌드는 converter `mvn clean test`(테스트 0개, 컴파일 검증), api-main `compileJava -Pprofile=vdi` — 전 시점 통과. 계약 교차검증(wire 필드·JSON 프로퍼티·큐 이름·JavaType 제거)은 양측 일치. MyBatis XML `#{...}` ↔ Java 프로퍼티는 전수 수동 대조했다(런타임 검증 항목으로 남음). 적대적 리뷰로 상태 기계·경합 시나리오(공존 스케줄러, 강제 종료, SIGTERM, visibility 초과)를 훑었다. **자동 테스트는 없다.**

---

## 부록 B. 인프라 런북 (큐·IAM·DB·컷오버) 전문

리전은 전부 `ap-northeast-2`.

### B-1. 큐 구성값

| 환경 | 요청 큐 | 결과 큐 |
|---|---|---|
| dev | `benjamin-map-convert-request-dev` | `benjamin-map-convert-result-dev` |
| stg | `benjamin-map-convert-request-stg` | `benjamin-map-convert-result-stg` |
| prd | `benjamin-map-convert-request-prd` | `benjamin-map-convert-result-prd` |

| 항목 | 요청 큐 | 결과 큐 | DLQ |
|---|---:|---:|---:|
| VisibilityTimeout | `2400`초 | `900`초 | `30`초 |
| MessageRetentionPeriod | 7일 | 7일 | 14일 |
| ReceiveMessageWaitTimeSeconds | `20`초 | `20`초 | `20`초 |
| maxReceiveCount | `3` | `3` | – |
| 암호화 | SSE 활성 | SSE 활성 | SSE 활성 |

### B-2. IAM·S3 전제

- WAS(= `backend-api-main`) EC2 역할과 MAP(= `backend-map-converter`) EC2 역할에 각각 해당 큐 정책을 연결하고, **EC2에서 실제로 큐에 접근되는지** 확인한 상태다.
- **`sqs:CreateQueue`는 허용하지 않는다.** 애플리케이션이 없는 큐를 자동 생성해서 조용히 엉뚱한 큐로 붙는 것을 막는다. 큐 이름이 틀리면 기동 실패(`ConverterProperties` fail-fast)로 드러나야 한다.
- S3 결과 경로 `result/`에 **lifecycle을 설정하지 않았음**을 확인했다(§5의 8번).
- 맵 버킷: dev `airbot-s3-map-dev` / stg `skmg-benjamin-stg-s3-map` / prd `skmg-benjamin-prd-s3-map`. api-main 쪽 키는 `aws.s3.map.bucket`, converter 쪽 키는 `map-convert.aws.s3.bucket`이며 **양쪽이 같은 버킷을 가리켜야 한다.**

### B-3. 애플리케이션 설정값

- converter refiner timeout: `1800`초
- reconciliation: `create_date` 기준 3시간, 기본 10분 주기
- outbox 재발행: `update_date` 기준 60초, 배치 100, 주기 60,000ms
- reconcile 배치 100, 주기 600,000ms
- S3 결과 lifecycle: **설정하지 않음**

`backend-api-main` 설정 키(전부 `aws.sqs.` 하위):

```
map-convert-processing-enabled          # 결과 큐 소비 + ENQUEUE_PENDING 재발행
map-convert-request-publish-enabled     # 신규 요청을 SQS 로 발행
map-convert-republish-stale-seconds     # 60
map-convert-republish-batch-size        # 100
map-convert-republish-interval-ms       # 60000
map-convert-reconcile-stale-seconds     # 10800
map-convert-reconcile-batch-size        # 100
map-convert-reconcile-interval-ms       # 600000
map-convert-request-queue               # benjamin-map-convert-request-{env}
map-convert-result-queue                # benjamin-map-convert-result-{env}
```

`backend-map-converter` 설정 키:

```
spring.cloud.aws.sqs.enabled            # 단일 스위치
converter.sqs.request-queue
converter.sqs.result-queue
converter.sqs.concurrency               # 4
converter.work-root                     # /app/refiner/data/work
converter.max-download-bytes            # 10485760
converter.max-pgm-pixels                # 10000000
refiner.timeoutSeconds                  # 1800
```

**설정을 SSM으로 옮기지 마라.** `backend-api-main`은 `spring.config.import`로 SSM을 읽는데, **그 값은 yml보다 우선순위가 낮다.** 같은 키가 양쪽에 있으면 SSM 값이 조용히 무시된다. 저장소 안에 이미 같은 함정을 기록한 주석이 있다(`src/main/resources/application.yml`의 `app.internal.enforce-api-key` 바로 위). 큐 이름과 플래그는 민감정보도 아니므로 yml에 두는 것이 맞다.

### B-4. dev/stg DB

초기 변경은 2026-08-03에 완료했다. 신규 코드 배포 전에는 §7-2 확인만 하고, **없는 컬럼·인덱스가 발견된 경우에만 목표 스키마로 전진 보정**한다. 기존 백업 테이블로 원복하지 않는다. 추가 백업이 필요하면 새 이름으로 만든다.

```sql
CREATE TABLE user_map_convert_backup_YYYYMMDD_pre_deploy LIKE user_map_convert;
INSERT INTO user_map_convert_backup_YYYYMMDD_pre_deploy SELECT * FROM user_map_convert;
```

원본과 백업 건수를 대조한다. 배포 직후 1회 보정은 §7-3에 있다.

### B-5. prd 1차 DB 작업 — 코드 배포 **전**

작업 직전 전체 백업을 만들고 건수를 대조한다.

```sql
CREATE TABLE user_map_convert_backup_YYYYMMDD_pre_sqs LIKE user_map_convert;
INSERT INTO user_map_convert_backup_YYYYMMDD_pre_sqs SELECT * FROM user_map_convert;
```

`SHOW CREATE TABLE`을 먼저 보고 **이미 있는 항목은 다시 추가하지 않는다.** 순서는 ① `update_date` NULL 제거 ② outbox 컬럼 추가 ③ status backfill ④ 제약 적용 ⑤ 인덱스 2종.

```sql
UPDATE user_map_convert
   SET update_date = COALESCE(update_date, create_date, NOW())
 WHERE update_date IS NULL;
```

```sql
ALTER TABLE user_map_convert
    ADD COLUMN status             VARCHAR(32)  NULL,
    ADD COLUMN request_payload    TEXT         NULL,
    ADD COLUMN enqueue_attempts   INT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN last_enqueue_error VARCHAR(1000) NULL,
    ADD COLUMN last_enqueue_at    DATETIME     NULL;
```

```sql
UPDATE user_map_convert
   SET status = CASE
       WHEN code = 200 THEN 'SUCCEEDED'
       WHEN code = 0   THEN 'QUEUED'
       ELSE 'FAILED'
   END
 WHERE status IS NULL;
```

```sql
ALTER TABLE user_map_convert
    MODIFY COLUMN status VARCHAR(32) NOT NULL DEFAULT 'QUEUED'
        COMMENT 'ENQUEUE_PENDING/ENQUEUE_FAILED/QUEUED/SUCCEEDED/FAILED',
    MODIFY COLUMN update_date DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        COMMENT '수정일시';
```

```sql
CREATE INDEX ix_user_map_convert_status_update_date ON user_map_convert (status, update_date);
CREATE INDEX ix_user_map_convert_status_create_date ON user_map_convert (status, create_date);
```

**이 단계에서 `UNIQUE(job_id)`를 만들지 않는다.** 구버전 코드에는 공통 중복 방어가 없어서 즉시 위반이 난다.

### B-6. prd 2차 DB 작업 — 플래그 OFF 신규 코드 배포 **후**

1. §7-3의 `QUEUED + code=200` 보정을 실행한다.
2. 기존 HTTP 요청·응답이 정상인지 확인한다.
3. 신규 코드의 공통 중복 방어가 동작하는지 확인한다.
4. **맵 변환 신규 요청을 잠시 중단한다.**
5. 중복을 다시 조회한다.

```sql
SELECT job_id, COUNT(*) AS cnt
FROM user_map_convert
WHERE job_id IS NOT NULL AND TRIM(job_id) <> ''
GROUP BY job_id HAVING COUNT(*) > 1;
```

중복이 있으면 dev/stg에서 검증한 보존 기준으로 백업·정리한다. **현재 활성 맵과 최신 행이 다른 그룹은 자동 삭제하지 않는다.** 중복 0건을 확인한 뒤에 실행한다.

```sql
CREATE UNIQUE INDEX ux_user_map_convert_job_id ON user_map_convert (job_id);
```

UNIQUE 생성이 성공하면 같은 컬럼의 기존 일반 인덱스를 제거한다. **`SHOW INDEX FROM user_map_convert;`로 실제 이름을 확인한 뒤에만** `DROP INDEX`를 실행한다. 이후 맵 변환 신규 요청을 재개한다.

### B-7. 잔여 체크리스트

- [x] dev DB 목표 상태 전진 보정 (**저장소와 불일치 — §8-1의 2번으로 재확인 필요**)
- [x] stg DB 목표 상태 전진 보정 (동일)
- [x] 리팩토링 코드·DDL 커밋을 원격 브랜치에 반영
- [ ] `backend-api-main` 브랜치 리베이스 (부록 D)
- [ ] dev 플래그 OFF 1차 배포 및 HTTP 회귀
- [ ] dev 배포 직후 `QUEUED + code=200` 보정
- [ ] dev SQS 단계적 컷오버 및 왕복 확인
- [ ] stg 플래그 OFF 1차 배포 및 HTTP 회귀
- [ ] stg 배포 직후 `QUEUED + code=200` 보정
- [ ] stg SQS 단계적 컷오버 및 왕복 확인
- [ ] prd 1차 DB 작업
- [ ] prd 플래그 OFF 신규 코드 배포 및 HTTP 회귀
- [ ] prd 2차 DB 작업 및 UNIQUE 적용
- [ ] prd 알람 구성·발화 테스트
- [ ] prd SQS 단계적 컷오버 및 왕복 확인
- [ ] 안정화 후 레거시 HTTP 경로 폐기 여부 결정

---

## 부록 C. DDL 전문 (`db-schema` `refactor/mapconvert` `e8f745f`)

`prod.sql`·`stg.sql` 양쪽의 `user_map_convert` 정의를 아래로 바꾼다. `AUTO_INCREMENT` 값만 파일별로 다르고(`prod.sql`은 `18897`, `stg.sql`은 없음) 나머지는 동일하다.

변경 전 (2026-09-22 현재 `origin/main` 상태):

```sql
CREATE TABLE `user_map_convert` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '일련번호',
  `map_id` bigint NOT NULL COMMENT '맵아이디',
  `job_id` varchar(50) DEFAULT NULL COMMENT '잡아이디',
  `serial` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL COMMENT '시리얼',
  `code` int NOT NULL DEFAULT '0' COMMENT '코드',
  `message` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL COMMENT '메시지',
  `create_date` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '생성일시',
  `update_date` datetime DEFAULT NULL COMMENT '수정일시',
  PRIMARY KEY (`id`),
  KEY `user_map_convert_user_map_FK` (`map_id`),
  KEY `user_map_convert_job_id_IDX` (`job_id`) USING BTREE,
  CONSTRAINT `user_map_convert_user_map_FK` FOREIGN KEY (`map_id`) REFERENCES `user_map` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=18897 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='맵변환정보';
```

변경 후 (목표 상태):

```sql
CREATE TABLE `user_map_convert` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '일련번호',
  `map_id` bigint NOT NULL COMMENT '맵아이디',
  `job_id` varchar(50) DEFAULT NULL COMMENT '잡아이디',
  `serial` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL COMMENT '시리얼',
  `code` int NOT NULL DEFAULT '0' COMMENT '코드',
  `message` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL COMMENT '메시지',
  `create_date` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '생성일시',
  `update_date` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정일시',
  `status` varchar(32) NOT NULL DEFAULT 'QUEUED' COMMENT 'ENQUEUE_PENDING/ENQUEUE_FAILED/QUEUED/SUCCEEDED/FAILED',
  `request_payload` text COMMENT 'SQS 요청 메시지 원본(JSON), 재발행용',
  `enqueue_attempts` int unsigned NOT NULL DEFAULT '0' COMMENT 'SQS 발행 시도 횟수',
  `last_enqueue_error` varchar(1000) DEFAULT NULL COMMENT '마지막 발행 실패 사유',
  `last_enqueue_at` datetime DEFAULT NULL COMMENT '마지막 발행 시도 시각(성공·실패 모두)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_user_map_convert_job_id` (`job_id`),
  KEY `user_map_convert_user_map_FK` (`map_id`),
  KEY `ix_user_map_convert_status_update_date` (`status`,`update_date`),
  KEY `ix_user_map_convert_status_create_date` (`status`,`create_date`),
  CONSTRAINT `user_map_convert_user_map_FK` FOREIGN KEY (`map_id`) REFERENCES `user_map` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=18897 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='맵변환정보';
```

차이는 다음 7가지다. 기존 일반 인덱스 `user_map_convert_job_id_IDX`가 `UNIQUE KEY`로 대체된다.

| 항목 | 이유 |
|---|---|
| `update_date` → `NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` | NULL이면 재발행 스캔이 3값 논리로 그 행을 영영 못 잡는다(§5의 18번) |
| `status` 추가 | 상태 기계의 본체(§3-2) |
| `request_payload` 추가 | outbox — 발행 실패 시 재발행할 원본 |
| `enqueue_attempts`, `last_enqueue_error`, `last_enqueue_at` 추가 | 발행 실패 진단 |
| `ux_user_map_convert_job_id` UNIQUE | 중복 3,718행 사고의 최종 방어선. **구버전 코드에는 공통 중복 방어가 없으므로 신규 코드 배포 후에 만든다** |
| `ix_..._status_update_date` | 재발행 스캔용 |
| `ix_..._status_create_date` | reconciliation 스캔용. 기준 시각이 달라 인덱스도 따로 필요하다 |

**주의**: 이 저장소에는 `prod.sql`과 `stg.sql` 두 파일만 있고 **dev용 파일이 없다.** dev DB의 기준 문서가 저장소에 없다는 뜻이므로, dev는 실제 DB를 직접 확인하는 수밖에 없다.

---

## 부록 D. `backend-api-main` 브랜치 425커밋 뒤처짐 — 리베이스 vs 재작업

### D-1. 결론

**리베이스(또는 dev를 브랜치로 머지)한다. 재작업하지 않는다.** 실제로 충돌하는 파일은 **6개**이고, 그중 실질적인 작업은 Java 3개와 설정 파일 위치 이동 4개뿐이다.

### D-2. 측정 결과

2026-09-22 기준 실측:

```bash
git -C backend-api-main rev-list --count origin/refactor/mapconvert..origin/dev   # 425
git -C backend-api-main rev-list --count origin/dev..origin/refactor/mapconvert   #   1
git -C backend-api-main merge-base origin/dev origin/refactor/mapconvert          # 4f0d3f03 (2026-07-30)
```

숫자만 보면 무섭지만, 425커밋 대부분은 리팩토링과 무관한 파일을 건드렸다. 리팩토링이 수정한 39개 파일 중 dev에서도 바뀐 것은 13개이고, 그중 7개는 설정 파일 이동·테스트 파일 삭제처럼 기계적인 변경이다.

```bash
git -C backend-api-main merge-tree --write-tree --name-only origin/dev origin/refactor/mapconvert
```

충돌 6건:

| 파일 | 충돌 종류 | dev 쪽 변경 규모 |
|---|---|---|
| `api/device/control/service/ResponseDeviceService.java` | content | 17커밋 `+154/−261` (파일 전체 4,553행 중) |
| `api/map/controller/MapConvertController.java` | content | 4커밋 `+0/−71` |
| `api/map/service/MapConvertService.java` | content | 2커밋 `+11/−14` |
| `src/main/resources-local/application.yml` | **modify/delete** | dev에서 파일 자체가 사라짐 |
| `src/main/resources-vdi/application.yml` | **modify/delete** | dev에서 파일 자체가 사라짐 |
| `src/main/resources/application-stg.yml` | content(rename 감지됨) | – |

자동 병합된 것: `AsyncConfig.java`, `application-prd.yml`(rename 감지), `mapper/device/HistoryControl.xml`, 그리고 나머지 30여 개 신규 파일 전부.

### D-3. 충돌 하나하나의 실체

**설정 파일 4개 — 위치 이동이지 논리 충돌이 아니다.** dev가 2026-08 중 설정 레이아웃을 통째로 바꿨다.

```
(옛) src/main/resources-local/application.yml     →  (현) src/main/resources/application-local.yml
     src/main/resources-vdi/application.yml       →       src/main/resources/application-dev.yml
     src/main/resources-stag/application.yml      →       src/main/resources/application-stg.yml
     src/main/resources-prod/application.yml      →       src/main/resources/application-prd.yml
```

프로필 이름도 `vdi→dev`, `stag→stg`, `prod→prd`로 바뀌었고, 시크릿은 SSM 런타임 로딩으로 넘어갔다(`6b2a4526` 2026-08-11 정적 AWS 자격증명 제거, `b8c0f28e` 2026-08-13 SSM 전환). 리팩토링이 이 파일들에 넣은 것은 **`aws.sqs.` 하위 10줄과 주석 1줄**뿐이고, dev의 새 파일에도 `aws.sqs:` 블록이 같은 위치에 그대로 있다. 즉 **10줄을 새 파일 4개의 `aws.sqs:` 블록 아래로 옮겨 붙이면 끝난다.**

이 이동이 §7-1에서 말한 **개발계 플래그 함정을 없앨 기회**다. 새 `application-dev.yml`에 옮길 때 `map-convert-processing-enabled`·`map-convert-request-publish-enabled`를 **`false`로 넣어라.** 그러면 "개발계는 배포 = 즉시 활성화"가 사라지고 세 환경 모두 배포와 활성화를 분리할 수 있다.

**`MapConvertController.java` — 수렴 충돌이다.** dev도 리팩토링도 **같은 죽은 엔드포인트를 삭제**했다(`POST /map/convert/request/{serial}/{mapId}`, `GET /map/convert/request` 테스트 엔드포인트, `RestTemplate`·`FileService`·`convUrl` 필드). dev 쪽은 `+0/−71`, 즉 삭제만 했다. **양쪽 삭제를 합치면 되고 잃을 로직이 없다.**

**`MapConvertService.java` — dev 쪽 변경이 2커밋 `+11/−14`뿐이다.** 둘 다 작성자 본인의 민감정보 로그 정리(`78225fcc`, `bde60691`, 2026-08-13)다. 리팩토링이 이 파일을 671행 규모로 다시 썼으므로 **리팩토링 버전을 채택하고, dev가 지운 민감정보 로그가 리팩토링 버전에 되살아나 있지 않은지만 확인**하면 된다. 확인 지점은 맵 변환 요청·결과 처리 경로의 payload 전문 로그다.

**`ResponseDeviceService.java` — 유일하게 손이 가는 파일.** dev에서 17커밋 `+154/−261`이 들어갔다. 다만 리팩토링이 이 파일에서 바꾼 것은 **63행**이고 내용도 좁다 — `mapping`·`mapUpload` 입구에 중복 방어(`existsConvertRequest`) 추가, `MapConvertCommand` 조립, `lang` 조회 위치 이동. **dev 쪽을 기준으로 두고 이 세 군데만 다시 얹는 것이 가장 안전하다.** dev의 17커밋에는 DLQ 수정(`22aaf21d`의 `airSensorRestTemplate` 주입 등)이 포함돼 있으니 그쪽을 잃지 않아야 한다.

### D-4. 나머지 두 저장소

| 저장소 | 상태 | 조치 |
|---|---|---|
| `backend-map-converter` | `origin/dev` HEAD가 `841f7a9`(2026-07-10)로 **분기점 그 자체**다. 0커밋 뒤처짐 | 리베이스 불필요. fast-forward 머지가 그대로 된다 |
| `db-schema` | `origin/main` 대비 27커밋 뒤처졌지만, 그 27커밋 중 `user_map_convert` 정의를 건드린 것은 **없다**(현재 `main`의 정의가 `e8f745f`의 변경 전 원문과 완전히 일치) | 리베이스 또는 cherry-pick이 충돌 없이 된다 |

### D-5. 권장 절차

```bash
# 0) 안전망 — 현재 브랜치를 백업 참조로 남긴다
git -C backend-api-main branch backup/mapconvert-20260922 origin/refactor/mapconvert

# 1) dev 를 브랜치 쪽으로 머지한다 (리베이스보다 충돌 해결이 1회로 끝난다)
git -C backend-api-main checkout refactor/mapconvert
git -C backend-api-main merge origin/dev

# 2) 충돌 6건을 위 D-3 기준으로 해결
#    - resources-local/vdi yml: 리팩토링 쪽 파일을 삭제(git rm)하고
#      새 application-{local,dev,stg,prd}.yml 의 aws.sqs: 블록에 10줄을 수작업으로 옮긴다.
#      이때 dev 의 두 플래그를 false 로 넣는다.
#    - MapConvertController: 양쪽 삭제를 합친다
#    - MapConvertService: 리팩토링 버전 채택 + 민감정보 로그 재유입 여부 확인
#    - ResponseDeviceService: dev 기준 + 리팩토링의 3군데 재적용

# 3) 검증
git -C backend-api-main diff --stat origin/dev            # 리팩토링 변경만 남아야 한다
./gradlew compileJava -Pprofile=dev                        # 프로필 이름이 vdi→dev 로 바뀐 것에 주의

# 4) 재확인 — 리베이스 과정에서 잃기 쉬운 것들
grep -rn 'map-convert-processing-enabled\|map-convert-request-publish-enabled' \
     src/main/resources/application-*.yml                  # 4개 파일 전부에 있어야 하고 전부 false
grep -rn 'create_date' src/main/java/com/skmagic/core/configuration/MapConvertReconcileScheduler.java
grep -rn 'update_date' src/main/java/com/skmagic/core/configuration/MapConvertRepublishScheduler.java
grep -rn 'MAP_CONVERT_SIDE_EFFECT_FAILED\|MAP_CONVERT_TIMED_OUT' src/main/java
grep -rn 'doNotSendPayloadTypeHeader' src/main/java
grep -rn 'updatePgmImage\|getAllAreasBySerial' src/main/java src/main/resources/mapper   # 0건이어야 한다
```

마지막 줄이 중요하다. **이 쿼리들은 지금 `origin/dev`에 그대로 살아 있다**(`api/map/mapper/MapMapper.java`, `src/main/resources/mapper/map/BuildingMap.xml`에서 grep 적중, 2026-09-22 실측). 리팩토링 브랜치만 지웠으므로, 충돌 해결 중 dev 쪽 버전을 채택하는 순간 되살아나고 §1-2의 "기기 먹통" 경로가 재도입된다. 머지 후 반드시 0건을 확인하라.

---

## 부록 E. 이 작업에서 남기는 교훈

다음 리팩토링을 하는 사람에게 유용한 것만 적는다.

1. **파괴 후 검증 금지.** 비동기 검증(변환) 전에 원본을 커밋으로 파괴하면 복구가 불가능하다. 새 버전을 옆에 만들고 포인터만 전환하라.
2. **커밋 경계 규율.** "트랜잭션 안에서는 결정만, 실행(발행·FCM·MQTT·캐시)은 커밋 후." outbox와 side-effect 이벤트는 같은 원칙의 두 적용이다.
3. **폴백은 서버가 소유해야 한다.** conv/edit 폴백을 클라이언트 4곳이 각자 구현하다 관제만 빠져 기능이 죽었다(§6).
4. **부분 문서는 새로운 상태다.** "`map_data`가 NULL"에 익숙한 소비자에게 "필드가 빠진 문서"를 주면 폴백 가드를 그냥 통과한다. 최소 시딩을 폐기한 이유다.
5. **정리(cleanup)는 정상 종료를 전제로 할 수 없다.** `finally`는 `kill -9`에 무력하다. 크래시 정합성은 부팅 시 청소로 확보한다.
6. **같은 쿼리라도 질문이 다르면 수정도 다르다.** "현재 맵"(상태)과 "최근 작업 결과"(이벤트)는 진실 원천이 다르다(§5의 20번).
7. **SDK가 제공하는 판정 메서드를 그대로 믿지 마라.** `SdkException.retryable()`은 이름과 달리 대부분의 재시도 가능 오류에 `false`를 준다. 판정 기준으로 쓰면 메시지를 유실한다.
8. **FIFO는 공짜가 아니다.** 순서 보장의 대가는 앞 메시지 실패 시의 연쇄 차단이다. 순서가 정말 필요한지 먼저 물어라. 맵 변환은 필요 없어서 Standard를 골랐다.
9. **타임아웃과 visibility는 한 쌍이다.** 한쪽만 조정하면 중복 실행이나 재배달이 생긴다. 코드에 부등식을 주석으로 남겨 두는 편이 낫다.
