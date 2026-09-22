# [작업] AfterCommit 중첩 등록 유실 수정 (커밋 후 콜백에서 미루기)

| 항목 | 내용 |
|---|---|
| **상태** | 코드 완료 · **개발계·검증계 브랜치 반영 완료(2026-09-08)** · 개발계 실기기 확인 완료 · **검증계 재검증 미완** · 운영계 미반영 |
| **작업 기간** | 2026-09-02 ~ 2026-09-08 (결함 발생 2026-09-04, 수정 2026-09-08) |
| **직접 수정한 저장소** | `backend-api-main` (단일) |
| **요청서를 전달한 대상** | 없음. 서버 내부 수정이며 앱·프론트·타 팀 작업 없음 |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §7-1의 1번 — 검증계에 배포된 형상인지 확인하고, 실기기 연동 해제 1건으로 `mqtt_publish_log` 재검증 |

---

## 0. 세 줄 요약

1. 검증계에서 **기기 연동 해제를 하면 DB 이력은 남는데 기기로 나가야 할 MQTT 토픽이 조용히 사라지는** 사고가 있었다. 원인은 "커밋 후에 실행" 유틸(`AfterCommit`)을 **이중으로 걸었을 때 안쪽 등록이 영영 실행되지 않는** Spring 트랜잭션 동기화의 성질이다. 예외도 로그도 남지 않는다.
2. 유틸 자체를 **중첩 안전**하게 고쳤다(콜백 안에서 다시 미루면 즉시 실행). 이어진 코드 리뷰로 **콜백이 여러 개일 때 `REQUIRES_NEW` 복귀 후 다시 유실되는 2차 결함**과, **앱 연동 해제가 DB 처리보다 통보를 먼저 내보내던 기존 결함**을 함께 고쳤다.
3. 1차·2차 수정 모두 `dev`·`stg` 브랜치에 들어가 있다(실측). `main`에는 이 트랙 전체가 없다. 남은 일은 **검증계 실기기 재검증**과 **운영계 승격 시 순서 준수**뿐이다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **`backend-api-main`** | 나무엑스(namuhx) 앱·관제 백엔드. Spring Boot 3.2.4 / Java 17 / MySQL 8. EC2에서 단일 jar로 뜬다(§6). |
| **AfterCommit** | 이 저장소의 유틸 `com.skmagic.core.utils.AfterCommit`. `AfterCommit.run(stage, action)` 으로 **되돌릴 수 없는 부작용**(MQTT 발행·S3 삭제·FCM 발송)을 트랜잭션 커밋 이후로 미룬다. 트랜잭션이 없으면 즉시 실행한다. |
| **트랜잭션 동기화 (TransactionSynchronization)** | Spring이 제공하는 커밋/롤백 전후 콜백 인터페이스. `TransactionSynchronizationManager.registerSynchronization()` 으로 현재 스레드의 트랜잭션에 등록한다. `AfterCommit`은 이 위에 얹은 얇은 래퍼다. |
| **afterCommit 단계** | 커밋이 끝난 직후 Spring이 등록된 동기화들의 `afterCommit()` 을 순서대로 부르는 구간. **이 구간에서도 동기화는 여전히 "활성"** 이고, 트랜잭션 자원도 아직 스레드에 바인딩돼 있다. |
| **스냅샷 순회** | Spring이 `afterCommit` 을 돌릴 때 등록 목록을 먼저 복사해 두고 그 복사본만 순회한다는 뜻. **이 사고의 핵심 성질**이다(§3-1). |
| **REQUIRES_NEW / suspend / resume** | 새 트랜잭션을 여는 전파 방식. Spring은 새 트랜잭션을 열 때 **등록된 모든 동기화**의 `suspend()` 를, 돌아올 때 `resume()` 을 부른다. |
| **crEvent** | 서버가 기기에 상태 변경을 알리는 MQTT 이벤트 통보. 토픽은 `skmg/airbot/{serial}/v1/command/event` (AQM은 `skmg/aqm/...`). 진입점은 `RequestDeviceService.requestCrEvent` / `RequestControlRobotService.requestCrEvent` 두 벌이다. |
| **AIRBOT_REGISTRATION / unregistered** | 연동·연동 해제 통보 이벤트 이름과 상태값. 연동 해제는 `eventName=AIRBOT_REGISTRATION`, `eventStatus=unregistered` 로 나간다. 기기는 이 토픽을 받아야 "연동 해제됨" 상태로 넘어간다. |
| **`publish` vs `publishStrict`** | `PublishDeviceService`의 두 발행 메서드. `publish`는 발행 실패를 삼킨다(호출자 약 70곳의 기존 계약). `publishStrict`는 예외를 호출자에게 전파한다. |
| **`mqtt_publish_log`** | 발행 시도 기록 테이블. "이 토픽으로 실제 나갔는가"의 유일한 서버측 증거다. 컬럼: `serial, topic, payload, success, error_message, published_at`. |
| **`history_control`** | 기기 제어·이벤트 이력 테이블. 연동 해제 이력은 `op_command = 'CREV0003'` 으로 적재된다. |
| **연동 해제 (unLink)** | 사용자가 앱에서 기기를 홈에서 떼어내는 동작. 앱 API는 `POST /app/product/device/unLink`. |

### 1-2. 이 작업의 출발점 — 앞선 "커밋 후로 미루기" 트랙

이 수정은 단독 티켓이 아니라 **2026-09-02에 시작한 트랜잭션 경계 정리 작업의 뒤처리**다. 인수자가 앞 맥락을 모르면 왜 이중 등록이 생겼는지 이해할 수 없으므로 먼저 적는다.

| 날짜 | 커밋 | 무엇을 했나 |
|---|---|---|
| 09-02 | `0ad4f805` | MQTT 발행·S3 삭제·FCM 발송이 트랜잭션 **안**에서 바깥으로 나가고 있었다. 롤백돼도 취소되지 않으므로 "DB는 원복됐는데 기기만 연동 해제 통보를 받은" 상태가 가능했다. `AfterCommit` 유틸을 새로 만들고 **호출 지점 3곳**에서 감쌌다. 공유 코드를 건드리지 않으려고 `requestCrEvent`는 9종 이벤트 중 `AIRBOT_REGISTRATION` 일 때만 미뤘다. |
| 09-02 | `cc971056` | 리뷰 반영. 미루는 대상을 넓혔다 — `SCHEDULE_CHANGE` 통보와 `requestSetSchedule` 의 `remove` 명령 추가, 커밋 후 파일 삭제를 `REQUIRES_NEW`로, 연동 해제 이력 적재를 커밋 후 새 트랜잭션으로. |
| 09-03 | `ce9ce510` | 리뷰 반영. **커밋 후 콜백에서 DB를 건드리면 안 된다**는 규칙을 못박았다(완료된 트랜잭션 자원이 아직 바인딩돼 있어 기본 전파로 INSERT하면 커밋되지 않고 사라진다). 발행 로그 적재를 `MqttPublishLogWriter`(`REQUIRES_NEW`) 별도 빈으로 분리했다. |
| 09-04 | **`775d9da9`** | **이 사고의 방아쇠.** 기기 제어 이력의 `response_date`가 영구 NULL로 남는 별개 결함을 고치면서, **`PublishDeviceService.publish()` 자체가 커밋 후로 미루도록** 바꿨다. (발행이 이력 INSERT와 같은 트랜잭션 안에 있어, 기기 응답이 아직 커밋되지 않은 이력을 앞지르면 `UPDATE history_control ... WHERE correlation_id=?` 가 0행을 맞히고 조용히 끝났다. 실측 기록률: 설정 변경 0%, 청정 명령별 0.6%~100%.) |

