# [작업] DB 시각 UTC 통일 · DB 접속 TLS 강제

| 항목 | 내용 |
|---|---|
| **상태** | 두 건 모두 **코드 완료 · dev/stg/main 전 브랜치 반영 · 3환경 배포 완료**. 표시 정정 1건(`skix-medcare` kmeta)만 배포 여부 미확인 |
| **작업 기간** | 2026-08-07 ~ 2026-08-18 |
| **직접 수정한 저장소** | `skix-security`, `skix-openapi`, `skix-medcare` — 이 셋뿐이다. 대상 선정 근거는 §1-3 |
| **요청서를 전달한 대상** | 없음. 서버 내부 작업이며 앱·관제 계약은 의도적으로 바꾸지 않았다(§3-7) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §6-2의 **기동 로그 확인**(세 서비스 × 세 환경에서 `DB session UTC verified` 가 찍히는지). 그다음 §7-1의 1번(kmeta 표시 정정이 배포됐는지) |

---

## 0. 세 줄 요약

1. **DB 서버 시간대가 KST인데 애플리케이션 컨테이너는 UTC라서**, 같은 DB 안에 9시간 벌어진 두 기준이 공존했다. 세 저장소 모두 **커넥션 세션을 UTC로 강제 + 기동 시 실측 검증 + 기존 행 보정**으로 정리했고, 업무 경계(일자·마감·파기 기준일)만 KST로 남겼다.
2. **`useSSL=true`는 TLS 강제가 아니다.** 드라이버 기본값(`PREFERRED`)과 동작이 같아 서버가 TLS를 주지 못하면 오류 없이 평문으로 붙는다. 세 저장소 모두 `sslMode=REQUIRED`로 바꿔 평문 폴백 경로를 닫았다.
3. 두 건 모두 브랜치 반영·배포까지 끝났다. 남은 것은 **kmeta 표시 정정의 배포 확인**, **서버 인증서 검증(RDS CA 번들)**, 그리고 **범위 밖으로 둔 `benjamin` 스키마 3개 서비스**(아직 KST + TLS 미강제)다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **KST / UTC** | 한국 표준시(UTC+9) / 협정 세계시. 이 문서에서 "9시간 어긋난다"는 항상 이 차이다. |
| **DB 서버 시간대** | MySQL의 `@@global.time_zone`. 우리 Aurora는 **`Asia/Seoul`** 이다. `NOW()`·`CURRENT_TIMESTAMP`·`CURDATE()`·`DEFAULT CURRENT_TIMESTAMP` 가 이 값을 따른다. |
| **세션 시간대** | `@@session.time_zone`. 커넥션마다 따로 설정할 수 있고, 설정하지 않으면 서버 기본값을 물려받는다. **이번 작업의 핵심 손잡이가 이것이다.** |
| **JVM 기본 시간대** | `user.timezone`. `LocalDateTime.now()` 가 어느 벽시계를 주는지 결정한다. 컨테이너 베이스 이미지가 `distroless` 라 설정이 없으면 UTC다. |
| **벽시계(wall clock)** | 시간대 정보 없이 "2026-08-14 15:00" 처럼 숫자만 있는 시각. Java `LocalDateTime`, MySQL `DATETIME` 이 여기에 해당한다. **같은 숫자가 KST인지 UTC인지는 타입에 실려 있지 않다** — 이 작업의 모든 버그가 여기서 나왔다. |
| **순간(instant)** | 시간대와 무관하게 하나로 정해지는 시점. Java `Instant`·`OffsetDateTime`, MySQL `TIMESTAMP` 가 여기에 해당한다. |
| **라벨 교체 vs 순간 보존** | `atOffset(+09:00)` 은 숫자를 그대로 두고 **이름표만** 붙인다(라벨 교체). `withOffsetSameInstant(+09:00)` 은 같은 순간을 유지하며 **숫자를 옮긴다**(순간 보존). 둘 다 컴파일되고 예외도 없어서, 잘못 쓰면 값만 9시간 틀린다. |
| **Connector/J** | MySQL의 JDBC 드라이버(`com.mysql:mysql-connector-j`). `skix-security`·`skix-openapi` 가 쓴다. |
| **r2dbc-mysql** | MySQL의 R2DBC(리액티브) 드라이버(`io.asyncer:r2dbc-mysql`). `skix-medcare` 가 쓴다. Hikari가 없어 JDBC와 설정 방법이 다르다. |
| **`sslMode`** | 드라이버의 TLS 정책. `DISABLED` < `PREFERRED`(기본값) < `REQUIRED` < `VERIFY_CA` < `VERIFY_IDENTITY`. **`PREFERRED` 는 "되면 하고 안 되면 평문"** 이다. |
| **SSM** | AWS Systems Manager Parameter Store. ECS 태스크 정의가 `DB_URL` 등을 여기서 읽어 환경변수로 주입한다. |
| **`benjamin` 스키마** | 로봇 본체 서비스가 쓰는 DB 스키마. `backend-api-main`·`backend-scheduler-main`·`skix-streaming` 이 공유한다. **이번 작업 범위 밖**이다(§1-3). |

### 1-2. 문제 두 가지

#### 문제 ① — 한 DB 안에 시각 기준이 두세 개였다

**실측(2026-08-14, 개발계)**: 세 저장소의 DB 모두 `@@global.time_zone = Asia/Seoul`(오프셋 32400초), `@@system_time_zone = UTC`. 애플리케이션 컨테이너는 셋 다 `distroless/java21` + `TZ` 미설정이라 **UTC**.

그래서 같은 테이블 안에 이런 값들이 섞였다.

| 값의 출처 | 실제 기준 |
|---|---|
| `DEFAULT CURRENT_TIMESTAMP`, 매퍼의 `NOW()`·`CURDATE()` | **KST** (DB 세션 = 서버 기본값) |
| 애플리케이션이 넣는 `LocalDateTime.now()` | **UTC** (컨테이너 기본 시간대) |
| `skix-medcare` `PhrService` 의 `now(KST)` | **KST** (당시 세션에 맞춰 일부러 맞춰 놓은 것) |

결정적 증거는 `skix-openapi` 에서 나왔다. **토큰 발급 시각(DB `NOW()`)과 그 토큰을 발급한 API 호출 로그(앱이 넣는 값)가 같은 사건인데 9시간 벌어져 기록**됐다 — 개발계에서 `16:20:00.000`(KST) / `07:20:00.606`(UTC)로, 0.6초 차이의 한 사건이었다.

실제로 사용자에게 나간 증상은 네 가지다.

| 증상 | 저장소 | 원인 |
|---|---|---|
| 포털의 "오늘 사용량"이 **매일 KST 00:00~09:00 동안 0** 으로 보임 | skix-openapi | `called_at`(UTC 저장)을 `CURDATE()`(KST)와 비교. 아직 오지 않은 시각을 기준으로 잡아 한 건도 걸리지 않았다 |
| 접속기록을 **보유기간보다 9시간 일찍 파기** | skix-openapi | 파기 기준 `NOW()`(KST)가 UTC 저장값보다 9시간 앞섰다 |
| 신고 이력을 **보유기간보다 9시간 일찍 파기** | skix-medcare | 같은 구조 |
| 대화 목록의 시각이 **9시간 이르게** 표시 (저녁 7시 대화가 오전 10시로) | skix-medcare | 별건. §3-7 참조 |

이 부류의 버그는 **타입도 맞고 파싱도 되고 예외도 나지 않는다.** 컴파일러도 일반 단위 테스트도 잡지 못하고 값만 조용히 틀린다. 그래서 조치가 "고치기"로 끝나지 않고 "규약을 코드로 고정하기"까지 간다.

#### 문제 ② — DB 접속이 평문으로 떨어질 수 있었다

**실측(2026-08-18, TLS를 끈 MySQL + Connector/J 8.1.0)**:

| 설정 | TLS 미지원 서버 상대로 연결하면 |
|---|---|
| 파라미터 없음 / `useSSL=true` / `sslMode=PREFERRED` | **연결됨 — 평문** (`Ssl_cipher` 가 빈 값) |
| `useSSL=true&requireSSL=true` / `sslMode=REQUIRED` | 연결 거부 |

즉 `useSSL=true` 는 드라이버 기본값과 동작이 같아 **아무것도 강제하지 않는다.** 이름만 보고 "SSL 켜져 있다"고 읽히는 것이 이 설정의 위험이다.

작업 착수 시점의 세 저장소 상태:

| 저장소 | 상태 |
|---|---|
| `skix-medcare` | `application-ecs.yml` 에 `sslMode: ${DB_SSL_MODE:REQUIRED}` — **이미 강제됨**. 나머지 둘이 따를 본보기가 됐다 |
| `skix-security` | SSM `DB_URL` 의 `useSSL=true` 가 전부, yml에 `sslMode` 없음 → **미강제** |
| `skix-openapi` | URL에도 yml에도 SSL 설정이 **아예 없음** → 드라이버 기본값 `PREFERRED` → **미강제** |

RDS는 TLS를 지원하므로 평시에는 실제로 TLS로 붙고 있었을 가능성이 높다. 문제는 **그 사실이 어디에도 드러나지 않고, 협상이 실패하면 조용히 평문으로 떨어진다**는 점이다.

### 1-3. 대상 범위 — 왜 이 세 저장소인가

UTC 통일은 **`skix-security`·`skix-openapi`·`skix-medcare`** 만 했다. 각각 자기 전용 스키마(`security` / `openapi` / `medcare`)를 갖기 때문이다.

**`benjamin` 스키마는 일부러 건드리지 않았다.** 이 스키마는 `backend-api-main`·`backend-scheduler-main`·`skix-streaming`·Lambda 가 함께 쓰고, 이력 컬럼이 전부 DB `NOW()` = KST 기준으로 수년치 쌓여 있다. 애플리케이션 넷을 동시에 바꾸고 데이터를 전부 보정해야 하는 별개 규모의 작업이다.

