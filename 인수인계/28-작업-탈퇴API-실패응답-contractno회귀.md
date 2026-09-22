# [작업] 회원 탈퇴 실패 응답과 연동 해제 이력 contract_no 회귀

| 항목 | 내용 |
|---|---|
| **상태** | 서버 수정 완료 · **dev·stg 브랜치 반영, 운영계(`main`) 미반영** · 실제 서버 배포 여부 미확인 · iOS 앱 수정 `develop` 반영(스토어 미릴리스) |
| **작업 기간** | 2026-09-02 ~ 2026-09-03 (원인이 유입된 것은 2026-06-01) |
| **직접 수정한 저장소** | `backend-api-main` |
| **다른 담당자가 수정한 것** | `benjaminios`(iOS 담당이 직접 수정, 2026-09-02). Android는 수정 불필요 — 근거는 §7-2 |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §9-1의 1번(운영 이력 오염 규모 SQL 실행 — 쿼리는 부록 B)과 2번(개발계·검증계 실제 배포 여부 확인) |

---

## 0. 세 줄 요약

1. 회원 탈퇴 API가 **내부 처리가 실패해도 HTTP 200 "회원 탈퇴 되었습니다"를 돌려주고 있었다.** 실패를 500 + `success=false`로 바꿨고, 그 김에 실패를 가리고 있던 진짜 원인(아래 2번)이 드러났다.
2. 진짜 원인은 **연동 해제 이력(`history_control`) 적재 SQL의 회귀**다. 2026-06-01 커밋이 계약번호 서브쿼리에 `linked_yn = 'Y'` 조건을 붙였는데, 이 INSERT는 연동을 해제한 **직후**에 돌기 때문에 서브쿼리가 0행이 되어 `contract_no`가 NULL이 된다. 조건을 제거해 회귀 이전 동작으로 원복했다.
3. **같은 결함이 3환경 모두에서 발생하지만 스키마 drift 때문에 개발계에서만 예외로 터졌다.** 개발계 `history_control.contract_no`는 NOT NULL이라 탈퇴가 실패했고, 검증계·운영계는 nullable이라 **NULL이 조용히 기록**됐다. 그래서 **운영계 이력이 2026-06-01부터 오염돼 있을 가능성**이 남아 있고, 그 규모 확인이 인수 후 첫 작업이다(부록 B).

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **backend-api-main** | 나무엑스(NAMUH X) 앱과 백오피스가 쓰는 메인 백엔드. Spring Boot 3.2.4 / Java 17 / MySQL 8 / MyBatis. EC2에서 `air-bot-api.sh start {dev\|stg\|prd}`로 단일 jar를 프로필만 바꿔 띄운다 |
| **회원 탈퇴** | 앱의 `PUT /app/user/delete`. 로컬 DB 정리 + 외부 시스템 4곳(Cognito·medcare·openapi·OASYS) 파기·통보를 함께 수행한다 |
| **Cognito** | AWS 사용자 풀. 앱 로그인 계정의 실체. 한 사람(한 전화번호)에 **여러 계정(sub)이 달릴 수 있다** — 자체 가입 + 소셜 로그인 |
| **sub** | Cognito 계정 식별자(UUID). `user_accounts.sub`에 계정마다 한 행씩 저장된다 |
| **loginId** | 앱 사용자 로그인 아이디. 실제 값은 전화번호(`01012345678` 형식)이며 `user.login_id`가 UNIQUE다 |
| **medcare (skix-medcare)** | 마이헬스케어(건강상담·PHR) 백엔드. 별도 저장소·별도 DB. 탈퇴 시 여기 데이터도 파기해야 한다 |
| **케이론(Cheiron)** | medcare가 대화 생성·보관을 위탁한 외부 LLM 서비스. 수탁자라 탈퇴 파기 대상에 포함된다 |
| **openapi (skix-openapi)** | 외부 파트너에게 개방한 API 게이트웨이 백엔드. 사용자별 권한·클라이언트·웹훅을 보관한다 |
| **OASYS** | SK매직 계약·고객 시스템. 탈퇴를 HTTP 전문(R008 `syncMember`, 구분값 `03`)으로 통보한다 |
| **ABT / A1** | 로봇 공기청정기. 코드에서 기기 타입은 `ABT`, 제품 표기는 **A1**이다. `AQM`/`AQM_WLS`는 에어센서 |
| **빌딩(건물)** | 앱의 "집" 개념. `user_building` 테이블. 권한 `01`이 중앙관리자(집 주인), 그 외는 초대된 홈 멤버 |
| **연동 해제** | 기기를 집에서 떼어내는 동작. DB의 `device.linked_yn`을 `'N'`으로 바꾸고 기기에 MQTT로 `unregistered`를 통보한다 |
| **CREV0003** | 제어이력 테이블 `history_control`의 `op_command` 값. 이벤트명 `AIRBOT_REGISTRATION`(연동/연동 해제 통보)에 대응한다 |
| **스키마 drift** | 같은 테이블인데 환경(dev/stg/prd)마다 제약·컬럼이 다른 상태. 이 작업의 핵심 열쇠다 |
| **db-schema 저장소** | `dev.sql` / `stg.sql` / `prod.sql` 3개 덤프 파일만 있는 저장소. **실제 DB와 다를 수 있다**(§6의 10번) |

### 1-2. 문제

두 개의 결함이 겹쳐 있었고, 하나가 다른 하나를 가리고 있었다.

**문제 A — 탈퇴 실패를 성공으로 응답했다.**
`UserController.deleteUser`의 `catch` 블록이 예외 종류와 무관하게 `success=true`, HTTP 200, `"회원 탈퇴 되었습니다."`를 하드코딩해 돌려줬다. `UserService.deleteUser`는 여러 단계를 순차 실행하고 당시에는 트랜잭션도 없었으므로, 중간에 터지면 **일부만 처리된 채 끝난다**. 그런데 앱은 성공 화면을 띄웠다. 사용자는 탈퇴됐다고 믿고, 서버에는 계정이 남는다. 게다가 이 catch가 예외를 삼켰기 때문에 문제 B가 몇 달간 드러나지 않았다.

**문제 B — 연동 해제 이력의 계약번호가 유실된다.**
연동 해제 이력을 적재하는 `createHistoryControlUnLink` INSERT의 `company_id`·`contract_no` 두 서브쿼리에 `AND dev.linked_yn = 'Y'`가 붙어 있다. 이 INSERT는 **연동을 해제한 직후**에 호출되므로 그 시점의 `device.linked_yn`은 이미 `'N'`이다. 서브쿼리가 0행을 돌려주고,

- `company_id`는 `IFNULL(..., 1)` 폴백이 씌워져 있어 **회사 아이디 1로 잘못 기록**되고,
- `contract_no`는 폴백이 없어 **NULL**이 된다.

개발계는 `contract_no`가 NOT NULL이라 INSERT가 예외로 터지고, 그 예외가 탈퇴 전체를 실패시켰다(그리고 문제 A가 그것을 200으로 감췄다). 검증계·운영계는 nullable이라 **INSERT가 성공하고 NULL이 조용히 쌓였다.**

### 1-3. 요구사항

- 탈퇴가 실패하면 앱이 실패로 인지해야 한다. 성공 화면을 띄우면 안 된다.
- 다만 **앱이 서버 응답을 확인하기 전에 로그아웃해 버리면** 500을 주는 순간 "서버엔 계정이 남았는데 앱만 로그아웃되어 재시도 불가"가 된다. 서버 변경과 앱 변경이 한 쌍이다.
- 연동 해제 이력에 계약번호가 정상 기록돼야 한다.
- 탈퇴 도중 실패해도 **복구할 수 없는 상태**(예: Cognito는 지워졌는데 DB 계정은 살아 있음)가 남으면 안 된다.
- 이미 운영계에 쌓인 NULL 계약번호 이력의 규모를 파악하고, 소급 보정 여부를 판단한다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 서버 코드 (`backend-api-main`)

