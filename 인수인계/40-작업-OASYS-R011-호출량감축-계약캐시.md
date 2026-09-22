# [작업] OASYS R011 호출량 감축 — 현재계약 테이블·계약정보 캐시

| 항목 | 내용 |
|---|---|
| **상태** | 코드 완료 · **dev·stg·main(운영) 3브랜치 전부 반영** (2026-08-04 ~ 08-06) · **운영 DB DDL 반영 여부 미확인** · **ECS 배포 여부 미확인** · 감축 효과 실측 미확인 |
| **작업 기간** | 2026-07-01 (OASYS 게이트웨이 단일 통로 정리, 선행 작업) · 2026-08-03 ~ 08-06 (본 작업) |
| **직접 수정한 저장소** | `skix-security` (단일) |
| **요청서를 전달한 대상** | 없음. 다만 OASYS 측 구두 확인 5건이 설계 전제다 (§1-4) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §8-1의 1번(운영 DB에 `device_current_contract` 테이블이 실제로 있는지 확인)과 2번(감축이 실제로 일어났는지 §부록 D 쿼리로 측정) |

---

## 0. 세 줄 요약

1. OASYS 계약조회 전문 `IF_NMX_R011`을 매번 외부에 되묻던 구조를 없앴다. **serial별 현재 계약번호를 `device_current_contract` 테이블에 보유**하고, 계약정보(고객번호·계약번호·주소)는 **1시간 Redis 캐시**를 둔다. 목표는 skix-security 몫 일 2,000~4,400건을 **일 350~800건**으로 줄이는 것이다.
2. 코드는 `feature/oasys-r011-reduction` 두 커밋(`4942d5f`, `6482d5d`)으로 완성돼 **2026-08-04 dev, 08-04 stg, 08-06 main에 이미 반영됐다.** 작업 당시 기록의 "push 안 함 / 운영 backport 별도 작업 필요"는 낡은 서술이고, Git 실측으로 전부 반영 확인했다.
3. 남은 것은 **운영 DB에 DDL이 실제로 들어갔는지 확인**, **ECS 배포 여부 확인**, **감축 효과 실측**이다. 테이블이 없어도 서비스는 정상 동작하지만 전부 miss로 강등돼 **감축 효과가 0**이 되므로, 조용히 실패한다 — 이것이 첫날 확인 항목인 이유다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **skix-security** | Safe Care. 로봇청소기(AMR) 기반 가정 보안 서비스 백엔드. 보안모드·출동·구독을 담당한다. |
| **OASYS** | SK매직 계약·고객 시스템. 우리 백엔드가 HTTP 전문(인터페이스 ID별)으로 호출한다. 단일 엔드포인트 `/api/nmx/interface`에 body의 `interfaceid`를 바꿔 보낸다. |
| **R011 (`IF_NMX_R011`)** | 기본 계약정보 조회 전문. 시리얼(`prdtQcrsNo`)을 주면 통합고객번호(`icstNo`)·계약번호(`ctrtNo`=응답의 `ordNo`)·주소를 한 번에 돌려준다. **이번 감축 대상.** |
| **R046 (`IF_NMX_R046`)** | 멤버십 구독상태 조회 전문. **계약번호와 시리얼을 함께** 보내며, OASYS가 계약-기기 소유관계를 검증한다. |
| **R047** | OASYS → 우리 쪽으로 오는 구독상태 웹훅(푸시). 우리가 받는 엔드포인트는 `POST /internal/v1/oasys/subscription/status`. 코드에서는 `subscriptSecurityMembershipInfo`가 처리한다. |
| **R021 (`IF_NMX_R021`)** | 납입내역 조회 전문. **계약번호만 싣고 시리얼을 보내지 않는다** — 이 비대칭이 §5의 3번 결정을 낳았다. |
| **R045 (`IF_NMX_R045`)** | 멤버십 가입(계약 접수) 전문. |
| **ctrtNo (계약번호)** | 기기에 연결된 메인 계약번호. R045·R046·R021의 필수 파라미터. |
| **icstNo (통합고객번호)** | OASYS가 고객 한 명에게 부여하는 번호. S1(외부 출동 연동사) 출동·라이브뷰 등에 소유자 식별자로 나간다. |
| **platform (플랫폼)** | `backend-api-main`. skix-security가 기기·계약 보조정보를 얻으려고 호출하는 자사 백엔드. 여기의 `contract.contract_no`는 열화·과거 계약일 수 있다. |
| **화이트리스트 시리얼** | `oasys_test_serial` 테이블에 등록된 테스트 기기. `OasysWhitelistClient`가 실제 OASYS 대신 스텁 응답을 준다. |
| **callable 계약번호** | `OasysService.isCallableContractNo` 통과값. 형식 `[A-Z0-9]+` + `T` 접두 + 더미(`T202ZEA19458`) 제외. 이 판정을 못 넘으면 OASYS를 아예 호출하지 않는다. |

### 1-2. 문제

OASYS 운영팀이 부하를 통보했다. 실측 수치는 **`IF_NMX_R011` 일 약 15,000건**, `IF_HMG_R003` 일 약 90,000건이었다. (`IF_HMG_R003`은 하이매직 팀 소관이라 이번 범위 밖이다.)

R011 호출 주체는 `backend-api-main`과 `skix-security` 두 곳뿐임을 확인했다.

| 주체 | 비중 | 처리 |
|---|---|---|
| `backend-api-main` | 약 77% | **다른 담당자가 별도 처리.** 이번 작업에서 건드리지 않았다 |
| `skix-security` | 일 2,000~4,400건 (약 23%) | 이번 작업 범위 |

skix-security 내부 분해(운영계 실측):

- **60.4%** — MQTT 구독조회 경로. 기기가 구독상태를 물으면(`MqttMessageHandler`, message_type `REQUEST_SUBSCRIPTION_STATUS`) 24시간 멤버십 캐시를 보고, 만료됐으면 R046을 부른다. 그런데 **R046의 파라미터인 계약번호를 얻으려고 그 앞에서 R011을 한 번 더 부르는** 구조였다.
- **약 40%** — 캐시가 전혀 없던 HTTP 조회 경로들(주소 조회, 출동이력, 라이브뷰, 출동상태). 동일 serial 재호출의 **55%가 1시간 이내**에 발생했다.
- 정작 **계약번호 자체는 전체 기기에서 하루 2~3건만 바뀐다**(R047 웹훅 수신량 기준). 읽기:쓰기가 약 **1000:1** — 캐시가 아니라 "보유"가 맞는 비율이다.

> **재측정 시 함정.** 초기 분석에서 `LEFT JOIN` fanout 때문에 호출 주체를 잘못 귀속시킨 적이 있다. 상관 서브쿼리 + `LIMIT 1`로 재측정해서 위 수치를 얻었다. 부록 D에 그 형태의 쿼리를 넣어뒀다.

### 1-3. 목표

- skix-security의 R011 일 호출량을 **350~800건**으로 낮춘다.
- 서비스 동작(구독 판정·출동·주소·납입내역)은 바뀌지 않아야 한다.
- 계약 변경(이전·재등록·해지)이 일어났을 때 **사람 개입 없이 스스로 교정**되어야 한다. 사용자가 로그 알람을 운영하지 않기로 결정했기 때문에, 안전장치는 전부 코드가 스스로 실행하는 것이어야 한다.

### 1-4. OASYS 측에 확인한 외부 사실 (설계의 근거, 전부 구두 확인)

이 다섯 가지가 설계 판단을 좌우했다. 코드 주석에도 "(확인됨)"으로 박아뒀다. **다시 확인하지 않고 뒤집으면 §5의 결정들이 근거를 잃는다.**