**RDS 파라미터 그룹(`time_zone`)도 건드리지 않았다.** 세 서비스의 DB는 `skmg-benjamin-{env}-aurora` **공유 클러스터**에 스키마로 들어 있다. 파라미터 그룹을 UTC로 바꾸면 `benjamin` 스키마를 쓰는 다른 서비스의 `NOW()`·`CURDATE()` 까지 함께 움직이고, 운영자가 콘솔에서 조회할 때 보는 값도 바뀐다. **애플리케이션 세션만 강제하는 것이 유일하게 안전한 경로였다.**

TLS 강제는 같은 세 저장소에만 적용했다. 이유는 소극적이다 — 그 셋이 마침 작업 중이던 저장소였고, 코드 한 줄이라 범위를 넓히는 데 기술적 장벽은 없다(§7-2의 7번).

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 커밋 반영 (`git merge-base --is-ancestor` 로 확인)

| 저장소 | 커밋 | 날짜 | 내용 | dev | stg | main(운영) |
|---|---|---|---|---|---|---|
| `skix-medcare` | `ee8780b` | 08-07 | DB 접속 TLS 강제(R2DBC) | 반영 | 반영 | 반영 |
| `skix-security` | `242196e` | 08-13 | DB 저장 시각 UTC 통일 | 반영 | 반영 | 반영 |
| `skix-openapi` | `014ef2c` | 08-14 | 사용량 조회·접속기록 파기 기준 UTC 정정 | 반영 | 반영 | 반영 |
| `skix-openapi` | `f1adacb` | 08-14 | DB 세션 UTC 강제 + 기동 검증 | 반영 | 반영 | 반영 |
| `skix-medcare` | `deb2c33` | 08-14 | DB 저장 시각 UTC 통일(R2DBC 세션 강제·기동 검증·업무 경계 분리) | 반영 | 반영 | 반영 |
| `skix-openapi` | `dcb1a5a` | 08-18 | DB 접속 TLS 강제 | 반영 | 반영 | 반영 |
| `skix-security` | `d7e953c` | 08-18 | DB 접속 TLS 강제 | 반영 | 반영 | 반영 |
| `skix-medcare` | `621a5dd` | 08-18 | kmeta 대화 시각 표시 정정 | 반영 | 반영 | 반영 |

**8건 전부 세 브랜치에 들어가 있다.** 세 저장소 모두 작업 트리가 깨끗하고 미push 커밋은 없다(2026-09-22 실측).

### 2-2. 설정·배포 실측

| 항목 | 상태 | 근거 |
|---|---|---|
| 세 저장소 `application-ecs.yml` 의 세션 UTC 강제 | **적용됨** | `skix-security`·`skix-openapi` 는 `connection-init-sql: "SET time_zone = '+00:00'"`, `skix-medcare` 는 `R2dbcUtcSessionConfig`(프로필 무관 코드) |
| 세 저장소의 기동 검증기 스위치 | **켜짐**(`app.database.require-utc-session: true`) | ecs·localhost 두 프로필 모두. 값이 리터럴이라 환경변수로 끌 수 없다 |
| 세 저장소 `sslMode` | **`${DB_SSL_MODE:REQUIRED}`** | JDBC 둘은 `hikari.data-source-properties`, medcare 는 `spring.r2dbc.properties` |
| 태스크 정의에 `DB_SSL_MODE` 완화 값이 들어 있는지 | **없음** — 9개 태스크 정의(3저장소 × 3환경) 전부에 이 키가 없다 | `deploy/task-definition-{dev,stg,prd}.json` 실측. 즉 세 환경 모두 `REQUIRED` 로 돈다 |
| Dockerfile `-Duser.timezone=UTC` | **적용됨** | security 1곳, openapi 2개 파일(`Dockerfile`·`Dockerfile_Datadog`), medcare 2개 스테이지(`runtime`·`runtime-debug`) |
| 3환경 애플리케이션 배포 | **완료** (UTC 통일·TLS 강제 모두) | 작업 당시 기록. 로컬에서 ECS 상태를 볼 수 없어 이미지 태그로 재확인은 불가 |
| 기존 행 보정 | **완료** — openapi 열댓 행, medcare `customer_if_log`·`common_code` (작업 당시 기록) | 보정 스크립트는 커밋하지 않았다(§3-8). 각 DB에 원장 테이블이 남아 있으므로 `SELECT * FROM open_api_schema_migration` / `medcare_schema_migration` 로 재확인할 수 있다. 재구성 템플릿은 부록 C |
| SSM `DB_URL`·`SPRING_R2DBC_URL` 에서 `serverTimezone`·`serverZoneId`·`useSSL` 제거 | **미확인** (작업 당시 정리 완료로 기록) | 로컬 맥에서 SSM에 접근할 수 없다. VDI에서 `aws ssm get-parameter --name /skix-{security,openapi}/{env}/DB_URL --with-decryption` 로 확인. **정리되지 않았어도 동작에는 영향이 없다**(§4-2의 우선순위) |
| `skix-medcare` kmeta 표시 정정(`621a5dd`) 배포 | **미확인** | 확인 방법은 §7-1의 1번 |

### 2-3. 범위 밖 저장소의 현재 상태 (실측)

이 표는 "아직 안 한 것"의 목록이다. 조사·이관 때 전제를 틀리지 않도록 적어 둔다.

| 저장소 / 대상 | DB 시각 | DB TLS | 근거 |
|---|---|---|---|
| `backend-api-main` (`benjamin` 스키마) | **KST** — 세션 설정 없음, 이력 컬럼이 DB `NOW()` | **미강제** — `sslMode`·`useSSL` 없음 | `src/main/resources/application-{dev,stg,prd}.yml` 의 `jdbc-url` 에 파라미터가 하나도 없다 |
| `backend-scheduler-main` (`benjamin`) | **KST** | **미강제** | `src/main/resources-{vdi,stag,prod}/application.yml` 의 `datasource.url` 에 파라미터 없음 |
| `skix-streaming` (`benjamin`) | **KST** — URL에 `serverTimezone=Asia/Seoul` 명시 | **미강제** | `src/main/resources-{vdi,stag,prod}/application.yml` |
| MQTT 원본 S3 데이터레이크 (Athena `iot_messages_raw`) | **UTC** — 페이로드 최상위 `timestamp` 가 `Instant.now()` 기반 | 해당 없음 | 기기 에이전트가 만드는 값 |
| 위 데이터를 `benjamin` 에 적재하는 Lambda | **KST로 변환해 저장** | 해당 없음 | `benjamin-lambda/RDS/historyHandler.mjs` 의 `formatTimestampForMysql` 이 파싱한 순간에 `9 * 60 * 60 * 1000` 을 더한다 |

**따라서 Athena(UTC)와 `benjamin` MySQL(KST)을 조인할 때는 9시간 보정이 필수다.** 보정을 빠뜨려도 값이 그럴듯하게 나와서 눈으로는 안 잡힌다.

같은 이유로, `benjamin` 스키마의 값을 응답으로 내보내는 API는 여전히 KST 벽시계를 다룬다. 실제로 2026-09-04에 파트너 API의 명령 상태 응답에서 이 경계가 문제가 됐다 — 같은 이름의 필드가 접수 응답에서는 UTC인데 상태 조회 응답에서는 `benjamin` 의 KST 값이 오프셋 없이 실려 9시간 어긋났다(`backend-api-main 41af4e53`, `skix-openapi c20c33a`, 둘 다 **dev·stg 반영·main 미반영**). 이 두 커밋은 파트너 API 작업의 일부이며 이 문서의 범위는 아니지만, **경계를 넘는 시각은 오프셋을 명시하라**는 같은 교훈의 사례다.

---

## 3. 무엇을 만들었나 — (1) 시각 UTC 통일

### 3-1. 규약 (세 저장소 공통)

```
저장      : UTC.   DB에 들어가는 모든 시각은 UTC 벽시계다.
업무 경계 : KST.   일자·마감·집계 창·파기 기준일은 KST 달력으로 계산하고,
                   DB와 비교하기 직전에 UTC로 옮긴다.
표시      : KST.   사람이 보는 응답·관제 화면은 KST. 단 '라벨 교체'가 아니라
                   '순간 보존' 변환으로 옮긴다.
```

이 규약의 선례는 `skix-security` `242196e` 다. 저장을 전부 UTC로 옮기면서 `OasysService` 의 청구 조회 기준일 **하나만** `LocalDate.now(KST)` 로 바꿨다 — "1년 전 청구분"은 한국 달력의 업무 개념이기 때문이다. 나머지 두 저장소가 이 구분을 그대로 따랐다.

### 3-2. 방어는 세 겹이다

| 겹 | 무엇 | 없으면 |
|---|---|---|
| ① JVM 기본 시간대 | Dockerfile ENTRYPOINT의 `-Duser.timezone=UTC` | 베이스 이미지가 바뀌면 `LocalDateTime.now()` 가 조용히 다른 시간대로 움직인다. 지금은 distroless가 UTC라 없어도 맞지만, 그건 우연이다 |
| ② DB 세션 시간대 | JDBC: Hikari `connection-init-sql: "SET time_zone = '+00:00'"` / R2DBC: `R2dbcUtcSessionConfig` | `DEFAULT CURRENT_TIMESTAMP` 와 매퍼의 `NOW()` 가 전부 KST로 돌아간다 |
| ③ 기동 시 실측 검증 | `DatabaseUtcSessionVerifier` — 어긋나면 **기동 실패** | ①②가 실제로 먹었는지 아무도 모른 채 9시간 틀린 값이 쌓인다 |

③의 검증 SQL은 세 저장소가 동일하다.

