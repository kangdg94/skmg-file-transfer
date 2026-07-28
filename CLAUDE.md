# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

벤자민 통합관제 시스템(air-bot-api) — IoT 기기(공기청정 로봇 등) 데이터 기반 모니터링·제어·통계를 제공하는 Spring Boot 백엔드 API.

- Java 17, Spring Boot 3.2.4, Gradle
- MySQL 8.0 (MyBatis), Redis (세션/토큰/캐시)
- AWS: IoT Core(MQTT 제어/FOTA), S3(file/map/recommend/ai 버킷), Cognito, CloudFront(서명 URL), KMS
- Firebase FCM 푸시, OpenCV(네이티브 라이브러리, 앱 시작 시 로드)

## 주요 명령어

```shell
# 로컬 인프라 기동 (MySQL 3306 - db: air_bot, Redis 6379)
docker-compose up -d

# 빌드 (profile: local | stag | vdi | prod, 기본값 local)
./gradlew clean bootjar -Pprofile=local -Pversion={VERSION_NUMBER}

# 로컬 실행
./gradlew bootRun -Pprofile=local

# 전체 테스트 / 단일 테스트
./gradlew test
./gradlew test --tests "com.skmagic.JwtTest"
```

- `-Pprofile`은 `src/main/resources-{profile}/` 디렉터리(application.yml, banner.txt)를 리소스에 추가하는 방식으로 환경을 분리한다. 공용 리소스(mapper XML, 키파일, 폰트 등)는 `src/main/resources/`에 있다.
- `spring-boot-devtools`는 OpenCV와 충돌하여 주석 처리되어 있음 — 다시 활성화하지 말 것.
- `libs/`의 로컬 jar(java-otp, NiceID, idw 등)에 의존하므로 삭제 금지.

## 아키텍처

### 레이어 구조 (도메인별 패키지)

`com.skmagic.api.{도메인}` 아래에 controller / service / mapper / model 4계층:

| 폴더 | 역할 | 예시 |
|---|---|---|
| `api/**/controller` | 웹 요청/응답 처리 | `BuildingController.java` |
| `api/**/service` | 비즈니스 로직 | `BuildingService.java` |
| `api/**/mapper` | MyBatis 매퍼 인터페이스 (`@Mapper`) | `BuildingMapper.java` |
| `api/**/model` | DTO/도메인 객체 | `Building.java` |
| `core/**` | Spring 설정, 보안, 공통 유틸 | `DataSourceConfiguration.java` |

- SQL은 `src/main/resources/mapper/{도메인}/*.xml`에 작성하며 namespace는 매퍼 인터페이스 FQCN과 일치시킨다.
- MyBatis 설정: `map-underscore-to-camel-case: true` (DB snake_case ↔ Java camelCase 자동 매핑). `RefreshableSqlSessionFactoryBean`으로 매퍼 XML 핫 리로드 지원.
- 의존성 주입은 필드 `@Autowired` 스타일이 지배적이다. Lombok(`@Slf4j`, `@Getter` 등) 사용.

### 응답 규약

컨트롤러는 `ResponseEntity.ok(ResponseUtil.getResponseInfo(new ReponseResult(), data))` 형태로 응답한다.
- 공통 응답 VO는 `ReponseResult`(오타지만 기존 명칭 유지 — Response 아님).
- 목록 + 페이징은 `PageUtil.getPageInfo(요청모델, list)`로 감싼다.

### DataSource read/write 분리

- 비-local 프로파일: `DataSourceConfiguration` + `RoutingDataSource`가 `@Transactional(readOnly = true)` 여부로 read/write DB를 라우팅한다 (`LazyConnectionDataSourceProxy` 경유).
- **조회 전용 서비스 메서드에는 `@Transactional(readOnly = true)`를 붙여야 read 레플리카로 라우팅된다.** 기본값은 write.
- local 프로파일은 `DataSourceLocalConfiguration`(단일 DB)을 사용.

### 인증/인가