| # | 확인된 사실 | 이 사실이 지탱하는 것 |
|---|---|---|
| 1 | **R047은 계약 이전·재등록 시에도 발행된다** | `device_current_contract`가 계약 식별의 oracle로 성립하는 근거 |
| 2 | **R047은 10분 배치로 전달되며, 시리얼별 전달 순서는 보장된다** | 배치 안에서 순서가 뒤집히지 않으므로 테이블의 R047 값은 항상 최신 이벤트다. 단 계약 변경 후 **최대 10분간 테이블이 구계약을 들고 있는 창**이 존재한다 |
| 3 | **R046은 계약-기기 소유관계를 검증한다** | 낡은 계약번호는 `CONTRACT_NOT_FOUND`로 걸러진다. 즉 테이블 값은 **사용될 때마다 OASYS가 재검증**하는 셈 — 자가교정의 핵심 근거 |
| 4 | **OASYS는 `resultCode=-1`에 재전송하지 않는다** | R047 처리 실패는 되돌릴 기회가 없다 → §5의 6번(실패해도 부수효과는 수행) |
| 5 | Redis에 계약정보(icstNo·계약번호·주소)를 저장하는 것은 정책상 허용 | 1시간 계약정보 캐시의 전제 |

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 코드 반영

| 저장소 | 브랜치 | 반영 여부 | 근거 |
|---|---|---|---|
| `skix-security` | `origin/dev` | **반영** | `git merge-base --is-ancestor 6482d5d origin/dev` 성공. 진입 머지커밋 `d8a31ef` (2026-08-04, `Merge branch 'feature/oasys-r011-reduction' into 'dev'`) |
| `skix-security` | `origin/stg` | **반영** | 진입 머지커밋 `6e26363` (2026-08-04, `Merge branch 'dev' into 'stg'`) |
| `skix-security` | `origin/main` (운영) | **반영** | 진입 머지커밋 `5edda72` (2026-08-06, `Merge branch 'stg' into 'main'`) |

선행 작업(게이트웨이 단일 통로, §3-6)인 `98b0d67`·`11ff2fe`(2026-07-01)도 dev·stg·main 전부 반영 확인.

> **작업 당시 기록이 낡았던 부분(실측으로 정정).** 작업 당시 메모에는 "브랜치 push 안 함", "이 브랜치는 dev 기반이라 KVS/SQS 변경이 섞여 있어 운영 기준 브랜치로 backport하는 별도 작업 필요"라고 적혀 있었다. 실제로는 정상적인 dev → stg → main 승격으로 8/6에 운영 브랜치까지 올라갔고, **backport 작업은 필요 없다.** 승격이 dev를 통째로 끌고 갔으므로 KVS·SQS 변경도 함께 올라갔다.

### 2-2. 배포·검증

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| 단위·통합 테스트 | **통과.** 작업 시점 전체 336건, failures/errors/skipped 0. 실제 MySQL 8.0 기반 쓰기 권위·문장 단위 인터리빙·만료 검증 13건, 실제 Redis round-trip 3건 포함 | `./gradlew clean test` 재실행으로 현재 수치 재확인 필요(기준일에 재실행하지 않음) |
| 개발계 DDL(`device_current_contract`) | **미확인** | 부록 C의 존재 확인 쿼리. DB는 VDI에서만 접근 가능 |
| 검증계 DDL | **미확인** | 위와 동일 |
| **운영계 DDL** | **미확인.** 작업 당시 "다음 날 직접 반영 예정"으로 남긴 항목이고, 반영 완료 기록이 없다 | 부록 C. **이것이 첫날 1순위** |
| 개발·검증·운영 ECS 배포 | **미확인.** 로컬 맥에서는 AWS 콘솔·CLI에 접근할 수 없다 | VDI에서 skix-security ECS 서비스의 태스크 정의 이미지 태그 / 실행 중 리비전 확인 |
| R011 감축 효과 | **미측정** | 부록 D |

> **테이블이 없으면 조용히 실패한다.** `findCtrtNo` 조회는 `SafeRunner`로 감싸여 있어 테이블 부재 시 예외를 삼키고 miss로 강등된다. 서비스는 정상 동작하지만 모든 호출이 `[CTRTNO_TABLE_MISS]`를 찍으며 R011로 내려가고, **감축 효과는 0이 된다.** 애플리케이션 배포보다 DDL이 먼저여야 하는 이유이자, 배포 후에도 반드시 부록 D로 측정해야 하는 이유다.

### 2-3. 타 팀 / 범위 밖

| 대상 | 상태 |
|---|---|
| `backend-api-main` R011 감축 (전체의 약 77%) | **다른 담당자 소관.** 이번 작업에서 코드를 건드리지 않았다. OASYS 전체 R011 추이를 볼 때 우리 몫만 줄어도 전체는 크게 안 줄 수 있음에 유의 |
| `IF_HMG_R003` 일 약 90,000건 | 하이매직 팀 소관, 범위 밖 |
| MQTT 구독 폴링 자체의 폭증 | 2026-07-30부터 일 약 64,000건으로 증가. **펌웨어 문제이고 어쩔 수 없다고 종결됐다.** 이번 작업은 각 폴링을 싸게 만든 것이지 폴링 수를 줄인 것이 아니다 |

---

## 3. 무엇을 만들었나

### 3-1. 변경 전후 흐름

**변경 전 — 구독조회 1건당 R011 1건**

```
기기 MQTT 구독조회 (또는 앱 GET /security/v1/devices/{serial}/subscription)
  → Redis 24h 멤버십 캐시(oasys:membership) 확인 → 만료
  → R011 호출해서 ctrtNo 확보          ← 매번 외부 호출
  → R046(ctrtNo, serial) 호출 → 구독상태
```

**변경 후 — 테이블에서 ctrtNo를 꺼낸다**

```
기기 MQTT 구독조회 / 앱 구독조회 / 내부 구독조회
  → Redis 24h 멤버십 캐시(oasys:membership) 확인 → 만료
  → device_current_contract.findCtrtNo(serial)      ← R011 없이 ctrtNo 확보
       ├ callable → 그대로 사용
       └ miss/만료/non-callable → [CTRTNO_TABLE_MISS] 로그
                                  → OasysContractResolveService.resolve() (1h 캐시 → 미스면 R011)
  → 최종 non-callable 가드: 더미값이면 R046 스킵하고 "미구독" 기본값 반환
  → callMembershipStatusAndPersist: R046 호출 + 24h 멤버십 캐시 + oasys_membership_snapshot 적재
       (※ 현재계약 테이블은 여기서 절대 건드리지 않는다)
  → catch OasysClientRequestException
       └ errorCode == "OASYS_CONTRACT_NOT_FOUND" 인 경우만
            → [R011_FRESH_RETRY] 로그 → resolveFresh()로 R011 재해석
            → 값이 다르고 callable이면 정확히 1회만 R046 재시도
  → (전체는 기존 outer catch(OasysApiException | CallNotPermittedException)
      → oasys_membership_snapshot DB fallback 안에 중첩돼 있다)
```

`OasysClientRequestException`은 `BusinessException` 계열이라 바깥 `catch(OasysApiException | CallNotPermittedException)`에 걸리지 않는다(형제 관계). 그래서 inner catch가 따로 필요하다.

**R047 웹훅 수신 시 (`OasysService.subscriptSecurityMembershipInfo`)**

```
evict(serial)                        ← Redis 계약 캐시 + 현재계약 테이블 행 동시 삭제 (둘 다 best-effort)
upsertFromR047(serial, ctrtNo)       ← 최고 권위로 덮어씀. 실패하면 flag만 세우고 계속 진행
oasys_membership_snapshot upsert (source='R047')
Redis 24h 멤버십 캐시 저장
구독상태 MQTT 통보
(해지면) 보안모드 정지 명령 + 스케줄 삭제 + PIN 설정 이력 무효화
notifyS1DeviceJoin (내부에서 resolveFresh → R011)
return !contractSaveFailed           ← false면 컨트롤러가 resultCode=-1로 응답
```

### 3-2. 해석 경로 3종 (`OasysContractResolveService`)

계약정보를 얻는 방법이 세 가지로 갈라졌다. **어느 경로를 쓰는지가 곧 그 기능의 안전 등급**이다.