```sql
SELECT @@session.time_zone AS session_time_zone,
       TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(3), CURRENT_TIMESTAMP(3)) AS utc_offset_seconds
```

**판정은 이름이 아니라 오프셋으로 한다.** `@@session.time_zone` 은 `'SYSTEM'`·`'+00:00'`·`'UTC'` 등 표기가 여러 가지라 문자열 비교로는 걸러지지 않는다. `utc_offset_seconds != 0` 이면 `IllegalStateException` 을 던져 컨텍스트 기동을 중단한다. 성공하면 로그에 이 한 줄이 남는다.

```
DB session UTC verified: sessionTimeZone=+00:00, offsetSeconds=0
```

**부작용을 알고 있어야 한다**: 검증기가 `@PostConstruct` 에서 커넥션을 잡으므로, **기동 시점에 DB에 닿지 못하면 태스크가 뜨지 않는다**(세 저장소 공통). DB 장애 중 배포하면 재시작 루프에 빠진다. 의도된 동작이지만 장애 대응 중에는 기억해야 한다.

### 3-3. 저장소별 구현

| 항목 | `skix-security` | `skix-openapi` | `skix-medcare` |
|---|---|---|---|
| DB 접근 | JDBC (MyBatis) | JDBC (MyBatis) | **R2DBC** (리액티브) |
| 세션 강제 방법 | Hikari `connection-init-sql` (ecs·localhost yml) | Hikari `connection-init-sql` (ecs·localhost yml) | `R2dbcUtcSessionConfig` — **yml이 아니라 프로필 무관 코드**. Hikari 훅이 없어 `ConnectionFactoryOptionsBuilderCustomizer` 로 `connectionTimeZone=UTC` + `forceConnectionTimeZoneToSession=true` 주입 |
| 드라이버 제약 | Connector/J (Boot 3.2.0 BOM) | Connector/J 8.3.0 고정 | **r2dbc-mysql 1.0.5 → 1.4.1 올림.** 세션 옵션이 1.2.0부터라 1.0.x로는 애초에 불가능했다 |
| 애플리케이션 시각 생성 | `UtcDateTimes.now()` / `fromEpochMillis()` 유틸 도입 | **유틸 없음.** JVM 기본 시간대(UTC)에 의존하고 `LocalDateTime.now()` 를 그대로 둔다 | `UtcDateTimes.now()` / `fromKst()` / `toKst()` 유틸 도입 |
| 기동 검증기 | `common/config/DatabaseUtcSessionVerifier` | `common/config/DatabaseUtcSessionVerifier` | `config/DatabaseUtcSessionVerifier` (R2DBC판, 15초 타임아웃, 판정 로직을 `judge()` 로 분리해 단위 테스트) |
| Dockerfile | 1개 | **2개** (`Dockerfile`, `Dockerfile_Datadog` — stg는 후자로 빌드) | 1개 / **2 스테이지** (`runtime`, `runtime-debug`) |
| Dockerfile 검사 가드 | **없음** (§7-2의 4번) | `deploy/build-export.sh` 에서 `grep -q -- '-Duser.timezone=UTC'` | `deploy/build-export.sh` 에서 `grep -c` 로 **2개 이상**인지 확인 |
| 규약 고정 테스트 | `UtcDateTimesTest`, `DatabaseUtcSessionVerifierTest` | `MapperTimeZonePolicyTest`(매퍼별 규약 + 프로필 스위치 + Dockerfile 2개), `DatabaseUtcSessionVerifierTest` | `TimeZonePolicyTest`(프로필 스위치 + 드라이버 하한 + Dockerfile 2블록 + **bare `now()` 금지**), `DatabaseUtcSessionVerifierTest`, `KmetaTimeZoneTest` |

`skix-openapi` 만 `UtcDateTimes` 유틸이 없다. 이 저장소는 애플리케이션이 시각을 만드는 지점이 감사 로그·사용량 몇 곳뿐이고 전부 JVM 기본 시간대에 의존해도 되는 자리라서, 유틸 대신 **Dockerfile 고정 + 빌드 스크립트 가드 + 매퍼 규약 테스트**로 같은 보장을 만들었다. 다만 이 저장소에는 `LocalDateTime.now()`·`LocalDate.now()` 가 남아 있으므로(`AuditLogFilter`, `WsAuditAspect`, `LogCleanupScheduler`, `BuildingService`), **JVM 기본 시간대가 흔들리면 이쪽이 먼저 깨진다.** 그게 `build-export.sh` 가드가 있는 이유다.

### 3-4. 매퍼·SQL 함수 규약 (`skix-openapi`)

세션이 UTC가 되면 `NOW()` 도 UTC가 된다. 그래도 매퍼마다 규약을 나눠 고정했다 — 세션 강제가 사라지면 모든 `NOW()` 가 한꺼번에 KST로 되돌아가기 때문이다. `MapperTimeZonePolicyTest` 가 이 표를 지킨다.

| 분류 | 매퍼 | 규약 | 이유 |
|---|---|---|---|
| **세션과 무관하게 UTC** | `UsageMapper.xml`, `audit/AuditLogMapper.xml` | `UTC_DATE()` · `UTC_TIMESTAMP()` 를 **직접** 쓴다. `NOW()`·`CURDATE()` 금지 | 애플리케이션이 UTC로 넣은 `called_at`·`date` 를 읽는다. 세션 강제가 뚫려도 이쪽만은 흔들리지 않아야 한다 |
| **DB측 시계 유지** | `AuthMapper.xml`, `WebhookMapper.xml`, `UserPermissionMapper.xml` | `NOW()` 를 **그대로 둔다** | 쓰기도 읽기도 DB측이라 짝이 맞는다. **한쪽만 `UTC_TIMESTAMP()` 로 바꾸면 그 순간 깨진다** — `AuthMapper` 는 유효한 토큰이 만료로 판정되고, `WebhookMapper` 는 파기 기준이 밀린다 |
| **시각 함수 미사용** | `ClientMapper.xml`, `PlanMapper.xml` | 생성 시각을 DDL `DEFAULT` 에 위임 | — |

`AuthMapper` 의 `FROM_UNIXTIME` 은 세션과 무관한 대응물이 없어서, 발급·만료 판정 짝은 어차피 세션 강제에 의존한다. 그래서 테스트가 매퍼뿐 아니라 **전제(두 프로필의 세션 강제 + Dockerfile 두 개)** 까지 함께 붙잡는다.

### 3-5. DDL 주석 규약

시각 컬럼의 코멘트에 `'UTC - '` 접두를 붙였다. 세 저장소 공통이다.

```sql
`requested_at`    datetime(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT 'UTC - 요청 시각',
`closed_at`       datetime(3)  NULL     COMMENT 'UTC - 종료 시각',
```

`DATETIME` 값만 봐서는 어느 벽시계인지 알 수 없으므로, **스키마 자체가 답을 들고 있게** 한 것이다. 조사·이관 때 이 코멘트가 유일한 1차 근거가 된다. 현재 반영 범위: `skix-security` 12개 중 10개 파일, `skix-openapi` 10개 중 7개, `skix-medcare` 17개 중 7개(`src/main/resources/db/` 기준). **나머지는 시각 컬럼이 없거나 kmeta 계열이다. 새 DDL을 쓸 때 이 접두를 빠뜨리지 말 것.**

### 3-6. 업무 경계는 KST로 남긴 지점

| 저장소 | 지점 | 처리 |
|---|---|---|
| `skix-security` | `OasysService` — 청구 이력 "1년 전" 기준일 | `LocalDate.now(KST)` 로 계산해 `yyyyMMdd` 문자열로 넘긴다 |
| `skix-openapi` | 일 사용량 집계·조회 | **예외적으로 UTC 일자 기준**(`UTC_DATE()`). 기존 일 집계(`aggregateDailySummary`)가 `targetDate`·`called_at` 모두 UTC로 이미 정합해 있어 조회를 거기에 맞췄다. 파트너에게 보이는 "하루"를 KST로 바꿀지는 기획 판단으로 남아 있다(§7-2의 12번) |
| `skix-medcare` | `LogService` 일·주·월 사용량 창 | KST 달력으로 계산 → `UtcDateTimes.fromKst()` 로 옮겨 쿼리 파라미터에 넣는다. 시계 주입도 `clock.withZone(KST)` 로 바꿨다 |
| `skix-medcare` | `LogService` 관제 조회 창 (`yyyyMMdd` 두 개) | 관제가 넘기는 날짜는 KST 날짜다. `fromKst` 로 옮긴 뒤 비교 |
| `skix-medcare` | `RetentionPurgeService` 파기 컷오프 | KST 자정 → UTC 비교값. **감사 로그에는 KST 기준일로 환산해 기록**한다 |

`skix-medcare` 의 사용량 창에는 **잠복 버그가 함께 있었다.** 종전에는 컨테이너 시계(UTC)의 날짜로 창을 계산해서, 매일 **KST 00:00~09:00 사이에는 전날 창**이 잡혔다. UTC 전환과 같은 커밋에서 바로잡았다.

주간 창은 "직전 수요일 00시(KST)부터 1주"다. `BETWEEN` 이 양끝을 포함하므로 끝 경계에서 1나노초를 뺀다 — 이렇게 하지 않으면 다음 주 시작 정각이 두 창에 이중 집계된다.

### 3-7. 표시(읽기) 지점과 앱·관제 영향

**저장 기준을 바꾸면 표시도 같이 바꿔야 한다.** 그러지 않으면 화면이 9시간 틀린다. 응답 계약(필드명·포맷·오프셋 표기)은 전부 그대로 두고 **값만** 맞췄다. 그래서 앱·관제 쪽에 요청한 작업은 없다.