- Stateless JWT 방식. `TokenAuthenticationFilter`(OncePerRequestFilter)가 토큰 검증, Redis로 중복 로그인/토큰 상태 관리.
- 인증 제외 URL은 `SystemConstants.getExcludeUrls()`에서 관리.
- 쓰기 권한 체크: 컨트롤러 메서드에 `@CheckWritePermission` 어노테이션 → `MenuAuthChecker`가 DB 메뉴 권한(URL 패턴 + HTTP 메서드)으로 판단.

### 필드 암호화

- 모델 필드에 `@Encrypt`를 붙이면 `EncryptAspect`(AOP)가 모든 `api..mapper..*Mapper` 메서드 호출 전후로 자동 암/복호화한다(Jasypt). 개인정보 컬럼 추가 시 이 어노테이션을 활용할 것.
- 개인정보 응답 시 `StringUtil.maskId()`, `StringUtil.phoneMasking()` 등으로 마스킹하는 패턴을 따른다.

### IoT / 외부 연동

- AWS IoT Core MQTT 발행/구독: `IoTConfiguration`, `MqttMessageDispatcher` (기기 제어, FOTA).
- WebSocket: `api/websocket/` — AWS API Gateway WebSocket 연동.
- FCM 푸시: `FCMConfig` + `src/main/resources/firebase/` 키.

## 개발 규칙

- 커밋 메시지는 한국어로, `[feat]`, `[fix]`, `[refactor]`, `[perf]` 접두사 또는 JIRA 키(`[AXDO-xxxx]`, `[SQEA1DEF1-xxxx]`)를 사용한다.
- 브랜치: `feature/{이슈키 또는 기능명}` → `develop`(또는 `dev`)으로 머지.
- 테스트는 `src/test/java/com/skmagic/`에 위치 (JUnit 5, `useJUnitPlatform`).
- API 수동 테스트용 Bruno 컬렉션이 `api/벤자민/`에 있다. 엔드포인트 추가/변경 시 참고.
- 프로파일별 `application.yml`에 환경 자격증명이 포함되어 있으므로 값을 코드나 문서로 복사하지 말 것.

---

# 로직 추적 가이드 (개인용 — 이 저장소의 주 사용 목적)

이 세션의 주 용도는 **코드 작성이 아니라 로직 추적**이다: "X 진입점으로 요청이 오면 어떤 데이터가 어떻게 처리되고 무엇이 나가는가". 아래 지도와 규칙을 항상 따를 것.

## 진입점 지도 (4종 — 스케줄러/배치 없음)

### 1. REST API (컨트롤러 99개)
- `server.servlet.context-path: /api` → 실제 URL = `/api` + `@RequestMapping` 경로
- 흐름: `{도메인}Controller` → `{도메인}Service` → `{도메인}Mapper`(인터페이스) → `src/main/resources/mapper/{도메인}/*.xml` (namespace = 매퍼 FQCN)
- URL로 컨트롤러 찾기: `rg -n "경로조각" --iglob '*Controller.java'` (클래스 @RequestMapping + 메서드 @{Get,Post,Put,Delete}Mapping 조합 주의)

### 2. MQTT (AWS IoT Core) — ★리플렉션 매핑. grep으로 핸들러가 안 나옴★
- 구독: `IoTConfiguration.java` — `$share/{group}/{type}/airbot/+/+/{토픽끝}` 공유구독, QOS 1
- 라우팅: `IotInboundMessageHandler.resolveHandlerName()` — **토픽 마지막 세그먼트 == ResponseDeviceService의 메서드 이름** (리플렉션 호출)
  - 화이트리스트: `IotInboundMessageHandler.VALID_TOPIC_ENDS` (여기 없으면 무시됨)
  - 특례: 토픽 끝 `IN_PROGRESS|succeeded|failed|rejected` → `jobUpdate` 핸들러
- 실행: `MqttMessageDispatcher` 스레드풀(core8/max16/큐500, CallerRunsPolicy)로 **비동기, 예외는 로그만 남기고 스왈로잉** (기기는 응답 못 받고 타임아웃)
- 수신 핸들러: `ResponseDeviceService.java` (4,653줄) / 응답·명령 발행: `RequestDeviceService.java` (3,329줄, 37개 메서드) → 발행 토픽 상수는 `Topic.java`
- payload는 대부분 `HistoryControl`로 역직렬화, `correlationId`로 요청-응답 상관관계 추적