브랜치 반영 여부는 `git merge-base --is-ancestor <hash> origin/{dev,stg,main}`으로 실측했다.

| 커밋 | 내용 | dev | stg | main(운영) |
|---|---|---|---|---|
| `2844154c` | 매퍼 수정 — 계약 조회에서 `linked_yn='Y'` 제거 | 반영 | 반영 | **미반영** |
| `1b814394` | 컨트롤러 — 실패 시 500 + `success=false` | 반영 | 반영 | **미반영** |
| `c097f860` | 탈퇴 뒷정리(인증코드 무효화·이력 적재) 실패 격리 | 반영 | 반영 | **미반영** |
| `cc971056` | 연동 해제 이력 적재를 커밋 이후 별도 트랜잭션으로 이동 | 반영 | 반영 | **미반영** |
| `b3951aa3` | 탈퇴 순서 재배치 — 로컬 DB 먼저 커밋, 외부 파기는 그 뒤 | 반영 | 반영 | **미반영** |
| `389f02de` | 후속 정리(중복 알람 설정 조회 제거) | 반영 | 반영 | **미반영** |
| `b2138c78` / `471a9d45` | 탈퇴 시 medcare 데이터 파기 연동 (2026-08-11·12) | 반영 | 반영 | **미반영** |
| `ee2b1fc5` | 탈퇴 시 openapi 권한·자격증명 파기 연동 (2026-08-27) | 반영 | 반영 | **미반영** |
| `ee65e763` / `ab6eb394` | **회귀를 유입시킨 2026-06-01 커밋 2건** | 반영 | 반영 | **반영(운영계에 결함이 살아 있다)** |

브랜치 헤드(2026-09-22): `origin/dev` `3fdd8447` · `origin/stg` `ed6dc871` · `origin/main` `7d9ac170`(2026-09-01).

운영계 `main`의 `src/main/resources/mapper/device/HistoryControl.xml`을 직접 열어 확인했다 — `AND dev.linked_yn = 'Y'` **두 곳이 그대로 있다.**

### 2-2. 배포·검증

| 항목 | 상태 |
|---|---|
| 개발계(dev) 애플리케이션 배포 | **미확인.** 브랜치 반영만 실측 가능하다. 확인 방법은 §8-3 |
| 검증계(stg) 애플리케이션 배포 | **미확인.** 2026-09-03 검증계 배포 라운드의 배포 후 확인 항목에 "탈퇴 API 500 전환"이 들어 있었으나, 리비전 등록·배포 실행 여부까지는 로컬에서 볼 수 없다 |
| 운영계(prd) | **미배포.** `main` 미승격. §9-2 게이트 통과 전 금지 |
| 개발계 재현·수정 확인 | 2026-09-02 수행. 개발계에서 탈퇴가 실패하던 것이 수정 후 완주했다. 재현 조건은 부록 C |
| 검증계·운영계 판정 기준 | **에러 유무가 아니라 `contract_no`에 값이 채워지는지**로 본다. 두 환경은 NULL을 받아주므로 에러가 나지 않는다 |
| 운영 이력 오염 규모 | **미확인.** 로컬 맥에서 DB에 직결할 수 없다(회사 VDI에서만 가능). 쿼리는 부록 B |
| 스키마 drift 통일 방향 | **미결.** dev만 `contract_no` NOT NULL이다. 어느 쪽으로 맞출지 정해지지 않았다 |

### 2-3. 앱 (별도 담당자)

| 대상 | 상태 | 실측 근거 |
|---|---|---|
| iOS (`benjaminios`) | **수정 완료, `origin/develop` 반영.** 커밋 `4fb6649f`(2026-09-02). `origin/main`에는 **미반영** | `AccountWithdrawalService.swift`가 `guard ret.is_success() else { return ret }`로 실패 시 세션을 유지한다 |
| Android (`benjaminandroid`) | **수정 불필요.** 원래부터 성공일 때만 로그아웃한다 | `MyProfileWithdrawReasonScreen.kt`의 `if (result.isSuccess) { viewModel.logout(...) } else { ModalManager.alert(...) }` |
| 앱 스토어 릴리스 | **없음.** iOS `main` 브랜치는 2026-07-15(v2.2.1), Android `main`은 2026-07-10(2.2.1)에서 멈춰 있다. 즉 **iOS 수정본은 아직 사용자 기기에 없다** | 각 저장소 `main` 브랜치 최종 커밋일 |

---

## 3. 탈퇴 흐름 전체

인수자가 이 흐름을 모르면 두 결함의 의미도, 배포 순서 제약도 판단할 수 없다. 아래는 **dev·stg 기준(수정 후)** 흐름이다.

### 3-1. 흐름도

```
[앱]  PUT /app/user/delete
      헤더: IdToken: <Cognito ID Token>
      바디: { "exitCode": "WTHD0001".."WTHD0007", "exitReason": "..." }
         │
         ▼
[UserController.deleteUser]
      loginId 는 요청 바디가 아니라 토큰에서 꺼낸다 (awsTokenUtil.getLoginId())
      try { userService.deleteUser(...) } → 200 성공
      catch { 500 + success=false }            ← 결함 A 수정 지점
         │
         ▼
[UserService.deleteUser]

  ┌─ 0) 입력 확정 (DB 를 건드리기 전에) ───────────────────────────┐
  │   exitCode 를 EXITWTHD enum 으로 검증 → 잘못된 코드면 여기서 예외 │
  │   awsTokenUtil.validateToken(token)                             │
  └────────────────────────────────────────────────────────────────┘
         │
  ┌─ 1) 로컬 DB 변경 — txTemplate 하나의 트랜잭션 ─────────────────┐
  │   ① user, user_accounts 스냅샷 읽기 (파기 대상 sub 확보)        │
  │   ② clearUserBuildings(loginId)                                 │
  │       · 권한 01  → buildingService.deleteBuilding()             │
  │                    = 건물 삭제 · 스케줄 삭제 · 지도 매핑 삭제    │
  │                      · device.linked_yn = 'N'  ← 여기!          │
  │                      · 기기별 설정 삭제 + 연동 해제 MQTT 발송    │
  │       · 그 외     → deleteBuildingMemberLeave() (건물에서 나감)  │
  │       실패하면 IllegalStateException 으로 바꿔 롤백시킨다        │
  │   ③ deleteUserAccountByLoginId — user_accounts 행 hard DELETE   │
  │   ④ userMapper.deleteUser — user.delete_yn='Y', exit_* 기록     │
  └────────────────────────────────────────────────────────────────┘
         │  ← 여기서 커밋. 이후는 전부 되돌릴 수 없다.
         ▼
  ┌─ 2) 커밋 이후 외부 파기 — 각각 runBestEffort 로 격리 ──────────┐
  │   SMS_AUTH_INVALIDATE  미사용 인증코드 무효화 (로컬 DB)          │
  │   MEDCARE_PURGE        POST {medcare}/medcare/v1/internal       │
  │                          /users/purge?authSub={sub}&apply=true  │
  │                          (sub 마다 · 멱등)                      │
  │   OPENAPI_PURGE        POST {openapi}/internal/v1/users/{sub}   │
  │                          /purge?apply=true                      │
  │                          (sub 마다 · 멱등)                      │
  │   COGNITO_DELETE       adminUserGlobalSignOut + adminDeleteUser │
  │                          (스냅샷의 sub 합집합 전부)             │
  │   OASYS_WITHDRAW       R008 syncMember(구분 '03')               │
  └────────────────────────────────────────────────────────────────┘
         │
         ▼
  (커밋 후 콜백) AfterCommit "CREV_UNLINK_HISTORY"
      → CommandHistoryService.createUnLinkHistory (REQUIRES_NEW)
      → INSERT INTO history_control ... CREV0003   ← 결함 B 발생 지점
```

### 3-2. 단계별로 알아야 할 것