| 저장소 | 지점 | 처리 | 클라이언트 영향 |
|---|---|---|---|
| `skix-medcare` | `CustomerIfLogResponse` (관제 사용량 목록) | 포맷이 오프셋 없는 `yyyy-MM-dd HH:mm:ss` 라 `toKst()` 로 변환 | **없음** — 관제가 보던 KST 벽시계가 그대로 유지된다 |
| `skix-medcare` | `PhrService.phrReceiveDateTime` | `atOffset(KST)`(라벨 교체) → `withOffsetSameInstant(KST)`(순간 보존) | **없음** — `+09:00` 표기 유지, 값만 정확해진다 |
| `skix-medcare` | kmeta 대화 목록·상세 (`621a5dd`) | 아래 별도 설명 | **있음** — 기존 대화 시각이 9시간 **뒤로** 이동해 보인다 |

**kmeta 대화 시각 정정(`621a5dd`)** 은 UTC 통일과 뿌리가 같은 별건이다.

- 외부 AI 연동 측은 대화 시각을 UTC로 준다(`"2026-08-18T03:02:47.155191Z"`). 실측으로 확정했다 — `kmeta_streams.last_used_time` 과 UTC가 확정된 `customer_if_log` 를 164건 대조해 평균 차 0분.
- `ConvertService` 가 `OffsetDateTime.toLocalDateTime()` 으로 **오프셋을 버리고** 숫자만 저장했고, `ConversationMapper` 가 그 값에 `atOffset(+09:00)` 으로 **라벨만** 붙여 내보냈다. UTC 03:02가 "03:02 KST"로 나갔다 — 실제(12:02 KST)보다 9시간 이르다.
- 세 환경 모두 태스크 정의의 대화 라우팅 설정이 내부 경로(`INTERNAL`)라 **사용자에게 서비스 중**이었다. 외부 경로는 응답을 그대로 통과시켜 정상이었으므로, **같은 대화가 라우팅에 따라 9시간 다르게** 보이는 상태였다.
- 수정: 적재 6곳은 `toUtcLocal()`(UTC 정규화 후 절단), 노출 2곳은 `atOffset(UTC).withOffsetSameInstant(KST)`. **저장 데이터는 이미 UTC라 보정이 필요 없다.**
- 검색 결과의 발행일(`parseDate`)은 대상이 아니다. 날짜만("2022-03-08") 오는 경우가 대부분이라 시간대를 부여하면 없던 정보를 만들고 날짜 경계가 하루 밀린다.

**배포하면 기존 대화 목록의 시각이 9시간 뒤로 이동해 보인다.** 값이 정정되는 것이지만 사용자 눈에는 변화다. iOS·웹 담당에게 사전 공유가 필요하다(§7-1의 1번).

### 3-8. 기존 행 보정 — 2단계 경계 스냅샷

세션을 UTC로 바꾸면 **그 이후 행만** UTC다. 이전 행 중 DB `NOW()`·`DEFAULT` 가 만든 값은 KST로 남아 한 컬럼 안에 두 기준이 섞인다. 이걸 정리하는 절차다.

```
PART 1 (배포 직전)  : 보정 대상 테이블의 '경계'를 원장에 기록한다.
                      (auto_increment PK가 있으면 MAX(id), 없으면 그 시점의 시각 최대값)
        ↓
배포                : 이 시점 이후 들어오는 행은 UTC로 쌓인다.
        ↓
확인                : 기동 로그의 'DB session UTC verified' + 새 행이 UTC인지 데이터로 확인.
        ↓
PART 2              : 경계 이하 행만 -9시간 이동. 원장에 실행 시각과 건수를 남긴다.
```

**한 번에 하지 않는 이유**가 이 방식의 전부다.

- 배포 창이 길어져도 **그 사이 들어온 행(이미 UTC)은 자동으로 대상에서 빠진다.** 시각 범위로 자르면 이 구분이 안 된다.
- 배포가 롤백되면 **PART 2를 돌리지 않으면 그만이다.** 데이터를 건드리기 전이라 되돌릴 것이 없다.
- 원장(`open_api_schema_migration` / `medcare_schema_migration`)이 재실행 가드다. `applied_at IS NULL` 조건으로 두 번 실행해도 -18시간이 되지 않는다.

**실제 보정 규모는 작았다.**

| 저장소 | 대상 | 규모 |
|---|---|---|
| `skix-openapi` | `clients`, `user_permissions` (전부 표시용 컬럼) | 환경당 1~7행. `webhooks`·`webhook_deliveries` 는 세 환경 모두 0행 |
| `skix-openapi` | `open_api_tokens` | **보정 대상 아님.** TTL 1시간에 매일 정리라 반나절이면 자연 소멸하고, 실제 접근 허용은 JWT 서명·`exp`·블랙리스트가 판정한다 |
| `skix-medcare` | `customer_if_log`(경계 = `MAX(id)`), `common_code` | DEFAULT가 발동해 온 두 테이블뿐 |
| `skix-medcare` | `customer_if_phr`, `customer_if_phr_data` | **보정 금지.** `TIMESTAMP` 타입이라 내부 순간이 이미 올바르다. -9h 하면 지금 맞는 값을 틀리게 만든다 |
| `skix-medcare` | `folders` 계열 | 이미 UTC (애플리케이션이 넣는 값) |
| `skix-medcare` | `kmeta_*` | 외부 AI 연동 측이 넣는 값이라 제외. 별도 조사 후 §3-7로 결론 |
| `skix-security` | 해당 없음 | — |

**보정 스크립트는 커밋하지 않았다.** 운영 시점에만 쓰는 1회성 로컬 스크립트라 세 저장소 모두 `deploy/migrations/` 를 `.gitignore` 에 넣었고 실행 후 삭제했다. 원장 테이블만 각 DB에 남아 있다. 같은 절차가 다시 필요할 때 쓸 재구성 템플릿은 **부록 C**에 있다.

**재사용 가치가 높은 측정 기법**: 조용한 테이블의 시간대는 **활발한 테이블을 기준자로 삼아 잰다.** 기준이 확정된 테이블과 같은 사건을 키로 조인해 평균 차를 보면, 0분이면 같은 기준이고 ±540분이면 9시간 어긋난 것이다. kmeta 조사에서 `customer_if_log`(UTC 확정)를 기준자로 썼고, openapi에서는 "토큰이 그 토큰을 만든 API 호출보다 먼저 찍힘"으로 혼재를 증명했다. 쿼리는 부록 B-5.

---

## 4. 무엇을 만들었나 — (2) DB 접속 TLS 강제

### 4-1. 조치

세 저장소 모두 **`sslMode` 를 `REQUIRED` 로, 그리고 URL(SSM)이 아니라 코드에** 둔다.

| 저장소 | 위치 | 값 |
|---|---|---|
| `skix-security` | `application-ecs.yml` → `spring.datasource.hikari.data-source-properties.sslMode` | `${DB_SSL_MODE:REQUIRED}` |
| `skix-openapi` | `application-ecs.yml` → `spring.datasource.hikari.data-source-properties.sslMode` | `${DB_SSL_MODE:REQUIRED}` |
| `skix-medcare` | `application-ecs.yml` → `spring.r2dbc.properties.sslMode` | `${DB_SSL_MODE:REQUIRED}` |

`REQUIRED` 는 TLS가 아니면 **연결 자체를 거부한다.** RDS는 TLS를 지원하므로 정상 경로는 그대로이고, 평문으로 떨어질 수 있던 경로만 닫힌다.

**r2dbc-mysql의 `sslMode` 파싱**: 드라이버가 문자열을 `toUpperCase` 후 enum 파싱하는 것을 확인했다. 대소문자는 문제가 안 된다.

**로컬 프로필은 반대로 명시적으로 껐다.** 로컬 docker MySQL에는 TLS가 없어 `REQUIRED` 를 켜면 기동이 막힌다. `skix-security`·`skix-openapi` 의 `application-localhost.yml` URL에 `useSSL=false` 를 남겨 두었고, 테스트가 이 명시를 고정한다 — **'설정을 빠뜨린 것'과 '끈 것'이 구분되어야** 하기 때문이다.

### 4-2. 우선순위 (실측으로 확인)

```
data-source-properties.sslMode  >  URL(SSM)의 useSSL
R2DBC customizer 옵션            >  URL(SSM)의 serverZoneId
```

URL에 `useSSL=true` 가 남아 있어도 `data-source-properties.sslMode=REQUIRED` 가 이긴다(TLS 없는 서버 상대로 연결 거부됨을 확인). 마찬가지로 SSM URL에 `serverZoneId=Asia/Seoul` 이 남아 있어도 `R2dbcUtcSessionConfig` 의 세션 강제가 이긴다(`serverZoneId` 는 드라이버의 **해석 힌트**일 뿐이고 `forceConnectionTimeZoneToSession` 은 **세션 자체를 SET** 한다 — 층이 달라 충돌하지 않는다).

**실무적 의미**: 배포와 SSM 정리를 분리할 수 있었다. 배포 즉시 강제가 걸리고, SSM의 낡은 파라미터는 나중에 걷어내면 된다. 시간대 때도 같은 구조였다.

### 4-3. 런타임 검증기를 두지 않은 이유

시간대에는 `DatabaseUtcSessionVerifier` 를 뒀는데 TLS에는 두지 않았다. **`sslMode=REQUIRED` 는 드라이버가 직접 연결을 거부하므로 드라이버 자신이 곧 강제 장치**이기 때문이다. 시간대는 아무도 막지 않아서 검증기가 필요했던 것과 대비된다.

다만 **설정이 사라지는 것은 아무 데서도 실패하지 않는다** — 그냥 평문으로 떨어질 수 있는 상태로 조용히 돌아갈 뿐이다. 그래서 `DatabaseTlsPolicyTest`(security·openapi)로 yml 내용을 직접 검사해 고정했고, 위반을 주입하면 실제로 실패하는 것까지 확인했다.