| 메서드 | 동작 | R011 실패 시 | 쓰는 곳 |
|---|---|---|---|
| **`resolve()`** | 1시간 Redis 캐시 우선, 미스면 `resolveFresh()`로 강등 | platform fallback | `DeviceAccessInfoService.getAccessInfo`(주소)<br>`DeviceDispatchService.searchHistoryPage`(출동이력)<br>`S1SecurityService.resolveS1DispatchStatus`·`getJoinState`<br>`KvsLiveViewService`(라이브뷰 세션 icstNo)<br>`OasysService.fetchFromOasysOrFallback`(테이블 miss 시) |
| **`resolveFresh()`** | 항상 R011 | platform 계약정보로 fallback | `S1SecurityService.requestDispatch`·`cancelDispatch`·`requestDeactivate`<br>`OasysService.applyMembership`(R045)·`notifyS1DeviceJoin`<br>`DeviceMediaService.getAppUrl`<br>`DirectMembershipService`(직접 구독 신청)<br>`OasysService`의 `[R011_FRESH_RETRY]` 재시도 |
| **`resolveFreshStrict()`** | 항상 R011, **platform fallback 없음(fail-closed)** | 예외 그대로 전파 | `OasysService.getPaymentHistory`(R021) **전용** |

> **`resolve()` 경로들의 의미가 바뀌었다.** 변경 전에는 "항상 live"였고 이제는 "최대 1시간 낡을 수 있음"이다. `KvsLiveViewService` 두 곳은 파일을 수정하지 않았지만 `resolve()`의 의미 변경으로 동작이 바뀌었다. 무효화 훅(기기 등록·해제, R047)이 실패했을 때만 이 낡음이 노출된다.

### 3-3. 왜 납입내역만 strict인가 (외부 리뷰 P1, `6482d5d`)

`getPaymentHistory`는 처음에 `resolveFresh`를 썼다. 캐시는 우회했지만 **fallback이라는 두 번째 입구가 남아 있었다** — R011이 실패하면 platform 계약번호로 내려가고, `isCallableContractNo`는 형식(`[A-Z0-9]+` + `T` 접두)만 보므로 열화된 과거 계약번호가 그대로 통과한다.

R021은 **요청에 시리얼이 없어 OASYS가 계약-기기 소유관계를 검증할 수 없다.** 그래서 낡은 계약번호가 넘어가면 **현재 사용자가 이전 계약자의 결제 이력을 오류 없이 조회**하게 된다. 변경 전에는 R021 내부에서 매번 R011을 부르고 그 실패가 그대로 전파되던 fail-closed 동작이었으므로, fallback 허용은 회귀에 해당했다.

규칙은 한 문장이다 — **검증자가 없는 호출에는 검증되지 않은 계약번호를 넘기지 않는다.** R045(가입)와 S1 출동 계열은 요청에 시리얼이 함께 들어가거나 수신자가 S1이라 대상이 아니다. 운영상 R021은 현재도 호출당 R011 1회이므로 **이 결정으로 호출량이 늘지는 않는다.**

### 3-4. 데이터

#### MySQL `security.device_current_contract` (신규)

serial(PK) / ctrt_no / source(`R011`|`R047`) / updated_at. 전문은 부록 B.

| 항목 | 내용 |
|---|---|
| **쓰는 곳 (두 곳뿐)** | `OasysContractResolveService.buildAndPersist()`의 R011 성공 분기 → `upsertFromR011`<br>`OasysService.subscriptSecurityMembershipInfo()`의 R047 핸들러 → `upsertFromR047` |
| **절대 쓰지 않는 곳** | **R046 성공.** 읽은 값을 되쓰면 `updated_at`이 밀려 30일 만료가 영원히 오지 않는 자기강화가 생긴다 |
| **테스트 시리얼** | `oasys_test_serial` 화이트리스트 기기는 테이블 오염 방지를 위해 쓰지 않는다 |
| **쓰기 권위** | R047 > R011, **Java 호출 순서가 아니라 DB 조건으로 원자 판정** |
| **읽기** | `findCtrtNo` — `updated_at > DATE_SUB(NOW(3), INTERVAL 30 DAY)` 조건이 붙어 만료 행은 miss로 떨어진다 |

쓰기 권위 구현(`DeviceCurrentContractMapper.xml`):

- `upsertAuthoritative` (R047용): `INSERT ... ON DUPLICATE KEY UPDATE`, 무조건 덮는다.
- `upsertFromR011`은 **두 문장**이다 — `insertIgnore`(행 부재 시에만 생성) + `updateIfNotProtected`(`WHERE serial=? AND (source != 'R047' OR updated_at < now - 1 HOUR)`).
- 각 문장이 개별적으로 원자적이라 R047 upsert가 두 문장 **사이에 끼어들어도** R047 값이 살아남는다. 실제 MySQL 통합 테스트로 인터리빙을 재현해 확인했다.
- 보호창 1시간의 trade-off: **R047이 아예 누락된 계약 변경만** 1시간이 지난 뒤 R011이 교정할 수 있다.

#### MySQL `security.oasys_membership_snapshot` (기존, 2026-06-01 도입)

이번 작업에서 만든 것이 아니라 **선행 작업**(`2166a0a`/`8dc949b` "[feat] 구독상태 영속캐시 추가")의 산물이다. 역할이 다르므로 혼동하지 말 것.

| 테이블 | 무엇을 답하는가 | PK | 갱신 출처 |
|---|---|---|---|
| `device_current_contract` | **"이 기기의 현재 계약번호는 무엇인가"** (계약 신원) | `serial` 단독 | R011 성공, R047 |
| `oasys_membership_snapshot` | **"이 기기가 구독 중인가"** (구독 상태) | `(serial, ctrt_no)` 복합 | R046 성공, R047 |

`oasys_membership_snapshot`은 R046이 아예 불가능할 때(OASYS 장애·서킷 오픈) 마지막으로 답을 내는 DB fallback이고, 출동이력 보유기간 파기 배치의 앵커(`expired_dt`)이기도 하다. **계약 신원 저장소로 재사용하지 않은 이유는 §6에 있다.**

#### Redis

실제 키는 `DeviceStateRedisService.buildKey`가 `security:` + attr + `:` + serial로 만든다(String 값, JSON 직렬화).

| 실제 키 | 코드상 attr | TTL | 내용 | 무효화 |
|---|---|---|---|---|
| `security:oasys:contract:{serial}` | `oasys:contract` | **1시간** | `OasysContractInfo`(icstNo·ctrtNo·userAddr1·userAddr2) | `evict()` |
| `security:oasys:membership:{serial}` | `oasys:membership` | **24시간** | `OasysMembershipStatusResult`(구독 여부·요금제 등) | **`evict()` 대상이 아니다.** R046·R047 수신 시 덮어쓰기로만 갱신된다 |

두 키가 별개라는 점이 §5의 6번(R047 처리 중단 금지)의 핵심 근거다.

#### 로그 마커

| 마커 | 의미 | 정상 기저값 |
|---|---|---|
| `[CTRTNO_TABLE_MISS]` | 현재계약 테이블에서 callable 계약번호를 못 얻어 R011로 강등 | 배포 초기엔 대량, 테이블이 채워지며 하락해야 정상. **하락하지 않으면 DDL 미반영을 의심** |
| `[R011_FRESH_RETRY]` | R046이 `CONTRACT_NOT_FOUND`를 반환해 R011 fresh 재해석 후 1회 재시도 | **상시 0이 아니다.** R047이 10분 배치로 오므로 계약 변경 직후 최대 10분간 이 경로를 타는 것이 정상. 알람을 건다면 발생 자체가 아니라 **지속 상승**에 걸어야 한다 |
| `[OASYS_R046_UNCLASSIFIED]` | R046 실패를 계약없음으로 분류하지 못함. **OASYS 응답 문구가 바뀌었다는 신호** | 0이어야 정상 |
| `[R047_CONTRACT_SAVE_FAILED]` | R047 계약번호 테이블 저장 실패. 이 경우 ack가 `resultCode=-1` | 0이어야 정상 |

계약번호는 `OasysService.mask()`로 **앞 4자만** 로그에 남는다.

### 3-5. getPaymentHistory 이중 호출 제거

`OasysPort.getPaymentHistory(String ctrtNo, String prdtQcrsNo)`로 시그니처를 바꿔, `OasysApiClient` 내부에 있던 `this.getBaseContract()` self-call을 제거했다. self-call은 **내부 R011의 독립 프록시 경계(자체 서킷브레이커 advice·화이트리스트 분기)를 우회**하는 구조였다. 이제 계약번호는 서비스 계층에서 해석해 파라미터로 넘긴다.