`0ad4f805`/`cc971056`(바깥에서 발행을 감쌈)과 `775d9da9`(발행이 스스로 미룸)는 **각각은 옳은데 겹치면 이중 등록**이 된다. 이것이 §3의 결함이다.

### 1-3. 문제 (관측된 증상)

2026-09-04 검증계 승격 이후, 검증계에서 기기를 연동 해제하면

- `history_control` 에 연동 해제 이력(`CREV0003`)은 **정상적으로 남고**,
- `mqtt_publish_log` 에 `command/event` 토픽 행이 **한 줄도 없으며**,
- 기기는 `unregistered` 를 받지 못해 **연동된 상태로 남고**,
- 애플리케이션 로그에 **예외도 경고도 없다**.

개발계 검증(2026-09-03)은 `775d9da9` 이전 형상이라 통과했다. 운영계(`main`)는 이 트랙이 통째로 없어 영향이 없었다. **두 변경이 함께 있는 검증계에서만** 드러난 조합 결함이다.

### 1-4. 목표

1. 결함을 유발한 조합을 개별 호출 지점에서 걷어내는 데 그치지 않고, **유틸 자체가 중첩을 막게** 한다. "미루기"는 멱등이어야 한다 — 미루는 호출자가 내부에서 또 미루는 서비스를 부르는 조합은 앞으로도 자연스럽게 생긴다.
2. 고치면서 "커밋 뒤에만 바깥이 움직인다"는 기존 불변식을 깨지 않는다.
3. 회귀를 **가짜 동기화가 아니라 실제 트랜잭션 매니저 위에서** 고정한다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 브랜치 반영

`backend-api-main` 단일 저장소다. 아래는 `git merge-base --is-ancestor` 실측 결과다.

| 커밋 | 날짜 | 내용 | dev | stg | main(운영) |
|---|---|---|---|---|---|
| `0ad4f805` | 09-02 | `AfterCommit` 유틸 도입 + 호출 지점 3곳 | 반영 | 반영 | **미반영** |
| `cc971056` | 09-02 | 미룸 대상 확대 · 커밋 후 DB 작업 `REQUIRES_NEW` | 반영 | 반영 | **미반영** |
| `ce9ce510` | 09-03 | 콜백에서 DB 금지 · 발행 로그 분리 | 반영 | 반영 | **미반영** |
| `775d9da9` | 09-04 | `publish()` 자체를 커밋 후로 (결함 방아쇠) | 반영 | 반영 | **미반영** |
| **`f5d82cf4`** | 09-08 | **1차 수정 — 중첩 안전화 + 바깥 래핑 4곳 제거** | 반영 | 반영 | **미반영** |
| **`1eff45ce`** | 09-08 | **2차 수정 — 다중 콜백 suspend/resume · 앱 연동 해제 tx 경계** | 반영 | 반영 | **미반영** |

- dev 머지: `8d09176d`(1차, `fix/after-commit-nesting`), `b6fadab1`(2차, `fix/after-commit-review`).
- stg 승격: `f0fe5f70`(09-08 09:08, 1차), **`742fb766`(09-08 12:16, 2차 — "R045 재시도 비활성·AfterCommit 트랜잭션 경계")**.
- `git diff origin/dev origin/stg` 로 `AfterCommit.java` · `ProductDeviceUnlinkService.java` · `ProductDeviceController.java` 를 대조한 결과 **차이 0**. 두 브랜치의 해당 형상은 동일하다.
- `main` tip은 `7d9ac170`(2026-09-01). `AfterCommit.java` 파일 자체가 없고 `publish()`는 즉시 발행하는 옛 형태다.
- `1eff45ce` 이후 오늘까지 `AfterCommit.java` · `ProductDeviceUnlinkService.java` · `ProductDeviceController.java` · `PublishDeviceService.java` · `ProductDeviceService.java` 를 건드린 커밋은 **0건**이다. 이후 작업이 덮어쓰지 않았다.

> **주의**: 작업 당시의 진행 기록에는 2차 수정이 "검증계 승격 잔여"로 남아 있다. 그 기록이 남은 뒤 같은 날 `742fb766` 로 승격이 끝났고, 위 실측이 그것을 확인한다. **다시 승격할 필요가 없다.**

### 2-2. 배포·검증

| 항목 | 상태 |
|---|---|
| 개발계 배포·확인 | **완료.** 1차 수정 후 개발계에서 실기기 연동 해제가 정상 동작하는 것을 확인했다 |
| 검증계 브랜치 | **반영 완료**(§2-1) |
| 검증계 애플리케이션 배포 | **미확인.** 로컬 맥에서는 EC2·AWS 콘솔에 접근할 수 없다. VDI에서 §6-2의 방법으로 확인해야 한다 |
| 검증계 실기기 재검증 | **미완.** 사고 확인 쿼리(§6-3 A의 `mqtt_publish_log` 조회)가 지난번 검증계에서 **0행**이었다. 배포 후 다시 돌려 행이 생기는지 확인해야 마무리다 |
| 운영계 | **미반영·미배포.** 브랜치·배포 모두 없음 |
| 단위 테스트 | 커밋 시점 `./gradlew test --rerun-tasks` 기준 1차 487건 / 2차 491건 전건 통과(커밋 메시지 기재값). 인수자가 재실행해 확인할 수 있다 |

### 2-3. DB·설정 변경

**없다.** DDL·마이그레이션·SSM 파라미터·환경변수 신규 항목이 하나도 없다. 애플리케이션 코드만 바뀐 배포다.

---

## 3. 결함 메커니즘 — 왜 콜백이 소리 없이 사라졌나

인수자가 이 절만 이해하면 재발을 막을 수 있다.

### 3-1. Spring의 성질 세 가지

1. **afterCommit 단계에서도 동기화는 활성이다.** 즉 `TransactionSynchronizationManager.isSynchronizationActive()` 가 콜백 안에서도 `true` 를 돌려준다. 그래서 `AfterCommit.run()` 은 "트랜잭션 안"이라고 판단하고 **등록 경로를 탄다**.
2. **콜백 목록은 스냅샷으로 순회한다.** Spring은 `afterCommit` 을 돌리기 전에 `getSynchronizations()` 로 목록을 복사해 두고 그 복사본만 순회한다. 콜백 실행 중에 새로 등록한 동기화는 내부 집합에는 들어가지만 **이미 뜬 스냅샷에는 없다**. 그 트랜잭션은 이미 커밋을 마쳤으므로 그 동기화의 `afterCommit()` 은 **영영 호출되지 않는다.**
3. **등록도 조회도 예외를 던지지 않는다.** 등록은 성공했고, 실행되지 않았다는 사실을 아무도 보고하지 않는다. **예외 없음 · 로그 없음 · 실패 표시 없음.** 그래서 코드를 읽어서는 절대 찾을 수 없고, "이력은 있는데 토픽만 없다"는 데이터 대조로만 드러난다.