### 4-4. 서버 인증서 검증(`VERIFY_CA`/`VERIFY_IDENTITY`)을 하지 않은 이유

`REQUIRED` 는 "암호화는 하지만 서버가 진짜인지는 확인하지 않는" 단계다. 그 위로 올라가려면 **RDS CA 번들을 이미지에 동봉하고 트러스트스토어를 구성**해야 한다(`rds-ca-rsa2048-g1` 계열 번들 + 만료 시 이미지 재배포 절차). 이번 범위에 넣지 않았고 후속 과제로 남겼다(§7-2의 5번).

문제가 생기면 **재빌드 없이** 태스크 정의에 `DB_SSL_MODE=PREFERRED` 를 넣어 즉시 완화할 수 있다.

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **RDS 파라미터 그룹(`time_zone`)을 건드리지 않는다.** 앱 세션만 강제한다 | 세 서비스의 DB는 `skmg-benjamin-{env}-aurora` **공유 클러스터**에 스키마로 들어 있다. 파라미터 그룹을 UTC로 바꾸면 `benjamin` 스키마를 쓰는 다른 서비스(`backend-api-main`·`backend-scheduler-main`·`skix-streaming`)의 `NOW()`·`CURDATE()` 가 전부 함께 움직인다. 그쪽은 KST 기준으로 수년치 데이터가 쌓여 있어 조용히 전부 어긋난다. 운영자가 콘솔로 조회할 때 보는 값도 바뀐다 |
| 2 | **시간대·TLS 설정을 SSM URL이 아니라 코드(yml / Config 클래스)에 둔다** | 둘 다 환경별로 갈리는 값이 아니다. SSM에 두면 코드 리뷰에 잡히지 않고 **환경 하나만 빠뜨리는 사고**가 가능해진다. 게다가 코드 쪽이 URL을 이기므로(§4-2) SSM에 두면 "URL을 고쳤는데 안 먹는" 혼란만 남는다 |
| 3 | **세션 판정은 이름이 아니라 오프셋으로 한다** (`TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(3), CURRENT_TIMESTAMP(3)) = 0`) | `@@session.time_zone` 은 `'SYSTEM'`·`'+00:00'`·`'UTC'` 등 표기가 여러 가지다. `'SYSTEM'` 은 문자열 비교를 통과하면서 실제로는 서버 기본값(KST)을 가리킨다 — 검증기가 통과시키는 순간 방어가 전부 무력해진다 |
| 4 | **기동 검증기는 fail-fast다.** `require-utc-session` 을 끄지 않는다 | 끄면 세션 강제가 실패해도 조용히 뜬다. 9시간 틀린 값이 쌓이는 것보다 **배포 실패가 낫다**. 대가로 "DB 미도달이면 태스크가 뜨지 않는" 성질이 붙는다 — 이건 알고 감수한 것이다 |
| 5 | **저장은 UTC, 업무 경계는 KST.** 둘을 한쪽으로 통일하지 않는다 | 파기 컷오프를 UTC 자정으로 바꾸면 보유기간이 9시간 어긋나 실제로 일찍 지운다(이번에 고친 버그가 정확히 그거다). 반대로 사용량 창을 UTC 날짜로 쓰면 **매일 KST 00:00~09:00에 전날 창**이 잡힌다. 한국 사용자의 일·주·월 한도는 KST 자정에 돌아야 한다 |
| 6 | **`skix-medcare` PHR 두 테이블(`customer_if_phr`, `customer_if_phr_data`)의 기존 행은 보정하지 않는다** | `TIMESTAMP` 타입이라 **내부 순간이 이미 올바르다.** `DATETIME` 과 달리 세션 시간대로 해석·저장되므로, 세션이 바뀌면 표시만 달라지고 값은 그대로다. 여기에 -9시간을 걸면 **지금 맞는 값을 틀리게** 만든다. `DATETIME` 컬럼과 `TIMESTAMP` 컬럼을 같은 스크립트로 처리하지 말 것 |
| 7 | **`r2dbc-mysql` 을 1.2.0 미만으로 내리지 않는다** (현재 1.4.1) | `connectionTimeZone` / `forceConnectionTimeZoneToSession` 이 1.2.0부터다. 1.0.x·1.1.x에는 `serverZoneId`(드라이버 해석 힌트)뿐이라 **세션 자체를 바꿀 수 없다.** 버전을 내리면 기동이 실패하고(옵션 미지원), 어떤 경로로든 조용히 무시되더라도 `DatabaseUtcSessionVerifier` 가 잡는다. `build.gradle` 에 주석으로도 적혀 있다 |
| 8 | **Dockerfile의 `-Duser.timezone=UTC` 를 지우지 않는다. 그리고 검사는 `deploy/build-export.sh` 에 있다** | `skix-openapi` 는 Dockerfile이 **둘**이고 stg만 `Dockerfile_Datadog` 으로 빌드한다. `skix-medcare` 는 스테이지가 **둘**이다. 한쪽만 고정하면 **그 환경에서만** 시간대가 다른, 가장 찾기 어려운 형태로 어긋난다. 검사를 테스트가 아니라 빌드 스크립트에 둔 이유는 **Dockerfile이 빌드 컨텍스트로 COPY되지 않아** 이미지 안의 테스트에서는 읽을 수 없기 때문이다 |
| 9 | **`skix-openapi` 매퍼별 규약(§3-4)을 유지한다.** 일괄 치환하지 않는다 | `AuthMapper`·`WebhookMapper`·`UserPermissionMapper` 는 쓰기와 읽기가 **둘 다 DB측 `NOW()`** 라 짝이 맞는다. 한쪽만 `UTC_TIMESTAMP()` 로 바꾸면 그 순간 깨진다 — 유효한 토큰이 만료로 판정되고, 웹훅 파기 기준이 밀린다. `MapperTimeZonePolicyTest` 가 이 표를 지키며, **규약 선언 없이 새 매퍼가 들어오면 실패한다** |
| 10 | **표시 변환은 '순간 보존'이다.** `atOffset(KST)` 이 아니라 `atOffset(UTC).withOffsetSameInstant(KST)` | `atOffset(KST)` 은 숫자를 그대로 두고 라벨만 붙인다. **타입도 맞고 파싱도 되고 예외도 없다** — 값만 9시간 틀린다. 컴파일러도 일반 단위 테스트도 못 잡으므로 `KmetaTimeZoneTest` 가 `Instant` 비교로 고정한다. "오프셋 붙이는 코드가 두 줄이라 지저분하다"는 이유로 되돌리면 그대로 재발한다 |
| 11 | **기존 행 보정은 2단계(경계 → 배포 → 확인 → 이동)로 한다** | 한 번에 하면 ⑴ 배포 창 사이에 들어온 UTC 행까지 -9시간 옮기고 ⑵ 롤백 시 되돌릴 데이터가 생긴다. 시각 범위로 자르는 방식은 ⑴을 구조적으로 구분할 수 없다. 원장의 `applied_at IS NULL` 가드가 이중 실행(-18시간)을 막는다 |
| 12 | **로컬 프로필의 `useSSL=false` 는 지우지 않는다** | 로컬 docker MySQL에는 TLS가 없어 이걸 지우면 `REQUIRED` 로 올라가 로컬 기동이 막힌다. 더 중요한 건 **'빠뜨린 것'과 '끈 것'이 구분되어야** 한다는 점이다. `DatabaseTlsPolicyTest` 가 이 명시를 고정한다 |

---

## 6. 배포 방법

두 건 모두 이미 3환경 배포가 끝났다. 아래는 **신규 환경 구축·재적용·유사 작업**에 쓸 절차다.

### 6-1. 순서

**저장소 간 순서 의존은 없다.** 각 서비스가 자기 스키마만 만지고 서로의 계약을 바꾸지 않는다. 저장소 하나 안에서의 순서는 이렇다.

```
[TLS 강제만 하는 경우 — DDL도 데이터 보정도 없다]
  1. 이미지 빌드 → ECR push → 태스크 정의 리비전 등록 → 서비스 업데이트
  2. 기동 성공 = 검증 완료 (REQUIRED 는 TLS 실패 시 연결을 거부하므로)

[UTC 통일]
  1. PART 1 — 보정 대상 테이블의 경계를 원장에 기록 (배포 직전, 부록 C)
  2. ./deploy/build-export.sh        ← Dockerfile TZ 가드가 여기서 돈다
       (skix-openapi stg 는 ./deploy/build-export.sh dd — Datadog 이미지)
  3. ECR push → 태스크 정의 리비전 등록 → 서비스 업데이트
  4. 기동 로그에 'DB session UTC verified ... offsetSeconds=0' 확인
  5. 새 행이 UTC로 쌓이는지 데이터로 확인 (부록 B-3)
  6. PART 2 — 경계 이하 행만 -9시간 이동 (부록 C)
```

**함정**: `deploy/cloudshell-push.sh` 는 브랜치를 보지 않는다. 로컬 이미지 tar를 환경별 ECR로 밀 뿐이라 **배포와 브랜치 승격이 완전히 별개**다. 태스크 정의 리비전 등록을 빠뜨리면 이미지는 올라갔는데 서비스는 옛 리비전을 계속 쓴다.

### 6-2. 배포 후 검증

#### ① 기동 로그 (가장 중요, 이것부터 본다)

```
DB session UTC verified: sessionTimeZone=+00:00, offsetSeconds=0
```

이 줄이 없으면 둘 중 하나다 — 태스크가 뜨지 못했거나(세션이 UTC가 아님 → `IllegalStateException`), `require-utc-session` 이 꺼져 있다. **태스크가 `RUNNING` 이면 그 시점의 세션은 UTC였다**는 것이 검증기가 주는 보장이다.

#### ② 시각 — DB에서 직접 확인

