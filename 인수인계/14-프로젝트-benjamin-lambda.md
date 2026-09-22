# [프로젝트] benjamin-lambda

| 항목 | 내용 |
|---|---|
| **프로젝트 한 줄 정의** | IoT Kinesis 원문을 Redis·RDS·서비스별 SQS로 분기하는 Lambda 3종 |
| **저장소** | `benjamin-lambda` |
| **기술 스택** | Node.js 22, ES modules, AWS Lambda, Kinesis ESM, Redis, MySQL2, SQS |
| **배포 형태** | 함수별 zip 3개를 수동 빌드해 Lambda 콘솔·CloudShell에 업로드 |
| **작성자 담당 범위** | Redis/RDS 함수 리팩토링, Router 도입, IoT 토픽 라우팅, ESM 필터, RDS write 축소 |
| **기준일** | 2026-09-22 (원격 추적 브랜치를 당일 fetch한 뒤 실측) |
| **인수자가 첫날 할 일** | 세 환경의 실제 함수 코드·ESM 필터를 Git과 대조하고 FOLLOW_ME 수정 브랜치를 검토한다 |

---

## 0. 세 줄 요약

1. 이 저장소는 Kinesis로 들어온 IoT 메시지를 Redis 최신 상태, RDS 제한 이력, backend·security SQS로 분배한다. S3 원문 적재는 IoT Rule·Firehose 인프라가 담당하며 이 저장소 코드 밖이다.
2. 6~7월에 함수 3종을 재작성하고 Router·ESM 필터·zip 배포 체계를 만들었다. 코드는 `dev`·`stg`·`main` 사이 미승격이 없지만 콘솔 업로드 방식이라 실제 배포 버전은 Git으로 알 수 없다.
3. 첫 과제는 3환경 실코드·트리거 대조, 미머지 FOLLOW_ME correlationId 수정, 공기질 원장 쓰기 제거 전 Athena 대사다.

### 브랜치 상태

| 측정 | 값 |
|---|---:|
| `origin/main..origin/stg` | **0커밋** |
| `origin/stg..origin/dev` | **0커밋** |
| `origin/main` 최신 | `478305e`, 2026-08-06 |

브랜치 수치가 0이어도 함수가 최신 zip을 실행한다는 뜻은 아니다. 배포 대상 함수와 zip은 사람이 선택한다.

---

## 1. 이 프로젝트가 하는 일

| 함수 | 역할 |
|---|---|
| **Redis** | `operation`, `battery`, `power`, `lastSeen`, 공기질 신선도 등 최신 상태를 Redis에 쓴다. |
| **RDS** | FOLLOW_ME 시작·종료 같은 제한된 관계형 이력을 MySQL에 쓴다. 공기질 원장 쓰기는 아직 남아 있다. |
| **Router** | topic을 해석해 backend 또는 security FIFO SQS로 전달하고 필요한 필드를 합성한다. |

이 함수들은 API를 제공하지 않는다. Kinesis Event Source Mapping이 입력을 공급하고 부분 배치 실패 응답으로 재시도를 제어한다.

### 용어

| 용어 | 뜻 |
|---|---|
| **ESM** | Lambda Event Source Mapping. Kinesis batch·재시도·필터를 관리한다. |
| **Router allowlist** | 어떤 topic을 어느 SQS로 보낼지 정한 목록이다. |
| **aqtarget 합성** | 공기질 원문에서 서버가 기대하는 target 구조를 Router가 만든다. |
| **write-back** | Redis 최신 상태를 스케줄러가 `statistics_operation`에 주기적으로 반영하는 구조다. |
| **bronze 필드** | S3·Athena 분석용 `topic`, `prefix`, `meta_*`다. WAS SQS payload에는 제거해야 한다. |

---

## 2. 전체 구성

```text
IoT Core
  ├─ Firehose → S3 → Athena iot_messages_raw
  └─ Kinesis
       ├─ Redis Lambda → Redis 최신 상태
       ├─ RDS Lambda   → MySQL 제한 이력
       └─ Router Lambda
            ├─ backend FIFO SQS → backend-api-main
            └─ security FIFO SQS → skix-security
```