### 3. SQS (같은 IoT 이벤트의 큐 경로)
- `SqsIotEventListener` `@SqsListener("${aws.sqs.iot-event-queue}")` → `IotInboundMessageHandler.handleFromQueue()`
- MQTT와 **같은 리플렉션 매핑**이지만 **동기 + 예외 전파** → SQS 재시도/DLQ 동작. 실패 처리 방식이 MQTT 경로와 다름을 항상 구분해서 답할 것

### 4. WebSocket (AWS API Gateway HTTP integration)
- `WebSocketGatewayController` — `$connect`/`$disconnect`/`message` 라우트가 HTTP로 `/api/connect`, `/api/disconnect`, `/api/message` 호출. 2xx 반환해야 성공

### 아웃바운드 (진입점 아님, 흐름의 끝)
- MQTT 발행: `RequestDeviceService` → `Topic.java` 상수
- FCM 푸시: `FCMService`/`FCMSendService` (호출처: BuildingMemberService, VitalSign, 기기 제어 등)
- S3 presigned URL, CloudFront 서명 URL

## 횡단 관심사 (추적 시 반드시 짚을 것)
| 항목 | 위치 | 추적 시 확인 |
|---|---|---|
| JWT 인증 | `TokenAuthenticationFilter` | 해당 URL이 인증 필요한지 |
| 인증 제외 URL | `SystemConstants.getExcludeUrls()` | 제외 목록 포함 여부 |
| 쓰기 권한 | `@CheckWritePermission` → `MenuAuthChecker` | DB 메뉴 권한(URL패턴+HTTP메서드) |
| 필드 암복호화 | `@Encrypt` → `EncryptAspect` | `api..mapper..*Mapper` 호출 전후 자동 암/복호화 — DB값과 자바값이 다름 |
| 마스킹 | `StringUtil.maskId()/phoneMasking()` | 응답에 개인정보 마스킹 여부 |
| DB 라우팅 | `@Transactional(readOnly=true)` | read 레플리카 vs write DB (비-local만) |
| 응답 봉투 | `ReponseResult`(오타 아님) + `ResponseUtil.getResponseInfo()` / 페이징 `PageUtil.getPageInfo()` | 실제 응답 JSON 구조 |

## 토큰 효율 규칙 (거대 파일 대응)
- `ResponseDeviceService`(4,653줄), `RequestDeviceService`(3,329줄)는 **절대 전체 읽기 금지** — `rg -n`으로 위치 찾고 `sed -n 'START,ENDp'`로 필요한 범위만 발췌
- 다른 파일도 원칙은 grep-first: 위치 특정 → 해당 범위만 Read
- 탐색 범위가 넓거나 위치가 불명확하면 Explore 서브에이전트에 위임하고 결론만 회수
- 하나의 추적이 끝나면 다음 주제 전에 `/compact` 또는 `/clear` 권장 (사용자에게 안내)

## 로직 추적 답변 형식 (질문받으면 이 형식으로)
1. **한 줄 요약** — 이 진입점이 무엇을 하는지
2. **흐름 표** — 단계 | `파일:줄번호`(클릭 이동용) | 하는 일
3. **Input** — 실제 JSON/파라미터 예시 (필드 의미 포함)
4. **처리 로직** — 분기·계산·변환을 순서대로. SQL 관여 시 매퍼 XML id·테이블명·쿼리 요지 포함
5. **Output** — 응답 JSON 예시 / 발행 토픽 / DB 변경 / 푸시 등 부수효과 전부
6. **주의점** — 비동기 여부, 예외 처리 방식, 트랜잭션 경계, 만료시간, 인증/권한, 암호화 필드
7. 코드로 검증한 사실과 추측을 구분 표기. 확인 못 한 부분은 "미확인"이라고 명시