```sql
-- 서버 시간대. 파라미터 그룹을 건드리지 않았으므로 Asia/Seoul 이어야 '정상'이다.
SELECT @@global.time_zone AS global_tz,
       @@system_time_zone AS system_tz,
       @@session.time_zone AS my_session_tz;
```

> **주의**: SQL 클라이언트로 직접 붙으면 `my_session_tz` 는 서버 기본값(KST)이 나온다. **이건 정상이다.** UTC로 강제되는 것은 애플리케이션 커넥션의 세션뿐이고, 이 쿼리로는 그걸 볼 수 없다. 애플리케이션 세션은 ①의 기동 로그로, 결과물은 아래 ③으로 확인한다.

#### ③ 실제로 UTC로 쓰고 있는지 (데이터로)

```sql
SELECT NOW()           AS db_now_kst,
       UTC_TIMESTAMP() AS db_now_utc,
       MAX(<시각컬럼>)  AS latest_row
  FROM <테이블>;
-- latest_row 가 db_now_utc 근처여야 한다. db_now_kst 근처면 아직 KST 로 쓰고 있다.
```

저장소별 확인용 테이블: `skix-security` → `mqtt_publish_log` 또는 `inbound_api_log`, `skix-openapi` → `open_api_inbound_log`(`called_at`), `skix-medcare` → `customer_if_log`(`customer_if_create_date`).

#### ④ TLS

```sql
SHOW STATUS LIKE 'Ssl_cipher';    -- 값이 비어 있으면 이 접속은 평문이다
SHOW STATUS LIKE 'Ssl_version';
```

> **주의**: 이것도 '내 접속'이지 애플리케이션 접속이 아니다. 애플리케이션 관점의 검증은 **"태스크가 정상 기동했다"** 가 전부다 — `REQUIRED` 는 TLS로 못 붙으면 연결 자체를 거부하므로, 기동에 성공했다면 TLS로 붙은 것이다.

서버 전체의 접속을 보고 싶으면 부록 B-7을 쓴다.

#### ⑤ 회귀 확인 (UTC 전환 배포에서만)

| 저장소 | 확인 |
|---|---|
| `skix-openapi` | 포털 "오늘 사용량"이 **KST 09시 이전에도** 값이 나오는지. 이게 전환 전 대표 증상이었다 |
| `skix-openapi` | 토큰 발급 → 즉시 API 호출 → `open_api_tokens.created_at` 과 `open_api_inbound_log.called_at` 이 **초 단위로 붙어 있는지**. 9시간 벌어지면 세션 강제가 안 먹은 것이다 |
| `skix-medcare` | 관제 사용량 목록의 시각이 **종전과 같은 KST 벽시계**로 보이는지(`toKst` 변환이 살아 있는지) |
| `skix-medcare` | 일·주·월 사용량 한도가 **KST 자정에** 리셋되는지 |
| `skix-security` | 청구 이력 조회가 정상인지(`OasysService` 의 KST 기준일) |

### 6-3. 롤백

| 대상 | 방법 | 주의 |
|---|---|---|
| **TLS 강제** | 태스크 정의에 환경변수 `DB_SSL_MODE=PREFERRED` 추가 → 리비전 등록 → 서비스 업데이트. **재빌드 불필요** | 세 저장소 모두 같은 변수명이다. 완화 상태로 방치하지 말 것 |
| **UTC 통일 — PART 2 실행 전** | 이전 이미지로 되돌린다 | 데이터를 건드리기 전이라 **되돌릴 것이 없다.** 원장의 경계 행만 남는데 무해하다 |
| **UTC 통일 — PART 2 실행 후** | 이전 이미지로 되돌리면 **과거 행은 UTC, 신규 행은 KST**가 되어 다시 어긋난다. 되돌리려면 PART 2의 반대 보정(+9시간)을 같은 경계로 실행해야 한다 | **그래서 PART 2는 반드시 §6-2의 ①②③ 확인 후에 실행한다.** 이 순서가 절차의 핵심이다 |
| **세션 강제만 끄기** | 불가능하다. 강제는 코드(yml literal / Config 클래스)에 있고 환경변수 스위치가 없다 | `require-utc-session` 도 리터럴 `true` 라 태스크 정의로 끌 수 없다. 의도된 설계다(§5의 4번) |

---

## 7. 남은 일

### 7-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **`skix-medcare` kmeta 표시 정정(`621a5dd`)이 세 환경에 배포됐는지 확인.** 브랜치에는 반영돼 있으나 배포는 미확인 | 백엔드 | 확인법 두 가지. ⑴ VDI에서 ECS 태스크 정의의 이미지 태그 확인. ⑵ 데이터로: `kmeta_streams.last_used_time`(UTC 저장)과 같은 대화의 목록 API 응답 시각을 비교해, 응답이 **같은 순간의 KST**(저장값 +9h)면 배포됨, **저장값과 같은 숫자에 `+09:00` 라벨만** 붙어 있으면 미배포. 배포 시 기존 대화 시각이 9시간 뒤로 이동해 보이므로 iOS·웹 담당에게 사전 공유 필요 |
| 2 | **세 서비스 × 세 환경의 기동 로그에서 `DB session UTC verified` 확인** | 백엔드 | 없으면 §6-2의 ①로 원인 판별 |
| 3 | **SSM `DB_URL`·`SPRING_R2DBC_URL` 의 낡은 파라미터 잔재 확인** — `serverTimezone`·`serverZoneId`·`useSSL` | 백엔드 | 작업 당시 정리 완료로 기록했으나 로컬에서 확인 불가. **남아 있어도 동작에는 영향 없다**(§4-2). 정리는 혼란 방지 목적 |

### 7-2. 후속 (이 작업을 막지는 않음)

| # | 항목 | 우선순위 근거 |
|---|---|---|
| 4 | **`skix-security` `deploy/build-export.sh` 에 Dockerfile TZ 가드가 없다.** openapi·medcare에는 있다 | 비대칭. security는 Dockerfile이 1개라 위험도가 낮지만, `-Duser.timezone=UTC` 가 삭제돼도 아무 데서도 실패하지 않는 것은 똑같다. `grep -q -- '-Duser.timezone=UTC' Dockerfile` 한 줄이면 된다 |
| 5 | **서버 인증서 검증(`VERIFY_CA` / `VERIFY_IDENTITY`) 도입** | RDS CA 번들을 이미지에 동봉하고 트러스트스토어를 구성하는 작업이 선행돼야 한다. 번들 만료 시 재배포 절차도 함께 정해야 한다. 현재 `REQUIRED` 는 암호화는 하지만 서버 신원은 확인하지 않는다 |
| 6 | **내부 NLB 구간 TLS** (medcare 보안성검토 T10) | **미착수.** DB 구간은 닫았지만 서비스 간 내부 통신 구간은 별개 과제다 |
| 7 | **`benjamin` 스키마 3개 서비스의 DB TLS 강제** (`backend-api-main`·`backend-scheduler-main`·`skix-streaming`) | 현재 셋 다 `sslMode` 미설정 → `PREFERRED` → **평문 폴백 가능**(§2-3 실측). 코드 한 줄이고 프로필별 yml이라 저장소당 3~4곳이다. UTC 전환과 달리 데이터 보정이 없어 **난이도 대비 효과가 가장 크다** |
| 8 | **`benjamin` 스키마의 UTC 전환** | 애플리케이션 4개(Lambda 포함)를 동시에 바꾸고 수년치 이력을 보정해야 한다. 큰 작업이며 당장 필요하지는 않다. 착수한다면 이 문서의 절차(§3-2·§3-8)를 그대로 쓸 수 있다 |
| 9 | **Athena ↔ `benjamin` MySQL 조인의 9시간 보정을 조사 쿼리 템플릿으로 고정** | 보정을 빠뜨려도 결과가 그럴듯하게 나와서 검토로는 안 잡힌다. 실제 조사에서 반복될 함정이다 |
| 10 | 파트너 API 명령 상태 시각 정정(`backend-api-main 41af4e53`, `skix-openapi c20c33a`) — **dev·stg 반영, main 미반영** | 이 문서 범위 밖(파트너 API 작업)이지만 같은 뿌리의 문제다. 운영 릴리스 때 함께 올라간다 |
| 11 | (조치 불필요, 오해 방지용) `skix-medcare` `JacksonConfig` 의 `FALLBACK_OFFSET(+9)` | 오프셋 없는 시각 문자열이 들어왔을 때의 해석 기본값이다. **의도적으로 KST로 남겼다** — 서비스 사용자가 한국에 있기 때문이지 저장 규약 때문이 아니며, 코드 주석에도 "두 결정은 별개"라고 적혀 있다. 현재 입력은 오프셋이 붙어 와서(08-18 운영계 실측 `...Z`) 이 폴백을 거의 타지 않는다. "저장이 UTC니까 폴백도 UTC로" 식으로 바꾸지 말 것 |
| 12 | `skix-openapi` 사용량의 "하루"가 UTC 일자다(KST 09:00에 날짜가 바뀜) | 전환 전의 "KST 00~09시 0으로 보임" 버그는 해소됐지만, 일 경계 자체는 UTC다. 파트너 계약상 하루를 KST로 정의해야 한다면 집계 배치·조회·과금 한도를 함께 바꿔야 하는 별도 작업이다 |

---

## 8. 운영 중 자주 만날 상황