### 3-6. 선행 작업 — OASYS 게이트웨이 단일 통로 (`feature/oasys-gateway-refactor`, 2026-07-01)

이번 감축의 **측정 기반**을 만든 작업이라 함께 남긴다. 두 커밋 모두 dev·stg·main 반영 완료.

| 커밋 | 내용 |
|---|---|
| `98b0d67` | `OasysResponseReader` 신설 — 응답 봉투(단일/이중)를 자동 감지해 가장 안쪽 봉투를 기존 DTO로 역직렬화(2026-07-31 예정이던 전문 이중 통일 대비). `OasysApiClient`의 4개 전문(R045·R046·R011·R021)을 **단일 엔드포인트 `/api/nmx/interface` + body `interfaceid` 분기**로 통일. R011 요청에 `interfaceid` 추가, R021의 `interfaceId` → `interfaceid`(소문자) 정정 |
| `11ff2fe` | 단일 엔드포인트로 전문이 뭉쳐 로그에서 구분이 안 되는 문제 해결 — `OutboundCallContext`에 `operation`(전문 ID)을 추가하고 `OutboundApiLogInterceptor`가 `endpoint` 뒤에 `[IF_NMX_R0xx]`를 붙인다. `OasysBaseContractResponse` 필드 스펠을 실제 응답에 맞추고 누락 필드 추가 |

그 결과 `security.outbound_api_log.endpoint`에 `'/api/nmx/interface [IF_NMX_R011]'` 형태로 기록되고, **전문별 호출량을 SQL 한 줄로 셀 수 있게 됐다.** 부록 D의 측정 쿼리가 전부 여기에 기댄다. 이 라벨을 떼면 감축 효과를 다시는 측정할 수 없다.

---

## 4. 캐시·테이블 수명 규칙 (TTL · 갱신 트리거 · 무효화)

현재 코드(`origin/dev` 기준) 실측이다. 이 표가 이 작업의 전부라고 봐도 된다.

| 저장소 | 유효기간 | 채워지는 트리거 | 지워지는/덮이는 트리거 | 저장 안 하는 조건 |
|---|---|---|---|---|
| **Redis `oasys:contract`** | **TTL 1시간** | `resolveFresh()`·`resolveFreshStrict()`의 R011 **성공** 직후 (`buildAndPersist`) | `evict(serial)` → 기기 등록·해제(`DeviceCommandService.invalidatePinHis`), R047 수신 | ① **platform fallback 결과**(열화 가능) ② **icstNo 또는 ctrtNo가 빈 응답** |
| **MySQL `device_current_contract`** | 행 자체는 무기한. **조회 시 `updated_at`이 30일 초과면 miss** | ① R011 성공 → `upsertFromR011`(insertIgnore + 보호창 가드 UPDATE) ② R047 수신 → `upsertFromR047`(무조건 덮음) | `evict(serial)` → 기기 등록·해제, R047 수신(삭제 후 즉시 재기록) | ① **R046 성공(절대 금지)** ② platform fallback 결과 ③ ctrtNo 공백 ④ `oasys_test_serial` 화이트리스트 기기 |
| R011 쓰기 **보호창** | **1시간** | – | – | `source='R047'`이고 `updated_at`이 1시간 이내인 행은 R011이 덮지 못한다 |
| **Redis `oasys:membership`** | **TTL 24시간** | R046 성공, R047 수신 | 덮어쓰기만. **`evict()` 대상이 아니다** | – |
| **MySQL `oasys_membership_snapshot`** | 만료 없음 | R046 성공(`source='R046'`), R047 수신(`source='R047'`) | 덮어쓰기만(`(serial, ctrt_no)` 단위) | 화이트리스트 기기는 R046 경로에서 제외 |

**무효화(`evict`) 지점은 정확히 두 곳이다.**

| 지점 | 코드 | 순서 상의 주의 |
|---|---|---|
| 기기 등록·해제 | `DeviceCommandService.invalidatePinHis` (`:51`) | **`evict`를 PIN 이력 삭제보다 먼저** 실행한다. 호출측(`backend-api-main`)이 이 API의 오류를 삼키고 기기 등록·해제를 계속 진행하기 때문에, PIN 삭제를 먼저 했다가 실패하면 evict가 아예 실행되지 않는다. 호출측이 등록·해제 **양쪽**에서 이 API를 부르는 것은 확인했다 |
| R047 웹훅 수신 | `OasysService.subscriptSecurityMembershipInfo` (`:217`) | `evict` → `upsertFromR047` 순. 그리고 이 메서드는 `DeviceCommandService.invalidatePinHis`를 쓰지 않고 **`PinHistoryPort`를 직접** 호출한다 — 그 메서드가 내부에서 `evict`를 부르는데, 그러면 바로 위에서 확정한 R047 계약번호를 되돌려 지우기 때문이다 |

두 evict는 모두 best-effort다(`SafeRunner`). 실패해도 예외가 전파되지 않으며, 그 경우의 복구는 R046 소유관계 검증 → `[R011_FRESH_RETRY]` 경로와 30일 만료가 담당한다.

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다. 나머지는 코드 주석과 테스트가 지킨다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **R046 성공은 `device_current_contract`를 절대 쓰지 않는다** | R046이 읽은 값을 되쓰면 `updated_at`이 매번 갱신돼 **30일 만료가 영원히 오지 않는다**(자기강화). 그러면 구계약 행이 스스로 최신화되며 영구 고착된다. 이 불변식과 30일 만료는 **둘 다 있어야** 성립한다 — 한쪽만 남기면 의미가 없다 |
| 2 | **R011 쓰기는 두 문장(`insertIgnore` + 보호창 가드 `UPDATE`)이고, 권위 판정은 Java가 아니라 DB `WHERE` 조건이 한다** | 한 문장 upsert로 "단순화"하면 OASYS 전파 지연 중 완료된 stale R011이 R047 신계약을 덮는다. ECS 다중 태스크라 Java 호출 순서에 기댈 수 없다. MySQL 통합 테스트가 인터리빙을 재현해 고정하고 있다 |
| 3 | **`getPaymentHistory`만 `resolveFreshStrict`(platform fallback 없음)를 쓴다** | R021은 요청에 시리얼이 없어 OASYS가 소유관계를 검증할 수 없다. fallback을 허용하면 열화·과거 계약번호가 형식만 맞으면 통과해 **이전 계약자의 결제 이력이 오류 없이 반환된다.** 검증자가 없는 호출에는 검증되지 않은 계약번호를 넘기지 않는다 |
| 4 | **platform fallback 결과와 식별자 결손 응답은 캐시·테이블에 저장하지 않는다** | 결손(icstNo 또는 ctrtNo 공백)을 캐시하면, 매 호출 재조회로 **자가치유되던 것이 TTL 1시간 내내 고착된다**(납입내역은 1시간 내내 `OASYS_INVALID_CONTRACT`, 멤버십은 1시간 내내 "미구독" 오답) |
| 5 | **`resolveFresh`의 `try` 범위는 R011 본 호출로만 좁혀져 있다** | 성공 후 보조 작업(캐시 저장·테스트시리얼 판별·테이블 적재)까지 `try`에 넣으면, 그 실패가 **정상 OASYS 결과를 platform fallback으로 뒤집는다.** 보조 작업은 전부 `SafeRunner` best-effort다 |
| 6 | **R047 처리에서 계약번호 저장이 실패해도 중단하지 않는다.** 부수효과(구독상태 통보·보안모드 정지·스케줄 삭제·PIN 이력 무효화·S1 통보)를 전부 수행한 뒤 `false`를 반환해 `resultCode=-1`로 응답한다 | OASYS는 `-1`에 **재전송하지 않으므로**(확인됨) 중단은 되돌릴 수 없는 유실이 된다. 중단하면 해지 통보에서 보안모드 정지·스케줄 삭제가 누락돼 **해지된 기기가 무장 상태로 남고**, 24시간 멤버십 캐시(`oasys:membership` — 별도 키라 evict 대상이 아니다)도 갱신되지 않아 **하루 동안 "구독 중"을 답한다.** 부수효과는 전부 멱등이다. `resultCode=-1`은 복구 트리거가 아니라 양측 로그에 남는 실패 신호일 뿐이고, 실제 데이터 교정은 R046 검증 → `[R011_FRESH_RETRY]`가 담당한다 |
| 7 | **`evict`를 PIN 이력 삭제보다 먼저 실행한다** (`DeviceCommandService.invalidatePinHis`) | 호출측이 이 API의 오류를 삼키고 기기 등록·해제를 계속 진행한다. PIN 삭제를 먼저 뒀다가 실패하면 **계약 캐시·테이블 무효화가 아예 실행되지 않아** 새 계약자에게 이전 계약자의 계약번호·주소가 남는다 |
| 8 | **`OasysService.subscriptSecurityMembershipInfo`는 `DeviceCommandService.invalidatePinHis`가 아니라 `PinHistoryPort`를 직접 쓴다** | 그 메서드는 내부에서 `evict`를 부르고, `evict`는 현재계약 테이블 행까지 지운다. 바로 위에서 `upsertFromR047`로 확정한 R047 계약번호가 **되돌려 지워진다.** "중복 제거"로 묶으면 여기서 깨진다 |
| 9 | **`findCtrtNo`의 만료 판정에 `NOW(3)`(DB 시계)을 쓴다** | 애플리케이션 시각을 쓰면 ECS 다중 태스크 간 시계 편차가 만료 판정에 섞인다 |
| 10 | **`[R011_FRESH_RETRY]`는 정확히 1회만 재시도하고, 값이 같으면 재시도하지 않는다** | 무제한 재시도는 OASYS 장애 시 증폭이 되고, 같은 값 재시도는 확실한 낭비다. 재시도 후에도 실패하면 원래 예외를 그대로 던져 outer catch의 DB 스냅샷 fallback으로 내려간다 |
| 11 | **설정 플래그(`contract-source-mode`)·1% 표본감사·로그 알람을 다시 넣지 않는다** | 사용자가 명시적으로 제거를 지시한 항목이다. **알람을 운영하지 않기로 했으므로 안전장치는 전부 코드가 스스로 실행하는 것**(R046 검증에 의한 교정, 30일 만료, 실패해도 수행되는 해지 안전조치)이어야 한다는 전제 위에 설계가 서 있다. 롤백 수단은 플래그가 아니라 **코드 revert + ECS 재배포**다 |