| 단계 | 알아야 할 것 |
|---|---|
| **loginId 출처** | 요청 바디가 아니라 **토큰에서 꺼낸 값**을 쓴다. 바디 값을 쓰던 시절, 바디가 비어 오면 인증코드 무효화 매퍼의 동적 조건절이 빠져 **전체 사용자의 인증코드가 대상**이 됐다(`c097f860`에서 수정) |
| **한 사람에 Cognito 계정이 여러 개** | `getUserAccountListByLoginId`가 `List`를 돌려준다. 자체 가입 + 소셜 로그인이 같은 전화번호에 붙을 수 있다. 그래서 **토큰의 sub 하나만 지우면 안 되고** 계정 전부를 순회한다 |
| **파기 대상 sub 합집합** | `collectPurgeSubs` = `user_accounts.sub` 전부 + 토큰 sub + 레거시 `user.sub`. openapi는 `user.sub`로 키잉되던 시기의 행이 남아 있을 수 있어 이 합집합을 쓰고, medcare는 계정 sub + 토큰 sub만 쓴다 |
| **medcare는 반드시 Cognito 삭제보다 먼저** | 탈퇴 시점에 Cognito 계정이 사라지면 전화번호 → sub 해석이 불가능해져 파기 대상을 특정할 수 없다. 코드 주석으로 고정돼 있다 |
| **medcare 파기의 실패 정책** | medcare 쪽은 **위탁사(케이론) 대화를 먼저 지우고 하나라도 실패하면 로컬을 건드리지 않는다**(부분 성공을 완료로 기록하지 않음). 신고 테이블만 분쟁 대비로 보존하고 건수를 응답에 노출한다. 호출은 멱등이라 재호출로 소진할 수 있다 |
| **파기 실패 시 재시도 단서는 로그뿐** | `user_accounts`가 이미 hard delete 된 뒤라 DB로는 대상을 복원할 수 없다. `탈퇴 후처리 실패: stage=...` ERROR 로그가 유일한 작업 목록이다 |
| **OASYS 통보는 조건부** | `user.icst_no`(통합고객번호)가 없으면 미연동 사용자로 보고 건너뛴다 |
| **user 행은 남는다** | 탈퇴는 `user`를 지우지 않고 `delete_yn='Y'` + `exit_code`/`exit_reason`/`exit_date`만 세운다. 지우는 것은 `user_accounts` 행이다 |
| **연동 해제 이력은 커밋 이후에 적재** | `cc971056` 이후 `AfterCommit`으로 미뤄 `REQUIRES_NEW` 트랜잭션에서 INSERT 한다. 이유는 §6의 5번 |

### 3-3. 이 작업이 건드리는 테이블 (backend-api-main DB)

| 테이블 | 이 흐름에서의 역할 | 관련 컬럼 |
|---|---|---|
| `user` | 탈퇴 표시 | `login_id`(UNIQUE), `delete_yn`, `exit_code`, `exit_reason`, `exit_date`, `sub`(레거시), `icst_no` |
| `user_accounts` | Cognito 계정 목록. 탈퇴 시 **hard DELETE** | `user_id`, `sub`, `auth_provider` |
| `user_building` / `building_member` | 집과 멤버십 | 권한 `01` 여부로 삭제/나가기가 갈린다 |
| `device` | 기기. 연동 해제 시 `linked_yn='N'` | `id`, `serial`, `contract_id`(FK → `contract.id`), `linked_yn` |
| `contract` | 계약 | `id`, `contract_no`(NOT NULL), `company_id`(**nullable int**) |
| `history_control` | 제어·연동 이력. **이번 결함의 피해 테이블** | `company_id`(bigint **NOT NULL**), `contract_no`(varchar(50), 3환경 덤프상 **DEFAULT NULL**), `login_id`, `serial`, `op_command`, `op_param`, `request_date` |

`contract.company_id`가 nullable이라 `history_control.company_id`(NOT NULL)에 넣을 때 `IFNULL(..., 1)` 폴백이 필요했던 것이고, 그 폴백이 이번 결함을 **조용하게** 만든 원인이기도 하다.

---

## 4. 결함 B — 연동 해제 이력 `contract_no` 회귀

### 4-1. 무엇이 어떻게 깨졌나

`src/main/resources/mapper/device/HistoryControl.xml`의 `createHistoryControlUnLink`가 `company_id`와 `contract_no`를 서브쿼리로 채운다. 운영계(`main`)에 아직 남아 있는 형태는 이렇다.

```sql
IFNULL((SELECT con.company_id
        FROM device dev INNER JOIN contract con ON dev.contract_id = con.id
        WHERE dev.serial = #{serial}
          AND dev.linked_yn = 'Y'          -- ← 이 줄
        ORDER BY dev.id DESC LIMIT 1), 1)
,
(SELECT con.contract_no
   FROM device dev INNER JOIN contract con ON dev.contract_id = con.id
  WHERE dev.serial = #{serial}
    AND dev.linked_yn = 'Y'                -- ← 이 줄
  ORDER BY dev.id DESC LIMIT 1)
```

호출 시점에는 이미 `linked_yn = 'N'`이다. 그래서 두 서브쿼리 모두 0행이고, `company_id`는 `1`, `contract_no`는 NULL이 된다.

### 4-2. 원인 사슬 (git blame으로 확정)

| 시점 | 커밋 | 무슨 일이 있었나 |
|---|---|---|
| 2025-08-12 | `57aad9c5` | 원본. 서브쿼리가 **serial로만** 조회했다. 해제된 기기도 계약을 찾을 수 있었다 |
| 2026-06-01 15:16 | `ee65e763` | "계약정보에서 앱 사용자 아이디 제거" 작업에서 `AND dev.linked_yn = 'Y'`가 **두 서브쿼리에 붙었다.** 여기서 회귀가 유입됐다 |
| 2026-06-01 16:09 | `ab6eb394` | 같은 날, `company_id`에만 `IFNULL((SELECT ...), 1)`을 4곳에 씌웠다. **`contract_no`는 누락됐다.** 이 조치가 `company_id` 쪽 예외를 막아버려 결함이 더 안 보이게 됐다 |
| 2026-09-02 | `2844154c` | 두 서브쿼리에서 `AND dev.linked_yn = 'Y'`를 제거. **원복에 해당한다.** `company_id`도 폴백값 `1`이 아니라 실제 값이 기록된다 |

### 4-3. 왜 개발계에서만 터졌나 — 스키마 drift

**이 한 가지 사실이 모든 관찰을 정합적으로 설명한다.**

| 환경 | `history_control.contract_no` 실제 제약 | 결과 |
|---|---|---|
| dev | **NOT NULL** | INSERT 예외 → 연동 해제 이력 적재 실패 → (수정 전에는) 탈퇴 전체 실패 |
| stg | nullable | INSERT 성공. **`contract_no`가 NULL로 조용히 기록**, `company_id`는 `1`로 잘못 기록 |
| prd | nullable | stg와 동일 |

`db-schema` 저장소의 `dev.sql`·`stg.sql`·`prod.sql` **세 파일 모두** `contract_no varchar(50) ... DEFAULT NULL`로 적혀 있다. 즉 **덤프는 stg/prd와 일치하고 dev만 어긋난다.** 실제 제약 차이는 사용자가 각 환경 DB에서 직접 확인해 확정했다(2026-09-02).

이 하나로 다음이 전부 설명된다.
- 개발계에서만 에러가 났던 것
- 검증계에서 회원가입 → 기기연동 → 탈퇴가 정상 완주한 것
- 운영계 탈퇴 완주 건수가 2026-06 이후에도 끊기지 않은 것

조사 초반에 세웠던 "같은 serial에 `linked_yn='Y'` 행이 여러 개라 `LIMIT 1`이 엉뚱한 행을 집는다"는 가설은 **불필요했다.** serial당 `linked_yn='Y'` 행은 한 개임을 확인했다.

### 4-4. 고친 방식과 고르지 않은 대안

**채택**: 두 서브쿼리에서 `AND dev.linked_yn = 'Y'` 제거. `ORDER BY dev.id DESC LIMIT 1`은 그대로 둬 serial당 최신 device 행을 집는다.