| 상황 | 확인·대응 |
|---|---|
| **"시각이 9시간 틀려요"** | 먼저 **어느 DB인지** 확인한다. `security`/`openapi`/`medcare` 스키마는 UTC 저장이고, `benjamin` 스키마는 KST 저장이다(§2-3). 두 스키마의 값을 나란히 놓고 비교하는 순간 9시간이 나온다 — 대부분 이 경우다 |
| **"9시간이 아니라 애매하게 틀려요"** | 시간대 문제가 아니다. 서머타임이 없는 KST에서 9시간이 아닌 차이는 시간대로 설명되지 않는다 |
| **배포 후 태스크가 안 뜬다 / 재시작 루프** | 로그에서 `DB UTC session verification failed` 또는 `DB session must be UTC` 를 찾는다. 세션 강제가 안 먹은 것이다(드라이버 버전 하향, yml에서 `connection-init-sql` 제거, `R2dbcUtcSessionConfig` 제거 중 하나). **DB에 아예 못 닿아도 같은 증상**이다 — 예외 원인(cause)이 연결 실패인지 오프셋 불일치인지로 구분한다 |
| **배포 후 태스크가 안 뜨는데 TLS가 의심될 때** | 태스크 정의에 `DB_SSL_MODE=PREFERRED` 를 임시로 넣어 리비전을 올려 본다. 그걸로 뜨면 TLS 협상 문제다. **완화 상태로 방치하지 말고** 원인(서버 TLS 설정·인증서)을 잡은 뒤 되돌린다 |
| **포털 "오늘 사용량"이 0** | 전환 전 대표 증상이었다(KST 00:00~09:00). 지금도 재현되면 `UsageMapper.xml` 에 `CURDATE()` 가 다시 들어간 것이다. `MapperTimeZonePolicyTest` 가 막고 있으므로 테스트를 껐거나 우회한 경우다 |
| **파기가 예상보다 일찍 돈다** | 파기 컷오프를 UTC 자정으로 바꾼 것이 아닌지 본다. 컷오프는 KST 자정을 계산한 뒤 `fromKst` 로 옮겨야 한다(§3-6) |
| **새 테이블을 만들 때** | 시각 컬럼 코멘트에 `'UTC - '` 접두를 붙인다(§3-5). 값만 봐서는 벽시계의 기준을 알 수 없다 |
| **새 매퍼를 만들 때 (`skix-openapi`)** | `MapperTimeZonePolicyTest` 의 세 목록 중 하나에 등록해야 한다. 등록 없이 추가하면 테스트가 실패한다. 애플리케이션이 넣은 값을 읽으면 `UTC_*` 계열, DB가 쓰고 DB가 읽으면 `NOW()` 유지 |
| **조사 쿼리에서 Athena와 MySQL을 조인할 때** | Athena `iot_messages_raw` 의 `payload.timestamp` 는 UTC, `benjamin` MySQL 이력은 KST다. **9시간 보정 필수.** 참고로 같은 MQTT 메시지 안에서도 최상위 `timestamp` 는 UTC이고 error 토픽의 `data.errorDate` 는 기기 로컬(KST)이다 |
| **어떤 테이블의 시간대를 모르겠을 때** | 기준이 확정된 활발한 테이블을 기준자로 삼아 같은 사건을 조인하고 평균 차를 본다(부록 B-5). 0분이면 같은 기준, ±540분이면 9시간 어긋난 것이다 |
| **로그 키워드** | 기동 검증 성공/실패 모두 `DB session UTC` 로 잡힌다 |

---

## 부록 A. 핵심 커밋 (시간순)

| 날짜 | 저장소 | 커밋 | 내용 | dev/stg/main |
|---|---|---|---|---|
| 08-07 | skix-medcare | `ee8780b` | R2DBC `sslMode=REQUIRED` — 세 저장소 중 최초. 이후 둘이 이 패턴을 따랐다 | 전부 반영 |
| 08-13 | skix-security | `242196e` | UTC 통일 1호. 세션 강제 + 기동 검증기 + `UtcDateTimes` + DDL 주석 규약 + 청구 기준일만 KST 유지. 27파일 | 전부 반영 |
| 08-14 | skix-openapi | `014ef2c` | 매퍼 기준 정정 — `UsageMapper` `CURDATE()`×7 → `UTC_DATE()`, `AuditLogMapper` `NOW()`×2 → `UTC_TIMESTAMP()`. `MapperTimeZonePolicyTest` 신설 | 전부 반영 |
| 08-14 | skix-openapi | `f1adacb` | 세션 UTC 강제 + 기동 검증 + Dockerfile 2종 + DDL 주석 | 전부 반영 |
| 08-14 | skix-medcare | `deb2c33` | UTC 통일(R2DBC). 드라이버 1.0.5→1.4.1, `R2dbcUtcSessionConfig`, 기동 검증기, `UtcDateTimes`, 업무 경계 KST 분리, 표시 순간 보존, `build-export.sh` 가드. 33파일 | 전부 반영 |
| 08-18 | skix-openapi | `dcb1a5a` | `sslMode=REQUIRED` + `DatabaseTlsPolicyTest` | 전부 반영 |
| 08-18 | skix-security | `d7e953c` | `sslMode=REQUIRED` + `DatabaseTlsPolicyTest` | 전부 반영 |
| 08-18 | skix-medcare | `621a5dd` | kmeta 대화 시각 표시 정정 — 라벨 교체 → 순간 보존. `KmetaTimeZoneTest` | 전부 반영 |

세 저장소 모두 2026-09-22 기준 작업 트리가 깨끗하고, 위 8건은 `origin/dev`·`origin/stg`·`origin/main` 전부의 조상이다.

---

## 부록 B. 검증 SQL 모음

각 쿼리의 `<...>` 는 대상 테이블·컬럼으로 바꿔 쓴다.

```sql
-- ─────────────────────────────────────────────────────────────
-- B-1. 서버·세션 시간대
-- ─────────────────────────────────────────────────────────────
SELECT @@global.time_zone  AS global_tz,     -- 기대: Asia/Seoul (파라미터 그룹 미변경이 정상)
       @@system_time_zone  AS system_tz,     -- 기대: UTC (OS 시간대)
       @@session.time_zone AS my_session_tz; -- SQL 클라이언트로 붙으면 서버 기본값(KST)이 정상
--
-- 이 쿼리로는 '애플리케이션 커넥션의 세션'을 볼 수 없다.
-- 애플리케이션 세션은 기동 로그로 확인한다:
--   DB session UTC verified: sessionTimeZone=+00:00, offsetSeconds=0


-- ─────────────────────────────────────────────────────────────
-- B-2. 애플리케이션이 쓰는 검증 쿼리 (수동으로 흉내내 볼 때)
--      세션을 UTC 로 바꾼 뒤 판정이 통과하는지 확인한다.
-- ─────────────────────────────────────────────────────────────
SET SESSION time_zone = '+00:00';
SELECT @@session.time_zone AS session_time_zone,
       TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(3), CURRENT_TIMESTAMP(3)) AS utc_offset_seconds;
-- 기대: utc_offset_seconds = 0.
-- 판정은 이름이 아니라 이 오프셋으로 한다 — 'SYSTEM' 표기가 문자열 비교를 통과해 버린다.


-- ─────────────────────────────────────────────────────────────
-- B-3. 애플리케이션이 실제로 UTC 로 쓰고 있는지 (배포 후 핵심 확인)
-- ─────────────────────────────────────────────────────────────
SELECT NOW()             AS db_now_kst,
       UTC_TIMESTAMP()   AS db_now_utc,
       MAX(<시각컬럼>)    AS latest_row
  FROM <테이블>;
-- latest_row 가 db_now_utc 근처   → UTC 로 쓰고 있다 (정상)
-- latest_row 가 db_now_kst 근처   → 아직 KST 다 (세션 강제 미적용)
--
-- 저장소별 권장 테이블:
--   skix-security : mqtt_publish_log / inbound_api_log
--   skix-openapi  : open_api_inbound_log (called_at)
--   skix-medcare  : customer_if_log (customer_if_create_date)


-- ─────────────────────────────────────────────────────────────
-- B-4. 한 컬럼에 두 기준이 섞여 있는지 (전환 경계 찾기)
-- ─────────────────────────────────────────────────────────────
SELECT DATE(<시각컬럼>) AS d,
       MIN(<시각컬럼>)  AS first_row,
       MAX(<시각컬럼>)  AS last_row,
       COUNT(*)        AS cnt
  FROM <테이블>
 WHERE <시각컬럼> >= '<전환일> 00:00:00' - INTERVAL 3 DAY
 GROUP BY d
 ORDER BY d;
-- 전환 경계 근처에서 하루치가 9시간 겹치거나 9시간이 비어 보인다.


-- ─────────────────────────────────────────────────────────────
-- B-5. 조용한 테이블의 시간대를 활발한 테이블로 재기
--      (기준이 확정된 테이블을 '자'로 삼아 같은 사건의 차를 본다)
-- ─────────────────────────────────────────────────────────────
SELECT COUNT(*) AS n,
       AVG(TIMESTAMPDIFF(MINUTE, a.<확정_UTC_컬럼>, b.<미확정_컬럼>)) AS avg_diff_min,
       MIN(TIMESTAMPDIFF(MINUTE, a.<확정_UTC_컬럼>, b.<미확정_컬럼>)) AS min_diff_min,
       MAX(TIMESTAMPDIFF(MINUTE, a.<확정_UTC_컬럼>, b.<미확정_컬럼>)) AS max_diff_min
  FROM <기준_테이블> a
  JOIN <대상_테이블> b ON b.<조인키> = a.<조인키>;
-- avg_diff_min 이 0 근처   → 같은 기준
-- avg_diff_min 이 ±540    → 9시간 어긋남
-- 실제 사용 예: kmeta_streams.last_used_time 을 customer_if_log(UTC 확정)로 164건 대조해
--               평균 차 0분 → 저장값이 UTC임을 확정했다.


-- ─────────────────────────────────────────────────────────────
-- B-6. 지금 이 접속의 TLS 여부
-- ─────────────────────────────────────────────────────────────
SHOW STATUS LIKE 'Ssl_cipher';    -- 값이 빈 문자열이면 평문 접속이다
SHOW STATUS LIKE 'Ssl_version';
-- 또는
SELECT VARIABLE_NAME, VARIABLE_VALUE
  FROM performance_schema.session_status
 WHERE VARIABLE_NAME IN ('Ssl_cipher', 'Ssl_version');
--
-- 주의: 이건 '내 접속'이다. 애플리케이션 접속의 TLS 검증은
--       "REQUIRED 로 설정된 서비스가 정상 기동했다" 가 곧 증명이다.


-- ─────────────────────────────────────────────────────────────
-- B-7. 서버에 붙어 있는 모든 접속의 TLS 여부 (권한·뷰 필요)
--      환경에 따라 performance_schema 뷰가 없거나 권한이 없으면 건너뛴다.
-- ─────────────────────────────────────────────────────────────
SELECT t.PROCESSLIST_USER AS db_user,
       t.PROCESSLIST_HOST AS client_host,
       MAX(CASE WHEN s.VARIABLE_NAME = 'Ssl_cipher'  THEN s.VARIABLE_VALUE END) AS ssl_cipher,
       MAX(CASE WHEN s.VARIABLE_NAME = 'Ssl_version' THEN s.VARIABLE_VALUE END) AS ssl_version,
       COUNT(DISTINCT t.THREAD_ID) AS conns
  FROM performance_schema.threads t
  JOIN performance_schema.status_by_thread s ON s.THREAD_ID = t.THREAD_ID
 WHERE t.PROCESSLIST_ID IS NOT NULL
   AND s.VARIABLE_NAME IN ('Ssl_cipher', 'Ssl_version')
 GROUP BY t.PROCESSLIST_USER, t.PROCESSLIST_HOST, t.THREAD_ID;
-- ssl_cipher 가 빈 문자열인 행이 평문 접속이다.
```