---

## 6. 검토하고 기각한 대안 (다시 제안하지 말 것)

| 대안 | 기각 이유 |
|---|---|
| **캐시만 강화하고 테이블은 안 만든다** | 60.4% 구간은 **24시간 멤버십 캐시 만료가 트리거**라 1시간 계약 캐시가 절대 히트하지 않는다. 감축 효과 약 0%. 테이블이 아니면 이 구간을 건드릴 수 없다 |
| **기존 `oasys_membership_snapshot`을 계약 신원 저장소로 재사용** | ① R046이 쓰는 테이블이라 만료 기반 검증을 붙이면 §5의 1번 자기강화가 그대로 발생한다. ② PK가 `(serial, ctrt_no)`라 시리얼당 다중 행이고 조회가 "구독 중 우선, 없으면 최신"이라 **계약 신원이 아니라 구독 상태를 답하는 구조**다 |
| **platform의 `contractNo`를 그대로 쓴다** | 열화 데이터일 수 있고 더미값(`T202ZEA19458` 등)이 섞인다. 그래서 코드도 fallback 결과를 캐시·테이블에 저장하지 않는다 |
| **Redis 전용 7일 키로만 보유** | Redis 유실 시 fleet 규모의 R011 스톰이 발생한다. 내구성이 부족하다 |
| **push-authoritative 멤버십** (스냅샷에서 구독상태를 직접 서빙해 R046까지 감축) | 기술적으로 타당하고 더 큰 감축이지만, 구독 상태를 "확인형"에서 "신뢰형"으로 바꾸는 큰 결정이라 이번 범위 밖으로 뒀다. **후속 후보로 가치 있음**(§8-3) |

---

## 7. 배포 방법

### 7-1. 순서 (역순 금지)

```
1. DDL 적용:  security.device_current_contract          ← 반드시 먼저
2. ECS 배포:  skix-security 애플리케이션
3. 측정:      배포 후 1~2일 R011 추이 관찰 (부록 D)
```

**1이 2보다 먼저여야 하는 이유**: 테이블이 없어도 서비스는 정상 동작한다(`SafeRunner`가 삼킨다). 대신 **전부 miss로 강등돼 감축 효과가 0**이고, 오류가 나지 않으므로 아무도 모른다. 반대로 테이블만 있고 애플리케이션이 구버전이면 아무 일도 일어나지 않는다(누구도 안 쓰는 빈 테이블).

skix-security 단일 저장소 변경이고 **다른 백엔드와의 API 계약은 바뀌지 않았다.** `backend-api-main`·`skix-streaming` 배포 순서와 무관하다.

### 7-2. 배포 후 검증

1. **테이블이 채워지기 시작하는지** — 배포 직후에는 비어 있고, 조회가 들어오며 R011 성공분이 적재된다.
   ```sql
   SELECT source, COUNT(*) FROM security.device_current_contract GROUP BY source;
   ```
   배포 1시간 뒤에도 0행이면 DDL은 들어갔는데 **애플리케이션이 구버전**이거나 화이트리스트 기기만 호출되고 있는 것이다.

2. **R011 일별 추이** — 부록 D-1. 테이블이 채워지며 **1~2일에 걸쳐 점진 하락 후 안정**되는 모양이 정상이다. 계단식 즉시 하락은 나오지 않는다(24시간 멤버십 캐시 만료 주기 때문).

3. **R046 건수는 변하지 않아야 한다** — 부록 D-2. R046이 함께 줄었다면 구독조회가 어딘가에서 죽고 있는 것이다.

4. **`[CTRTNO_TABLE_MISS]` 하락 추세** — 부록 D-4. 하락하지 않으면 테이블 쓰기가 안 되고 있다.

5. **기능 회귀 스모크** (운영계에서는 실제 출동·실제 접수가 발생하는 호출은 하지 말 것)
   - `GET /security/v1/devices/{serial}/subscription` — 구독상태가 이전과 같이 나오는지
   - `GET /security/v1/devices/{serial}/subscription/payment/history` — 납입내역. **strict 전환 후 회귀 위험이 가장 큰 지점**. R011이 실패하면 이제 fail-closed로 함께 실패한다
   - 기기 등록·해제 후 `device_current_contract`에서 해당 serial 행이 사라지는지

### 7-3. 롤백

- **코드 revert + ECS 재배포.** 설정 플래그는 의도적으로 두지 않았다(§5의 11번).
- **테이블은 삭제하지 않는다.** 구버전 코드가 참조하지 않으므로 남아 있어도 무해하고, 다시 배포할 때 재사용된다.
- Redis 키(`oasys:contract`)도 구버전이 읽지 않으므로 방치해도 TTL 1시간 뒤 사라진다.

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **운영 DB에 `security.device_current_contract`가 실제로 있는지 확인**하고 없으면 적용 | 백엔드 | 부록 B의 DDL, 부록 C-1의 존재 확인 쿼리. 개발계·검증계도 같이 확인. DB는 VDI에서만 접근 가능 |
| 2 | **R011 감축이 실제로 일어났는지 측정** | 백엔드 | 부록 D. 8월 초(배포 전) 대비 현재. 목표 일 350~800건 |
| 3 | 3환경 ECS에 8/6 이후 이미지가 배포됐는지 확인 | 백엔드 | VDI에서 태스크 정의 이미지 태그·실행 중 리비전 확인 |
| 4 | `./gradlew clean test` 재실행으로 현재 테스트 상태 확인 | 백엔드 | 작업 시점 336건 통과. 이후 다른 작업이 쌓였으므로 수치는 달라진다 |

### 8-2. 릴리스 게이트 — 없음

이 작업은 이미 운영 브랜치에 있고 앱·프론트 의존이 없다. 별도 게이트는 없다. 다만 §8-1의 1번이 안 되어 있으면 **운영에 나가 있는 코드가 아무 효과를 못 내고 있는 상태**이므로 사실상 미완이다.

### 8-3. 후속 (이 기능을 막지는 않음)