**고르지 않은 대안**: `BuildingService.deleteBuilding`에서 `updateDevicelink`(= `linked_yn='N'`)를 기기 루프 **뒤로** 옮기는 안. 그러면 루프 안의 MQTT 발행·설정 삭제가 `'Y'` 상태에서 돌게 되어 다른 의미 변화가 생긴다. 순서를 바꾸는 것보다 조회 조건을 되돌리는 쪽이 영향 범위가 좁다.

### 4-5. 영향 범위 확인

- `createHistoryControlUnLink`의 호출처는 **`CommandHistoryService.createUnLinkHistory` 한 곳**이고, 그것을 부르는 것은 `RequestDeviceService.deferUnLinkHistory`와 `RequestControlRobotService.deferUnLinkHistory` 둘뿐이다. 둘 다 이벤트명이 `AIRBOT_REGISTRATION`(= `op_command` `CREV0003`)일 때만 탄다.
- 즉 **오염 대상은 `op_command='CREV0003'` 행에 한정**된다.
- `CREV0003`은 연동(`eventStatus=registered`)과 연동 해제(`unregistered`) **둘 다** 쓴다. 연동 시점에는 `linked_yn`이 `'Y'`라 정상 기록됐을 가능성이 높다. 부록 B의 쿼리에서 둘을 나눠 본다.
- 탈퇴 외에 **개별 기기 연동 해제**(앱의 기기 연동 해제, `ProductDeviceUnlinkService.unLinkByApp`)도 같은 경로를 탄다. 거기서도 MQTT 통보와 이력 적재가 커밋 이후로 미뤄져 있어 적재 시점의 `linked_yn`은 이미 `'N'`이다. **오염은 탈퇴 건보다 넓다.**
- `linked_yn='Y'`를 조건으로 쓰는 INSERT/UPDATE 21개를 전수로 훑었고, **추가 수정 대상은 없었다.** (`insertHistoryfactoryReset`은 호출처 0건인 죽은 코드다.)

---

## 5. 결함 A — 실패를 200으로 응답

### 5-1. 무엇이 어떻게 깨졌나

수정 전 `UserController.deleteUser`의 catch:

```java
} catch (Exception e) {
    log.error("Cognito error: type={}", e.getClass().getSimpleName());
    return ResponseEntity.ok(ResponseUtil.getResponseInfo(new ReponseResult(
            true, "200", "회원 탈퇴 되었습니다."), null));
}
```

예외 종류와 무관하게 성공이다. 로그도 "Cognito error"라 적혀 있어 실제 원인(이력 적재 SQL)과 무관한 방향으로 오인하게 만든다.

### 5-2. 고친 방식

```java
} catch (Exception e) {
    log.error("User withdrawal failed: type={}, message={}",
            e.getClass().getName(), e.getMessage(), e);

    String errorMessage = "회원 탈퇴 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
    // iOS 는 비 200 응답에서 최상위 message 만 읽어 알림에 노출하므로 error.message 와 동일하게 채운다.
    ResponseUtil<ReponseResult, Object> body = ResponseUtil.getResponseInfo(
            new ReponseResult(false, "500", errorMessage, 500), null);
    body.setMessage(errorMessage);
    return ResponseEntity.status(500).body(body);
}
```

진단을 위해 예외 클래스 **전체 이름**·메시지·스택을 남긴다. 토큰과 로그인 아이디는 기록하지 않는다.

### 5-3. `body.setMessage(...)`가 왜 필요한가 — 지우면 안 되는 두 줄

`ReponseResult`는 **실패 생성자에서도 최상위 `message`를 `"OK"`로 고정**한다. 실제 코드가 그렇다.

```java
public ReponseResult(Boolean success, String code, String message, Integer result) {
    if (result == null) result = 200;
    this.result = result;
    this.message = "OK";                                  // ← 실패여도 "OK"
    this.success = success;
    this.error = Map.of("code", code, "message", message);
}
```

한편 iOS `APIClientImpl.request`는 **비 200 응답에서 최상위 `message`만** 읽는다.

```swift
if let temp = try? JSONSerialization.jsonObject(with: data) as? [String:Any] {
    message = temp["message"] as? String ?? ""
    errorCode = (temp["error"] as? [String: Any])?["code"] as? String
}
```

`setMessage`로 덮지 않으면 **실패 알림 제목이 "OK"로 뜬다.** 원래 버그("탈퇴 되었습니다")와 똑같은 오해를 부른다. Android는 `error.code`/`error.message`를 파싱하므로 영향이 없다.

### 5-4. API 계약

**요청**

```http
PUT https://{app-api-host}/app/user/delete
IdToken: <Cognito ID Token>
Content-Type: application/json

{ "exitCode": "WTHD0001", "exitReason": "" }
```

`exitCode`는 `EXITWTHD` enum 값이다. `WTHD0007`(직접 입력)일 때만 `exitReason` 본문이 그대로 저장되고, 나머지는 enum의 고정 문구가 저장된다.

| 코드 | 문구 |
|---|---|
| `WTHD0001` | 더 이상 기기를 사용하지 않아요. |
| `WTHD0002` | 렌탈 서비스를 해지했어요. |
| `WTHD0003` | 기기의 고장이 많아요. |
| `WTHD0004` | A/S가 불만족스러워요. |
| `WTHD0005` | Hi NAMUH를 자주 사용 하지 않아요. |
| `WTHD0006` | Hi NAMUH의 사용법이 어려워요. |
| `WTHD0007` | 직접 입력 (`exitReason` 사용) |

**성공 `200`**

```json
{ "result": 200, "message": "OK", "success": true,
  "error": { "code": "200", "message": "회원 탈퇴 되었습니다." } }
```

**실패 `500`** (이번 변경)

```json
{ "result": 500,
  "message": "회원 탈퇴 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
  "success": false,
  "error": { "code": "500",
             "message": "회원 탈퇴 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요." } }
```

최상위 `message`와 `error.message`가 같은 값인 것은 **의도**다(§5-3).

---