### B-8. `useSSL=true` 가 강제가 아님을 로컬에서 재현하는 법

이 문서의 근거가 된 실측 절차다. 의심이 들 때 다시 돌려 볼 수 있다.

```bash
# 1) TLS 를 끈 MySQL 을 띄운다
docker run --rm --name mysql-nossl \
  -e MYSQL_ROOT_PASSWORD='<로컬전용-임의값>' \
  -e MYSQL_DATABASE=tlstest \
  -p 13306:3306 \
  mysql:8.0 --ssl=0

# 2) 파라미터를 바꿔 가며 붙어 본다. 각 접속에서 SHOW STATUS LIKE 'Ssl_cipher';
#
#    jdbc:mysql://localhost:13306/tlstest                              → 연결됨, Ssl_cipher 빈 값 (평문)
#    jdbc:mysql://localhost:13306/tlstest?useSSL=true                  → 연결됨, Ssl_cipher 빈 값 (평문!)
#    jdbc:mysql://localhost:13306/tlstest?sslMode=PREFERRED            → 연결됨, Ssl_cipher 빈 값 (평문)
#    jdbc:mysql://localhost:13306/tlstest?useSSL=true&requireSSL=true  → 연결 거부
#    jdbc:mysql://localhost:13306/tlstest?sslMode=REQUIRED             → 연결 거부
#
# 3) 우선순위 확인:
#    URL 에 useSSL=true + hikari.data-source-properties.sslMode=REQUIRED → 연결 거부
#    (= 코드 쪽 설정이 URL 을 이긴다)
```

---

## 부록 C. 기존 행 보정 스크립트 (2단계 경계 스냅샷) — 재구성 템플릿

실제 사용한 스크립트는 `deploy/migrations/`(gitignore)에 두고 실행 후 삭제했다. 아래는 **같은 절차를 다시 밟을 때 쓰라고 재구성한 템플릿**이다. 컬럼명·테이블명은 대상 스키마에 맞게 확인하고 쓴다.

> **적용 전 반드시 확인할 것**
> - 대상 컬럼이 **`DATETIME` 인지 `TIMESTAMP` 인지.** `TIMESTAMP` 컬럼은 **보정 금지**다 — 세션 시간대로 해석되므로 내부 순간이 이미 올바르다(§5의 6번).
> - 대상 값이 **DB가 만든 것인지**(`DEFAULT CURRENT_TIMESTAMP`, 매퍼의 `NOW()`) **애플리케이션이 넣은 것인지.** 애플리케이션이 넣은 값은 이미 UTC라 보정 대상이 아니다.
> - 수명이 짧은 데이터(TTL 토큰 등)는 **보정하지 말고 자연 소멸**시킨다.

### C-1. 원장 테이블 (한 번만 만든다)

```sql
-- 저장소별 이름: open_api_schema_migration / medcare_schema_migration
CREATE TABLE IF NOT EXISTS `<스키마>`.`<서비스>_schema_migration` (
    `migration_id` varchar(64)  NOT NULL COMMENT '마이그레이션 식별자 (예: 2026-08-14-utc-shift)',
    `table_name`   varchar(64)  NOT NULL COMMENT '대상 테이블',
    `boundary_key` varchar(64)  NOT NULL COMMENT 'PART1 시점의 경계 — id 또는 시각 문자열',
    `snapshot_at`  datetime(3)  NOT NULL COMMENT 'UTC - PART1 실행 시각',
    `applied_at`   datetime(3)  NULL     COMMENT 'UTC - PART2 실행 시각. NULL 이면 미적용',
    `rows_shifted` bigint       NULL     COMMENT 'PART2 가 옮긴 행 수',
    PRIMARY KEY (`migration_id`, `table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UTC 전환 보정 원장 — 재실행 가드';
```

### C-2. PART 1 — 경계 찍기 (**배포 직전**에 실행)

```sql
-- 패턴 A: auto_increment PK 가 있는 테이블 (권장)
INSERT INTO `<서비스>_schema_migration`
       (migration_id, table_name, boundary_key, snapshot_at)
SELECT '2026-08-14-utc-shift', '<테이블>',
       CAST(COALESCE(MAX(id), 0) AS CHAR), UTC_TIMESTAMP(3)
  FROM `<테이블>`
ON DUPLICATE KEY UPDATE boundary_key = boundary_key;   -- 재실행해도 경계가 밀리지 않는다

-- 패턴 B: PK 가 없거나 순증하지 않는 테이블 (common_code 등)
INSERT INTO `<서비스>_schema_migration`
       (migration_id, table_name, boundary_key, snapshot_at)
SELECT '2026-08-14-utc-shift', '<테이블>',
       DATE_FORMAT(COALESCE(MAX(<시각컬럼>), '1970-01-01'), '%Y-%m-%d %H:%i:%s.%f'),
       UTC_TIMESTAMP(3)
  FROM `<테이블>`
ON DUPLICATE KEY UPDATE boundary_key = boundary_key;

-- 확인
SELECT * FROM `<서비스>_schema_migration` WHERE migration_id = '2026-08-14-utc-shift';
```

### C-3. 배포 → 확인

§6-2의 ①②③을 전부 통과할 때까지 **PART 2로 넘어가지 않는다.** 여기서 멈추면 데이터는 손대지 않은 상태라 롤백 비용이 0이다.

### C-4. PART 2 — 경계 이하 행만 -9시간 (확인 후에만 실행)

```sql
START TRANSACTION;

-- 패턴 A: id 경계
UPDATE `<테이블>` t
  JOIN `<서비스>_schema_migration` m
    ON m.migration_id = '2026-08-14-utc-shift'
   AND m.table_name   = '<테이블>'
   AND m.applied_at IS NULL                       -- ← 이중 실행(-18h) 방지 가드
   SET t.<시각컬럼> = t.<시각컬럼> - INTERVAL 9 HOUR
 WHERE t.id <= CAST(m.boundary_key AS UNSIGNED)
   AND t.<시각컬럼> IS NOT NULL;

SET @shifted = ROW_COUNT();                       -- ← 다음 문장 전에 반드시 잡아 둔다

UPDATE `<서비스>_schema_migration`
   SET applied_at = UTC_TIMESTAMP(3), rows_shifted = @shifted
 WHERE migration_id = '2026-08-14-utc-shift'
   AND table_name   = '<테이블>'
   AND applied_at IS NULL;

SELECT @shifted AS rows_shifted;

-- 눈으로 확인한 뒤 COMMIT. 건수가 예상과 다르면 ROLLBACK.
COMMIT;
```

패턴 B(시각 경계)는 `WHERE` 만 바꾼다.

```sql
 WHERE t.<시각컬럼> <= CAST(m.boundary_key AS DATETIME(3))
```

**시각 컬럼이 여러 개인 테이블은 같은 `UPDATE` 문에서 함께 옮긴다.** 문장을 나누면 한쪽만 적용된 중간 상태가 생긴다.

```sql
   SET t.created_at = t.created_at - INTERVAL 9 HOUR,
       t.updated_at = CASE WHEN t.updated_at IS NULL THEN NULL
                           ELSE t.updated_at - INTERVAL 9 HOUR END
```

### C-5. 되돌리기 (PART 2를 잘못 돌렸을 때)

```sql
UPDATE `<테이블>` t
  JOIN `<서비스>_schema_migration` m
    ON m.migration_id = '2026-08-14-utc-shift'
   AND m.table_name   = '<테이블>'
   AND m.applied_at IS NOT NULL
   SET t.<시각컬럼> = t.<시각컬럼> + INTERVAL 9 HOUR
 WHERE t.id <= CAST(m.boundary_key AS UNSIGNED)
   AND t.<시각컬럼> IS NOT NULL;

UPDATE `<서비스>_schema_migration`
   SET applied_at = NULL, rows_shifted = NULL
 WHERE migration_id = '2026-08-14-utc-shift' AND table_name = '<테이블>';
```

같은 경계를 쓰므로 정확히 원복된다. **경계 행을 지우고 나면 되돌릴 수 없으니 원장은 남겨 둔다.**