인증은 API 토큰이 아니라 Lambda 실행 역할과 VPC·보안그룹, SQS·Redis·RDS IAM·네트워크 권한으로 이뤄진다.

---

## 3. 코드 구조

| 경로 | 책임 |
|---|---|
| `Redis/index.mjs` | topic 파싱과 Redis 명령 생성·batch 실행 |
| `RDS/index.mjs` | Kinesis decode, FOLLOW_ME 상태, DB 처리 |
| `RDS/historyHandler.mjs` | `history_status`와 공기질 원장 INSERT |
| `RDS/dbConnection.mjs` | mysql2 pool·named placeholder |
| `Router/index.mjs` | topic allowlist·target 선택·SQS batch 전송 |
| `infra/esm-filters.json` | 함수별 Kinesis ESM 필터 패턴 |
| `deploy.sh` | 함수별 `npm ci --omit=dev`, zip 3개와 묶음 압축 생성 |

각 레코드는 독립 처리하고 실패한 item만 ESM에 반환한다. `Promise.allSettled`를 유지해 한 레코드 실패가 전체 성공분 재처리로 번지지 않게 한다.

---

## 4. 데이터

### Redis

`operation:{serial}`, `battery:{serial}`, `power:{serial}`, `lastSeen:{serial}`, `lastSeen:{serial}:{areaId}`, `airQualityLastSeen:{serial}`이 핵심이다. airbot 전원 TTL은 2분, 무선 센서 계열은 긴 주기를 별도로 적용한다.

### RDS

`history_status`는 FOLLOW_ME 시작·종료를 저장한다. `operation_air_quality`, `history_part`, `statistics_operation` 쓰기는 대부분 제거됐지만 `AQM_AIR_QUALITY_INSERT`와 `AIRBOT_AIR_QUALITY_INSERT`는 아직 남아 있다.

### 큐·필터

Router의 allowlist와 `infra/esm-filters.json`, 소비자 allowlist 세 곳이 모두 맞아야 메시지가 도달한다. 필터·환경변수·batch size는 Lambda 콘솔에 있어 저장소만으로는 실값을 알 수 없다.

---

## 5. 빌드·테스트·로컬 실행·배포

```bash
./deploy.sh
ls dist/
# rds-lambda.zip redis-lambda.zip router-lambda.zip lambda-zips.tar.bz2
```

각 디렉터리는 `npm ci --omit=dev`로 lockfile을 사용한다. 런타임은 Node.js 22.x, 핸들러는 `index.handler`다. 배포 전 zip을 다시 만들고 함수 이름·환경을 육안 확인한다.

ESM의 batch size, 재시도 2, 오류 시 batch 분할, 필터 패턴을 `infra/esm-filters.json`과 대조한다. 설정·메모리·timeout·VPC는 콘솔에만 있으므로 VDI 확인이 필요하다.

롤백은 이전 커밋에서 zip을 다시 빌드해 올린다. write-back은 Lambda와 스케줄러를 한 쌍으로 되돌린다.

---

## 6. 최근 6개월 작업 이력

| 시기 | 무엇을 | 왜 | 결과 | 핵심 커밋 |
|---|---|---|---|---|
| 6월 | Router 초안과 Redis/RDS 분리, lastSeen·전원 TTL, `statistics_operation` UPSERT 제거를 구현했다. | IoT 수신이 RDS hot row와 원장을 과도하게 갱신했다. | 최신 상태를 Redis로 옮기고 write-back 기반을 만들었다. | `9569860`, `e77890b`, `9ab1bbb` |
| 7월 초 | Redis/RDS를 mysql2·pool·allSettled 구조로 재작성하고 Router SQS 라우팅·ESM 필터를 도입했다. | 함수 안정성과 서비스별 격리, 재현 가능한 zip 배포가 필요했다. | Lambda 3종 구조와 `deploy.sh`가 완성됐다. | `a531250`, `6eb3a50`, `1f094e8` |
| 7월 | SQS payload에서 분석용 필드를 제거하고 KVS·출동·undock topic을 security로 보냈다. | WAS Jackson 오류·컬럼 초과와 신규 KVS 기능 누락을 막기 위해서다. | 순수 payload 계약과 보안 topic 경로가 복구됐다. | `ce661a3`, `561f904`, `4ce9067` |
| 8월 | FOLLOW_ME 유지 중 correlationId 보존 수정이 별도 브랜치에 생겼다. | 종료 행이 시작 행과 연결되지 않는 결함이 있었다. | `03d3e53`은 아직 `dev`에 머지되지 않았다. | `03d3e53` |