## 6. 되돌리면 안 되는 설계 결정

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **`body.setMessage(errorMessage)` 두 줄을 지우지 않는다** | `ReponseResult`가 최상위 `message`를 `"OK"`로 고정하고 iOS는 비 200에서 그 필드만 읽는다. 지우면 실패 알림 제목이 "OK"가 되어 원래 버그와 같은 오해를 만든다. "중복이니 정리하자"가 가장 빠지기 쉬운 함정이다 |
| 2 | **로컬 DB 변경을 먼저 커밋하고, 외부 파기는 그 뒤에 한다** (`b3951aa3`) | 예전 순서는 되돌릴 수 없는 외부 파기(medcare·openapi·Cognito)가 되돌릴 수 있는 로컬 DB 삭제보다 **먼저**였다. 로컬 삭제가 실패하면 **Cognito는 지워졌는데 계정은 살아 있어 로그인조차 못 하는** 상태가 남고 복구 수단이 없다. 지금 순서면 최악이 "DB는 탈퇴됐고 외부 일부가 남음"이라 ERROR 로그를 작업 목록 삼아 재시도할 수 있다 |
| 3 | **파기 대상 sub는 트랜잭션 안에서 읽어 스냅샷으로 넘긴다.** 커밋 후에 `user_accounts`를 재조회하지 않는다 | 커밋 이후에는 `user_accounts`가 이미 hard delete 돼 **빈 목록**이 나온다. 재조회하면 Cognito 계정만 조용히 남는다. 반대로 트랜잭션 밖에서 미리 읽으면 조회와 삭제 사이에 추가된 계정이 로컬에서는 지워지고 파기 목록에서는 빠진다 |
| 4 | **커밋 이후 단계는 `runBestEffort`로 하나씩 격리하되 `Error`는 삼키지 않는다** | 하나라도 예외가 새어 나가면 "계정은 삭제된 채 실패 응답만 나가는" 상태가 다시 만들어진다. 반대로 `Error`까지 삼키면 JVM 수준 문제를 감춘다 |
| 5 | **연동 해제 이력 적재는 커밋 이후 `REQUIRES_NEW`에서 한다. 본 트랜잭션 안에서 적재하고 실패를 삼키면 안 된다** (`cc971056`) | 이 INSERT는 서브쿼리로 `device`를 읽는데 그 행은 방금 같은 트랜잭션이 갱신한 것이다. 게다가 본 트랜잭션 안에서 실패를 호출부가 잡으면 **deadlock처럼 DB가 이미 전체를 롤백한 오류까지 숨겨져** 앞선 변경은 사라지고 뒤 작업만 커밋되는 부분 커밋이 가능하다. 같은 이유로 `REQUIRES_NEW`만 걸고 트랜잭션 안에 두는 것도 안 된다 — 바깥 트랜잭션이 방금 갱신한 `device` 행을 새 트랜잭션이 다시 읽어 **락 대기**에 걸린다 |
| 6 | **인증코드 무효화 대상 loginId는 서버가 토큰에서 확보한 값을 쓴다** (`c097f860`) | 요청 바디 값을 쓰면, 바디가 비어 올 때 매퍼의 동적 조건절이 빠져 `used = 0`인 **전체 사용자의 인증코드**가 대상이 된다 |
| 7 | **인증코드 무효화는 조회 후 반복 UPDATE가 아니라 단일 UPDATE로 한다** | 행을 읽지 않으므로 컬럼 값 매핑 오류에 영향받지 않는다. 실제로 `create_date`가 zero date인 행 때문에 `Zero date value prohibited`로 탈퇴가 실패한 사례가 있었다 |
| 8 | **medcare 파기를 Cognito 삭제보다 뒤로 옮기지 않는다** | 탈퇴 시점에 Cognito 계정이 사라지면 전화번호 → sub 해석이 불가능해져 파기 대상을 특정할 방법이 없어진다 |
| 9 | **파기 감사 actor는 `platform-withdrawal` 고정값이다.** 사용자 전화번호를 넣지 않는다 | 파기 감사 로그에 탈퇴자의 개인정보가 남는다 |
| 10 | **`db-schema`의 `*.sql` 덤프를 제약 근거로 쓰지 않는다** | 이번 사고의 핵심이다. 세 덤프 모두 `contract_no DEFAULT NULL`인데 dev 실제 DB는 NOT NULL이었다. **제약은 반드시 해당 환경에서 `SHOW CREATE TABLE`로 확인**한다. 한 환경의 에러(혹은 무에러)를 다른 환경에 일반화하지 말고, 재현이 안 되면 스키마 차이부터 의심할 것 |
| 11 | **stg/prd 검증 판정 기준은 "에러가 안 난다"가 아니라 "`contract_no`에 값이 채워진다"** | 두 환경은 NULL을 받아주므로 결함이 있어도 에러가 나지 않는다. 에러 유무로 판정하면 고장난 채 통과한다 |

---

## 7. 앱 쪽 내용

### 7-1. iOS — 수정 완료 (`benjaminios`, `4fb6649f`, 2026-09-02)

서버가 500을 주기 시작하면 **앱이 응답 확인 전에 로그아웃하는 기존 동작이 곧바로 문제**가 된다. 서버엔 계정이 남았는데 앱만 로그아웃되어 사용자가 재시도할 수 없다. iOS 담당이 직접 고쳤고, 반영된 내용은 다음과 같다.

`AccountWithdrawalServiceImpl.deleteAccount`

```swift
guard ret.is_success() else {
    return ret          // 실패: 로그인·FCM·Cognito 세션을 전부 그대로 둔다
}
_ = await Api.shared.logout()   // 성공일 때만 정식 로그아웃 경로
```

같은 작업에서 앱 팀이 **추가 결함 두 건**을 더 잡았다.

- 예전 인라인 로그아웃 두 줄(`loginResult = nil`, `DataStore.logout()`)에는 **Amplify `signOut`이 빠져 있어 Cognito 세션이 기기에 남아 있었다.** 정식 경로(`Api.shared.logout()`)는 FCM 토큰 삭제 → Amplify `signOut` → `loginResult` 초기화 → `DataStore.logout()` 순으로 돈다.
- `AccountWithdrawalViewModel`이 서버 호출 **전에** FCM 토큰을 지우고 있었다. 탈퇴가 실패해 로그인이 유지되더라도 푸시 토큰은 이미 서버에서 사라진 뒤가 되고 `last_fcm_token`도 지워져 자동 재등록도 안 된다. 사전 삭제를 제거해 성공 확정 뒤 `logout()` 안에서 처리하게 바꿨다.
- `Api.swift`의 구버전 `withdrawal()`은 **서버 응답과 무관하게 `.init(success: true)`를 반환**하던 죽은 코드였다(호출부 0건). 같은 커밋에서 삭제됐다.

### 7-2. Android — 수정 불필요 (근거)

`MyProfileWithdrawReasonScreen.kt`가 원래부터 성공일 때만 로그아웃한다.

```kotlin
val result = viewModel.userDelete(exitReason = ..., exitCode = ...)
if (result.isSuccess) {
    viewModel.logout(NaviPath.MY_PROFILE_WITHDRAW_COMPLETE)
} else {
    ModalManager.alert(title = ..., subTitle = result.getApiErrorMessage(context))
}
```

실패 문구는 `error.message`에서 꺼내므로 §5-3의 최상위 `message` 문제도 해당 없다. **Android에는 요청한 것이 없다.**

---

## 8. 배포 방법

### 8-1. 저장소 하나뿐이라 순서 제약은 없다. 단 앱 게이트가 있다

`backend-api-main` 단독 배포다. DDL 변경도 설정 변경도 없다. 다만 **운영계에 올리려면 iOS 수정본이 스토어에 나가 있어야 한다**(§9-2).

두 수정을 쪼개서 올릴 수도 있다. 판단 근거는 이렇다.

| 변경 | 앱 게이트 | 근거 |
|---|---|---|
| `2844154c` 매퍼 수정 | **없음** | 응답 계약이 바뀌지 않는다. 운영계 이력 오염을 **더 쌓이지 않게 막는** 조치라 먼저 올릴수록 좋다 |
| `1b814394` 500 전환 | **있음** | 구버전 iOS 앱이 500을 받으면 서버엔 계정이 남았는데 앱만 로그아웃되어 재시도가 막힌다 |

다만 두 커밋은 이후의 트랜잭션 재배치(`b3951aa3`)·실패 격리(`c097f860`)·이력 적재 이동(`cc971056`)과 한 덩어리로 얽혀 있다. 쪼개려면 cherry-pick 충돌을 감수해야 하므로, **기본 방침은 통째 승격 + 앱 릴리스 게이트 준수**다.

### 8-2. 배포 절차 (EC2)

이 저장소는 ECS가 아니라 **EC2 수동 배포**다. 저장소에 `deploy/` 디렉터리가 없다(배포 스크립트 `air-bot-api.sh`는 저장소에서 제거됐고 서버 박스에서 관리한다).

```
1. 대상 브랜치 머지·push (dev → stg → main)
2. ./gradlew clean bootJar
3. 대상 EC2 로 jar 전달
4. ./air-bot-api.sh start {dev|stg|prd}
```

프로필은 실행 시점에 지정하고, 각 프로필이 EC2 인스턴스 프로파일로 `/backend-api-main/<env>/` SSM 파라미터 경로를 읽는다.

### 8-3. 배포 후 확인

**배포 여부 자체 확인 (로컬에서는 불가 — VDI 또는 EC2 접속 필요)**

```bash
# EC2 에서
git -C <앱 디렉터리> log -1 --format='%h %ad %s'   # 2844154c/1b814394 이후인지
ls -l <앱 디렉터리>/air-bot-api.jar               # jar 빌드 시각
grep -c "User withdrawal failed" <로그 경로>/*.log  # 신버전에만 있는 로그 문구
```