| # | 항목 |
|---|---|
| 5 | **`isCallableContractNo`의 `startsWith("T")` 게이트가 실계약과 모순된다.** 이 게이트는 R045·R046·R021 호출 전 필터이자 `[CTRTNO_TABLE_MISS]` 판정에도 쓰인다. 그런데 운영 계약번호 실측 결과 **실계약 접두는 `T` 외에 `M`·`F`가 있고, `G` 접두(예: `G257GB1065*`)도 고객번호·계약자가 실재하는 실계약으로 확인됐다.** 즉 T가 아닌 실계약 기기는 계약번호가 정상인데도 non-callable로 떨어져 **R046을 아예 건너뛰고 "미구독" 기본값을 받는다.** 게이트는 2026-06-23 `0c46696`에서 더미 차단 목적으로 넣은 것이고, 올바른 방향은 형식 스니핑이 아니라 **더미 여부를 데이터로 판별**하는 것이다(`backend-api-main`의 계약번호 정리 작업에서 `contract_type` 컬럼 도입이 검토 중). 조치 전에 운영에서 T가 아닌 계약번호를 가진 기기가 몇 대인지부터 세야 한다 |
| 6 | **push-authoritative 멤버십** — `oasys_membership_snapshot`에서 구독상태를 직접 서빙해 R046까지 줄인다. 구독 상태를 "확인형"에서 "신뢰형"으로 바꾸는 결정이라 별도 판단이 필요하지만, 감축 폭이 가장 큰 후속안이다 |
| 7 | **fresh R011마다 `oasys_test_serial`을 두 번 조회한다.** `OasysWhitelistClient`가 이미 조회한 뒤 `buildAndPersist`의 `isTestSerial`이 또 조회한다. 정확성 문제는 아니라 고치지 않았다 |
| 8 | **`OasysService.applyMembership`의 `log.warn`이 계약번호를 마스킹 없이 남긴다.** 이번 작업 이전부터 있던 코드라 손대지 않았다. 같은 클래스의 `mask()`를 적용하면 된다 |
| 9 | 통합 테스트 2개(`DeviceCurrentContractRepositoryTest`, `OasysContractInfoRedisRoundTripTest`)가 `com.skix.security.kvs` 패키지에 있다. Testcontainers 지원 클래스(`MySqlContainerSupport`, `RedisContainerSupport`)가 거기 있기 때문이고, 옮기려면 다른 사람 작업인 KVS 파일을 건드려야 해서 두었다 |
| 10 | 이상 기기 `WRBA1M10KRNGJ0125I00009` — 일 389건으로 단일 기기 최다 호출. 개별 조사 미완. 부록 D-3의 상위 시리얼 쿼리로 현재도 그런지 먼저 확인할 것 |
| 11 | MQTT 구독 폴링 자체가 2026-07-30부터 일 약 64,000건으로 폭증했다. 펌웨어 문제로 종결됐으나, 폴링 수 자체를 줄이는 것이 근본 대책이다 |

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| **"계약을 옮겼는데 구독이 안 보여요"** | 정상 지연일 수 있다. R047이 10분 배치라 **계약 변경 후 최대 10분간 테이블이 구계약을 들고 있다.** 그동안은 R046이 `CONTRACT_NOT_FOUND`로 튕기고 `[R011_FRESH_RETRY]`가 즉시 교정한다. 10분이 지나도 안 되면 아래 행들을 본다 |
| **특정 기기의 현재 상태 보기** | `SELECT serial, ctrt_no, source, updated_at FROM security.device_current_contract WHERE serial = '<serial>';` |
| **`[R011_FRESH_RETRY]`가 보인다** | **정상 기저값이 있다.** 계약 변경 직후 10분간 발생하는 것이 설계된 동작이다. 문제는 발생 자체가 아니라 **지속적인 상승**이다 |
| **`[OASYS_R046_UNCLASSIFIED]`가 보인다** | **OASYS 응답 문구가 바뀐 신호다.** 계약없음 판별이 응답 문구 substring(`"조건에 맞는 계약번호가 없습니다"`, `OasysApiClient.toMembershipStatusResult`)에 의존하므로, 문구가 바뀌면 `CONTRACT_NOT_FOUND` 분류가 죽고 자가교정이 멈춘다. 이때 남는 복구 수단은 30일 만료뿐이다. **문구를 확인해 코드의 판별 조건을 갱신할 것** |
| **`[CTRTNO_TABLE_MISS]`가 줄지 않는다** | ① 테이블이 없다(DDL 미반영) ② 애플리케이션이 구버전 ③ 해당 기기 계약번호가 non-callable(§8-3의 5번, `T` 접두 게이트) ④ 화이트리스트 테스트 기기. 순서대로 확인 |
| **`[R047_CONTRACT_SAVE_FAILED]`가 보인다** | DB 쓰기 실패다. 그 R047은 `resultCode=-1`로 ack됐고 **OASYS는 재전송하지 않는다.** 다만 보안모드 정지·스케줄 삭제 등 안전 조치는 이미 수행됐다. 해당 serial의 계약 데이터는 다음 R046 호출 시 `[R011_FRESH_RETRY]`가 교정한다 |
| **"납입내역이 안 나와요"** | strict 경로라 R011이 실패하면 함께 실패한다(의도된 fail-closed). `outbound_api_log`에서 해당 시각 `[IF_NMX_R011]` 행의 `http_code`·`response_body`를 본다. 계약번호가 non-callable이면 `OASYS_INVALID_CONTRACT`를 던진다 |
| **"구독 중인데 미구독으로 나와요"** | ① 계약번호가 non-callable이라 R046을 건너뛰고 미구독 기본값을 반환한 경우(로그 `Skipping R046 for serial=...`) ② R046 장애로 `oasys_membership_snapshot` fallback이 낡은 값을 답한 경우. 로그와 스냅샷 `synced_at`을 함께 본다 |
| **OASYS 없이 흐름 테스트** | `oasys_test_serial`에 등록된 시리얼은 `OasysWhitelistClient`(`@Primary`)가 스텁 응답을 준다. **이 기기들은 `device_current_contract`에 적재되지 않는다**(의도) |
| **캐시를 강제로 비우고 싶다** | Redis `security:oasys:contract:{serial}` 키와 `device_current_contract` 해당 행을 **둘 다** 지운다(`DEL security:oasys:contract:<serial>`, `DELETE FROM security.device_current_contract WHERE serial='<serial>'`). 하나만 지우면 다른 쪽이 낡은 값을 계속 답한다. 구독상태까지 다시 받게 하려면 `security:oasys:membership:{serial}`도 지운다 |

---

## 부록 A. 핵심 커밋 (시간순)

| 날짜 | 커밋 | 브랜치 | 내용 | dev/stg/main |
|---|---|---|---|---|
| 2026-06-01 | `8dc949b`, `2166a0a` | – | 구독상태 영속캐시(`oasys_membership_snapshot`) 추가. 이번 작업의 선행 자산 | 반영 |
| 2026-06-23 | `0c46696` | `feature/oasys-gateway-refactor` | R045·R046 계약번호 검증 일원화 — `T` 접두 규칙 추가, R045를 ResolveService 경유로 | 반영 (※ 이 게이트가 §8-3의 5번 이슈) |
| 2026-07-01 | `98b0d67` | `feature/oasys-gateway-refactor` | OASYS 호출 게이트웨이 통일(`/api/nmx/interface` 단일 엔드포인트) + 응답 봉투 자동 언래핑 | 반영 |
| 2026-07-01 | `11ff2fe` | `feature/oasys-gateway-refactor` | 아웃바운드 로그에 전문 구분 라벨(`[IF_NMX_R0xx]`) 추가 + R011 응답 DTO 필드 정합성 | 반영 |
| 2026-08-03 | `4942d5f` | `feature/oasys-r011-reduction` | **R011 호출량 감축 본체** — 현재계약 테이블·1시간 계약정보 캐시·쓰기 권위·30일 만료·R046 경로 재구성·무효화 훅·이중 호출 제거 | 반영 |
| 2026-08-03 | `6482d5d` | `feature/oasys-r011-reduction` | **외부 리뷰 P1** — 납입내역 계약번호 해석에서 platform fallback 차단(`resolveFreshStrict`) | 반영 |
| 2026-08-04 | `d8a31ef` | → `dev` | `feature/oasys-r011-reduction` 머지 | – |
| 2026-08-04 | `6e26363` | → `stg` | dev → stg 승격 | – |
| 2026-08-06 | `5edda72` | → `main` | stg → main 승격 (**운영 브랜치 반영 시점**) | – |