---

## 7. 알려진 이슈와 기술 부채

| 우선순위 | 이슈 | 근거 |
|---|---|---|
| P0 | 세 환경의 Lambda 코드·ESM 설정이 어떤 버전인지 미확인이다. | 7월 운영 배포는 리팩토링을 뺀 형상이었고 이후 재업로드 기록이 없다. |
| P1 | FOLLOW_ME 지속 중 correlationId가 NULL로 덮인다. | 수정 `03d3e53`이 미머지다. 8월 이후 종료 이력 일부가 연결되지 않는다. |
| P1 | 공기질 원장 INSERT가 남아 있다. | Athena 집계 대사 없이 제거하면 보고서 공백을 만들 수 있다. |
| P1 | ESM·Lambda 설정이 형상관리 밖이다. | 잘못된 환경 함수에 zip을 올리거나 필터가 drift해도 Git에서 보이지 않는다. |
| P2 | IoT rule·Firehose의 partition timezone이 미확인이다. | Athena 시간 분석에서 UTC/KST 9시간 오판이 가능하다. |

---

## 8. 남은 일

### 즉시

1. VDI에서 RDS·Redis·Router 함수의 코드, handler, runtime, 환경변수, VPC, ESM batch·filter를 3환경 대조한다.
2. `03d3e53`을 리뷰해 `dev → stg → main`에 반영하고 RDS 함수 재배포 여부를 결정한다.
3. DLQ 알람을 4개 DLQ × 3환경으로 완성한다.
4. stg Athena `iot_messages_raw`와 Firehose partition timezone을 확인한다.

### 원장 퇴역 게이트

1. `map_id` 백필을 끝낸다.
2. 동일 시간대 Athena 집계와 기존 원장 집계를 serial·area별로 대사한다.
3. RDS 함수의 공기질 INSERT 2종을 제거하고 필터를 줄인다.
4. 원장 보존기간과 삭제 절차를 확정한다.

### 후속

- aqtarget republish rule과 개별 KDS·Dynamo rule을 사용 여부 확인 뒤 제거한다.
- MQTT 수신·발행 코드 제거와 스트리밍 SQS 전환을 조율한다.
- Lambda 구성 자체를 코드로 관리할 방법을 마련한다.

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| 최신 상태가 멈춤 | Kinesis `IteratorAge` → Redis Lambda 오류·스로틀 → ESM 필터 → Redis 키 TTL 순서로 본다. |
| SQS 메시지가 오지 않음 | ESM filter, Router allowlist, 소비자 allowlist 세 곳에서 topic 철자를 비교한다. |
| FIFO DLQ가 연쇄 증가 | 첫 실패 메시지의 serial·topic·payload 크기를 본다. 한 기기의 실패가 뒤 메시지를 줄줄이 막는다. |
| 대시보드 write-back이 0건 | Redis Lambda의 `lastSeen` 생성과 스케줄러 로그를 함께 본다. |
| FOLLOW_ME 종료 correlationId가 NULL | 함수 코드에 `03d3e53` 상당 수정이 있는지와 캐시의 이전 correlationId를 확인한다. |
| 원문이 필요함 | Lambda 로그가 아니라 Athena `iot_messages_raw`를 사용한다. prefix·partition·payload timestamp를 모두 제한한다. |

---

## 부록. 핵심 커밋

`9569860` Router 초안, `a531250` Lambda 재작성, `6eb3a50` SQS Router, `1f094e8` 신선도 분리, `ce661a3` payload 복원, `4ce9067` KVS·undock topic, `03d3e53` FOLLOW_ME 수정이 핵심이다.