`"Cognito error: type="` 문구가 최신 로그에 보이면 **구버전이 돌고 있는 것**이다. 신버전은 `"User withdrawal failed: type=..."`로 남긴다.

**기능 확인 — 개발계**

1. 신규 계정 가입 → ABT(A1) 기기 연동 → 권한 `01`(집 주인) 상태를 만든다.
2. 앱에서 탈퇴한다. **수정 전에는 여기서 실패했다.**
3. DB 확인:

```sql
-- 연동 해제 이력에 계약번호가 채워졌는지 (핵심 판정)
SELECT id, login_id, serial, company_id, contract_no, op_command, request_date
  FROM history_control
 WHERE serial = '<테스트 기기 serial>'
   AND op_command = 'CREV0003'
 ORDER BY id DESC LIMIT 5;
-- contract_no 가 NOT NULL 이고 company_id 가 1 이 아닌 실제 값이어야 한다

-- 탈퇴가 반영됐는지
SELECT login_id, delete_yn, exit_code, exit_reason, exit_date
  FROM `user` WHERE login_id = '<테스트 loginId>';

-- Cognito 계정 행이 정리됐는지 (0행이어야 한다)
SELECT ua.* FROM user_accounts ua
  JOIN `user` u ON u.id = ua.user_id
 WHERE u.login_id = '<테스트 loginId>';
```

**기능 확인 — 검증계·운영계**

에러 유무로 판정하면 안 된다(§6의 11번). **`contract_no`에 값이 채워지는지**로만 판정한다. 같은 쿼리를 쓴다.

**실패 경로 확인(선택)**

실패를 인위적으로 만들기 어렵다면, 최소한 **iOS 앱에서 실패 알림이 떴을 때 제목이 "OK"가 아닌지**만이라도 확인한다. 그것이 §5-3 회귀의 유일한 눈으로 보이는 신호다.

### 8-4. 롤백

- 애플리케이션 이전 jar로 되돌린다. DDL·설정 변경이 없어 되돌림에 부수효과가 없다.
- **되돌리면 두 결함이 함께 돌아온다.** 탈퇴 실패가 다시 200으로 응답되고, 개발계에서는 탈퇴가 다시 실패한다.
- 이미 적재된 `history_control` 행은 건드리지 않는다.

---

## 9. 남은 일

### 9-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **운영 이력 오염 규모 확인.** 부록 B의 쿼리 1~3을 운영 DB에서 실행한다. 2026-06을 기점으로 `null_contract`가 치솟으면 확정이다 | 백엔드 | 로컬 맥에서는 DB 직결 불가 — VDI에서 실행 |
| 2 | **개발계·검증계 실제 배포 여부 확인.** 브랜치는 반영돼 있으나 배포는 미확인이다 | 백엔드 | 방법은 §8-3 |
| 3 | **소급 보정 여부 결정.** 1번 결과를 보고 `history_control` 이력의 용도(정산·통계 참조 여부)를 확인한 뒤 판단한다. 보정 SQL은 부록 B의 쿼리 4 | 백엔드 + 이력 소비자 | 보정하지 않기로 해도 **결정을 기록**해 둘 것 |
| 4 | **스키마 drift 통일 방향 결정.** `history_control.contract_no`를 3환경 모두 NOT NULL로 갈지, 모두 nullable로 갈지 | 백엔드 | 부록 B의 쿼리 5로 현 상태를 먼저 실측. NOT NULL 통일은 기존 NULL 행 보정이 선행돼야 한다 |

### 9-2. 운영계 릴리스 게이트

| # | 확인할 것 | 상대 |
|---|---|---|
| 5 | **iOS 수정본(`4fb6649f`)이 포함된 빌드가 앱 스토어에 릴리스됐는지.** 현재 iOS `main`은 2026-07-15(v2.2.1)에서 멈춰 있고 수정은 `develop`에만 있다 | iOS 담당 |
| 6 | `main` 승격 및 운영 배포. 위 5번 전에는 **500 전환을 운영계에 올리지 않는다** | 백엔드 |
| 7 | 배포 후 운영계에서 `contract_no`가 채워지는지 실측(§8-3) | 백엔드 |

**운영계에서 500이 갑자기 늘어날 걱정은 크지 않다.** 운영계 `contract_no`는 nullable이라 이 결함 자체로는 예외가 나지 않았다. 다만 탈퇴 경로에는 이 결함 외의 실패 원인도 있을 수 있고(예: zero date 인증코드 행), 그것들이 이제 **200이 아니라 500으로 드러난다.** 앱 게이트가 필요한 이유가 바로 이것이다.

### 9-3. 후속 (이 작업을 막지는 않음)

| # | 항목 |
|---|---|
| 8 | **개별 기기 연동 해제 경로의 오염도 같이 봐야 한다.** 탈퇴뿐 아니라 앱의 기기 연동 해제도 같은 INSERT를 타므로 `CREV0003` 오염은 탈퇴 건수보다 넓다(§4-5) |
| 9 | **탈퇴 후처리 실패분의 재시도 절차가 문서화돼 있지 않다.** 지금은 `탈퇴 후처리 실패: stage=...` ERROR 로그가 유일한 작업 목록이다. 재호출은 멱등이므로(medcare `users/purge`, openapi `users/{sub}/purge`) 로그에서 sub를 모아 다시 호출하면 소진된다. 이 절차를 운영 문서로 만들 가치가 있다 |
| 10 | **탈퇴 행 부활 문제(별건).** `user` 행을 지우지 않고 `delete_yn`만 세우는 구조 탓에, 같은 전화번호로 재가입하면 기존 행이 `delete_yn='N'`으로 되돌아가며 과거 FK 데이터를 승계한다. 역대 탈퇴 166건 중 96건에서 `delete_yn='N'`인데 `exit_date`가 남은 상태가 실측됐다. 노출 사고가 아니라 **파기 의무 문제**로 정리돼 있고 별도 티켓 대상이다. 이번 작업 범위 밖이지만 탈퇴를 손대는 사람이 반드시 알아야 한다 |
| 11 | **`history_control.company_id`의 `IFNULL(..., 1)` 폴백 자체를 재검토.** 폴백값 `1`이 "조용한 실패"를 만드는 장치였다. `contract.company_id`가 nullable인 한 NOT NULL 컬럼을 채우려면 폴백이 필요하지만, `1`이라는 유효해 보이는 값 대신 구분 가능한 값을 쓰는 편이 진단에 낫다 |

---

## 10. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| "탈퇴했는데 계정이 살아 있어요" | 구버전(운영계) 동작일 가능성이 높다. `user.delete_yn`을 확인하고, 로그에서 `Cognito error: type=`(구버전) 또는 `User withdrawal failed: type=`(신버전)을 찾는다 |
| 탈퇴 실패 알림 제목이 "OK"로 뜬다 | §5-3의 `body.setMessage(...)`가 지워졌다. iOS 전용 증상이다 |
| 탈퇴는 됐는데 medcare/openapi 데이터가 남았다 | `탈퇴 후처리 실패: stage=MEDCARE_PURGE` 또는 `stage=OPENAPI_PURGE` ERROR 로그를 찾는다. 로그의 sub로 `POST {medcare}/medcare/v1/internal/users/purge?authSub={sub}&apply=true`, `POST {openapi}/internal/v1/users/{sub}/purge?apply=true`를 재호출한다. **둘 다 멱등이다.** medcare 호출에는 `X-Internal-Api-Key`(공유키)와 `X-Acting-Admin`(탈퇴 경로는 고정값 `platform-withdrawal`) 헤더가 필요하다 |
| 탈퇴 후 Cognito 계정이 남았다 | `Cognito 계정 삭제 시작: count=` 로그로 대상 수를 확인한다. `Cognito 삭제 대상 sub 없음`이면 스냅샷이 비어 있었던 것이다 |
| 연동 해제 이력이 안 남았다 | `연동 해제 이력 적재 실패 (연동 해제는 이미 확정됨): serial=..., correlationId=...` ERROR 로그. **연동 해제 자체는 이미 확정된 상태**다 |
| 특정 기기의 연동/해제 이력 조회 | `SELECT id, login_id, serial, company_id, contract_no, op_command, op_param, request_date FROM history_control WHERE serial = '<serial>' AND op_command = 'CREV0003' ORDER BY id DESC LIMIT 10;` |
| 개발계에서만 탈퇴가 실패한다는 제보 | 스키마 drift를 먼저 의심한다(§6의 10번). 부록 B 쿼리 5로 환경별 제약을 비교한다 |
| 서버 로그 키워드 | 탈퇴 진입 `User withdrawal requested` · 실패 `User withdrawal failed: type=` · 후처리 실패 `탈퇴 후처리 실패: stage=` · 이력 적재 실패 `연동 해제 이력 적재 실패` · 커밋 후 콜백 태그 `CREV_UNLINK_HISTORY` |