### 3-2. 실제로 일어난 경로 (연동 해제)

```
[앱] POST /app/product/device/unLink
  └ ProductDeviceService.requestUnLinkAbtDevice()
     └ RequestDeviceService.requestCrEvent()   ← @Transactional 트랜잭션 T 시작
        │
        │  (수정 전) AfterCommit.run("CREV_PUBLISH:AIRBOT_REGISTRATION", () -> publish(...))
        │            → 동기화 S1 등록                                   [바깥 등록]
        │  deferUnLinkHistory(...)
        │            → 동기화 S2 등록 (연동 해제 이력 적재, REQUIRES_NEW)
        │
        └ T 커밋
           └ Spring: 스냅샷 [S1, S2] 을 뜨고 순회 시작
              ├ S1.afterCommit()
              │   └ publishDeviceService.publish(topic, payload, serial)
              │       └ (775d9da9 이후) AfterCommit.run("mqtt-publish", ...)
              │           → isSynchronizationActive() == true 이므로 등록 경로
              │           → 동기화 S3 등록                              [안쪽 등록]
              │              ↑ 스냅샷 [S1, S2] 에 없다 → afterCommit() 영원히 호출 안 됨
              │                → MQTT 발행 자체가 일어나지 않음
              └ S2.afterCommit()
                  └ 연동 해제 이력 INSERT  → 정상 적재
```

**결과: `history_control` 에는 행이 남고, `mqtt_publish_log` 에는 아무것도 없고, 기기는 통보를 못 받는다.** 관측 증상과 정확히 일치한다.

### 3-3. 왜 전 발행이 아니라 일부만 사라졌나 (영향 범위)

`775d9da9` 이후 **모든** `publish()` 는 스스로 미룬다. 하지만 **바깥에서 한 번 더 감싼 4곳**에서만 이중 등록이 됐다. 그 4곳이 이 사고의 전체 영향 범위다.

| # | 위치 | 대상 | 증상 |
|---|---|---|---|
| 1 | `RequestDeviceService.requestCrEvent` | `AIRBOT_REGISTRATION`(연동·연동 해제 통보), `SCHEDULE_CHANGE` | 연동 해제 통보 미발행 |
| 2 | `RequestControlRobotService.requestCrEvent` | 위와 동일(관제 경로) | 관제 경로 통보 미발행 |
| 3 | `RequestDeviceService.requestSetSchedule` | `command == "remove"` | 스케줄 삭제 토픽 미발행 |
| 4 | `RequestControlRobotService` 대기화면 테마 | `DEVICE_THEME_PUBLISH` | 테마 발행 미발행 |

나머지 이벤트(`OWNER_LOCATION`·`AQM_LINK`·`MAPPING_CHANGE`·`HOME_CHANGE`·`CALL_CHANGE`·`EDIT_MAP`·`FIRMWARE_RELEASED`)와 스케줄 `add`·`update`는 바깥 래핑이 없어 정상 발행됐다. **"연동 해제만 이상하다"** 는 문의가 들어온 이유가 이것이다.

### 3-4. 2차 결함 — 콜백이 여러 개일 때 (리뷰에서 발견)

1차 수정은 "지금 커밋 후 콜백 안인가"를 **ThreadLocal boolean** 으로 표시했다. 콜백 안에서 `REQUIRES_NEW` 를 여는 정당한 경우(파일 삭제·이력 적재)를 위해, 동기화의 `suspend()` 에서 표시를 내리고 `resume()` 에서 되돌리게 했다.

문제는 **Spring이 새 트랜잭션을 열 때 등록된 "모든" 콜백의 `suspend()` 를 순서대로 부른다**는 점이다.

```
등록 콜백: [A, B]     (A 실행 중, A 안에서 REQUIRES_NEW 를 연다)

A.suspend()  → 표시(true)를 저장하고 false 로 내림
B.suspend()  → 이미 false 인 것을 저장               ← 여기가 함정
   … 새 트랜잭션 …
A.resume()   → 저장해 둔 true 로 복원
B.resume()   → 저장해 둔 false 로 덮어씀             ← 표시가 꺼진 채 남는다

→ A 가 복귀한 뒤 부르는 AfterCommit.run() 이 다시 "등록 경로"를 타고 유실된다.
```

콜백이 하나뿐인 테스트만 있어서 1차에서 잡지 못했다. **실제 조합**도 만들어진다 — `publish()` 안의 발행 로그 적재(`MqttPublishLogWriter`)가 `REQUIRES_NEW` 이므로, **한 콜백에서 두 번 발행하면 두 번째가 유실**된다.

### 3-5. 3차 문제 — 앱 연동 해제의 트랜잭션 경계 (기존 결함, 같은 리뷰에서 발견)

중첩 유실과는 별개지만 같은 "언제 바깥이 움직이는가" 문제다.

수정 전 `ProductDeviceController.unLinkProductDevice` 는 네 단계를 **각각 별개 트랜잭션**으로 순서대로 불렀다.

```
① 지도 연결 해제 (deleteMapDeviceLink)
② 연동 해제 통보 (requestUnLinkAbtDevice → requestCrEvent)   ← 자체 tx 커밋 순간 MQTT 발행
③ 대기화면 테마 정리 (DeleteAllDeviceThemeBackGround)        ← 자체 tx 커밋 순간 S3 삭제
④ 실제 연동 해제 (unLinkProductDevice)                        ← 여기서 실패하면?
```

②가 자체 트랜잭션 커밋과 함께 이미 발행돼 버렸으므로, ④가 실패해도 **기기는 이미 `unregistered` 를 받은 뒤**다. DB에는 연동된 채로, 기기는 해제된 채로 갈린다. `AfterCommit` 을 아무리 잘 고쳐도 **경계 자체가 잘못 그어져 있으면** 막을 수 없다.

---

## 4. 무엇을 고쳤나

### 4-1. `AfterCommit` 중첩 안전화 (`f5d82cf4` → `1eff45ce`)

파일: `src/main/java/com/skmagic/core/utils/AfterCommit.java`

**수정 전** — 조건 하나로 바로 등록했다.

```java
public static void run(String stage, Runnable action) {
    if (!TransactionSynchronizationManager.isSynchronizationActive()) {
        action.run();   // 트랜잭션 밖이면 즉시 실행
        return;
    }
    TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
        @Override public void afterCommit() {
            try { action.run(); }
            catch (Exception e) { log.error("커밋 후 처리 실패: stage={}, ...", stage, e); }
        }
    });
}
```

**수정 후** — 익명 클래스를 `Deferred` 라는 이름 있는 내부 클래스로 바꾸고, **지금 실행 중인 콜백 객체**를 ThreadLocal에 둔다. 핵심만 옮기면 이렇다.