`4942d5f`가 건드린 파일은 21개(운영 코드 13, 테스트 8), `6482d5d`는 4개다. 신규 테스트 파일:

- `OasysContractResolveServiceTest` — 캐시 히트/미스, fallback 미저장, 결손 응답 미저장, strict 경로
- `OasysServiceMembershipStatusTest` — 테이블 우선 조회, `[CTRTNO_TABLE_MISS]` 강등, `CONTRACT_NOT_FOUND` 1회 재시도
- `OasysServicePaymentHistoryTest` — strict fail-closed
- `S1SecurityServiceResolveFreshTest` — 출동 계열이 캐시를 우회하는지
- `DeviceCommandServiceUndockTest` — evict 선행 순서
- `InternalOasysControllerSubscriptionAckTest` — `resultCode` 0/-1 분기
- `kvs/DeviceCurrentContractRepositoryTest` — **실제 MySQL 8.0** Testcontainers. 쓰기 권위·문장 단위 인터리빙·30일 만료 13건
- `kvs/OasysContractInfoRedisRoundTripTest` — **실제 Redis** record round-trip 3건

---

## 부록 B. DDL

저장소 경로: `src/main/resources/db/device_current_contract.sql`. **이 테이블은 `db-schema` 저장소에 없다** — `db-schema`는 `backend-api-main`의 플랫폼 DB 덤프이고, skix-security의 `security` 스키마 DDL은 각 테이블 파일이 유일한 원본이다.

```sql
CREATE TABLE `security`.`device_current_contract` (
    `serial`     varchar(64) NOT NULL COMMENT '디바이스 시리얼',
    `ctrt_no`    varchar(50) NOT NULL COMMENT '현재 메인 계약번호',
    `source`     varchar(8)  NOT NULL COMMENT '마지막 갱신 출처 R011 / R047',
    `updated_at` datetime(3) NOT NULL COMMENT 'UTC - 마지막 갱신 시각',
    -- 계약 식별을 단언하는 소스(R011 조회 성공, R047 웹훅)만 이 테이블을 갱신한다.
    -- R046(구독상태 조회) 성공은 "그 계약번호가 유효하다"는 검증일 뿐 "현재 계약"의 식별이
    -- 아니므로 절대 쓰지 않는다 — 구계약 행이 스스로 최신화되는 자기강화를 막기 위함.
    PRIMARY KEY (`serial`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='serial별 현재 계약번호 (R046용 R011 대체 조회처)';
```

인덱스는 PK 하나뿐이다. 조회가 전부 `WHERE serial = ?` 단건이고 스캔 쿼리가 없어서 그 이상이 필요 없다.

**함께 쓰이는 기존 테이블** (이번 작업에서 만들지 않았다. 없으면 별도 확인 필요)

```sql
-- security.oasys_membership_snapshot : R046 불가 시 구독상태 DB fallback + 출동이력 파기 앵커
--   PK (serial, ctrt_no), source 'R046' | 'R047' (코드 주석의 'PUSH'는 실제로 'R047'로 기록된다)
-- security.oasys_test_serial          : 화이트리스트 테스트 기기 (스텁 응답 + 테이블 적재 제외)
-- security.outbound_api_log           : 아웃바운드 호출 이력. 부록 D 측정의 기반
```

`security.outbound_api_log`의 시각 컬럼 `called_at`은 **UTC**다(`UtcDateTimes.now()`). 부록 D의 쿼리들이 `CONVERT_TZ`로 KST 환산하는 이유다.

---

## 부록 C. 정합성 검증 SQL

전부 `security` 스키마 기준이다. DB 접속은 VDI에서만 가능하다.

**C-1. 테이블 존재·구조 확인 (첫날 1번 항목)**

```sql
SELECT table_name, table_rows, create_time
  FROM information_schema.tables
 WHERE table_schema = 'security'
   AND table_name = 'device_current_contract';
-- 0행이면 DDL 미반영. 부록 B를 적용한다.

SHOW CREATE TABLE security.device_current_contract;
-- PK가 serial 단독인지, updated_at 이 datetime(3) 인지 확인.
```

**C-2. 적재 현황 — 출처별·신선도별**

```sql
SELECT source,
       COUNT(*)                                                              AS rows_total,
       SUM(updated_at > NOW(3) - INTERVAL 30 DAY)                            AS fresh_usable,
       SUM(updated_at <= NOW(3) - INTERVAL 30 DAY)                           AS expired_ignored,
       MIN(updated_at) AS oldest, MAX(updated_at) AS newest
  FROM security.device_current_contract
 GROUP BY source;
```

`expired_ignored`가 계속 늘어나면 그 기기들이 30일째 한 번도 조회되지 않았다는 뜻이다(정상일 수 있다 — 유휴 기기).

**C-3. 불변식 위반 탐지 — 규칙상 나오면 안 되는 행**

```sql
-- (a) source 가 R011/R047 이외의 값을 갖는 행. R046 이 쓴 흔적이면 §5의 1번 위반이다.
SELECT * FROM security.device_current_contract
 WHERE source NOT IN ('R011','R047');

-- (b) callable 규칙(대문자·숫자 + T 접두 + 더미 제외)을 못 넘는 계약번호가 적재된 행.
--     적재 자체는 막지 않으므로 존재할 수 있으나, 이 행들은 조회돼도 R046 을 스킵한다.
--     §8-3 의 5번(T 접두 게이트) 영향 규모를 세는 쿼리이기도 하다.
SELECT serial, ctrt_no, source, updated_at
  FROM security.device_current_contract
 WHERE ctrt_no NOT REGEXP '^[A-Z0-9]+$'
    OR ctrt_no NOT LIKE 'T%'
    OR ctrt_no = 'T202ZEA19458'
 ORDER BY updated_at DESC;

-- (c) 화이트리스트 테스트 기기가 적재된 행. 있으면 안 된다(§3-4).
SELECT c.* FROM security.device_current_contract c
  JOIN security.oasys_test_serial t ON t.serial = c.serial;

-- (d) 미래 시각 행. 시계 이상 또는 잘못된 수기 수정.
SELECT * FROM security.device_current_contract WHERE updated_at > NOW(3) + INTERVAL 5 MINUTE;
```

**C-4. 구독 스냅샷과의 계약번호 대사**

두 테이블이 같은 기기에 대해 서로 다른 계약번호를 들고 있는 경우를 찾는다. 계약 이전 직후에는 정상적으로 발생하지만, **오래 지속되면** 한쪽이 고착된 것이다.

```sql
SELECT c.serial,
       c.ctrt_no  AS current_ctrt_no, c.source, c.updated_at,
       s.ctrt_no  AS snapshot_ctrt_no, s.source AS snap_source, s.is_subscribed, s.synced_at
  FROM security.device_current_contract c
  JOIN security.oasys_membership_snapshot s
    ON s.serial = c.serial
   AND s.ctrt_no <> c.ctrt_no
 WHERE s.is_subscribed = 1              -- 활성 계약인데 현재계약과 다르다
   AND s.synced_at > NOW(3) - INTERVAL 7 DAY
 ORDER BY c.updated_at;
```

**C-5. 보호창이 실제로 동작하는지 — R047 직후 R011이 덮지 않았는지**

R047 수신 시각은 `oasys_membership_snapshot`의 `source='R047'` 행 `synced_at`에 남는다. 이를 대용으로 쓴다.

```sql
-- R047 수신 후 1시간 이내에 R011 이 다른 계약번호로 현재계약을 덮은 흔적.
-- 보호창이 동작하면 이 조합은 나올 수 없다. 나오면 updateIfNotProtected 의 WHERE 가드를 의심.
SELECT c.serial,
       c.ctrt_no    AS current_ctrt_no, c.updated_at AS r011_at,
       s.ctrt_no    AS r047_ctrt_no,    s.synced_at  AS r047_at
  FROM security.device_current_contract c
  JOIN security.oasys_membership_snapshot s
    ON s.serial = c.serial
   AND s.source = 'R047'
 WHERE c.source = 'R011'
   AND s.ctrt_no <> c.ctrt_no
   AND c.updated_at >  s.synced_at
   AND c.updated_at <  s.synced_at + INTERVAL 1 HOUR
 ORDER BY c.updated_at DESC
 LIMIT 50;
```