---

## 부록 A. 핵심 커밋 (시간순)

| 날짜 | 저장소 | 커밋 | 내용 | dev / stg / main |
|---|---|---|---|---|
| 2025-08-12 | backend-api-main | `57aad9c5` | (원본) 서브쿼리가 serial로만 조회 | 반영 / 반영 / 반영 |
| 2026-06-01 15:16 | backend-api-main | `ee65e763` | **회귀 유입** — 계약 서브쿼리에 `linked_yn='Y'` 추가 | 반영 / 반영 / 반영 |
| 2026-06-01 16:09 | backend-api-main | `ab6eb394` | `company_id`에만 `IFNULL(...,1)` 4곳. `contract_no` 누락 | 반영 / 반영 / 반영 |
| 2026-08-11 | backend-api-main | `b2138c78` | 탈퇴 시 medcare 사용자 데이터 파기 연동 | 반영 / 반영 / 미반영 |
| 2026-08-12 | backend-api-main | `471a9d45` | 위 브랜치 dev 머지 | 반영 / 반영 / 미반영 |
| 2026-08-27 | backend-api-main | `ee2b1fc5` | openapi 키 sub 정규화 + 탈퇴 파기 연동 | 반영 / 반영 / 미반영 |
| 2026-09-02 13:51 | backend-api-main | **`2844154c`** | **매퍼 수정** — 계약 조회에서 `linked_yn='Y'` 제거 | 반영 / 반영 / **미반영** |
| 2026-09-02 13:51 | backend-api-main | **`1b814394`** | **컨트롤러** — 탈퇴 실패를 500 + `success=false`로 | 반영 / 반영 / **미반영** |
| 2026-09-02 15:45 | benjaminios | `4fb6649f` | **iOS** — 성공 확정 뒤에만 로그아웃, FCM 사전 삭제 제거, 죽은 `withdrawal()` 삭제 | `develop` 반영 / `main` 미반영 |
| 2026-09-02 16:35 | backend-api-main | `c097f860` | 탈퇴 뒷정리 실패 격리, 인증코드 대상 loginId 정정, 단일 UPDATE 전환 | 반영 / 반영 / 미반영 |
| 2026-09-02 | backend-api-main | `54501100` / `d24bf1cd` | 가입 경로 인증코드 정리 통일, 대체된 매퍼 제거 | 반영 / 반영 / 미반영 |
| 2026-09-02 | backend-api-main | `0ad4f805` / `cc971056` | 비가역 부작용을 커밋 이후로 이동. **연동 해제 이력 적재를 `AfterCommit` + `REQUIRES_NEW`로** | 반영 / 반영 / 미반영 |
| 2026-09-02 17:03 | backend-api-main | **`b3951aa3`** | **탈퇴 순서 재배치** — 로컬 DB 먼저 커밋, 외부 파기는 그 뒤. `UserServiceWithdrawalTest` 신설 | 반영 / 반영 / **미반영** |
| 2026-09-03 | backend-api-main | `ce9ce510` / `389f02de` | 커밋 후 콜백에서 DB 미접근, 중복 알람 설정 조회 제거 | 반영 / 반영 / 미반영 |

고정 테스트: `src/test/java/com/skmagic/api/user/service/UserServiceWithdrawalTest.java` — 로컬 DB 변경이 실패하면 외부 파기가 **한 번도** 일어나지 않을 것, 커밋 이후 파기는 서로 독립적일 것, 예외가 호출자로 새어 나가지 않을 것을 고정한다. 실제 롤백은 통합 영역이라 개발계 실호출로 확인한다.

---

## 부록 B. 운영 이력 오염 규모 확인 SQL

**실행 위치**: 회사 VDI의 운영 DB(`backend-api-main` DB). 로컬 맥에서는 DB에 직결할 수 없다.
**테이블·컬럼은 모두 코드(`HistoryControl.xml`)와 스키마 덤프에서 확인한 것만 썼다.**

주의 두 가지.
- `op_command='CREV0003'`은 연동(`registered`)과 연동 해제(`unregistered`)를 **둘 다** 쓴다. `op_param`은 Java `Map.toString()` 결과가 그대로 들어가므로 `eventStatus=unregistered` 형태다. JSON 형태(`"eventStatus":"unregistered"`)로 적재된 시기가 섞여 있을 수 있어 아래 쿼리는 값 문자열만 LIKE로 찾는다.
- `request_date`는 조회 성능을 위해 항상 범위를 건다(`history_control_request_date_IDX`가 있다).

### 쿼리 1 — 월별 오염 추이 (가장 먼저 실행)

```sql
SELECT DATE_FORMAT(request_date, '%Y-%m')  AS ym,
       COUNT(*)                            AS total,
       SUM(contract_no IS NULL)            AS null_contract,
       SUM(company_id = 1)                 AS company_fallback
  FROM history_control
 WHERE op_command = 'CREV0003'
   AND request_date >= '2026-01-01'
 GROUP BY ym
 ORDER BY ym;
```

**판정**: 2026-06을 기점으로 `null_contract`와 `company_fallback`이 치솟으면 이 회귀가 원인으로 확정된다. 2026-05 이전에도 비슷한 수치가 나오면 다른 원인이 섞여 있는 것이므로 결론을 바꿔야 한다.

### 쿼리 2 — 연동 / 연동 해제 구분

```sql
SELECT DATE_FORMAT(request_date, '%Y-%m') AS ym,
       CASE WHEN op_param LIKE '%unregistered%' THEN 'unlink'
            WHEN op_param LIKE '%registered%'   THEN 'link'
            ELSE 'unknown' END              AS kind,
       COUNT(*)                             AS total,
       SUM(contract_no IS NULL)             AS null_contract
  FROM history_control
 WHERE op_command = 'CREV0003'
   AND request_date >= '2026-01-01'
 GROUP BY ym, kind
 ORDER BY ym, kind;
```

`'%unregistered%'`를 먼저 판정해야 한다. `unregistered`는 `registered`를 부분 문자열로 포함하므로 순서를 바꾸면 전부 `link`로 잡힌다.

**기대**: `unlink` 쪽에 `null_contract`가 몰려 있어야 한다. `link` 쪽에도 많다면 연동 시점에도 `linked_yn`이 아직 `'Y'`가 아닌 경로가 있다는 뜻이므로 추가 조사가 필요하다.

### 쿼리 3 — 소급 보정 가능 여부 (지금도 계약을 찾을 수 있는가)

```sql
SELECT COUNT(*)                                          AS null_rows,
       SUM(c.contract_no IS NOT NULL)                    AS recoverable,
       SUM(c.contract_no IS NULL)                        AS unrecoverable,
       SUM(c.contract_no IS NOT NULL AND c.company_id IS NOT NULL)
                                                         AS recoverable_with_company
  FROM history_control h
  LEFT JOIN (SELECT serial, MAX(id) AS device_id
               FROM device
              GROUP BY serial) x ON x.serial = h.serial
  LEFT JOIN device   d ON d.id = x.device_id
  LEFT JOIN contract c ON c.id = d.contract_id
 WHERE h.op_command = 'CREV0003'
   AND h.contract_no IS NULL
   AND h.request_date >= '2026-06-01';
```