```java
/** 이 스레드에서 지금 실행 중인 콜백. null 이면 콜백 밖이다. */
private static final ThreadLocal<Deferred> RUNNING = new ThreadLocal<>();

public static void run(String stage, Runnable action) {
    if (!TransactionSynchronizationManager.isSynchronizationActive()) {
        action.run();
        return;
    }
    if (RUNNING.get() != null) {
        // 이미 커밋 후 콜백 안이다. 여기서 등록하면 이번 커밋의 스냅샷에 없어 영영 실행되지 않는다.
        action.run();          // ← 즉시 실행. 이미 커밋이 끝난 뒤라 불변식은 지켜진다
        return;
    }
    TransactionSynchronizationManager.registerSynchronization(new Deferred(stage, action));
}

private static final class Deferred implements TransactionSynchronization {
    private boolean suspendedWhileRunning;   // "내가 실행 중일 때" 밀려났는가

    @Override public void afterCommit() {
        Deferred outer = RUNNING.get();
        setRunning(this);
        try { action.run(); }
        catch (Exception e) { log.error("커밋 후 처리 실패: stage={}, type={}", stage, e.getClass().getSimpleName(), e); }
        finally { setRunning(outer); }       // 중첩 콜백도 안전하게 복원
    }

    @Override public void suspend() {
        if (RUNNING.get() == this) {          // ← 자기 자신일 때만 (3-4의 2차 결함 수정)
            suspendedWhileRunning = true;
            setRunning(null);
        }
    }

    @Override public void resume() {
        if (suspendedWhileRunning) {          // ← 자기가 내렸을 때만 되돌린다
            suspendedWhileRunning = false;
            setRunning(this);
        }
    }
}
```

세 가지 판단이 들어 있다.

- **콜백 안의 재호출은 등록하지 않고 즉시 실행한다.** 이미 커밋이 끝난 뒤이므로 "커밋 뒤에만 바깥이 움직인다"는 약속은 그대로 지켜진다. 실행 순서도 그 콜백의 일부로 유지된다.
- **`suspend`/`resume` 은 자기 자신이 실행 중일 때만 표시를 다룬다.** 다른 콜백이 "꺼진 상태"를 저장했다가 덮어쓰는 §3-4의 경로가 사라진다.
- **`setRunning(null)` 은 `ThreadLocal.remove()` 를 쓴다.** 톰캣 풀 스레드에 값을 남기지 않기 위해서다.

### 4-2. 바깥 래핑 4곳 제거 (`f5d82cf4`)

`publish()` 가 스스로 미루므로 호출 지점의 래핑은 **중복이고, 남겨 두면 다음 사람이 "여기서 미루고 있다"고 오해한다.** §3-3의 4곳을 전부 걷어내고 그냥 `publish()` 를 부른다. 함께 쓰이던 선별 판정 메서드 `isDeferredPublishEvent()` 두 벌도 삭제했다 — `775d9da9` 로 전 발행이 미뤄지면서 "AIRBOT_REGISTRATION·SCHEDULE_CHANGE만 미룬다"는 선별이 의미를 잃었기 때문이다.

수정 후 `requestCrEvent` 의 해당 부분:

```java
// publish - 커밋 이후로 미루는 것은 publish() 자신이 한다. 여기서 또 감싸면
// 커밋 후 콜백 안에서 재등록되어 발행이 소리 없이 유실된다(AfterCommit 참고).
publishDeviceService.publish(topic, payload, historyControl.getSerial());
```

**이력·파일 삭제·FCM 래핑은 그대로 뒀다.** 그것들은 `publish` 가 아니라서 스스로 미루지 않는다.

### 4-3. 앱 연동 해제를 한 트랜잭션으로 (`1eff45ce`)

신규 파일: `src/main/java/com/skmagic/api/product/service/ProductDeviceUnlinkService.java`

컨트롤러가 순서대로 부르던 네 단계를 `@Transactional public void unLinkByApp(ProductDevice)` 한 경계 안으로 옮겼다. 통보·S3 삭제는 **이 트랜잭션의 커밋 뒤에만** 나가고, 어느 단계가 실패하든 DB도 통보도 함께 없던 일이 된다.

컨트롤러에는 검증만 남았다 — 중앙관리자 권한(`MBRG0002`), 홈 멤버 소속(`AUTH0001`), LLM 동작 중(`DVRG0002`). 검증 실패는 종전대로 `200` + `success:false` 응답이다. **API 계약은 한 글자도 바뀌지 않았다.**

```java
// ProductDeviceController#unLinkProductDevice — 검증 3건 통과 후
productDeviceUnlinkService.unLinkByApp(productDevice);
```

**왜 `ProductDeviceService` 에 넣지 않고 별도 빈을 만들었나**: 조율에는 `RequestControlRobotService` 가 필요한데, `ProductDeviceService` 와 그 주변은 이미 순환 참조로 얽혀 있다. 의존을 하나 더 얹는 대신 **아무도 의존하지 않는 잎(leaf) 빈**에서 조율한다. 컨트롤러에서 `RequestControlRobotService` 주입은 걷어냈다.

같은 커밋에서 `ProductDeviceService.unLinkProductDevice` 끝의 **연동 해제 FCM 루프**도 `AfterCommit.run("DEVICE_UNLINK_FCM", ...)` 으로 감쌌다. 트랜잭션 밖 호출 경로는 즉시 실행되므로 다른 두 호출자(`RequestDeviceService:2529`, `ResponseDeviceService:1945`)의 동작은 그대로다.

### 4-4. 바뀐 파일 전체

| 파일 (`backend-api-main` 기준 상대 경로) | 커밋 | 내용 |
|---|---|---|
| `src/main/java/com/skmagic/core/utils/AfterCommit.java` | `f5d82cf4`, `1eff45ce` | 중첩 안전화. 이 작업의 중심 |
| `src/main/java/com/skmagic/api/device/control/service/RequestControlRobotService.java` | `f5d82cf4` | 바깥 래핑 2곳 제거(crEvent·테마), `isDeferredPublishEvent` 삭제 |
| `src/main/java/com/skmagic/api/device/control/service/RequestDeviceService.java` | `f5d82cf4` | 바깥 래핑 2곳 제거(crEvent·스케줄 remove), `isDeferredPublishEvent` 삭제 |
| `src/main/java/com/skmagic/api/product/service/ProductDeviceUnlinkService.java` | `1eff45ce` | **신규.** 앱 연동 해제 조율을 한 트랜잭션으로 |
| `src/main/java/com/skmagic/api/product/controller/ProductDeviceController.java` | `1eff45ce` | 조율 구간을 서비스 호출 한 줄로 교체 |
| `src/main/java/com/skmagic/api/product/service/ProductDeviceService.java` | `1eff45ce` | 연동 해제 FCM 루프를 커밋 뒤로 |
| `src/test/java/com/skmagic/core/utils/AfterCommitNestingTest.java` | `f5d82cf4`(6건), `1eff45ce`(+2건) | **신규.** 중첩 회귀 고정 |
| `src/test/java/com/skmagic/api/product/service/ProductDeviceUnlinkServiceTest.java` | `1eff45ce` | **신규.** 연동 해제 트랜잭션 경계 고정 |