> 근사치다. 스냅샷 행은 같은 `(serial, ctrt_no)`로 이후 R046이 오면 `source='R046'`으로 덮이므로 R047 흔적이 사라질 수 있다(놓침은 있어도 오탐은 드물다). 보호창의 정확성 자체는 `DeviceCurrentContractRepositoryTest`가 실제 MySQL로 문장 단위 인터리빙을 재현해 고정하고 있다.

**C-6. 기기 등록·해제 후 무효화가 됐는지 (수동 확인)**

```sql
-- 등록/해제 직후 실행. 행이 남아 있으면 evict 가 실행되지 않은 것이다(§5의 7번).
SELECT * FROM security.device_current_contract WHERE serial = '<serial>';
```

---

## 부록 D. 호출량 측정

### D-0. 무엇에 기대는가

- **소스**: `security.outbound_api_log`. `OutboundApiLogInterceptor`가 모든 아웃바운드 호출을 적재한다.
- **전문 구분**: `endpoint` 컬럼에 `'/api/nmx/interface [IF_NMX_R011]'` 형태로 기록된다. 게이트웨이 단일 통로 전환(§3-6) 이후의 형식이므로 **2026-07-01 이전 데이터와는 endpoint 문자열이 다르다.**
- **시각**: `called_at`은 **UTC**다.
- **인덱스**: `idx_endpoint_called_at (endpoint, called_at)`, `idx_target_called_at (target, called_at)`. **`LIKE '%...%'`는 인덱스를 못 탄다** — 등호 비교를 쓰고, 기간을 반드시 좁힐 것. 운영 테이블은 크다.

### D-1. R011 일별 추이 (KST 기준) — 핵심 지표

```sql
SELECT DATE(CONVERT_TZ(called_at, '+00:00', '+09:00')) AS kst_day,
       COUNT(*)                                        AS r011_calls,
       SUM(http_code >= 400 OR http_code IS NULL)      AS failures
  FROM security.outbound_api_log
 WHERE endpoint = '/api/nmx/interface [IF_NMX_R011]'
   AND called_at >= UTC_TIMESTAMP() - INTERVAL 45 DAY
 GROUP BY 1
 ORDER BY 1;
```

**판정 기준**

| 관측 | 해석 |
|---|---|
| 일 350~800건 | 목표 달성 |
| 일 2,000~4,400건이 유지 | 변경이 효과를 못 내고 있다. DDL 미반영 또는 구버전 이미지 |
| 배포일 기준 1~2일 점진 하락 후 안정 | 정상 모양. 24시간 멤버십 캐시 만료 주기 때문에 계단식 즉시 하락은 나오지 않는다 |
| 하락했다가 다시 상승 | 30일 만료가 몰려 도는 주기이거나, evict가 과하게 불리고 있다 |

배포 전후 비교는 **2026-08-06 이전 주(8/01~08/05 부근)와 현재 주**를 대조한다.

### D-2. 전문별 구성 — R046이 함께 줄지 않았는지

```sql
SELECT DATE(CONVERT_TZ(called_at, '+00:00', '+09:00')) AS kst_day,
       endpoint,
       COUNT(*) AS calls
  FROM security.outbound_api_log
 WHERE target = 'OASYS'
   AND called_at >= UTC_TIMESTAMP() - INTERVAL 14 DAY
 GROUP BY 1, 2
 ORDER BY 1, 3 DESC;
```

R011은 줄고 **R046은 변하지 않아야** 정상이다. R046이 함께 줄었으면 구독조회 자체가 어딘가에서 실패하고 있는 것이다.

### D-3. R011을 많이 부르는 시리얼 상위

```sql
SELECT serial, COUNT(*) AS r011_calls
  FROM security.outbound_api_log
 WHERE endpoint = '/api/nmx/interface [IF_NMX_R011]'
   AND called_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY
 GROUP BY serial
 ORDER BY r011_calls DESC
 LIMIT 20;
```

특정 시리얼이 압도적이면 그 기기가 테이블에 적재되지 못하고 있다는 뜻이다 — non-callable 계약번호(§8-3의 5번), 화이트리스트 등록, 또는 반복 실패를 의심한다.

### D-4. 호출 유발 원인 귀속 (fanout 함정 회피)

각 R011 호출 **직전**의 트리거 한 건만 상관 서브쿼리로 붙인다. **`JOIN ... ON 시간범위` 형태로 쓰면 한 R011이 여러 트리거에 매칭돼 건수가 부풀려진다** — 초기 분석에서 실제로 겪은 오류다.

```sql
SELECT trigger_kind, COUNT(*) AS r011_calls
FROM (
  SELECT o.id,
         COALESCE(
           (SELECT CONCAT('HTTP ', i.endpoint)
              FROM security.inbound_api_log i
             WHERE i.serial = o.serial
               AND i.called_at <= o.called_at
               AND i.called_at >  o.called_at - INTERVAL 10 SECOND
             ORDER BY i.called_at DESC
             LIMIT 1),
           (SELECT CONCAT('MQTT ', m.message_type)
              FROM security.mqtt_subscribe_log m
             WHERE m.serial = o.serial
               AND m.received_at <= o.called_at
               AND m.received_at >  o.called_at - INTERVAL 10 SECOND
             ORDER BY m.received_at DESC
             LIMIT 1),
           'UNATTRIBUTED'
         ) AS trigger_kind
    FROM security.outbound_api_log o
   WHERE o.endpoint = '/api/nmx/interface [IF_NMX_R011]'
     AND o.called_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY
) t
GROUP BY trigger_kind
ORDER BY r011_calls DESC;
```

변경 전에는 `MQTT REQUEST_SUBSCRIPTION_STATUS`가 60.4%를 차지했다. 변경 후 이 항목이 상위에서 사라져야 한다. `UNATTRIBUTED`가 많으면 스케줄러·내부 배치에서 온 호출이거나 시리얼이 기록되지 않은 경로다.

### D-5. 로그 마커 — CloudWatch Logs Insights

애플리케이션 로그는 ECS 태스크의 CloudWatch 로그 그룹으로 나간다. **로그 그룹 이름은 이 문서 작성 시점에 확인하지 못했다(로컬 맥에서 AWS 접근 불가).** VDI에서 skix-security 태스크 정의의 `logConfiguration.options.awslogs-group` 값을 읽어 `<skix-security 로그 그룹>` 자리에 넣는다.

```
# 마커별 발생량 일별 추이
fields @timestamp, @message
| filter @message like /\[CTRTNO_TABLE_MISS\]|\[R011_FRESH_RETRY\]|\[OASYS_R046_UNCLASSIFIED\]|\[R047_CONTRACT_SAVE_FAILED\]/
| parse @message /\[(?<marker>CTRTNO_TABLE_MISS|R011_FRESH_RETRY|OASYS_R046_UNCLASSIFIED|R047_CONTRACT_SAVE_FAILED)\]/
| stats count() as hits by marker, bin(1d)
| sort bin(1d) desc, hits desc
```

```
# 특정 시리얼의 계약 해석 흐름 추적
fields @timestamp, @message
| filter @message like /<serial>/
| filter @message like /CTRTNO_TABLE_MISS|R011_FRESH_RETRY|Skipping R046|Oasys R011 lookup failed|Current contract/
| sort @timestamp asc
| limit 200
```

```
# R046 응답 문구 드리프트 감시 — 0이어야 정상
fields @timestamp, @message
| filter @message like /\[OASYS_R046_UNCLASSIFIED\]/
| sort @timestamp desc
| limit 50
```

판정 기준은 §3-4의 로그 마커 표와 같다. `[CTRTNO_TABLE_MISS]`는 배포 직후 대량 → 하락이 정상이고, `[R011_FRESH_RETRY]`는 낮은 기저값을 갖는 것이 정상이며 **지속 상승**만이 이상 신호다.