`MAX(id)`로 serial당 최신 `device` 행을 집는 것은 매퍼의 `ORDER BY dev.id DESC LIMIT 1`과 같은 규칙을 재현한 것이다. `unrecoverable`은 기기 행이나 계약이 이미 사라져 복원 불가한 건수다.

### 쿼리 4 — 소급 보정 UPDATE (§9-1의 3번을 결정한 뒤에만 실행)

**결정 없이 실행하지 말 것.** 먼저 쿼리 3으로 규모와 복원 가능성을 확인하고, `history_control` 이력을 읽는 쪽(정산·통계)이 있는지 확인한다.

```sql
-- 4-1) 드라이런: 실제로 무엇이 바뀌는지 먼저 본다
SELECT h.id, h.serial, h.request_date,
       h.company_id  AS company_id_before, c.company_id  AS company_id_after,
       h.contract_no AS contract_no_before, c.contract_no AS contract_no_after
  FROM history_control h
  JOIN (SELECT serial, MAX(id) AS device_id FROM device GROUP BY serial) x
       ON x.serial = h.serial
  JOIN device   d ON d.id = x.device_id
  JOIN contract c ON c.id = d.contract_id
 WHERE h.op_command = 'CREV0003'
   AND h.contract_no IS NULL
   AND h.request_date >= '2026-06-01'
   AND c.company_id IS NOT NULL
 ORDER BY h.id
 LIMIT 50;

-- 4-2) 보정 실행
UPDATE history_control h
  JOIN (SELECT serial, MAX(id) AS device_id FROM device GROUP BY serial) x
       ON x.serial = h.serial
  JOIN device   d ON d.id = x.device_id
  JOIN contract c ON c.id = d.contract_id
   SET h.contract_no = c.contract_no,
       h.company_id  = c.company_id
 WHERE h.op_command = 'CREV0003'
   AND h.contract_no IS NULL
   AND h.request_date >= '2026-06-01'
   AND c.company_id IS NOT NULL;
```

- `c.company_id IS NOT NULL` 조건은 필수다. `contract.company_id`는 nullable인데 `history_control.company_id`는 **NOT NULL**이라, 빼면 UPDATE가 제약 위반으로 터진다.
- 기기가 다른 계약으로 재연동된 뒤라면 보정값이 **당시 계약이 아니라 현재 계약**이 된다. 이 한계를 감수할 수 있는지가 보정 여부 판단의 핵심이다. 감수 못 하면 보정하지 말고 NULL을 "계약 불명"으로 남겨 두는 편이 낫다.
- 실행 전 대상 행을 별도 테이블로 떠 두는 것을 권한다: `CREATE TABLE history_control_contractno_backup_20260922 AS SELECT id, company_id, contract_no FROM history_control WHERE op_command='CREV0003' AND contract_no IS NULL AND request_date >= '2026-06-01';`

### 쿼리 5 — 환경별 스키마 drift 실측 (각 환경에서 따로 실행)

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, IS_NULLABLE, COLUMN_TYPE, COLUMN_DEFAULT
  FROM information_schema.COLUMNS
 WHERE TABLE_NAME = 'history_control'
   AND COLUMN_NAME IN ('contract_no', 'company_id');

SHOW CREATE TABLE history_control;
```

2026-09-02 실측 결과는 dev만 `contract_no` NOT NULL, stg는 nullable이었다. **현재도 같은지 다시 확인하고 결과를 기록해 둘 것.**

### 쿼리 6 — 탈퇴 완주 추이 (참고)

```sql
SELECT DATE_FORMAT(exit_date, '%Y-%m') AS ym, COUNT(*) AS withdrawals
  FROM `user`
 WHERE delete_yn = 'Y'
   AND exit_date IS NOT NULL
 GROUP BY ym
 ORDER BY ym;
```

운영계는 2026-06 이후에도 탈퇴가 끊기지 않았을 것이다(nullable이라 조용히 통과했으므로). 만약 2026-06을 기점으로 급감했다면 전제가 틀린 것이므로 조사를 다시 해야 한다.

---

## 부록 C. 개발계 재현 절차

**재현은 개발계에서만 된다.** 검증계·운영계는 NULL을 받아줘 에러가 나지 않는다.

**필요 조건**

| 항목 | 값 |
|---|---|
| 기기 타입 | **ABT** (제품 표기는 **A1**, 로봇). `AQM`/`AQM_WLS`는 에어센서라 경로가 다르다 |
| 빌딩 권한 | **`01`**(중앙관리자). `01`이라야 `deleteBuilding` → `updateDevicelink` → `requestUnLinkAbtDevice` 경로를 탄다. 그 외 권한은 "건물 나가기"라 연동 해제가 일어나지 않는다 |
| 스키마 | 개발계 `history_control.contract_no`가 NOT NULL이어야 한다(쿼리 5로 먼저 확인) |
| 앱 버전 | 서버 응답만 보면 되므로 앱 없이 curl로도 가능 |

**절차**

1. 개발계에 신규 계정을 만들고 집(빌딩)을 생성해 권한 `01`을 확보한다.
2. ABT 기기를 그 집에 연동한다. `SELECT id, serial, contract_id, linked_yn FROM device WHERE serial='<serial>';`로 `linked_yn='Y'`를 확인한다.
3. 탈퇴를 호출한다.

```bash
curl -s -i -X PUT \
  -H "IdToken: <dev Cognito ID Token>" \
  -H "Content-Type: application/json" \
  -d '{"exitCode":"WTHD0001","exitReason":""}' \
  "https://<dev 앱 API 호스트>/app/user/delete"
```

4. 결과 판정

| 형상 | 기대 응답 | 기대 DB |
|---|---|---|
| 수정 전 (`ee65e763` ~ `1b814394` 이전) | **200 + `success:true`** (실제로는 실패인데 성공으로 보인다) | `history_control`에 CREV0003 행이 **없다**. `user.delete_yn` 상태가 중간에 멈춰 있다 |
| `1b814394`만 적용 | **500 + `success:false`**, 최상위 `message`가 한글 안내 문구 | 위와 같다 |
| 둘 다 적용 (현재 dev/stg) | **200 + `success:true`** | `history_control`에 CREV0003 행이 있고 `contract_no`가 채워져 있다. `company_id`도 `1`이 아닌 실제 값 |

5. 이력 확인

```sql
SELECT id, login_id, serial, company_id, contract_no, op_command,
       LEFT(op_param, 120) AS op_param_head, request_date
  FROM history_control
 WHERE serial = '<serial>'
   AND op_command = 'CREV0003'
 ORDER BY id DESC
 LIMIT 5;
```

**IdToken 확보** — 개발계 앱 빌드의 네트워크 로그에서 `IdToken` 헤더를 복사하는 것이 가장 빠르다(기본 만료 1시간). 앱 없이 받으려면 dev Cognito 앱 클라이언트가 client secret을 쓰므로 `SECRET_HASH` 계산이 필요하다.

```bash
CLIENT_ID=<dev 앱 클라이언트 ID — backend-api-main dev 설정에서 확인>
SECRET=<aws.cognito.client.secret — SSM /backend-api-main/dev/ 에서 확인>
USER=<전화번호 형태의 로그인 아이디>
HASH=$(printf "%s%s" "$USER" "$CLIENT_ID" \
        | openssl dgst -sha256 -hmac "$SECRET" -binary | base64)
aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id "$CLIENT_ID" \
  --auth-parameters USERNAME=$USER,PASSWORD=<pw>,SECRET_HASH=$HASH \
  --query 'AuthenticationResult.IdToken' --output text
```

AWS CLI는 로컬 맥에 설치돼 있지 않다(2026-09-14 확인). VDI에서 실행한다.

**주의**: 탈퇴는 되돌릴 수 없다. 재현할 때마다 새 계정을 만든다. 같은 전화번호로 재가입하면 §9-3의 10번(탈퇴 행 부활) 상태가 되어 관찰이 오염된다.