### 4-5. 현재 `AfterCommit` 사용처 전체 (6곳, `origin/dev` 실측)

| 파일:줄 | stage | 미루는 것 |
|---|---|---|
| `api/device/control/service/PublishDeviceService.java:59` | `mqtt-publish` | **모든 MQTT/HTTPS 발행.** 가장 중요한 지점 |
| `api/device/control/service/RequestControlRobotService.java:1861` | `CREV_UNLINK_HISTORY` | 연동 해제 이력 적재(`REQUIRES_NEW`) |
| `api/device/control/service/RequestControlRobotService.java:2616` | `DEVICE_THEME_FILE_DELETE` | 대기화면 테마 S3·파일 행 삭제(`REQUIRES_NEW`) |
| `api/device/control/service/RequestDeviceService.java:2249` | `CREV_UNLINK_HISTORY` | 연동 해제 이력 적재(`REQUIRES_NEW`) |
| `api/product/service/ProductDeviceService.java:515` | `DEVICE_UNLINK_FCM` | 연동 해제 FCM 발송 |
| `api/building/service/BuildingMemberService.java:610` | `HOME_MEMBER_LEAVE_FCM` | 홈 멤버 나가기 FCM 발송 |

### 4-6. 검증 방법 — 테스트

**가짜 동기화가 아니라 실제 `DataSourceTransactionManager` 를 mock `DataSource` 위에 올려** 커밋·롤백·suspend·resume 생명주기를 그대로 태운다. 이게 아니면 §3-1의 스냅샷 성질이 재현되지 않는다.

`src/test/java/com/skmagic/core/utils/AfterCommitNestingTest.java` (8건)

| 테스트 메서드 | 고정하는 것 |
|---|---|
| `커밋_후_콜백_안에서_등록한_콜백도_실행된다` | 사고의 최소 재현. 수정 전 실패 |
| `중첩되어도_등록_순서가_유지된다` | `A → A-inner → B` 순서. **수정 전 `A-inner` 유실로 실패했던 테스트** |
| `롤백되면_중첩_콜백도_전부_실행되지_않는다` | "롤백되면 아무것도 나가지 않는다" 불변식 |
| `콜백_안에서_연_새_트랜잭션의_등록은_그_트랜잭션_커밋_뒤에_실행된다` | `REQUIRES_NEW` 안의 등록은 즉시 실행이 아니라 **그 트랜잭션 커밋 뒤**여야 한다 |
| `콜백_안에서_연_새_트랜잭션이_롤백되면_그_안의_등록은_실행되지_않는다` | 복귀 후 표시가 복원되는지까지 함께 본다 |
| `본문에서_연_새_트랜잭션은_바깥_표시를_건드리지_않는다` | 커밋 **전** 본문의 `REQUIRES_NEW` 가 표시를 잘못 켜면 이후 등록이 즉시 실행돼 버린다 |
| `콜백이_여러_개여도_새_트랜잭션에서_복귀한_뒤의_중첩_등록은_실행된다` | **§3-4의 2차 결함.** 리뷰에서 재현된 시나리오 |
| `한_콜백에서_두_번_발행하면_발행_로그_트랜잭션을_거친_뒤에도_둘_다_나간다` | 실제 조합(`publish` 안의 발행 로그가 `REQUIRES_NEW`). 실제 `PublishDeviceService` 를 태운다 |

`src/test/java/com/skmagic/api/product/service/ProductDeviceUnlinkServiceTest.java` (2건) — 실제 `@Transactional` 프록시(`TransactionInterceptor`)를 붙여 경계를 그대로 태운다.

| 테스트 메서드 | 고정하는 것 |
|---|---|
| `연동_해제_DB_처리가_실패하면_기기에_통보가_나가지_않는다` | §3-5. `unLinkProductDevice` 가 던지면 통보 0건 |
| `정상이면_DB_처리가_끝난_뒤에_통보가_나간다` | 순서가 `unlink → publish` 여야 한다 |

**함께 지켜보는 기존 테스트** — 이 트랙의 불변식을 나눠 갖고 있으므로 하나라도 깨지면 배포를 멈춘다.

| 파일 | 건수 | 고정하는 것 |
|---|---|---|
| `core/utils/AfterCommitTest.java` | 4 | 유틸의 기본 계약(트랜잭션 없으면 즉시 실행 · 커밋 전 미실행 · 등록 순서 · 실패 삼킴) |
| `core/utils/AfterCommitDbBoundaryTest.java` | 4 | 커밋 후 DB 작업이 **새 트랜잭션**에서 도는지(테마 파일 삭제·연동 해제 이력·발행 로그), 공유 진입점의 전파는 안 바뀌었는지 |
| `api/device/control/service/PublishAfterCommitTest.java` | 7 | `publish` 는 미루고 `publishStrict` 는 미루지 않는다 · 롤백 시 미발행 · 순서 · 트랜잭션 밖 즉시 발행 |
| `api/building/service/BuildingMemberLeaveNotifyTest.java` | 4 | 홈 멤버 나가기 FCM — 롤백 시 미발송, 콜백이 DB를 건드리지 않음 |
| `api/device/control/service/PublishDeviceServiceTest.java` | 9 | 발행/로그 실패 격리, `publishStrict` 가 payload를 로그 테이블에 남기지 않음 |

실행:

```bash
cd ~/benjamin/backend-api-main
./gradlew test --rerun-tasks

# 이 트랙만 빠르게
./gradlew test --tests '*AfterCommit*' --tests '*ProductDeviceUnlink*' --tests '*PublishDevice*' --rerun-tasks
```

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **커밋 후 콜백 안에서의 `AfterCommit.run()` 은 등록하지 않고 즉시 실행한다.** "일관되게 항상 등록하도록" 되돌리면 안 된다 | 그 등록은 이미 뜬 스냅샷에 없어 **영영 실행되지 않는다.** 예외도 로그도 없이 사라진다. 이것이 연동 해제 토픽 미발행 사고 그 자체다. 이미 커밋이 끝난 뒤이므로 즉시 실행해도 "커밋 뒤에만 바깥이 움직인다"는 불변식은 지켜진다 |
| 2 | **`suspend()`/`resume()` 에서 표시를 다루는 것은 "지금 실행 중인 콜백 하나"뿐이다.** 표시를 다시 `boolean` 으로 단순화하면 안 된다 | Spring은 `REQUIRES_NEW` 진입 시 **등록된 모든 콜백**에 `suspend()` 를 부른다. 콜백마다 저장·복원하면 먼저 표시를 끈 콜백 뒤의 콜백이 "꺼진 상태"를 저장했다가 복원 때 덮어써, **복귀 후의 중첩 등록이 다시 유실된다.** 콜백 1개 테스트로는 안 잡힌다(`콜백이_여러_개여도...` 테스트가 고정) |
| 3 | **`publish()` 를 바깥에서 또 `AfterCommit` 으로 감싸지 않는다** | 정확히 이 이중 등록이 사고를 만들었다. `publish()` 는 스스로 미룬다. 앞으로 다른 호출 지점에서 "여기도 커밋 뒤에 나가야 하는데"라고 생각되면 **감싸지 말고 이미 그렇다는 것을 확인**하면 된다. 유틸이 중첩을 막게 고쳤으므로 감싸도 지금은 동작하지만, 아무 일도 하지 않는 코드가 다음 사람을 오해시킨다 |
| 4 | **`publishStrict` 는 미루지 않는다** | 예외를 호출자에게 전파하는 것이 그 메서드의 계약이다. 미루면 예외가 호출자의 try-catch **바깥(커밋 후)** 에서 터져 실패를 상태·응답에 반영할 수 없다. `publish` 가 예외를 삼키는 계약(호출자 약 70곳)이라 그쪽만 미뤄도 안전하다 |
| 5 | **커밋 후 콜백 안에서 DB를 건드리려면 반드시 새 트랜잭션(`REQUIRES_NEW`)을 쓴다** | afterCommit 시점에는 **완료된 원 트랜잭션 자원이 아직 스레드에 바인딩돼 있다.** 기본 전파(REQUIRED)로 호출하면 이미 끝난 트랜잭션에 참여해 **커밋되지 않고 사라진다.** 같은 실수가 이 트랙에서 세 번 나왔다(테마 파일 삭제, 홈 멤버 알람 설정, 발행 로그). 대안은 필요한 값을 커밋 전에 스냅샷으로 확정하고 콜백에서는 DB를 안 건드리는 것이다 |
| 6 | **발행 로그(`MqttPublishLogWriter`)는 별도 빈 + `REQUIRES_NEW` 를 유지한다** | 같은 클래스 안에서 호출하면 프록시를 타지 않아 `REQUIRES_NEW` 가 적용되지 않는다. 또 이 로그는 "발행을 시도했다"는 사실의 기록이라 호출자가 롤백돼도 남아야 추적이 된다. `PublishDeviceService` 안으로 되돌리면 **발행은 나갔는데 기록만 사라진다** |
| 7 | **앱 연동 해제는 `ProductDeviceUnlinkService.unLinkByApp` 한 트랜잭션 안에 있어야 한다.** 컨트롤러로 되돌리지 않는다 | 되돌리면 통보(MQTT)와 S3 삭제가 자체 트랜잭션 커밋과 함께 먼저 나가고, 뒤의 연동 해제 DB 처리가 실패해도 **기기는 이미 `unregistered` 를 받은 뒤**가 된다. DB는 연동, 기기는 해제로 갈린다 |
| 8 | **조율을 `ProductDeviceService` 안으로 옮기지 않는다 (잎 빈 유지)** | `ProductDeviceService` 와 `RequestControlRobotService` 주변은 이미 순환 참조로 얽혀 있다. 의존을 하나 더 얹으면 기동 시 순환 참조 오류 범위가 넓어진다. 아무도 의존하지 않는 잎 빈이 그래서 있다 |
| 9 | **`AfterCommit` 은 커밋 후 실패를 삼키고 ERROR 로그만 남긴다** | 이미 커밋이 끝나 되돌릴 것이 없고, 예외를 던지면 **성공한 요청의 응답만 실패로 뒤집힌다.** 다만 이 클래스가 남기는 것은 `stage` 와 예외 타입뿐이므로, **재처리 대상을 식별해야 하는 작업은 콜백 안에서 직접 잡아 대상 식별자(serial·correlationId·file id)까지 로그에 남겨야 한다** |
| 10 | **운영계 승격 시 `775d9da9` 와 `f5d82cf4`·`1eff45ce` 를 분리해 올리지 않는다** | `775d9da9`(publish 자체 미룸)만 올라가면 §3-3의 4곳이 그대로 남아 **운영계에서 연동 해제 토픽이 사라진다.** 반대로 수정만 올리는 것은 무해하지만 의미가 없다. `main` 에는 이 트랙이 통째로 없으므로(§2-1), 승격은 **전체를 묶어서** 한다 |

---

## 6. 배포 방법

### 6-1. 순서

**의존성이 없다.** 이 수정은 `backend-api-main` 한 저장소 안에서 끝나고 DDL·설정 변경도 없다. 다른 저장소와의 배포 순서 제약이 없다.

```
backend-api-main 배포 → 끝
```

### 6-2. 검증계 배포 (남은 일)

`backend-api-main` 은 ECS가 아니라 **EC2에서 단일 jar** 로 뜬다. 기동 스크립트 `deploy/air-bot-api.sh` 는 2026-08-14 커밋 `d7eb1fbe` 로 저장소 추적에서 제거돼 **EC2 박스에서 관리**된다. 원문이 필요하면 다음으로 꺼낸다.

```bash
git -C ~/benjamin/backend-api-main show d7eb1fbe^:deploy/air-bot-api.sh
```

스크립트 요지(검증계 EC2, `/opt/www/airbot/back-end`):

```bash
./air-bot-api.sh status              # 현재 기동 여부·PID
./air-bot-api.sh stop
# 새 jar 를 /opt/www/airbot/back-end/air-bot-api.jar 로 교체
./air-bot-api.sh start stg
```

내부적으로는 `nohup java -Xms4076m -Xmx8192m -jar air-bot-api.jar --spring.profiles.active=stg` 이며, 설정은 `aws-parameterstore:/backend-api-main/stg/` 에서 읽는다. 로그는 `/opt/www/airbot/back-end/logs/app.log`.

빌드:

```bash
cd ~/benjamin/backend-api-main
./gradlew clean bootJar        # build/libs/air-bot-api.jar
```

**배포된 형상인지 확인하는 법** (로컬 맥에서는 EC2에 도달할 수 없다. VDI에서 수행):

```bash
# 검증계 EC2 에서
cd <애플리케이션 소스 디렉터리>   # 박스에 git clone 이 있는 경우
git log -1 --format='%h %ad %s' --date=iso
# 1eff45ce 또는 그 이후(742fb766 포함) 이면 반영된 형상이다

# 또는 jar 빌드 시각으로
ls -l /opt/www/airbot/back-end/air-bot-api.jar    # 2026-09-08 12:16 이후여야 한다
```

### 6-3. 배포 후 검증

**A. 핵심 — 연동 해제 실기기 1건** (이번 사고의 재현 쿼리)

1. 검증계 앱에서 ABT(에어봇) 기기 1대를 연동 해제한다 (`POST /app/product/device/unLink`).
2. 검증계 DB에서 아래를 돌린다. **행이 나와야 정상이다. 비어 있으면 결함이 그대로다.**

```sql
-- ① 발행이 실제로 나갔는가 (지난번 검증계에서 0행이었던 바로 그 쿼리)
SELECT id, serial, topic, success, error_message, published_at
  FROM mqtt_publish_log
 WHERE serial = '<연동 해제한 시리얼>'
   AND topic LIKE '%/command/event'
 ORDER BY id DESC
 LIMIT 5;
-- 기대: topic = 'skmg/airbot/<serial>/v1/command/event', success = 1
--       payload 안에 "eventName":"AIRBOT_REGISTRATION", "eventStatus":"unregistered"

-- ② 이력도 정상인가 (사고 때도 이쪽은 남았다 — 둘이 짝을 이뤄야 정상)
SELECT id, serial, login_id, op_command, correlation_id, request_date
  FROM history_control
 WHERE serial = '<연동 해제한 시리얼>'
   AND op_command = 'CREV0003'
 ORDER BY id DESC
 LIMIT 5;

-- ③ ①과 ②의 시각이 거의 같아야 한다. ②만 있고 ①이 없으면 이 사고의 재발이다.
```

3. **기기가 실제로 연동 해제 상태로 넘어갔는지** 확인한다. `mqtt_publish_log` 에 행이 있어도 기기가 안 받았으면 별개 문제(IoT 정책·인증서)다.

**B. 회귀 — 발행 시점이 바뀐 경로 전반**

`775d9da9` 로 모든 `publish()` 가 커밋 뒤로 밀렸고, 이번 수정으로 바깥 래핑 4곳이 걷혔다. 발행 타이밍이 바뀌는 경로를 한 번씩 훑는다.

| 확인 | 기대 |
|---|---|
| 기기 연동(등록) | `AIRBOT_REGISTRATION` / `registered` 발행됨 |
| 스케줄 등록·수정·**삭제** | 삭제(`remove`)가 이번에 래핑이 걷힌 4곳 중 하나다 |
| 대기화면 테마 변경·삭제 | 발행 + S3 삭제 |
| 기기 제어(청정·설정 변경 등) | 발행되고 **`history_control.response_date` 가 채워진다** (`775d9da9` 의 원래 목적) |
| 지도 편집·펌웨어·홈 변경 | 종전대로 발행 |
| 앱 연동 해제 후 FCM | 홈 멤버들에게 `ALWK0004` 알림 도달 |
| 홈 멤버 나가기 | FCM 발송 정상 |

**C. 로그 키워드**

| 키워드 | 뜻 |
|---|---|
| `커밋 후 처리 실패: stage=` | `AfterCommit` 콜백이 예외로 실패했다. `stage` 가 §4-5 표의 어느 지점인지 알려준다 |
| `stage=mqtt-publish` | 발행 자체가 커밋 후에 실패 |
| `연동 해제 이력 적재 실패 (연동 해제는 이미 확정됨)` | 이력만 빠졌다. `serial`·`correlationId` 가 함께 찍히므로 수동 보정 가능 |
| `publish error:` | 발행 시도 중 오류(로그는 `mqtt_publish_log.error_message` 에도 남는다) |

### 6-4. 롤백

- 애플리케이션 이전 jar로 되돌린다. **DB·설정 변경이 없어 되돌릴 데이터가 없다.**
- **단, 어느 형상으로 되돌리는지가 중요하다.** `1eff45ce`·`f5d82cf4` 만 빼고 `775d9da9` 가 남는 형상으로 되돌리면 **사고가 그대로 재현된다**(§5의 10번). 되돌린다면 `775d9da9` 이전(2026-09-04 이전) 또는 `1eff45ce` 이후 중 하나여야 한다.

---

## 7. 남은 일

### 7-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **검증계 배포 형상 확인.** `1eff45ce`(또는 `742fb766`) 이후 jar가 떠 있는지 §6-2로 확인하고, 아니면 배포 | 백엔드 | 로컬 맥에서는 EC2 접근 불가. VDI에서 수행 |
| 2 | **검증계 실기기 연동 해제 1건 → §6-3 A의 쿼리 3개.** `mqtt_publish_log` 에 `command/event` 행이 생기면 이 작업은 종결이다 | 백엔드 | 지난번 검증계에서 0행이었던 바로 그 확인. **이것이 남은 유일한 미완 항목** |
| 3 | 같은 배포 창에서 §6-3 B의 회귀 목록을 훑는다 | 백엔드 | 발행 타이밍이 전반적으로 바뀌었다 |

### 7-2. 운영계 릴리스 게이트

| # | 확인할 것 |
|---|---|
| 4 | **`main` 승격 시 `775d9da9` 와 `f5d82cf4`·`1eff45ce` 를 반드시 함께 올린다**(§5의 10번). `main` 에는 `AfterCommit.java` 파일 자체가 없고 `publish()` 도 옛 즉시 발행 형태다. 이 트랙을 쪼개면 운영계에서 연동 해제 토픽이 사라진다 |
| 5 | 운영 배포 후 §6-3 A를 운영 DB에서 1건 재확인. 운영은 실사용자 기기이므로, 반납·해지 등 이미 예정된 연동 해제 건의 `mqtt_publish_log` 를 사후 조회하는 방식이 안전하다 |
| 6 | 운영 배포 후 `history_control.response_date` 기록률이 올라가는지 확인. `775d9da9` 가 노린 효과이며 이번 수정 없이는 완성되지 않는다. 배포 전후 비교: `SELECT DATE(request_date), COUNT(*), SUM(response_date IS NOT NULL) FROM history_control WHERE request_date >= '<배포일-7d>' GROUP BY 1;` |

### 7-3. 후속 (이 기능을 막지는 않음)

| # | 항목 |
|---|---|
| 7 | **`AfterCommit` 은 "미룬 작업이 실제로 실행됐는가"를 스스로 증명하지 못한다.** 지금은 `mqtt_publish_log` 같은 각 도메인의 흔적으로만 확인된다. 콜백 실행 건수를 `stage` 별로 메트릭·로그에 남기면 같은 유형의 침묵 실패를 조기에 잡을 수 있다 |
| 8 | 연동 해제 통보 실패 시 재시도가 없다. `mqtt_publish_log.success = 0` 인 `command/event` 행을 주기적으로 훑는 보정 배치가 없다. 현재는 문의가 들어와야 안다 |
| 9 | `requestCrEvent` 가 `RequestDeviceService` 와 `RequestControlRobotService` 에 **거의 같은 코드로 두 벌** 존재한다. 이번에도 같은 수정을 두 곳에 똑같이 했다. 한쪽만 고치는 실수가 나기 쉬운 구조다 |
| 10 | `ProductDeviceService` ↔ `RequestControlRobotService` 주변의 순환 참조. 이번에는 잎 빈으로 우회했지만 근본 정리는 별도 티켓이다 |

---

## 8. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| **"연동 해제했는데 기기에서 안 풀려요"** | 이 사고의 대표 증상이다. §6-3 A의 쿼리 ①·②를 돌린다. **②만 있고 ①이 없으면** 중첩 유실 재발을 의심하고 배포 형상(`1eff45ce` 포함 여부)부터 확인한다. ①이 `success=0` 이면 발행 자체 실패이므로 `error_message` 와 IoT 쪽 문제다. 둘 다 정상이면 기기·펌웨어 쪽이다 |
| 발행이 나갔는지 알고 싶다 | `SELECT * FROM mqtt_publish_log WHERE serial = '<serial>' ORDER BY id DESC LIMIT 20;` — 이것이 서버측 유일한 증거다 |
| 제어 이력의 `response_date` 가 NULL | `775d9da9` 배포 **이전**의 이력은 구조적으로 비어 있다(설정 변경은 기록률 0%였다). 배포 이후 데이터로 판단해야 한다 |
| "이력은 남는데 뭔가 안 나갔다" 류 문의 전반 | `AfterCommit` 이 관여하는 6개 지점(§4-5)이 후보다. 로그에서 `커밋 후 처리 실패: stage=` 를 먼저 찾는다. **로그가 없다면 예외로 실패한 것이 아니라 "실행 자체가 안 된" 경우**이므로 중첩 유실 또는 롤백을 의심한다 |
| 롤백돼서 아무것도 안 나갔다 | 정상 동작이다. `AfterCommit` 은 커밋되지 않으면 실행하지 않는다. DB에도 흔적이 없어야 한다 |
| 배치·스케줄러 경로에서 즉시 발행되는 것 같다 | 정상이다. 트랜잭션 없이 호출되면 `AfterCommit` 은 즉시 실행한다. 트랜잭션 밖 기존 경로의 동작을 바꾸지 않으려는 의도다 |
| 새 코드에 커밋 후 처리를 넣고 싶다 | `AfterCommit.run("STAGE_NAME", () -> ...)` 를 쓴다. **콜백 안에서 DB를 건드린다면 반드시 다른 빈의 `REQUIRES_NEW` 진입점**을 거친다(§5의 5번). `publish()` 는 이미 미루므로 감싸지 않는다(§5의 3번) |

---

## 부록 A. 핵심 커밋 (시간순)

저장소는 전부 `backend-api-main` 이다. 반영 여부는 2026-09-22 실측.

| 날짜 | 커밋 | 내용 | dev | stg | main |
|---|---|---|---|---|---|
| 09-02 | `0ad4f805` | `AfterCommit` 유틸 도입, 호출 지점 3곳 감쌈 | 반영 | 반영 | 미반영 |
| 09-02 | `cc971056` | 리뷰 반영 — 미룸 범위 확대, 커밋 후 DB 작업 `REQUIRES_NEW` | 반영 | 반영 | 미반영 |
| 09-03 | `ce9ce510` | 리뷰 반영 — 콜백에서 DB 금지, `MqttPublishLogWriter` 분리 | 반영 | 반영 | 미반영 |
| 09-03 | `ef5aaace` | dev → stg 승격 (여기까지 검증계 반영) | – | 반영 | – |
| **09-04** | **`775d9da9`** | **`publish()` 자체를 커밋 후로 — 결함 방아쇠** | 반영 | 반영 | 미반영 |
| 09-04 | `b2ca3827` | dev → stg 승격 (**여기서부터 검증계에 결함 발생**) | – | 반영 | – |
| **09-08** | **`f5d82cf4`** | **1차 수정 — 중첩 안전화(ThreadLocal), 바깥 래핑 4곳 제거, `AfterCommitNestingTest` 6건** | 반영 | 반영 | 미반영 |
| 09-08 | `8d09176d` | `fix/after-commit-nesting` → dev 머지 | 반영 | 반영 | 미반영 |
| 09-08 | `f0fe5f70` | dev → stg (1차 수정 검증계 반영, 09:08) | – | 반영 | – |
| **09-08** | **`1eff45ce`** | **2차 수정 — 다중 콜백 suspend/resume, 앱 연동 해제 tx 경계, 중첩 테스트 +2건·연동 해제 테스트 신규 2건** | 반영 | 반영 | 미반영 |
| 09-08 | `b6fadab1` | `fix/after-commit-review` → dev 머지 (11:58) | 반영 | 반영 | 미반영 |
| 09-08 | `742fb766` | **dev → stg 승격 (2차 수정 검증계 반영, 12:16)** | – | 반영 | – |

테스트 규모(커밋 메시지 기재): 1차 `./gradlew test --rerun-tasks` 487건 통과, 2차 491건 통과, 실패 0.

`main` tip은 `7d9ac170`(2026-09-01)이며 위 트랙 전체가 **미반영**이다.

---

## 부록 B. 사고 재현 — 최소 코드

인수자가 이 결함을 손으로 재현해 보려면 아래를 임시 테스트로 돌려 보면 된다. **`1eff45ce` 이후 형상에서는 통과하고, `f5d82cf4` 이전 형상에서는 `A-inner` 가 유실되어 실패한다.**

```java
// 실제 DataSourceTransactionManager 를 mock DataSource 위에 올린다.
// 가짜 동기화로는 재현되지 않는다 — 스냅샷 순회가 Spring 구현의 성질이기 때문이다.
DataSource dataSource = mock(DataSource.class);
when(dataSource.getConnection()).thenReturn(mock(Connection.class));
DataSourceTransactionManager txManager = new DataSourceTransactionManager(dataSource);

List<String> order = new ArrayList<>();

new TransactionTemplate(txManager).executeWithoutResult(status -> {
    AfterCommit.run("A", () -> {
        order.add("A");
        // 커밋 후 콜백 안에서 다시 미룬다 — publish() 를 부르면 실제로 이 모양이 된다
        AfterCommit.run("A-inner", () -> order.add("A-inner"));
    });
    AfterCommit.run("B", () -> order.add("B"));
});

assertThat(order).containsExactly("A", "A-inner", "B");
// 수정 전: ["A", "B"] — "A-inner" 가 예외도 로그도 없이 사라진다
```

2차 결함(다중 콜백)은 아래가 재현한다. 1차 수정만 있는 형상에서 실패한다. `requiresNew()` 는 `PROPAGATION_REQUIRES_NEW` 를 지정한 `TransactionTemplate` 을 돌려주는 헬퍼다.

```java
new TransactionTemplate(txManager).executeWithoutResult(status -> {
    AfterCommit.run("A", () -> {
        // 콜백 안에서 REQUIRES_NEW — 발행 로그 적재가 실제로 이 모양이다
        requiresNew().executeWithoutResult(inner -> order.add("newTx"));
        // 복귀한 뒤의 중첩 호출. B 가 저장한 "꺼진 표시"가 A 의 표시를 덮어쓰면 여기서 유실된다
        AfterCommit.run("A-inner", () -> order.add("A-inner"));
    });
    AfterCommit.run("B", () -> order.add("B"));   // 표시를 잘못 덮어쓸 두 번째 콜백
});

assertThat(order).containsExactly("newTx", "A-inner", "B");
```

두 시나리오 모두 `src/test/java/com/skmagic/core/utils/AfterCommitNestingTest.java` 에 정식 테스트로 들어가 있으므로, 별도 파일을 만들지 말고 그 파일을 읽는 편이 빠르다.

---

## 부록 C. 교훈 한 줄

**"미루기"는 멱등이어야 한다.** 미루는 호출자가 내부에서 또 미루는 서비스를 부르는 조합은 리팩토링이 진행되면 자연스럽게 생긴다. 호출 지점마다 "여기는 안 감싸면 된다"고 규율로 막는 방식은 시간이 지나면 반드시 깨진다 — 유틸이 스스로 막아야 한다. 더구나 이 결함은 **예외도 로그도 남기지 않으므로**, 규율이 깨진 것을 알아차릴 방법이 데이터 대조밖에 없다.
