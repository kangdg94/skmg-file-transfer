# [작업] skix-medcare 소유자 키 통합 (owner-unification)

| 항목 | 내용 |
|---|---|
| **상태** | 코드 완료 · **개발계·검증계 롤아웃 P6 완주(2026-08-26)** · **운영계 미착수(`main` 미반영, P0 게이트 미해제)** |
| **작업 기간** | 2026-08-24 ~ 2026-08-31 (설계·실측은 08-24부터, 마지막 관련 커밋 08-31) |
| **직접 수정한 저장소** | `skix-medcare`, `backend-api-main` |
| **요청서를 전달한 대상** | 없음 (앱·프론트 변경 없음. 사용자 체감 변화 공지만 CS 협의 대상 — §7-3) |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §2-2 표에서 **검증계 실기기 확인 1건**이 남아 있는지 보고, §7-2 운영계 진입 체크리스트로 P0 게이트 두 항목(token-pii 운영계 배포 · `user_accounts` 운영계 백필)의 현재 상태를 확인한다 |

---

## 0. 세 줄 요약

1. My Healthcare(AI 건강 상담) 사용자 데이터가 **로그인 수단마다 달라지는 Cognito sub**을 소유자 키로 저장해, 같은 사람이 전화번호로 만든 대화를 카카오로 로그인하면 못 보는 상태였다. 이것을 **대표 sub(canonical owner key) 하나로 통합**했다.
2. 두 저장소 코드는 **개발계·검증계 브랜치에 전부 반영**됐고, 두 환경 모두 **플래그 ON → SQL 재키잉 → 병합 배치까지(P0~P6) 완주**해 최종 어긋남 0(`hasDrift()=false`)을 확인했다. **운영계 브랜치(`main`)에는 아직 없다.**
3. 남은 것은 **검증계 실기기 최종 확인 1건**과 **운영계 롤아웃**이며, 운영계는 다른 트랙(Cognito 토큰 PII 제거)의 운영계 배포와 `user_accounts` 운영계 백필이 끝나야 착수할 수 있다(P0 게이트).

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **skix-medcare (My Healthcare)** | 앱 안의 AI 건강 상담 서비스 백엔드. Spring WebFlux + R2DBC, 자체 `medcare` 스키마 단독 사용. 메인 DB(`benjamin`)에 직접 접근할 수 없다 |
| **케이론(Cheiron)** | 상담 대화를 실제로 생성·보관하는 외부 AI 플랫폼. 우리가 수정할 수 없는 블랙박스다 |
| **kmeta / 내재화** | 케이론의 대화를 우리 DB(`kmeta_streams`·`kmeta_chats` 등)로 복제해 목록·검색을 우리가 서비스하는 것. "내재화(convert)"라고 부른다 |
| **sub** | Cognito가 계정 하나에 발급하는 불투명 UUID. 이 서비스의 사용자 데이터 소유자 키다 |
| **sibling sub** | 같은 사람이 다른 로그인 수단(전화번호/카카오/네이버/구글/애플)으로 가입·연동해 받은 **다른 sub**들 |
| **canonical / OwnerKey (대표 sub)** | 한 사람의 sibling sub 중 하나를 정식 소유자 키로 고른 것. 규칙은 `user_accounts`에서 그 `user_id`의 **최초 행**(`ORDER BY create_date, id` 첫 행)의 sub |
| **OwnerScope** | 조회·수정·삭제·잠금에 쓰는 **집합**. canonical + sibling 전체 |
| **CheironUid** | 케이론 호출용 `X-API-UID` 헤더 값. 그 대화가 실제로 사는 파티션의 raw sub |
| **`user_accounts`** | `backend-api-main` 메인 DB의 표. `user_id` ↔ `sub` 1:N 매핑의 **권위**다. 활성 사용자 생명주기 안에서 append-only이고 탈퇴 시 hard delete |
| **재키잉(rekey)** | 기존 행의 소유자 컬럼을 sibling sub → canonical로 일괄 변경하는 SQL 작업(P5) |
| **drift** | 케이론과 우리 DB의 대화 수 대사 보고. `missingInKmeta`(케이론에 있는데 우리에 없음)·`staleInKmeta`(우리에 있는데 케이론에 없음) 등 |
| **P0~P6** | 이 트랙의 롤아웃 단계 번호. 아래 §6에서 쓴다 |

### 1-2. 문제

Cognito에 `AdminLinkProviderForUser`(계정 연결)를 쓰지 않는 구조라, **같은 사람이 로그인 수단을 바꾸면 다른 sub**을 받는다. medcare는 이 raw sub을 그대로 소유자 키로 저장해 왔다.

결과:

- 전화번호로 만든 대화·폴더가 **카카오로 로그인하면 목록·검색에 안 보인다.**
- 그 대화를 열 수도, 이어서 질문할 수도 없다.
- 채팅 한도·폴더 개수 상한이 **sub 단위**라 로그인 수단을 갈아타면 한도를 두 번 쓸 수 있었다.

규모(2026-08-25 운영계 실측):

| 항목 | 값 |
|---|---|
| 활성 사용자 | 7,278명 (`user_accounts` 8,846행) |
| **복수 sub 보유자** | **1,399명 (19.2%)** — sub 2개 1,246 / 3개 140 / 4개 10 / 5개 3 |
| medcare 사용자 | 약 420명 (서비스 오픈이 최근이라 전체 사용자 대비 적다) |
| **실제로 데이터가 갈라져 있던 사용자** | **4명 · 대화 12건** |

**이 두 숫자의 차이가 이 트랙의 성격을 정한다.** 지금 당장의 피해는 4명·12건으로 작지만, medcare 사용자가 늘수록 19.2%의 복수 sub 보유율이 그대로 파편화율로 전환된다. 즉 **피해 복구가 아니라 예방**이고, 마이그레이션 대상이 적은 지금이 적기다. 긴급도는 낮고 적기성은 높다.

### 1-3. 왜 케이론 쪽에서 못 고치나 (설계의 출발점)

케이론은 **자체가 sub 파티셔닝**이다. 모든 호출이 `X-API-UID` 헤더에 sub을 싣고, 다른 sub의 대화를 요청하면 404다(dev 2계정 실측). 케이론은 수정할 수 없으므로 **케이론에서 병합하는 선택지가 없다.**

→ 그래서 **내재화(kmeta) 층에서 sibling들의 코퍼스를 하나의 소유자 키 아래로 모으고, 케이론 호출만 원래 파티션 sub으로 보내는** 구조를 택했다. 그러려면 "이 대화가 어느 케이론 파티션에 사는가"를 행 단위로 기억해야 한다 → `kmeta_streams.cheiron_sub` 신설.

### 1-4. 목표

- 같은 사람이면 로그인 수단과 무관하게 **같은 대화·검색·폴더·사용 이력**을 본다.
- 다른 provider로 만든 과거 대화도 **이어가기·제목 변경·삭제**가 된다.
- 한도(채팅·폴더)는 **사람 단위**로 합산된다(의도된 변화).
- 탈퇴 파기는 sibling 파티션까지 **빠짐없이** 지운다.
- 기존 앱·프론트 계약은 **바뀌지 않는다**(앱 작업 0건).
- 롤아웃은 **단계마다 되돌릴 수 있게** 한다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 서버 코드

| 저장소 | dev | stg | main(운영) | 근거 |
|---|---|---|---|---|
| `backend-api-main` | **반영** (머지 `829c84eb`, 피처 `dea4fdd8`) | **반영** (`7c7275ef` = dev→stg 머지) | **미반영** | `git merge-base --is-ancestor`로 전 커밋 확인 |
| `skix-medcare` | **반영** (머지 `65a0eaa` + 후속 `c74f965`·`5e8e205`·`4971f08`) | **반영** (`d600dd5` = dev→stg 머지, 후속 커밋 전부 포함) | **미반영** | 동일 |

- 두 저장소 모두 피처 브랜치 `feature/owner-unification`이 원격에 **보존**돼 있다. `main` 승격은 이 브랜치를 `main`에 직접 머지하는 관례를 따른다.
- 리베이스 전 상태 태그가 두 저장소에 있다: `backup/pre-rebase-owner-unification-20260825`.
- 두 머지 모두 `--no-ff`다 → 되돌릴 때 `git revert -m 1 <머지커밋>` 하나로 끝난다. **단 backend는 이제 단독 revert가 위험하다(§2-4).**
- 브랜치 격차(실측): medcare `main`은 dev보다 30커밋 뒤, stg보다 37커밋 뒤. backend `main`은 dev보다 346커밋 뒤(운영 브랜치가 8/6 형상으로 되돌려져 있다 — 이 트랙과 무관한 별건).

### 2-2. 롤아웃 진행 (P0~P6)

| 단계 | 개발계 | 검증계 | 운영계 |
|---|---|---|---|
| **P0** 선행 게이트 확인 | 완료 | 완료 | **미완** (§7-2) |
| **P1** backend identity API 배포 | 완료 | 완료 | 미착수 |
| **P2** medcare 호환 배포(플래그 OFF) + DDL + backfill | 완료 | 완료 | 미착수 |
| **P3** `alias-read` ON | 완료 | 완료 | 미착수 |
| **P4** `canonical-write` ON | 완료 | 완료 | 미착수 |
| **P5** SQL 재키잉 | 완료 | 완료 | 미착수 |
| **P6** 병합 convertAll + drift | 완료 | 완료 | 미착수 |
| **P6-말** 실기기 최종 확인 | 완료 | **미완** | 미착수 |

### 2-3. 환경별 실행 결과 (2026-08-26)

| | 개발계 | 검증계 |
|---|---|---|
| P6 convertAll | 파티션 724 · 대화 545 · 성공 545 · 실패 0 | 파티션 376 · 대화 934 · 성공 934 · 실패 0 |
| drift 최종 | `hasDrift()=false` (잔존 59건 **정리 후**) | `hasDrift()=false` (**첫 시도 클린**) |
| `cheironTotal` = `kmetaTotal` | 540 = 540 | 934 = 934 |
| CONFLICT | 0 | 0 |
| 미통합 잔여 | sub 4개 · 대화 159건 | (기록 없음 — 이상 없음) |
| 실제로 갈라졌던 사용자 | 1명 (sub 2개 · 대화 72건) | — |
| `stalePendingChats` | 27 (판정식 밖) | 23 (판정식 밖) |

> **검증계가 첫 시도에 잔존 0으로 나온 것이 개발계 진단을 뒤에서 확증한다.** 같은 코드·같은 절차·더 많은 데이터(934 vs 540)인데 어긋남이 하나도 없었다. 개발계의 잔존 59건은 코드 결함이 아니라 그 환경의 이력 때문이었다(§8의 "잔존" 항목).
>
> **`cheironTotal == kmetaTotal`이 이 트랙에서 가장 강한 확인**이다. 앞은 파티션마다 케이론에 물어 합산한 값이고 뒤는 우리 테이블 전역 카운트라 **계산 경로가 완전히 다른데** 일치했다.

### 2-4. 플래그 실상태 (저장소 파일 기준 실측)

세 환경 ECS 태스크 정의 파일에 플래그 2종이 들어 있다.

| 환경 | 파일 | `IDENTITY_ALIAS_READ_ENABLED` | `IDENTITY_CANONICAL_WRITE_ENABLED` |
|---|---|---|---|
| dev | `deploy/task-definition-dev.json` | `true` | `true` |
| stg | `deploy/task-definition-stg.json` | `true` | `true` |
| **prd** | `deploy/task-definition-prd.json` | **`false`** | **`false`** |

2026-08-31 커밋 `5e8e205`가 dev·stg 파일을 실상태(ON)에 맞췄다. **운영계는 의도적으로 `false`다.**

> ⚠️ **실제 ECS 태스크 정의가 이 파일과 같은지는 로컬에서 확인할 수 없다(미확인).** 회사 VDI에서
> `aws ecs describe-task-definition --task-definition skix-medcare-{env}-tdef --query 'taskDefinition.containerDefinitions[0].environment'`
> 로 확인한다.
>
> ⚠️ **배포 스크립트 `deploy/cloudshell-push.sh`는 저장소 JSON을 등록하지 않는다.** `describe-task-definition`으로 **이미 등록된 최신 정의를 그대로 재사용**해 `update-service`만 한다. 즉 저장소 JSON의 값은 누군가 `register-task-definition`을 명시적으로 실행할 때만 반영된다(P3·P4 절차가 그것이다). 개발계 태스크에 과거 우회로 넣었던 `IDENTITY_BACKFILL_PAGE_SIZE=50`이 아직 남아 있을 수 있는 것도 같은 이유다 — **무해하니 다음에 태스크 정의를 손볼 때 제거하면 된다**(현재 코드는 페이지 크기와 무관하게 안전하다).

### 2-5. 메모리·문서와 실측이 달랐던 점

인수자가 옛 메모나 커밋 메시지를 인용하기 전에 알아야 할 것들이다.

| 항목 | 옛 기록 | 2026-09-22 실측 |
|---|---|---|
| medcare `c74f965`(backfill 순차 처리) | "dev, 미push" | **dev·stg 모두 반영** |
| 플래그 커밋 해시 | `dd15d55` / `9937e96` | 그 해시는 **어느 브랜치에도 없다**(리베이스 전 해시). 실제는 `bd7e849` / `3ee2e4b` |
| backend 피처 커밋 해시 | `8712356d` | 그 해시는 없다. 실제는 `dea4fdd8`(머지 `829c84eb`) |
| 세 환경 태스크 정의 플래그 "전부 false" | 그렇게 기록됨 | **dev·stg는 `true`**로 바뀌어 있다(`5e8e205`, 08-31). prd만 `false` |
| "PHR 오픈 전 체크리스트" 3개 항목 | 미착수로 기록됨 | **`4971f08`(08-31, dev·stg)로 이미 닫혔다** — `PhrApiController`·`PhrService`·`ChatService.resolvePhrForChat`이 `OwnerContext`로 전환됐다(§7-3) |
| backend identity API 소비자 | medcare 단독 | **소비자가 2개다.** `ee2b1fc5`(08-27, dev·stg)가 파트너 오픈API 연동 키를 canonical sub로 정규화하며 `SubIdentityResolver`를 쓴다. 현재 `SubIdentityResolver` 참조처는 `InternalUserController`·`OpenApiClientService`·`OpenApiUserService` 3곳 → **backend 머지 커밋의 단독 revert는 이제 다른 트랙을 깬다** |

---

## 3. 무엇을 만들었나

### 3-1. 전체 흐름

```
[요청 경로 — 매 요청]
  앱 → medcare  (Cognito ID Token, sub 하나)
    ① CurrentUserArgumentResolver
         IdentityResolutionService.resolve(sub)
           L2: medcare_user_identity PK 단건 조회        ← 1차 원천
           L2 miss → L3: backend GET /internal/user/identity?sub=
                          → {ownerKey, aliases[]} 를 레지스트리에 전량 upsert
           L3 실패 + L2 miss → 폴백 ownerKey=sub, scope={sub}  (현행 동작과 동일)
           그룹이 CONFLICT + alias-read ON → 요청 거부(fail-closed)
    ② AuthenticatedUser(loginUid, ownerKey, ownerScope) 조립 → OwnerContext
    ③ 조회·잠금·삭제는 ownerScope 로 IN(...)   (alias-read 플래그)
       신규 행의 소유자 도장은 ownerKey        (canonical-write 플래그)
       케이론 호출 UID 는
           신규 대화 → 지금 로그인한 sub
           기존 대화 → 그 대화의 kmeta_streams.cheiron_sub

[배치 경로]
  identity backfill (매일 02:30 · 수동 호출 가능)
    backend GET /internal/user/identities?afterUserId=&limit=  페이징 전량 순회
      → 레지스트리 upsert + 권위와 대조 → 달라졌으면 그룹 CONFLICT 전환 + 알람
      → 응답에 없는 기존 행은 지우지 않는다

  병합 convertAll (owner 단위)
    대상 owner = 레지스트리 distinct owner_key(NORMAL만)
      owner 별 접근 sub = sibling 전체 ∪ 그 owner 의 kmeta distinct cheiron_sub
        각 sub 으로 케이론 목록 조회 → 누락분 상세 조회 → ownerKey 소유로 저장

[탈퇴 파기]
  backend UserService.purgeMedcareOnWithdraw
    (user_accounts 삭제 **전에**, sibling sub 마다 개별 호출, 한 건 실패해도 계속)
      → medcare 파기 후보 = 요청 sub ∪ 레지스트리 그룹(CONFLICT 포함)
                          ∪ 권위 응답 그룹 ∪ 후보들의 distinct cheiron_sub
      → 파티션마다 케이론 삭제(멱등) → 로컬 삭제 → 레지스트리 행은 **맨 마지막**
```

### 3-2. 저장소별 역할

경계 원칙: **신원의 권위는 `backend-api-main`(메인 DB `user_accounts`), 그 사본과 적용은 `skix-medcare`.** medcare는 메인 DB에 접근할 수 없어 내부 API가 유일한 통로다.

| 책임 | 저장소 | 파일 (`src/main/java/...` 하위) |
|---|---|---|
| canonical 선정 규칙(**단독 소유**) | backend-api-main | `com/skmagic/core/component/SubIdentityResolver.java` |
| identity 내부 API 2종 | backend-api-main | `com/skmagic/api/user/controller/InternalUserController.java` |
| `user_accounts` 직접 조회 매퍼 | backend-api-main | `com/skmagic/api/user/mapper/UserMapper`(`findActiveUserIdBySub`·`findAccountsByUserId`·`findActiveUserIdsAfter`·`findAccountsByUserIds`) |
| 값 타입 3종 + 요청/배치 공용 컨텍스트 | skix-medcare | `domain/identity/OwnerKey.java`·`OwnerScope.java`·`CheironUid.java`·`OwnerContext.java` |
| 신원 주입 | skix-medcare | `security/CurrentUserArgumentResolver.java`·`security/AuthenticatedUser.java` |
| 해석(L2→L3)·CONFLICT 전환·폴백 | skix-medcare | `application/service/identity/IdentityResolutionService.java` |
| 레지스트리 backfill·일일 재검증 | skix-medcare | `application/service/identity/IdentityBackfillService.java`·`IdentityBackfillScheduler.java` |
| 레지스트리 저장소·엔티티 | skix-medcare | `adapter/out/persistence/repository/IdentityRegistryRepository.java`·`entity/UserIdentityEntity.java` |
| owner 단위 병합·drift·파티션 대사 | skix-medcare | `application/service/ConvertService.java`·`DriftCheckService.java` |
| 케이론 UID 분리·이어가기·한도 키 | skix-medcare | `application/service/ChatService.java`·`ConversationService.java` |
| 파기 후보 합집합·신고 보존·레지스트리 마지막 삭제 | skix-medcare | `application/service/UserDataPurgeService.java` |
| 내부 API 수신(backfill·convertAll·drift·purge) | skix-medcare | `adapter/in/web/InternalApiController.java` |
| 레지스트리 DDL / 파티션 컬럼 DDL | skix-medcare | `src/main/resources/db/medcare_user_identity.sql` · `db/kmeta_streams.sql` |
| 플래그·ECS 환경변수 | skix-medcare | `src/main/resources/application.yml`·`application-ecs.yml`·`deploy/task-definition-{dev,stg,prd}.json` |

### 3-3. 데이터

**신규 테이블 — `medcare_user_identity`** (medcare DB)

```sql
CREATE TABLE IF NOT EXISTS `medcare_user_identity` (
    `sub`              varchar(128) NOT NULL,
    `owner_key`        varchar(128) NOT NULL,
    `status`           varchar(20)  NOT NULL DEFAULT 'NORMAL',
    `last_verified_at` datetime(6)  NOT NULL COMMENT 'UTC - 권위(backend)로 확인한 시각',
    `create_date`      datetime(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'UTC - 생성 시각',
    `update_date`      datetime(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                       ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'UTC - 수정 시각',
    PRIMARY KEY (`sub`),
    KEY `idx_identity_owner` (`owner_key`),
    KEY `idx_identity_status` (`status`),
    CONSTRAINT `ck_identity_status` CHECK (`status` IN ('NORMAL', 'CONFLICT'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

역할 네 가지: ①요청 경로의 1차 해석(L2) ②배치의 `owner → subs` 열거 원천 ③**backend가 계정을 지운 뒤에도 파기 재시도가 끝날 때까지 sibling을 아는 장부** ④재키잉 SQL이 JOIN하는 매핑 원장.

**신규 컬럼 — `kmeta_streams.cheiron_sub varchar(128) NULL`**

그 대화가 사는 케이론 파티션의 **raw sub**을 행 단위로 영속한다. 기존 대화의 이어가기·PATCH·DELETE·read-repair는 이 값을 `X-API-UID`로 보낸다.

**플래그 2종**

| 프로퍼티 | ECS 환경변수 | 의미 | 켜는 순서 |
|---|---|---|---|
| `identity.alias-read-enabled` | `IDENTITY_ALIAS_READ_ENABLED` | 조회·잠금 범위 = canonical + sibling 전체(`IN`) | 먼저(P3) |
| `identity.canonical-write-enabled` | `IDENTITY_CANONICAL_WRITE_ENABLED` | 신규 행의 소유자 도장 = canonical | 나중(P4) |

기본값은 코드·`application.yml` 둘 다 `false`. **canonical-write를 단독으로 켜면 코드가 무시하고 기동 로그에 경고를 남긴다** — canonical로 적은 신규 행을 등호 조회가 못 봐서 방금 보낸 질문이 사라져 보이기 때문이다.

**배치 스케줄** (`Asia/Seoul`, 전부 `BatchExecutionLock` 공유)

| 배치 | 기본 시각 | 프로퍼티 |
|---|---|---|
| identity 재검증(backfill 재실행) | 매일 02:30 | `identity.backfill-cron` |
| reconcile(자동 내재화) | 일요일 03시 | `drift.auto-convert` 로 끔 |
| drift 점검 | 매일 04시 | `drift.check-cron` |

**재키잉(P5) 대상 5개 테이블 · 컬럼** — 전부 `utf8mb4_0900_ai_ci`라 JOIN에 `COLLATE` 명시가 필요 없다.

| 테이블 | 소유자 컬럼 | 재키잉 시 PK로 쓰는 값 |
|---|---|---|
| `kmeta_streams` | `user_strid` | `strid` |
| `kmeta_conversation_search` | `auth_sub` | `stream_strid` |
| `customer_folders` | `folder_auth_sub` | `folder_id` |
| `customer_folder_chat` | `chat_auth_sub` | `chat_id` |
| `customer_if_log` | `customer_if_auth_sub` | `customer_if_id` |

**제외 — 미서비스 3종**(2026-08-25 운영계 기준 전부 0건): `customer_if_phr`(+`_data`), `customer_chat_rating`, `customer_chat_report`. **데이터 마이그레이션만 면제**되고 코드 규칙(canonical-write)은 동일하게 적용된다. 평가·신고 소유자 컬럼은 `utf8mb4_bin`이라 대상에 넣었다면 `COLLATE` 처리가 필요했을 것인데, 제외로 그 복잡도도 함께 사라졌다.

### 3-4. 실측으로 확정된 전제 (Gate 0, 2026-08-25 운영계)

코드를 쓰기 전에 확인한 것들이다. **설계가 이 실측 위에 서 있으므로 운영계 재실행 전에 여기 적힌 값이 여전히 성립하는지 본다.**

| # | 확인한 것 | 결과 | 왜 중요한가 |
|---|---|---|---|
| G-1 | `user_accounts.sub` 길이 | max 36자, 50자 초과 0, 128자 초과 0 (8,846행) | 신규 컬럼 `varchar(128)` 안전, 기존 `varchar(50)` 컬럼에도 canonical이 들어간다 |
| G-2 | canonical 결정성 | 8,846행 / 고아 sub 0 / 사용자 7,278명 | 전원 canonical 계산 가능 |
| G-3 | 복수 sub 규모 | 1,399명(19.2%), 최대 5개 | alias-read의 `IN` 절이 2개로 끝나지 않는다 |
| G-5 | medcare distinct sub | streams 413 · search 413 · folders 6 · folder_chat 4 · rating 0 · report 0 · if_log 421 | 평가·신고 0건 = 미서비스 확정 → 대상 5개 테이블로 축소 |
| G-6 | 소유자 컬럼 비정상 값 | 0 | 재키잉 JOIN 누수 없음 |
| **G-5c** | **유령 sub**(medcare엔 있는데 `user_accounts`엔 없는 sub) | **60건** | §3-5 |
| G-7a | 폴더채팅 유니크 충돌 | 0행 | **유일한 충돌 위험 지점.** distinct sub이 4명뿐이라 표본 부족 → **P5 직전 재실행 필수** |
| G-8 | pending-turn 간섭 | `response IS NULL` 중 FAILURE 8건뿐, pending 술어 해당 0 | 재키잉이 살아있는 pending과 겹칠 우려 없음 |
| **M-1** | 케이론이 `X-API-UID`를 파티션 지시자로만 쓰는가 | 상세·PATCH·이어가기·삭제 **전부 정상** | **`cheiron_sub` 설계의 전제.** 케이론은 세션·발급 시각·토큰 주체를 검증하지 않는다 → 과거 다른 sub의 대화도 그 `cheiron_sub`을 UID로 보내면 이어가기·수정·삭제가 된다 |
| M-2a | 대화 strid 형식 | 755건 **전부 36자 UUID** | kmeta PK가 strid 단독이라, 파티션 로컬 번호였으면 병합 시 PK 충돌이 난다 |
| M-2a-2 | 답변 strid 구조 | 1,128건 전부 `{대화strid}_{uuid}`(73자, 구분자 언더스코어) | **대화에 종속**되어 대화가 유일하면 자동으로 유일 — 순수 UUID보다 강한 보증 |
| M-2c | sibling 파티션 간 strid 교집합 | 표본 3건 전부 상이, 교집합 0 | 부수로 케이론 `total_count`가 DB `convs`와 일치해 그 파티션 내재화 완전 확인 |

### 3-5. 유령 sub 60건 — 규명 완료

"medcare 소유자 컬럼에는 있는데 `user_accounts` 매핑에는 없는 sub" 60건의 정체를 가렸다.

| 판정 | 건수 | 의미·조치 |
|---|---|---|
| **A. 운영계 백필로 해소** | **56** | `user.sub`은 갖고 있고 활성 계정. P0 게이트의 `user_accounts` 운영계 백필로 자동 소멸 |
| **C. 탈퇴자 잔여** | **1** | 탈퇴했는데 medcare 데이터가 남았다 = 파기 미연동 확증. 병합 대상이 아니다(별건, 규모 1건) |
| **B. 2nd provider / 미상** | **3** | `user.sub`에도 없다 → 백필로도 해소 안 됨. **전원 90일 내 활동**이라 그 provider로 재로그인하면 자가치유된다 |

- 대사 확인: 백필 대상 모집단(활성 + `user_accounts` 결손)과 A가 **정확히 같은 집합**(`backfill_covered = 56`).
- 60건 **전원이 90일 내 활동**(30일 내 26건, 활동 로그 없음 0건). 유휴·죽은 계정이 아니다.
- → **커버리지 구멍은 3건**(medcare 사용자 약 420명의 0.7%). 이 수치가 "identity API가 `SubLoginIdResolver`를 우회하고 `user_accounts`만 본다"는 설계 결정을 유지할 근거다(§5의 2번).

### 3-6. 신규 발견 — collation 혼재 (매퍼 작성 시 함정)

| 테이블·컬럼 | collation |
|---|---|
| `user.sub` (메인, 레거시) | `utf8mb4_0900_ai_ci` |
| **`user_accounts.sub` (메인, 권위)** | **`utf8mb4_general_ci`** |
| 재키잉 대상 5개 컬럼 (medcare) | `utf8mb4_0900_ai_ci` (전부 동일) |
| `customer_chat_rating.rating_auth_sub` · `customer_chat_report.report_auth_sub` | `utf8mb4_bin` (컬럼 명시) |

**`user_accounts`와 `user`를 `sub`으로 조인하면 `Illegal mix of collations`(1267)로 실패한다.** identity API 매퍼는 `findActiveUserIdBySub`로 `user_id`(bigint)를 얻어 그걸로만 `user`를 잇도록 설계돼 **sub 조인이 없어 안전**하지만, 나중에 누가 sub 조인을 추가하면 런타임에서야 터진다. 매퍼 XML에 이 제약이 주석으로 있다.

부수 사실: `general_ci`는 대소문자를 무시하므로 `user_accounts.sub`의 UNIQUE는 대소문자를 구분하지 않는다. Cognito sub이 소문자 UUID라 실무상 무해하다.

---

## 4. API 계약

### 4-1. backend-api-main → 내부 소비자: identity 2종 (신규)

공통: `X-Internal-Api-Key` 헤더 검증. dev·stg는 `enforce-api-key=true`, prd는 `false`(`application-{env}.yml`).

```http
GET /internal/user/identity?sub=<sub>
```

```json
{ "ownerKey": "<대표 sub>", "aliases": ["<대표 sub>", "<sibling sub>", "..."] }
```

- `ownerKey`는 **항상 `aliases`에 포함**된다. `aliases` 순서는 canonical 선정 순서 그대로다.
- **매핑이 없으면 `404`다. `200` + `null`이 아니다.** 소비자는 404를 "raw sub 유지(현행 동작)"로 읽는다. 이 구분이 계약의 핵심이다.
- `SubLoginIdResolver`를 **타지 않는다**(§5의 2번).

```http
GET /internal/user/identities?afterUserId=<cursor>&limit=<n>
```

```json
{ "identities": [ { "ownerKey": "...", "aliases": ["..."] }, ... ], "nextCursor": 12345 }
```

- `user.id` keyset 페이징. **`nextCursor`가 `null`이면 마지막 페이지**다.
- `limit`은 1~500으로 클램프된다(기본 200).
- 커서 값은 내부 `user.id`지만 소비자에게는 **불투명 토큰**이다. 해석하지 않는다.
- 활성 사용자 중 계정을 가진 사람만 나온다.

> 응답에 PII가 없다(sub은 불투명 가명). 그래도 공유키를 검증하는 이유는 **이 매핑 자체가 "어떤 계정들이 같은 사람인가"라는 연결 정보**이기 때문이다.

### 4-2. medcare 내부 운영 API (이 트랙에서 쓰는 것)

기준 경로 `/medcare/v1/internal`. 헤더 `X-Internal-Api-Key`(필수) + `X-Acting-Admin`(감사용).

| 엔드포인트 | 용도 | 응답 |
|---|---|---|
| `POST /identity/backfill` | 레지스트리 적재 + 권위 대조(일일 재검증과 같은 실행) | **시작 여부만.** 결과는 로그 한 줄, 정본 수치는 테이블 직접 조회 |
| `POST /conversations/convertAll` | owner 단위 병합 배치 | 시작 여부만 |
| `GET /conversations/drift` | 케이론↔로컬 대사 보고 | `DriftReport` JSON |
| `POST /conversations/purge-stale` | 잔존(로컬에만 있는 대화) 진단·정리. **`apply=false`가 기본** | 모의 실행 결과 |
| `POST /users/purge?authSub=` | 탈퇴 파기(backend가 호출). **API 계약 무변경** | 파기 보고 |

`DriftReport` 필드: `users`, `checkFailedUsers`, `cheironTotal`, `kmetaTotal`, `missingInKmeta`, `staleInKmeta`, `ownerlessStreams`, `missingSearchIndex`, `orphanFolderChats`, `stalePendingChats`. `hasDrift()` 판정식에는 `stalePendingChats`가 **들어가지 않는다**.

### 4-3. 앱·프론트 계약

**변경 없음.** 앱은 지금과 똑같이 ID Token 하나를 보낸다. 소유자 키 해석은 전부 서버 안에서 끝난다.

---

## 5. 되돌리면 안 되는 설계 결정

"더 단순해 보이게 바꾸면 사고가 나는" 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **애플리케이션은 기존 행의 소유자를 절대 바꾸지 않는다.** 기존 행은 `OwnerScope`로 찾아 **저장된 owner를 유지**하고, 신규 행만 `OwnerKey`로 찍는다. 소유자 변경은 P5 런북 전유다 | 이 규칙이 깨지면 ①재키잉 전 기존 대화(owner=sibling)에 답변을 저장할 때 **PK 충돌**이 나고 ②소유자 변경이 journal 밖에서 일어나 **되돌릴 수 없게** 된다. 특히 `saveConversationToKmeta`의 root upsert 순서(스코프로 잠금·조회 → 있으면 내용·`cheiron_sub`만 UPDATE, `user_strid` 불변 → 완전히 없을 때만 INSERT)를 단순화하지 말 것 |
| 2 | **identity API는 `SubLoginIdResolver`를 우회하고 `user_accounts`만 본다.** 매핑 없으면 `404` | `SubLoginIdResolver`에는 `user.sub` 레거시 폴백이 있어, 붙이면 "`user_accounts`에 없으면 매핑 없음"이라는 계약이 깨진다. 폴백 없이 남는 구멍은 실측 **3건**(활성이라 재로그인 자가치유)뿐임을 확인하고 내린 결정이다. 200+null로 바꾸는 것도 금지 — 소비자가 404를 "raw sub 유지"로 읽는다 |
| 3 | **`cheiron_sub`에는 raw sub을 저장한다.** 케이론 응답의 `user_strid`를 그대로 넣으면 안 된다 | 케이론이 저장하는 `user_strid`는 `skix_{sub}` 형태(테넌트 접두)다. 우리가 헤더로 보내는 건 접두 없는 raw sub이고 케이론이 저장 시 붙인다. 응답값을 그대로 넣으면 **이후 모든 케이론 호출이 깨진다.** 현재 `Conversation` 도메인 모델은 이 필드를 아예 파싱하지 않아 안전하다 |
| 4 | **플래그는 2개다. 하나로 합치지 않는다.** 그리고 **P3(alias-read)가 전 태스크에 전파된 뒤 P4(canonical-write)를 별도 배포**한다 | 롤링 배포 중 구 태스크가 raw sub로, 신 태스크가 canonical로 써도 **모든 태스크가 alias-read라 어느 쪽 데이터도 사라지지 않는다.** 이 순서가 그 보장의 유일한 근거다. 한 플래그로 합치면 그 창을 만들 수 없고, 롤백 의미도 달라진다(아래 5번) |
| 5 | **P5 이후 두 플래그를 함께 끄면 안 된다.** 장애 시엔 `canonical-write`만 끄고 `alias-read`는 **유지**한다 | canonical로 옮긴 데이터가 sibling 로그인에서 안 보인다. **P4를 한 번이라도 켠 환경에서 alias-read는 운영 필수 호환 계층**이지 기능 토글이 아니다 |
| 6 | **매핑 충돌은 그룹 단위 fail-closed**(alias-read 활성 이후). 요청 sub 하나만 막지 않는다. **미등록 alias도 CONFLICT 행으로 INSERT**한다 | 한 sub만 막으면 다른 sibling 경로로 오귀속 데이터가 계속 노출된다. 미등록 alias를 빼면 운영자가 `status='CONFLICT'`로 교정 대상을 조회할 때 **그룹이 반쪽으로 보인다** — 교정이 수동 절차라 목록 완전성이 전제다 |
| 7 | **raw sub 폴백은 권위 조회 실패에만 적용한다**(`onErrorResume`을 `flatMap` **앞**에 둔다) | 체인 끝에 두면 레지스트리 **쓰기** 오류까지 삼켜, CONFLICT를 감지하고도 영속화에 실패하면 raw sub으로 진행돼 fail-closed가 풀린다. CONFLICT는 "그 sub의 귀속이 불확실하다"는 선언이라, 재귀속 상황이면 그 키에 **과거 다른 사람의 데이터**가 남아 있을 수 있다 |
| 8 | **탈퇴 파기만 CONFLICT의 예외다.** 차단이 아니라 **삭제 후보 확장**의 근거로 쓴다 | backend는 medcare 파기가 실패해도 탈퇴를 계속 진행해 `user_accounts`를 hard delete한다. 파기를 CONFLICT로 거절하면 **데이터는 남고 권위 매핑은 사라져** 재시도가 곤란해진다 |
| 9 | **레지스트리 행은 케이론·로컬 삭제가 전부 성공한 뒤 맨 마지막에 지운다** | 파기 실패 시 레지스트리가 남아 있어야 재시도가 sibling을 계속 안다. 먼저 지우면 재시도가 요청 sub 하나만 아는 상태가 된다 |
| 10 | **일일 재검증은 "응답에 없는 기존 행"을 지우지 않는다** | 페이지 일부 실패·backend 일시 누락을 탈퇴로 오인하면 sibling 정보를 잃는다. 레지스트리 삭제는 성공한 탈퇴 파기 경로 전유다 |
| 11 | **인메모리 L1 캐시를 두지 않는다**(L2 레지스트리 → L3 API) | TTL 캐시는 그룹 fail-closed의 즉시성과 충돌한다(태스크 간 최대 TTL만큼 오염 창). 소규모 트래픽에서 로컬 PK 단건 조회는 병목이 아니다. 병목이 실측되면 **버전 기반 무효화를 갖춘** 캐시를 넣는다 |
| 12 | **레지스트리 collation은 `utf8mb4_bin`이 아니라 테이블 기본(`utf8mb4_0900_ai_ci`)** | 이 표의 존재 이유가 kmeta·customer 소유자 컬럼(전부 `0900_ai_ci`)과의 JOIN이다(재키잉 SQL·미통합 잔여 지표·병합 배치). `bin`이면 그 JOIN이 전부 1267로 실패하거나 `COLLATE` 절을 강제당한다 |
| 13 | **대사·잔존 정리는 파티션 단위로 비교한다**(`findStridsByScopeAndPartition`) | 스코프 전체와 비교하면 **다른 파티션의 대화가 전부 잔존으로 보여** 오삭제가 된다 |
| 14 | **케이론 fallback 탐색에서 404만 다음 후보로 넘어간다.** 5xx·타임아웃은 즉시 중단 | "확인 못 함"을 "없음"으로 읽으면 안 된다. 로컬 root 부재 시 loginUid → sibling 순차 탐색을 하는데, 5xx에서 계속 넘어가면 존재하는 대화를 없다고 판정한다 |
| 15 | **identity backfill 페이징은 순차 재귀다. `Flux.expand`(너비 우선)로 되돌리지 않는다** | 너비 우선은 1페이지를 DB에 적재하는 동안 2페이지를 **미리 요청해 두고** 응답이 소켓에 도착해도 읽어가지 않는다. 채널의 `ReadTimeoutHandler`(`platform.api.timeout`=5s)가 그 침묵을 서버 무응답으로 보고 연결을 끊는다 — **상대는 멀쩡한데 우리가 우리를 끊는다.** `IdentityBackfillPagingTest`가 이 순서를 고정한다 |
| 16 | **잔존(`staleInKmeta`) 임계를 완화하지 않는다** | `purge-stale`의 대량 잔존 가드(5건↑ **AND** 20%↑)는 코드 상수다. dev에서 낮추면 stg·prd까지 따라간다. 일부만 지우는 절충을 두지 않는 이유는, **UID가 틀리면 목록도 상세도 함께 404**라서 "케이론에 없음"이 거짓 확증이 되기 때문이다 |
| 17 | **`SubIdentityResolver`의 canonical 선정 규칙을 바꾸지 않는다** | `user_accounts`가 활성 사용자 생명주기 안에서 append-only라(연동·자가치유는 INSERT만) 한 번 정해진 canonical은 바뀌지 않는다. **이 불변이 재키잉 결과와 런타임 판정을 잇는다.** 규칙을 바꾸면 이미 재키잉된 데이터의 owner와 런타임이 계산하는 owner가 갈라진다 |

---

## 6. 배포 방법

### 6-1. 저장소 순서

```
1. backend-api-main   ← 반드시 먼저
2. skix-medcare
```

medcare가 backend의 identity API를 호출한다. 반대 순서면 medcare만 배포된 창에서 L3 해석이 404를 받는다. raw sub 폴백이라 **동작은 하지만** 그 상태를 만들 이유가 없다.

### 6-2. 단계 순서와 성격

```
P0 선행 게이트 확인          (배포일 당일)
P1 backend identity API      소비자 없음 · 무위험
P2 medcare 호환 배포         DDL 선행 → 배포(플래그 OFF) → cheiron_sub 백필 → identity backfill → 게이트
P3 alias-read ON             데이터 무변경 · 조회 범위만 확장
P4 canonical-write ON        ★ 분수령 — 신규 행이 canonical 로 적힌다
P5 SQL 재키잉                ★ 비가역에 준함 — journal 로만 정밀 복원
P6 owner 단위 convertAll + drift
```

**대부분의 단계는 되돌릴 수 있고, P4·P5만 특별하다.**

- **P1·P2는 동작이 바뀌지 않는다.** 두 플래그가 꺼져 있으면 모든 키가 로그인 sub이라 현행과 완전히 같다. 배포 자체가 롤백 수단이다.
- **P3은 데이터를 바꾸지 않는다.** 끄면 즉시 원복된다.
- **P4부터 신규 행이 canonical로 적힌다.** 완전 복귀 불가 지점이다.
- **P5 이후** 데이터까지 되돌리려면 journal 역-UPDATE를 한다.

**단계별 실행 명령·검증·체크박스 전문은 §부록 C**에 있다. 재키잉 SQL 전문은 §부록 E, 착수 전 실측 SQL은 §부록 D다.

### 6-3. 배포 단위별 방식

- **P1·P2**: 이미지 빌드·배포(`deploy/cloudshell-push.sh`). DDL은 P2 배포 **전**에 손으로 실행한다.
- **P3·P4**: **이미지를 다시 만들지 않는다.** `deploy/task-definition-{env}.json`의 값만 바꿔 `register-task-definition` → `update-service --force-new-deployment`. 롤아웃·롤백이 리비전 등록 한 번으로 끝난다.

```bash
ENV=prd                                  # dev | stg | prd
CLUSTER="skmg-airbot-${ENV}-ecs-cluster"
SERVICE="skmg-airbot-${ENV}-medcare-svc"
FAMILY="skix-medcare-${ENV}-tdef"

aws ecs register-task-definition --cli-input-json "file://deploy/task-definition-${ENV}.json"
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --task-definition "$FAMILY" --force-new-deployment
```

### 6-4. 롤백 경계

| 시점 | 롤백 방법 |
|---|---|
| P3까지 | 두 플래그 `false` → 완전 현행 복귀 |
| P4 이후 | `IDENTITY_CANONICAL_WRITE_ENABLED=false`, **`alias-read`는 ON 유지**. 완전 복귀 불가 |
| P5 이후 | 위와 동일 + 데이터까지 되돌리려면 `rekey_journal` 역-UPDATE(§부록 E §4). quarantine 복원은 개별 판단(그 사이 같은 키가 다시 생겼을 수 있다) |
| 코드 전체 | 머지 커밋 `git revert -m 1`. **단 backend는 단독 revert 금지** — `SubIdentityResolver`를 파트너 오픈API 연동(`ee2b1fc5`)이 함께 쓰고 있다(§2-5) |

DDL(테이블·컬럼)은 **되돌리지 않는다.** 구버전 코드가 참조하지 않아 남겨도 무해하고, `cheiron_sub`은 한 번 잃으면 재키잉 후에 되살릴 수 없다.

### 6-5. `main` 승격 시 예상되는 충돌

`skix-medcare`는 **dev와 stg에 같은 변경을 각각 별도 커밋으로 넣는 관례**가 있다. 내용이 100% 같아도 SHA가 다르면 조상 관계가 없어 병합 기준점에 그 파일이 없고, git이 양쪽의 독립 추가로 보아 **add/add 충돌**이 난다.

dev→stg 승격 때 실제로 `PhrQaInvalidPayloadContractTest` 한 건에서 이 충돌이 났다(PHR QA 변경이 dev `2326f38`·`3617a1f` / stg `94bf443`·`accb96b`로 각각 들어가 있었다). 컨트롤러 diff는 한 글자도 다르지 않았고 실제 차이는 테스트 한 줄이었다. **dev 판본을 채택**했다 — 값 타입 3종 전환에서 단일 인자 public 생성자를 없앴으므로 stg 판본 `new AuthenticatedUser(sub)`는 컴파일되지 않는다(`AuthenticatedUser.rawSub(sub)`가 맞다).

**검증 요령**: 해소 후 `git diff origin/dev --stat`이 **비어야 한다.** 대상 브랜치 고유 변경이 이미 dev에도 있다면 머지 트리는 dev와 완전히 같아야 정상이고, 차이가 나오면 해소에서 뭘 잃은 것이다. **`main` 승격 때도 같은 유형의 충돌과 같은 검사를 예상한다.**

---

## 7. 남은 일

### 7-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **검증계 실기기 최종 확인.** 전화번호 로그인 → 대화 2건 + 폴더 담기 → 다른 provider 로그인 → ①목록·검색에 이전 대화 노출 ②그 대화 **이어가기** 정상 ③폴더 유지 ④제목 변경·삭제 정상 ⑤탈퇴 → 양 sub 파기 확인(신고는 보존) → 재가입 → 과거 데이터 미노출 | 백엔드 | 대상 계정은 §8의 "병합 성과 확인 쿼리"로 고른다 |
| 2 | 개발계·검증계 ECS 태스크 정의의 실제 플래그 값이 저장소 파일(둘 다 `true`)과 같은지 확인 | 백엔드 | 로컬에서 불가 — VDI |
| 3 | 개발계 태스크의 `IDENTITY_BACKFILL_PAGE_SIZE` 임시 환경변수 제거(있다면) | 백엔드 | 무해. 다음에 태스크 정의 손볼 때 같이 |
| 4 | **개발계 케이론 API 키 로테이션 확인.** 2026-08-26 잔존 조사 중 `X-API-KEY`를 명령줄에 직접 써서 화면 캡처와 베스천 `~/.bash_history`에 평문으로 남았다 | 백엔드 | 로테이션 여부 **미확인**. 이후 규칙: 케이론 키는 파일·명령줄 금지, `read -rs CHEIRON_KEY`로 env 주입만 |

### 7-2. 운영계 진입 체크리스트 (전부 충족 전 prd 롤아웃 금지)

**P0 게이트 — 다른 트랙에 걸려 있다**

- [ ] **Cognito 토큰 PII 제거 트랙(token-pii)의 운영계 배포 완료** — medcare가 그 트랙의 토큰 형태를 전제한다
- [ ] **`user_accounts` 운영계 백필 완료** ← 이게 없으면 매핑이 **60건 결손 상태로 시작**한다(유령 sub 60건 중 56건이 백필로 해소된다)
- [ ] 백필 후 재확인 — 기대: 백필 전 7,278명 → 백필 후 8,400명대(활성 사용자 수에 근접)
      ```sql
      SELECT COUNT(*) AS mappings, COUNT(DISTINCT user_id) AS users FROM user_accounts;
      ```
- [ ] pending-turn 트랙 운영계 배포 완료 (3환경 기완료로 기록됨 — **확인만**)

**코드 승격**

- [ ] `backend-api-main` `feature/owner-unification` → `main` 머지 (`--no-ff`)
- [ ] `skix-medcare` `feature/owner-unification` → `main` 머지 (`--no-ff`), **add/add 충돌 예상**(§6-5)
- [ ] 머지 후 `git diff origin/dev --stat` 확인, 전체 테스트 통과
- [ ] `deploy/task-definition-prd.json`의 플래그 2종이 **`false`인 채로 승격**되는지 확인(P2는 다크 런치다)

**실측 재확인 (운영계 데이터로, P5 직전)**

- [ ] **폴더채팅 유니크 충돌 0행** (§부록 E §0-4) ← 유일한 충돌 위험 지점. Gate 0 시점 0행이었으나 **표본이 4명뿐**이라 반드시 재실행
- [ ] `cheiron_sub` NULL 0건 (§부록 E §0-5)
- [ ] CONFLICT 0건 (§부록 C P2 2-5)
- [ ] 미통합 잔여 수치 기록 — **운영계 기대치는 소수(≈4건)**. 개발계 수치(sub 4개·대화 159건)를 기준선으로 쓰지 않는다
- [ ] 갈라진 사용자 수 기록 — Gate 0 기준 4명. 실기기 검증 대상이 된다

**실행 창**

- [ ] 유지보수 배치(identity backfill 02:30 · reconcile 일요일 03시 · drift 04시)와 **겹치지 않는 창** 확보. 가장 확실한 방법은 그 시간대를 피하는 것(예: 05시 이후)
- [ ] RDS 스냅샷 (재해복구용. 정밀 롤백은 journal로 한다)
- [ ] CS 공지 협의 — **채팅 한도·폴더 개수 상한이 사람 단위로 합산**된다(§7-3)

### 7-3. 알려진 변화·후속

| # | 항목 |
|---|---|
| 5 | **사용자 체감 변화(의도된 것)**: alias-read를 켜는 순간부터 채팅 한도와 폴더 개수 상한(50)이 **사람 단위로 합산**된다. provider를 갈아타 한도를 두 번 쓰던 우회가 닫힌다. CS 공지가 필요할 수 있다 |
| 6 | **PHR 오픈 전 체크리스트 — 대부분 닫혔다.** `4971f08`(dev·stg)로 `PhrApiController`·`PhrService`·`ChatService.resolvePhrForChat`이 `OwnerContext`로 전환됐다(조회·수정·삭제는 `OwnerScope`, 신규 행은 `OwnerKey`, 푸시는 loginUid). 특히 `deleteSnapshotsOlderThan`을 스코프로 바꾼 것이 중요하다 — 단일 키로 지우면 sibling sub로 적힌 과거 PHI 사본이 영구히 남는다. **남은 것**: ①오픈 전 테스트 데이터 정리 후 **0건 확인**(PHR은 재키잉 대상이 아니라 잔여가 있으면 옛 키로 남는다) ②오픈 시점에 `canonical-write`가 켜져 있는지 확인 ③`customer_if_phr`·`customer_if_phr_data` 소유자 컬럼 폭이 `varchar(50)`인지(canonical이 UUID 36자라 문제없으나 신규 컬럼 추가 시 정렬) |
| 7 | **평가·신고 오픈 전**: 같은 규칙. 오픈 시점 canonical-write ON + 테스트 데이터 0건 확인. 두 테이블 소유자 컬럼은 `utf8mb4_bin`이라 나중에 마이그레이션이 필요해지면 `COLLATE` 처리가 따라온다 |
| 8 | **유령 sub C 판정 1건**(탈퇴했는데 medcare 데이터 잔존 = 파기 미연동)은 이 트랙이 아니라 **탈퇴 파기 연계 별건**이다. 운영계 롤아웃 후 미통합 잔여가 크게 나오면 이쪽을 의심한다(판별 쿼리는 §부록 C P2 2-5) |
| 9 | **지표 수집 경로가 없다.** `medcare.identity{resolved\|fallback_raw_sub\|conflict}` 카운터는 코드에 있지만 actuator가 health/info만 열려 있고 이 서비스를 긁는 수집기가 없다. **관찰은 로그와 DB로 한다**(§8). 수집이 붙는 날 카운터가 바로 쓰인다 |
| 10 | L1 캐시 부재로 요청당 레지스트리 조회 1회가 추가된다. 현재 트래픽에서는 무해하나, 병목이 실측되면 **버전 기반 무효화를 갖춘** 캐시를 넣는다(TTL 캐시는 안 된다 — §5의 11번) |
| 11 | 커버리지 한계: 과거 sub이 `user_accounts`·레지스트리 어디에도 없으면(그 provider로 재로그인한 적 없는 레거시) 열거가 불가능하다. "미통합 잔여" 지표로 관측하며 재로그인 자가치유 + 일일 backfill + 배치 재실행으로 수렴한다. **완전 커버리지는 불가능하다** |

---

## 8. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| **특정 사용자만 요청이 거부됨** | 그 그룹이 `CONFLICT`다. `SELECT sub, owner_key, status FROM medcare_user_identity WHERE owner_key = '<owner>';` 로 그룹 전체를 보고 원인 규명 후 `owner_key`·`status`를 **수동 정정**한다. 자동 교정을 두지 않은 이유는 "권위가 바뀐 것"과 "sub이 타인에게 재귀속된 것"을 코드가 구분할 수 없어서다 |
| **대화 이어가기가 404** | `SELECT strid, user_strid, cheiron_sub FROM kmeta_streams WHERE strid = '<strid>';` — `cheiron_sub`이 NULL이면 P2의 파티션 출처 백필이 누락된 것이고, `skix_` 접두가 붙어 있으면 케이론 응답값을 잘못 저장한 것이다(§5의 3번) |
| **재키잉 후 일부 대화가 안 보임** | 두 플래그를 함께 껐다. `IDENTITY_ALIAS_READ_ENABLED=true`로 되돌린다 |
| **`fallback_platform_error` 급증 / 로그에 `identity L3 실패`** | backend identity API 장애다. 조회는 raw sub로 계속 동작한다(**더 나빠지지 않는다**). backend 복구가 우선 |
| **탈퇴 파기가 실패로 끝남** | 케이론 삭제 일부 실패다. 로컬은 손대지 않은 상태다. 같은 sub으로 재호출하면 남은 것부터 이어서 지운다(행 단위 멱등). 레지스트리는 맨 마지막에만 지우므로 재시도가 sibling을 계속 안다 |
| **감사 로그에 `scopeExpansionIncomplete=true`** | 확장 근거를 하나도 못 얻었고 그게 **원천 실패 때문**이다(두 원천의 결과가 모두 비었고 그중 하나 이상이 오류 — '레지스트리 미스 + 권위 오류' 조합도 포함). 파기는 요청 sub 범위로 실행됐다. **backend가 sibling마다 이 API를 개별 호출**하므로 나머지는 그 호출들이 덮지만, 수동 단건 호출이었다면 다른 sibling sub으로 각각 재호출한다 |
| **감사 로그에 `scopeExpansionIncomplete=false`인데 `scopeSize=1`** | 정상이다. 두 원천이 **정상적으로** "확장할 그룹이 없다"고 답한 상태다. 사유는 둘 중 하나 — 그 사람이 sub을 하나만 쓰거나, 이미 앞선 호출이 그룹을 파기해 레지스트리·`user_accounts`가 비었거나(탈퇴 연계의 두 번째 이후 호출) |
| **backfill이 `PrematureCloseException`으로 끊김** | 현재 코드에서는 나오면 안 된다(순차 재귀로 고쳤다). 재발 시 확인 순서: ①backend를 직접 curl해 응답 시간을 본다(느리면 backend 문제) ②빠른데도 medcare만 끊기면 §5의 15번 구조가 되돌아갔는지 본다 |
| **drift에 `staleInKmeta`(잔존)가 잡힘** | §아래 "잔존 해석 규칙" |
| **로그 키워드** | `identity 매핑 충돌`(ERROR — CONFLICT 탐지) · `identity L3 실패`(WARN — 플랫폼 장애 폴백) · `identity backfill 완료 — groups=…, conflicts=…`(일일 재검증) · `convertAll 완료 — 파티션 N개, 대화 M건, 성공…` |

### 병합 성과 확인 쿼리 (재키잉 **후** 판)

재키잉 후에는 소유자가 canonical로 통일돼 `COUNT(DISTINCT user_strid)`가 전부 1이 된다. 그러니 **한 소유자의 대화가 여러 케이론 파티션에 걸쳐 있는 것**이 곧 병합 성공 사례다.

```sql
SELECT user_strid AS owner, COUNT(DISTINCT cheiron_sub) AS partitions, COUNT(*) AS convs
FROM kmeta_streams
GROUP BY user_strid
HAVING COUNT(DISTINCT cheiron_sub) > 1
ORDER BY convs DESC;
```

여기 나오는 사람이 **전에는 로그인 수단을 바꾸면 대화가 안 보이던 사용자**다. 실기기 검증 대상도 이 목록에서 대화가 많은 계정으로 고른다(개발계는 1명·72건이 이렇게 잡혔다).

### 잔존(`staleInKmeta`) 해석 규칙

`hasDrift()` 판정식에 `staleInKmeta`가 들어 있어 **1건만 있어도 WARN**이 뜬다. 하지만 잔존은 "로컬에 더 많다"는 방향이라 **사용자에게 숨겨지는 데이터는 없다**(목록·검색은 로컬 기준). 이 트랙의 성적표는 반대 방향인 **`missingInKmeta`**다.

**잔존은 이 트랙이 만들 수 없다.** convertAll은 케이론에서 가져오기만 하고, 재키잉은 `user_strid`만 바꾸며 `cheiron_sub`은 건드리지 않는다. 따라서 잔존이 보이면 원인은 둘뿐이다 — ①케이론 쪽에서 삭제됐다 ②**우리가 잘못된 UID로 묻고 있다.**

판별 3종(`purge-stale?apply=false` 로그가 주는 것은 strid가 아니라 **sub**이다):

```sql
-- ① 레지스트리 위치 — 0건이면 미통합 sub(= backend 가 활성 사용자로 모르는 계정)
SELECT sub, owner_key, status FROM medcare_user_identity
WHERE sub = '<로그의 sub>' OR owner_key = '<로그의 sub>';

-- ② 파티션 구성과 답변 유무
--    LIKE 의 '_' 는 반드시 이스케이프 — 안 하면 남의 chat 까지 센다.
SELECT s.cheiron_sub, COUNT(*) AS convs,
       MIN(s.create_date) AS oldest, MAX(s.create_date) AS newest,
       SUM(CASE WHEN (SELECT COUNT(*) FROM kmeta_chats c
                      WHERE c.strid LIKE CONCAT(s.strid, '\_%')) = 0
                THEN 1 ELSE 0 END) AS no_chats
FROM kmeta_streams s
WHERE s.user_strid = '<로그의 sub>' OR s.cheiron_sub = '<로그의 sub>'
GROUP BY s.cheiron_sub;
```

```bash
# ③ 케이론에 직접 물어 UID 유효성 확정. 키는 반드시 env 로 — 명령줄에 쓰면 화면·히스토리에 남는다.
read -rs CHEIRON_KEY
curl -s -H "X-API-KEY: $CHEIRON_KEY" -H "X-API-UID: <로그의 sub>" \
     -H "X-Tenant-Domain: <테넌트>" \
     "$CHEIRON_URL/api/data_management/conversations?startIndex=0&limit=1" | jq '.total_count'
```

| ③ 결과 | 판정 |
|---|---|
| 로컬보다 작은 **정상 수치** | UID 정상 → 케이론 쪽에서 삭제된 것. 예외로 기록하고 통과 |
| **0 또는 404** | **UID·테넌트 문제.** 라우팅 설정부터 확인한다. **절대 정리하지 말 것** |

> ⚠️ **운영계에서 대량 잔존이 뜨면 삭제 대상이 아니라 조사 대상이다.** 운영계의 대화 삭제는 앱 경로뿐이고 그 경로는 케이론과 로컬을 함께 지운다. 잔존이 나온다는 것 자체가 설명을 요구한다.
>
> **개발계에서 잔존 59건을 삭제한 근거 5종 중 3종은 운영계에서 성립하지 않는다.**
>
> | 개발계 근거 | 운영계에서 |
> |---|---|
> | 케이론이 404가 아닌 정상 수치 응답 → UID 정상 | 동일하게 확인해야 함(생략 불가) |
> | 시기 분포가 케이론 쪽 데이터 유실을 가리킴 | 환경 리셋이 없으므로 **같은 패턴이 나오면 안 된다** |
> | 메인 DB에 사용자 실체 없음 | **성립하지 않을 가능성이 크다** — 실사용자면 행이 있다 |
> | 숫자 정합 | 동일하게 확인 |
> | 개발계라 데이터가 폐기 가능 | **성립하지 않는다** |
>
> 참고로 개발계 59건은 전량 한 계정이었고, 케이론 보유분은 `creation_time` 6/8~6/25인데 로컬 잔존은 6/18~8/20이었다 — **케이론에 6/25 이후가 통째로 없다**(백업 복원·테넌트 리셋의 모양). 오래된 것이 지워진 게 아니라 최근 것이 없으므로 retention 가설은 기각된다. medcare는 케이론이 strid를 발급해야만 로컬 행을 만들므로(스트림 완료 저장·`registerPendingTurn`·convertAll·read-repair **네 경로 전부** 케이론 응답에서 출발) "한때 케이론에 있었다가 사라졌다"가 유일한 설명이었다.

### 잔존을 SQL로 지워야 했던 이유 (경로가 둘 다 막힌다)

- 앱의 `deleteConversationDetail`은 케이론을 **먼저** 부르고 그 다음 로컬을 지운다. 케이론에 없으면 404 → `EntityNotFoundException`으로 체인이 끊겨 로컬 삭제에 도달하지 못한다. 케이론이 소유권 판정 주체라는 원칙의 귀결이라 우회 경로를 두지 않았다.
- `purge-stale?apply=true`는 대량 잔존 가드에 막히고 우회 파라미터가 없다.
- **폴더 담기 행을 같은 트랜잭션에서 함께 지우는 것이 필수다.** 대화만 지우면 그 행이 고아가 되어 경고가 `orphanFolderChats`(역시 `hasDrift()` 판정 대상)로 옮겨갈 뿐이다.
- 컬럼명 함정: `kmeta_streams`의 생성 시각은 `create_date`가 아니라 **`creation_time`**(케이론 필드명 미러링). `kmeta_conversation_search`는 `kmeta_streams(strid)`로 FK CASCADE가 걸려 있지만 명시 삭제를 함께 두면 순서와 무관하게 안전하다(이미 지워졌으면 0행).

---

## 부록 A. 핵심 커밋 (시간순)

| 날짜 | 저장소 | 커밋 | 내용 | dev | stg | main |
|---|---|---|---|---|---|---|
| 08-25 | backend-api-main | `dea4fdd8` | identity API 2종 + `SubIdentityResolver` + 매퍼 4종 | 반영 | 반영 | 미반영 |
| 08-25 | skix-medcare | `a82b603` | P2 배관 — 값 타입 3종·레지스트리·해석·backfill·플래그 | 반영 | 반영 | 미반영 |
| 08-25 | skix-medcare | `e6bf502` | C·E·F — 키 분리 스윕·owner 단위 병합·파기 후보 확장 | 반영 | 반영 | 미반영 |
| 08-25 | skix-medcare | `bd7e849` | 플래그를 ECS 환경변수로 노출 | 반영 | 반영 | 미반영 |
| 08-25 | skix-medcare | `3ee2e4b` | 세 환경 태스크 정의에 플래그 추가(전부 false) | 반영 | 반영 | 미반영 |
| 08-25 | skix-medcare | `47899c3` | 1차 리뷰 — CONFLICT 원자성·오염 그룹 배치 제외·관제 이력 스코프 | 반영 | 반영 | 미반영 |
| 08-25 | skix-medcare | `aaaa4d7` | 2차 리뷰 — CONFLICT 영속화·관제 조회 fail-closed·파기 감사 정확성 | 반영 | 반영 | 미반영 |
| 08-25 | skix-medcare | `194e013` | 3차 리뷰 — 폴백 경계(플랫폼 오류만)·확장 판정 정정 | 반영 | 반영 | 미반영 |
| 08-25 | skix-medcare | `3e683c9` | 4차 리뷰 — 파기 감사값 설명 현행화 | 반영 | 반영 | 미반영 |
| 08-26 | backend-api-main | `829c84eb` | `feature/owner-unification` → dev 머지(`--no-ff`) | — | — | — |
| 08-26 | skix-medcare | `65a0eaa` | `feature/owner-unification` → dev 머지(`--no-ff`) | — | — | — |
| 08-26 | skix-medcare | `c74f965` | **배포 후 수정** — backfill 페이지 순차 처리(자체 유발 ReadTimeout 제거) | 반영 | 반영 | 미반영 |
| 08-26 | backend-api-main | `7c7275ef` | dev → stg 승격 | — | — | — |
| 08-26 | skix-medcare | `d600dd5` | dev → stg 승격 (add/add 충돌 1건 해소) | — | 반영 | — |
| 08-27 | backend-api-main | `ee2b1fc5` | (별건) 파트너 오픈API 연동 키를 canonical sub로 정규화 — **`SubIdentityResolver`의 두 번째 소비자** | 반영 | 반영 | 미반영 |
| 08-31 | skix-medcare | `5e8e205` | dev·stg 태스크 정의를 실상태(ON)로 정정. **prd는 `false` 유지** | 반영 | 반영 | 미반영 |
| 08-31 | skix-medcare | `4971f08` | (별건 겸용) PHR 202+FCM 전환 + **PHR 소유자 키를 `OwnerContext`로 전환** | 반영 | 반영 | 미반영 |

**백업 태그**(두 저장소): `backup/pre-rebase-owner-unification-20260825` — 리베이스 전 상태.
**피처 브랜치**(두 저장소 원격 보존): `feature/owner-unification`. **지우지 않는다** — `main` 승격의 원본이다.

테스트 규모(2026-08-26 기준): skix-medcare **601건 통과**(dev→stg 머지 후 재실행, 82 스위트), backend-api-main 전체 그린. 이 트랙에서 신설한 계약 테스트: `IdentityResolutionServiceTest`, `IdentityBackfillPagingTest`(페이징 순서 고정), `OwnerScopeTest`(생성자 불변식), `LogOwnerScopeTest`(관제 이력 스코프), `PurgeScopeExpansionTest` 6건(sibling 전체 삭제·확장 실패 시 요청 sub 보장·레지스트리 마지막 삭제·케이론 실패 시 무변경).

---

## 부록 B. 구현 중 추가된 설계 결정 (계획에 없던 것)

인수자가 코드를 읽다가 "왜 이렇게 됐지?"를 물을 만한 것들이다.

| # | 결정 | 이유 |
|---|---|---|
| 1 | `OwnerContext` 도메인 record 신설 | 요청 경로(`AuthenticatedUser`)와 배치 경로(파티션 sub)가 같은 형태를 쓰게 해 `ConvertService` 이하가 호출자를 구분하지 않는다. application → security 역의존도 없앤다 |
| 2 | `PendingRegistration.cheironSub` 추가 | 이어가기 케이론 UID를 **잠금과 같은 문장에서** 확보해 별도 재조회(그 사이 변경 창)를 없앤다 |
| 3 | `ConversationService.cheironFallbackDetail` | 로컬 root 부재 시 loginUid → sibling 파티션 순차 탐색, 찾으면 즉시 로컬 적재(다음부터 1회 호출). **404만 다음 후보로**, 5xx·타임아웃은 즉시 중단 |
| 4 | 대사·잔존 정리는 파티션 단위 비교(`findStridsByScopeAndPartition`) | 스코프 전체와 비교하면 다른 파티션의 대화가 전부 잔존으로 보여 오삭제가 된다 |
| 5 | 관제 이력 조회(`LogService`)를 `OwnerScope IN`으로 | 재키잉 후 loginId로 해석한 sub이 canonical이 아니면 빈 결과였다 |
| 6 | 관제 조회의 `onErrorResume` 제거 | 매핑없음·플랫폼장애는 해석부가 이미 raw sub로 정상 반환하므로, 여기까지 올라오는 예외의 실체는 CONFLICT다. 단일 sub로 물러서면 fail-closed가 이 경로에서만 무효가 되고 **귀속 미확정 이력을 운영자에게 열람시킨다** |
| 7 | `findDistinctNormalOwnerKeys`가 그룹에 CONFLICT가 **없는** owner만 반환 | 혼합 그룹이 병합되면 오귀속이 굳는다 |
| 8 | alias upsert는 순차·단일 트랜잭션 | 부분 저장 시 스코프가 반쪽이 되는데 L2 히트라 자가치유가 안 된다 |
| 9 | 기존 행이 CONFLICT면 그룹 유지 | 전환이 `status`만 바꾸고 `owner_key`는 남아, 미등록 sub이 L3에서 NORMAL로 재등록되면 차단이 우회된다 |
| 10 | 재키잉 SQL의 보존 행 선정을 `ROW_NUMBER(... ORDER BY create_date DESC, chat_id DESC)`로 | `MAX(create_date)`와 `MAX(chat_id)`를 따로 구하면 **서로 다른 행**을 가리킬 수 있어, 문서 정책("최근 담은 폴더 유지")과 SQL이 다른 행을 고른다 |
| 11 | `scopeExpansionIncomplete` 판정을 AND → "근거 없음 + 실패"로 | 종전엔 '레지스트리 미스 + 권위 오류'가 **완전**으로 기록됐다. `AtomicBoolean` 2개를 `SourceResult` record로 대체해 재구독 상태 누수도 제거 |

**의도적으로 거부한 제안**(다시 올라오면 이 근거로 판단한다):

- **upsert 후 재조회로 경쟁 확정** — CONFLICT 자체가 희귀하고 일일 backfill 안전망이 이미 있다. 모든 L3에 DB 왕복을 추가하는 것은 과잉이다.
- **ODKU 영향 행수로 판정** — 드라이버마다 1/2/0으로 달라 오작동이 더 위험하다.
- **파기 확장 실패 시 파기를 실패로 기록** — backend가 sibling sub마다 개별 호출하고(`user_accounts` 삭제 **전**) 행 단위 멱등이라 이중 안전망이 이미 있다. 실패로 기록하면 플랫폼 일시 장애가 '파기 실패'가 되는데, backend는 탈퇴를 계속 진행하므로 **기록이 실제와 어긋난다.**
- **backend `purgeMedcareOnWithdraw` 회귀 테스트 추가** — 이번 트랙이 만들지 않은 기배포 코드라 범위 확대다. 대신 그 계약 의존(sibling 순회 · `user_accounts` 삭제 전 호출 · 실패해도 계속)을 문서에 명시했다.

> ⚠️ **파기 안전성은 backend 계약에 의존한다.** medcare의 후보 확장은 **보강**이지 유일한 경로가 아니다. `UserService.purgeMedcareOnWithdraw`가 ①`user_accounts`의 sibling sub 전부를 순회하고 ②`user_accounts` 삭제보다 **먼저** 호출하고 ③한 건 실패해도 다음 sub을 계속 호출한다는 세 가지가 전제다. **backend 탈퇴 로직을 손댈 때 이 순서·순회를 깨면 medcare의 sibling 파기가 함께 약해진다.**

---

## 부록 C. 롤아웃 런북 P0~P6 (실행 전문)

환경별로 순서대로 실행하고 체크한다. 아래는 운영계 기준이며 `ENV`만 바꾸면 다른 환경에도 같다.

```bash
# 공통 변수
ENV=prd
KEY=<X-Internal-Api-Key>          # backend·medcare 공유 값(SSM)
ADMIN=<관리자 식별자>              # 감사 로깅용
BASE=<backend 내부 주소>
MEDCARE=<medcare 내부 주소>
CLUSTER="skmg-airbot-${ENV}-ecs-cluster"
SERVICE="skmg-airbot-${ENV}-medcare-svc"
FAMILY="skix-medcare-${ENV}-tdef"
```

### P0 — 선행 게이트 확인

**배포일 당일에 확인한다. 미리 체크하지 않는다** — 확인 시점과 배포 시점 사이의 간격을 메우는 것이 이 단계의 존재 이유다. 항목은 §7-2 체크리스트와 같다.

### P1 — backend identity API 배포

소비자가 medcare 외에 하나 더 있으므로(파트너 오픈API 연동), 이 배포는 **그 트랙과 함께** 나갈 수도 있다. 엔드포인트가 늘어날 뿐 기존 트래픽에는 영향이 없다.

```bash
# 1) 단건 — 복수 sub 보유자로 확인한다
curl -s -H "X-Internal-Api-Key: $KEY" "$BASE/internal/user/identity?sub=<sub>" | jq .
# 기대: {"ownerKey":"<대표 sub>","aliases":["<대표>","<sibling>", ...]}
#       ownerKey 는 반드시 aliases 에 포함된다.

# 2) 어느 sibling 으로 물어도 같은 그룹이 나오는지 (canonical 결정성)
curl -s -H "X-Internal-Api-Key: $KEY" "$BASE/internal/user/identity?sub=<sibling>" | jq .ownerKey

# 3) 매핑 없는 sub 은 404 — 200+null 이 아니다(소비자가 404 를 "raw sub 유지"로 읽는다)
curl -s -o /dev/null -w "%{http_code}\n" -H "X-Internal-Api-Key: $KEY" \
     "$BASE/internal/user/identity?sub=00000000-0000-0000-0000-000000000000"

# 4) 페이징 — nextCursor 가 null 이 될 때까지 도는지
curl -s -H "X-Internal-Api-Key: $KEY" "$BASE/internal/user/identities?limit=5" \
  | jq '{n:(.identities|length), next:.nextCursor}'
```

- [ ] 1~4 전부 기대값
- [ ] 기존 `/internal/user`가 그대로 동작(medcare PHR 스크래퍼 경로 — 회귀 확인)

**롤백**: 엔드포인트만 늘었으므로 되돌릴 이유가 거의 없다. **단 단독 revert 금지**(§6-4).

### P2 — medcare 호환 배포(플래그 OFF) + backfill

**이 배포는 동작을 바꾸지 않는다.** 두 플래그가 꺼져 있어 모든 키가 로그인 sub이다.

#### 2-1. DDL 선행 (배포 **전**)

```sql
-- 신규 테이블 — 파일 전문을 그대로 실행한다(§3-3 참조, 마이그레이션 도구 없음)
SOURCE src/main/resources/db/medcare_user_identity.sql;

-- 파티션 출처 컬럼
ALTER TABLE kmeta_streams ADD COLUMN cheiron_sub varchar(128) NULL;
```

- [ ] 두 DDL 실행 완료

#### 2-2. 배포

- [ ] 배포 완료
- [ ] 태스크 정의에 두 변수가 **`false`**로 있는지 확인
      ```bash
      aws ecs describe-task-definition --task-definition "$FAMILY" \
        --query 'taskDefinition.containerDefinitions[0].environment'
      ```
- [ ] 기동 로그에 `canonical-write-enabled 는 alias-read 없이 켤 수 없다` 경고가 **없음**(있으면 플래그를 잘못 켠 것이다)

#### 2-3. 파티션 출처 백필 (배포 **직후**, 재키잉 전)

```sql
-- 이 시점의 user_strid 는 전부 raw sub 이라 출처가 정확하다.
-- 재키잉(P5) 후에는 canonical 로 바뀌어 출처를 되살릴 수 없다 — 순서가 계약이다.
UPDATE kmeta_streams SET cheiron_sub = user_strid WHERE cheiron_sub IS NULL;

-- 기대: 0
SELECT COUNT(*) AS cheiron_sub_null FROM kmeta_streams WHERE cheiron_sub IS NULL;
```

- [ ] NULL 0건 확인

#### 2-4. identity backfill 실행

```bash
curl -X POST -H "X-Internal-Api-Key: $KEY" -H "X-Acting-Admin: $ADMIN" \
     "$MEDCARE/medcare/v1/internal/identity/backfill"
```

시작 여부만 응답한다. 결과는 로그의 `identity backfill 완료 — groups=…, conflicts=…` 한 줄이고, **정본 수치는 테이블을 직접 세는 것**이다.

#### 2-5. ★ P3 진입 게이트 ★

```sql
-- (a) CONFLICT 0건 — 아니면 P3 진입 금지(운영자 교정 선행)
SELECT status, COUNT(*) FROM medcare_user_identity GROUP BY status;

-- (b) 매핑 커버리지 — medcare 소유자 키 중 레지스트리가 모르는 것(= 미통합 잔여 예상치)
SELECT COUNT(*) AS unmapped_owner_rows
FROM kmeta_streams s
LEFT JOIN medcare_user_identity i ON i.sub = s.user_strid OR i.owner_key = s.user_strid
WHERE s.user_strid IS NOT NULL AND i.sub IS NULL;
-- 운영계 기대: Gate 0 의 유령 60건 중 백필로 해소되지 않은 소수(≈4건)

-- (c) 실제로 갈라진 사용자 — 병합의 수혜자 규모(운영계 Gate 0 기준 4명)
--     ※ 재키잉(P5) 전 전용 쿼리다. 재키잉 후에는 전부 1 이 되어 0건이 나온다.
SELECT m.owner_key, COUNT(DISTINCT k.user_strid) AS subs, COUNT(*) AS convs
FROM kmeta_streams k JOIN medcare_user_identity m ON m.sub = k.user_strid
GROUP BY m.owner_key HAVING COUNT(DISTINCT k.user_strid) > 1;
```

- [ ] (a) CONFLICT 0건
- [ ] (b) 미통합 잔여 수치 기록: ______
- [ ] (c) 갈라진 사용자 수 기록: ______ ← P3 이후 실기기 검증 대상이다

> **(b)를 어떻게 읽나.** 레지스트리는 backend `user_accounts`(활성 사용자)의 사본이므로, "레지스트리가 모르는 소유자"란 **backend가 활성 사용자로 갖고 있지 않은 sub**이다. 셋 중 하나다 — ①탈퇴자(계정은 지워졌는데 medcare 데이터가 남음 = 파기 누락) ②Cognito에만 있는 테스트 계정(앱 가입 절차 미이행) ③환경 초기화 시점 불일치.
> **P3를 막지 않는다** — 이 sub들은 해석이 폴백으로 떨어져 `scope={자기 자신}`이 되므로 현행과 완전히 동일하게 동작한다. 재키잉도 레지스트리를 JOIN하니 건드리지 않는다. "통합되지 않은 채 현행 유지"이지 깨진 상태가 아니다.
> **운영계에서 수치가 크게 나오면 ①(파기 누락)을 의심**하고, 이 트랙이 아니라 탈퇴 파기 연계에서 따로 다룬다. 판별은 아래 두 쿼리로 갈린다.

```sql
-- medcare DB: 미통합 sub 목록
SELECT DISTINCT s.user_strid
FROM kmeta_streams s
LEFT JOIN medcare_user_identity i ON i.sub = s.user_strid OR i.owner_key = s.user_strid
WHERE s.user_strid IS NOT NULL AND i.sub IS NULL;

-- 메인 DB: 위 목록 대입 — 0건이면 ②(Cognito 전용), delete_yn='Y' 면 ①(파기 누락)
SELECT ua.sub, ua.user_id, u.delete_yn, u.create_date
FROM user_accounts ua LEFT JOIN `user` u ON u.id = ua.user_id
WHERE ua.sub IN (/* 위 결과 */);
```

#### 2-6. 관찰 (하루)

> ⚠️ `medcare.identity` 카운터는 밖에서 볼 수 없다(§7-3의 9번). **관찰 게이트는 로그와 DB로 판정한다.**

**로그** — CloudWatch Logs에서 고정 문자열로 찾는다. 셋 다 나오지 않는 것이 정상이다.

| 찾을 문자열 | 뜻 | 조치 |
|---|---|---|
| `identity 매핑 충돌` | CONFLICT 탐지(ERROR) | **P3 진입 금지.** 원인 규명 후 수동 교정 |
| `identity L3 실패` | 플랫폼 장애로 raw sub 폴백(WARN) | 산발이면 무해, 지속되면 P3 보류 |
| `identity backfill 완료 — groups=…, conflicts=` | 일일 재검증 결과 | `conflicts=0` 확인 |

**DB** — 커버리지의 정본이다.

```sql
SELECT status, COUNT(*) FROM medcare_user_identity GROUP BY status;
SELECT COUNT(*) AS mappings, MAX(last_verified_at) AS last_verified FROM medcare_user_identity;
```

- [ ] 최소 하루 관찰 — 위 로그 3종·DB 2종 확인

**롤백**: 플래그가 이미 OFF라 코드 롤백만으로 완전 원복된다. DDL은 남겨도 무해하다.

### P3 — alias-read ON

**데이터를 바꾸지 않는다.** 조회 범위가 canonical + sibling 전체로 넓어질 뿐이다. **이미지를 다시 만들지 않는다.**

```bash
# deploy/task-definition-${ENV}.json 에서 IDENTITY_ALIAS_READ_ENABLED 를 "true" 로 고친 뒤
aws ecs register-task-definition --cli-input-json "file://deploy/task-definition-${ENV}.json"
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --task-definition "$FAMILY" --force-new-deployment
```

- [ ] 태스크 정의 값 변경 + 리비전 등록 + 서비스 갱신
- [ ] **전 태스크 교체 완료 확인** — 이게 P4의 전제다
      ```bash
      aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
          --query 'services[0].deployments[].{rev:taskDefinition,running:runningCount,status:status}'
      # 기대: PRIMARY 하나만 남고 runningCount = desiredCount (ACTIVE 배포가 사라짐)
      ```

**검증 (P2 2-5 (c)에서 나온 사용자로)**

- [ ] 두 provider로 각각 로그인해 **양쪽 모두에서 대화 목록이 동일하게 보인다**
- [ ] 그 대화를 열어 **이어가기 질문 → 답변 정상**(다른 provider로 만든 대화 = `cheiron_sub` 경로)
- [ ] 제목 변경·삭제 정상
- [ ] 폴더 목록·폴더에 담기 정상
- [ ] 검색에 양쪽 대화가 다 나온다

**롤백**: 플래그를 `false`로 되돌리고 재배포. 데이터 무변경이라 완전 원복이다.

### P4 — canonical-write ON ★ 분수령

P3와 같은 방식으로 값만 바꿔 새 리비전을 등록한다(`IDENTITY_CANONICAL_WRITE_ENABLED=true`, **`alias-read`는 `true` 그대로 둔다**).

- [ ] **P3가 전 태스크에 전파된 뒤에** 켠다(별도 배포)
- [ ] 전 태스크 드레인 완료 확인
- [ ] 신규 대화 1건을 만들어 소유자 확인
      ```sql
      SELECT strid, user_strid, cheiron_sub FROM kmeta_streams
      ORDER BY creation_time DESC LIMIT 3;
      -- 기대: user_strid = canonical(대표 sub), cheiron_sub = 지금 로그인한 sub
      ```
- [ ] **기존 대화(sibling owner)에 이어서 질문 → 답변 저장 성공**
      ← canonical-write 공통 규칙의 실전 검증이다. 실패하면 PK 충돌이 나는 것이니 **즉시 플래그를 끈다.**

**롤백(P5 전)**: `IDENTITY_CANONICAL_WRITE_ENABLED=false`. **`alias-read`는 켠 채로 둔다.**

### P5 — 재키잉 ⚠️

SQL 전문은 §부록 E. 여기는 실행 조건만 다룬다.

**선행 (전부 충족해야 실행)**

- [ ] P4 전 태스크 드레인 완료
- [ ] **유지보수 배치와 겹치지 않는 창 확보** — identity backfill(02:30) · reconcile(일요일 03시) · drift(매일 04시). 같은 행을 동시에 건드리면 journal과 실제가 어긋난다. **가장 확실한 방법은 그 시간대를 피하는 것**이다(예: 05시 이후 착수).
      플래그로 끄려면 셋을 각각 꺼야 한다 — `drift.auto-convert=false`는 **reconcile만** 막고 drift 점검과 identity backfill은 그대로 돈다.

      | 배치 | 끄는 방법 |
      |---|---|
      | identity backfill | `identity.backfill-cron`을 실행 안 하는 값으로 |
      | reconcile | `drift.auto-convert=false` |
      | drift 점검 | `drift.check-cron`을 실행 안 하는 값으로 |

      셋 다 `BatchExecutionLock`을 공유하므로 겹쳐도 한쪽은 양보하지만, **런북 SQL은 그 잠금을 쓰지 않는다** — 잠금이 런북을 막아주지 않는다는 뜻이라 창 확보가 필요하다.
- [ ] identity backfill 재실행(증분 반영) 후 CONFLICT 0건 재확인
- [ ] §부록 E §0 사전 점검 전부 기대값 — **특히 §0-4 유니크 충돌 0행**
- [ ] **RDS 스냅샷**(재해복구용. 정밀 롤백은 journal로 한다)

**실행**

- [ ] §1 충돌 해소(§0-4가 0행이 아닐 때만) → quarantine 이동 후 §0-4 재확인 0행
- [ ] §2 journal + UPDATE 단일 트랜잭션
- [ ] **§2-3 COMMIT 전 검증 3종 전부 기대값** → COMMIT (아니면 ROLLBACK)
      1. journal 건수 = §0-3 dry-run 건수
      2. 잔여 0(sibling 키로 남은 행 없음)
      3. `cheiron_sub` NULL 수가 §0-5와 동일(파티션 출처 보존)
- [ ] §3 사후 확인 — 미통합 잔여 추세, 소유자별 파티션 병합 표본
- [ ] 유지보수 배치 재개

### P6 — 병합 convertAll + drift

케이론에 있는데 아직 내재화되지 않은 sibling 파티션의 대화를 끌어와 한 소유자 아래로 모은다.

```bash
curl -X POST -H "X-Internal-Api-Key: $KEY" -H "X-Acting-Admin: $ADMIN" \
     "$MEDCARE/medcare/v1/internal/conversations/convertAll"
# 완료 후
curl -s -H "X-Internal-Api-Key: $KEY" "$MEDCARE/medcare/v1/internal/conversations/drift" | jq .
```

- [ ] convertAll 완료 로그 확인(`convertAll 완료 — 파티션 N개, 대화 M건, 성공…`)
- [ ] drift 보고서에 이상 없음 — 특히 `missingInKmeta`·`ownerlessStreams`·`missingSearchIndex`
- [ ] **`cheironTotal == kmetaTotal`** 확인 ← 가장 강한 확인
- [ ] 미통합 잔여 수치가 P2 2-5 (b) 대비 늘지 않았음
- [ ] `staleInKmeta`가 0이 아니면 §8의 "잔존 해석 규칙"으로 판별. **운영계에서는 삭제가 아니라 조사다**
- [ ] 실기기 최종 확인 — P3 검증 항목 전체 재실행 + **탈퇴 → 재가입 시 과거 데이터 미노출**

---

## 부록 D. Gate 0 실측 SQL (착수 전 / 운영계 재확인용)

읽기 전용이다. 유일한 쓰기는 세션 한정 `TEMPORARY` 테이블이라 DB에 흔적이 남지 않는다.

### D-0. MySQL Workbench 실행 순서

메인 DB와 medcare DB가 **서로 다른 서버**라 서브쿼리로 이을 수 없다. 매핑을 `CONCAT`으로 INSERT문으로 만들어 상대 DB의 `TEMPORARY` 테이블에 붙여넣는 방식이다.

```
1) [메인 DB 탭]   G-1 ~ G-3 실행 → 결과 기록
2) [메인 DB 탭]   G-4 실행(분석용) → G-4c 실행 후 insert_stmt 컬럼 전체 복사
                  (컬럼 헤더 클릭 → 우클릭 → Copy Field (unquoted))
3) [medcare 탭]   G-5 ~ G-6 실행 → 결과 기록
4) [medcare 탭]   G-7 준비 절의 CREATE TEMPORARY TABLE 실행
                  → 2)에서 복사한 INSERT문 붙여넣고 실행 → 적재 검증
5) [medcare 탭·같은 커넥션]  G-5c, G-7a~c, G-8 실행 → 결과 기록
```

- ⚠️ **4)~5)는 반드시 같은 쿼리 탭에서.** `TEMPORARY` 테이블은 커넥션(=탭)이 끊기면 사라진다.
- ⚠️ 워크벤치 기본 row limit 1000행에 잘린다. 실행 전 툴바 row limit을 **"Don't Limit"**으로.
- ⚠️ 운영계는 매핑 행이 만 단위다. 붙여넣기가 무거우면 G-4c 결과를 `.sql` 파일로 저장해 `mysql < file`로 넣거나, **G-4c-lite**(복수 sub 사용자만)를 쓰고 G-5c는 생략한다.
- ⚠️ **MySQL `TEMPORARY` 테이블은 한 쿼리에서 두 번 참조할 수 없다**(Error 1137). CTE와 메인에서 각각 참조하면 실패한다. `IN` 리스트로 참조를 한 번으로 줄이거나 복사본 테이블을 만든다.

### D-1. 메인 DB

```sql
-- G-1. user_accounts.sub 길이 분포 — 신규 컬럼 폭 varchar(128) 전제 +
--      customer_* varchar(50) 에 canonical(sub)이 들어가는지. 기대: max ≤ 36, 초과 0건.
SELECT MAX(LENGTH(sub)) AS max_len,
       SUM(LENGTH(sub) > 50)  AS over_50,
       SUM(LENGTH(sub) > 128) AS over_128,
       COUNT(*) AS total_rows
FROM user_accounts;

-- G-2. canonical 선정 결정성 — 사용자마다 정확히 1건인지.
--      규칙: user_id 별 ORDER BY create_date, id LIMIT 1. id 가 UNIQUE 라 동률 불가이므로
--      결정성은 구조적으로 보장되지만, user_id NULL(고아 sub)은 canonical 을 계산할 수 없다.
--      기대: orphan_subs = 0.
SELECT COUNT(*) AS total_rows,
       SUM(user_id IS NULL) AS orphan_subs,
       COUNT(DISTINCT user_id) AS distinct_users
FROM user_accounts;

-- G-2b. orphan 이 있으면 목록 (G-2 결과 0 이면 생략)
SELECT id, sub, auth_provider, create_date
FROM user_accounts
WHERE user_id IS NULL;

-- G-3. 복수 sub 사용자 규모 — 재키잉·병합 대상의 실제 크기.
SELECT accounts_per_user, COUNT(*) AS users
FROM (
    SELECT user_id, COUNT(*) AS accounts_per_user
    FROM user_accounts
    WHERE user_id IS NOT NULL
    GROUP BY user_id
) t
GROUP BY accounts_per_user
ORDER BY accounts_per_user;

-- G-4. 복수 sub 사용자의 매핑(분석용 보기). canonical 선정 규칙을 그대로 구현한 쿼리이므로,
--      is_canonical=1 행이 identity API 가 반환할 ownerKey 와 일치해야 한다
--      (P1 배포 후 대사에도 재사용).
WITH canonical AS (
    SELECT user_id, sub AS owner_key
    FROM (
        SELECT ua2.user_id, ua2.sub,
               ROW_NUMBER() OVER (PARTITION BY ua2.user_id ORDER BY ua2.create_date, ua2.id) AS rn
        FROM user_accounts ua2
        WHERE ua2.user_id IS NOT NULL
    ) ranked
    WHERE rn = 1
)
SELECT ua.user_id, ua.sub, ua.auth_provider, ua.create_date,
       (ua.sub = c.owner_key) AS is_canonical
FROM user_accounts ua
JOIN canonical c ON c.user_id = ua.user_id
WHERE ua.user_id IN (
    SELECT user_id FROM user_accounts
    WHERE user_id IS NOT NULL
    GROUP BY user_id HAVING COUNT(*) > 1
)
ORDER BY ua.user_id, ua.create_date, ua.id;

-- G-4c. ★ 매핑 → INSERT문 생성기 (DB 간 이동용) ★
--      전 사용자 포함(단일 sub 포함) — G-5c 유령 sub 대조에 전량이 필요하다.
WITH canonical AS (
    SELECT user_id, sub AS owner_key
    FROM (
        SELECT ua2.user_id, ua2.sub,
               ROW_NUMBER() OVER (PARTITION BY ua2.user_id ORDER BY ua2.create_date, ua2.id) AS rn
        FROM user_accounts ua2
        WHERE ua2.user_id IS NOT NULL
    ) ranked
    WHERE rn = 1
)
SELECT CONCAT('INSERT INTO tmp_owner_map (sub, owner_key) VALUES (''',
              ua.sub, ''', ''', c.owner_key, ''');') AS insert_stmt
FROM user_accounts ua
JOIN canonical c ON c.user_id = ua.user_id
ORDER BY ua.user_id, ua.create_date, ua.id;
-- (sub 는 Cognito 발급 UUID 라 따옴표·이스케이프 문제 없음 — G-1 에서 형태 확인됨.)

-- G-4c-lite. 행이 많아 붙여넣기가 무거우면 이것만 쓴다 — 복수 sub 사용자로 한정.
--      G-7 충돌 점검에는 이 부분집합으로 충분하다(단일 sub 사용자는 canonical==자기 sub 이라
--      재키잉 대상이 아니고 충돌도 만들 수 없다). 단 G-5c 는 전량이 필요하므로 lite 를 쓰면
--      G-5c 는 건너뛰고 P2 backfill 후 DB 조회로 확인한다.
WITH canonical AS (
    SELECT user_id, sub AS owner_key
    FROM (
        SELECT ua2.user_id, ua2.sub,
               ROW_NUMBER() OVER (PARTITION BY ua2.user_id ORDER BY ua2.create_date, ua2.id) AS rn
        FROM user_accounts ua2
        WHERE ua2.user_id IS NOT NULL
    ) ranked
    WHERE rn = 1
)
SELECT CONCAT('INSERT INTO tmp_owner_map (sub, owner_key) VALUES (''',
              ua.sub, ''', ''', c.owner_key, ''');') AS insert_stmt
FROM user_accounts ua
JOIN canonical c ON c.user_id = ua.user_id
WHERE ua.user_id IN (
    SELECT user_id FROM user_accounts
    WHERE user_id IS NOT NULL
    GROUP BY user_id HAVING COUNT(*) > 1
)
ORDER BY ua.user_id, ua.create_date, ua.id;
```

### D-2. medcare DB

```sql
-- G-5. 소유자 컬럼 distinct sub 전수 — P2 backfill 후 "미해석 목록" 산출의 기준선.
SELECT 'kmeta_streams' AS src, COUNT(DISTINCT user_strid) AS distinct_subs
  FROM kmeta_streams WHERE user_strid IS NOT NULL
UNION ALL SELECT 'kmeta_conversation_search', COUNT(DISTINCT auth_sub) FROM kmeta_conversation_search
UNION ALL SELECT 'customer_folders',          COUNT(DISTINCT folder_auth_sub) FROM customer_folders
UNION ALL SELECT 'customer_folder_chat',      COUNT(DISTINCT chat_auth_sub) FROM customer_folder_chat
UNION ALL SELECT 'customer_chat_rating',      COUNT(DISTINCT rating_auth_sub) FROM customer_chat_rating
UNION ALL SELECT 'customer_chat_report',      COUNT(DISTINCT report_auth_sub) FROM customer_chat_report
UNION ALL SELECT 'customer_if_log',           COUNT(DISTINCT customer_if_auth_sub) FROM customer_if_log;

-- G-6. 소유자 컬럼에 비정상 값(빈 문자열·공백)이 있는지 — 재키잉 JOIN 누수 방지. 기대: 전부 0.
SELECT 'kmeta_streams' AS src,
       SUM(user_strid = '' OR user_strid <> TRIM(user_strid)) AS bad
  FROM kmeta_streams WHERE user_strid IS NOT NULL
UNION ALL
SELECT 'customer_if_log',
       SUM(customer_if_auth_sub = '' OR customer_if_auth_sub <> TRIM(customer_if_auth_sub))
  FROM customer_if_log;

-- G-7 준비. ⚠️ 생성·INSERT·G-5c·G-7 을 반드시 같은 탭에서.
CREATE TEMPORARY TABLE tmp_owner_map (
    sub       varchar(128) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    owner_key varchar(128) COLLATE utf8mb4_bin NOT NULL,
    KEY idx_tmp_owner (owner_key)
);
-- >>> 여기에 G-4c 결과 INSERT문들 붙여넣고 실행 <<<
-- 적재 검증(메인 DB G-2 의 total_rows − orphan_subs 와 일치해야 함):
SELECT COUNT(*) AS mapped_subs, COUNT(DISTINCT owner_key) AS distinct_owners FROM tmp_owner_map;

-- G-5c. 유령 sub 확정 — medcare 소유자 컬럼에 있으나 매핑에 없는 sub.
SELECT s.sub
FROM (
    SELECT user_strid AS sub FROM kmeta_streams WHERE user_strid IS NOT NULL
    UNION SELECT auth_sub            FROM kmeta_conversation_search
    UNION SELECT folder_auth_sub     FROM customer_folders      WHERE folder_auth_sub IS NOT NULL
    UNION SELECT chat_auth_sub       FROM customer_folder_chat  WHERE chat_auth_sub IS NOT NULL
    UNION SELECT rating_auth_sub     FROM customer_chat_rating
    UNION SELECT report_auth_sub     FROM customer_chat_report
    UNION SELECT customer_if_auth_sub FROM customer_if_log      WHERE customer_if_auth_sub IS NOT NULL
) s
LEFT JOIN tmp_owner_map m ON m.sub = s.sub
WHERE m.sub IS NULL
ORDER BY s.sub;

-- G-7. 재키잉 유니크 충돌 사전 점검. 기대: G-7a~c 전부 0행.
--      원리: 재키잉 후 유니크 키의 owner 자리가 owner_key 로 바뀌므로, (owner_key, 나머지 키)
--      기준으로 지금 세어서 2행 이상이면 그게 곧 충돌이다. 기존 uk 가 sub 단위 중복은 이미
--      막고 있어 COUNT(*)>1 은 반드시 서로 다른 sibling 의 행이다.

-- G-7a. 폴더채팅 (uk_folder_chat_sub_str: owner + chat_str_id) ← 유일한 실제 위험 지점
SELECT m.owner_key, fc.chat_str_id, COUNT(*) AS rows_
FROM customer_folder_chat fc
JOIN tmp_owner_map m ON m.sub = fc.chat_auth_sub
GROUP BY m.owner_key, fc.chat_str_id HAVING COUNT(*) > 1;

-- G-7b. 평가 (uk_chat_rating_owner_target: owner + conversation + chat) — 미서비스라 참고용
SELECT m.owner_key, r.rating_conversation_strid, r.rating_chat_str_id, COUNT(*) AS rows_
FROM customer_chat_rating r
JOIN tmp_owner_map m ON m.sub = r.rating_auth_sub
GROUP BY m.owner_key, r.rating_conversation_strid, r.rating_chat_str_id HAVING COUNT(*) > 1;

-- G-7c. 신고 (uk_chat_report_sub_request: owner + request_id) — 미서비스라 참고용
SELECT m.owner_key, rp.report_request_id, COUNT(*) AS rows_
FROM customer_chat_report rp
JOIN tmp_owner_map m ON m.sub = rp.report_auth_sub
GROUP BY m.owner_key, rp.report_request_id HAVING COUNT(*) > 1;

-- G-8. pending-turn 트랙과의 간섭 확인. 재키잉 시점에 pending 행이 있어도 함께 재키잉되며
--      잠금이 OwnerScope 라 무해하다.
SELECT status, COUNT(*) FROM kmeta_chats WHERE response IS NULL GROUP BY status;
```

### D-3. 유령 sub 판정 (A/B/C 분류)

유령 sub이 나왔을 때 **백필로 해소될 것(A)/탈퇴자 잔여(C)/커버리지 구멍(B)** 을 가른다. 셋의 후속 조치가 완전히 다르다.

> **핵심 배경**: `user_accounts` 백필은 `user.sub` 하나만 심는다. 그런데 `user.sub`은 **최초 가입 provider의 sub만 고착**돼 있고 2nd provider 연동 시 갱신되지 않는다. → 2nd provider sub은 백필로도 채워지지 않고, 그 provider로 재로그인해야 자가치유된다. 이 구분이 커버리지 한계의 실제 크기를 정한다.

```sql
-- ── [medcare DB, tmp_owner_map 이 살아있는 탭] ────────────────────────────────

-- F-1. 유령 sub 의 데이터 규모·시기 — 병합 대상인지 파기 대상인지의 1차 단서.
WITH ghost AS (
    SELECT s.sub
    FROM (
        SELECT user_strid AS sub FROM kmeta_streams WHERE user_strid IS NOT NULL
        UNION SELECT auth_sub            FROM kmeta_conversation_search
        UNION SELECT folder_auth_sub     FROM customer_folders      WHERE folder_auth_sub IS NOT NULL
        UNION SELECT chat_auth_sub       FROM customer_folder_chat  WHERE chat_auth_sub IS NOT NULL
        UNION SELECT rating_auth_sub     FROM customer_chat_rating
        UNION SELECT report_auth_sub     FROM customer_chat_report
        UNION SELECT customer_if_auth_sub FROM customer_if_log      WHERE customer_if_auth_sub IS NOT NULL
    ) s
    LEFT JOIN tmp_owner_map m ON m.sub = s.sub
    WHERE m.sub IS NULL
)
SELECT g.sub,
       (SELECT COUNT(*) FROM kmeta_streams        k  WHERE k.user_strid = g.sub)           AS streams,
       (SELECT COUNT(*) FROM customer_folders     f  WHERE f.folder_auth_sub = g.sub)      AS folders,
       (SELECT COUNT(*) FROM customer_folder_chat fc WHERE fc.chat_auth_sub = g.sub)       AS folder_chats,
       (SELECT COUNT(*) FROM customer_if_log      l  WHERE l.customer_if_auth_sub = g.sub) AS if_logs,
       (SELECT MIN(k.creation_time)  FROM kmeta_streams k WHERE k.user_strid = g.sub)      AS first_conv,
       (SELECT MAX(k.last_used_time) FROM kmeta_streams k WHERE k.user_strid = g.sub)      AS last_conv,
       (SELECT MAX(l.customer_if_create_date) FROM customer_if_log l
         WHERE l.customer_if_auth_sub = g.sub)                                             AS last_activity
FROM ghost g
ORDER BY last_activity DESC;

-- F-1b. 활동 시기 분포 — 활성 여부 감 잡기.
WITH ghost AS (
    SELECT s.sub
    FROM (
        SELECT user_strid AS sub FROM kmeta_streams WHERE user_strid IS NOT NULL
        UNION SELECT auth_sub            FROM kmeta_conversation_search
        UNION SELECT folder_auth_sub     FROM customer_folders      WHERE folder_auth_sub IS NOT NULL
        UNION SELECT chat_auth_sub       FROM customer_folder_chat  WHERE chat_auth_sub IS NOT NULL
        UNION SELECT rating_auth_sub     FROM customer_chat_rating
        UNION SELECT report_auth_sub     FROM customer_chat_report
        UNION SELECT customer_if_auth_sub FROM customer_if_log      WHERE customer_if_auth_sub IS NOT NULL
    ) s
    LEFT JOIN tmp_owner_map m ON m.sub = s.sub
    WHERE m.sub IS NULL
),
act AS (
    SELECT g.sub,
           (SELECT MAX(l.customer_if_create_date) FROM customer_if_log l
             WHERE l.customer_if_auth_sub = g.sub) AS last_activity
    FROM ghost g
)
SELECT COUNT(*) AS ghost_total,
       SUM(last_activity >= UTC_TIMESTAMP() - INTERVAL 30 DAY) AS active_30d,
       SUM(last_activity >= UTC_TIMESTAMP() - INTERVAL 90 DAY) AS active_90d,
       SUM(last_activity IS NULL)                              AS no_log_activity
FROM act;

-- F-2. ★ 유령 sub → 메인 DB 이동용 INSERT문 생성 ★
WITH ghost AS (
    SELECT s.sub
    FROM (
        SELECT user_strid AS sub FROM kmeta_streams WHERE user_strid IS NOT NULL
        UNION SELECT auth_sub            FROM kmeta_conversation_search
        UNION SELECT folder_auth_sub     FROM customer_folders      WHERE folder_auth_sub IS NOT NULL
        UNION SELECT chat_auth_sub       FROM customer_folder_chat  WHERE chat_auth_sub IS NOT NULL
        UNION SELECT rating_auth_sub     FROM customer_chat_rating
        UNION SELECT report_auth_sub     FROM customer_chat_report
        UNION SELECT customer_if_auth_sub FROM customer_if_log      WHERE customer_if_auth_sub IS NOT NULL
    ) s
    LEFT JOIN tmp_owner_map m ON m.sub = s.sub
    WHERE m.sub IS NULL
)
SELECT CONCAT('INSERT INTO tmp_ghost (sub) VALUES (''', sub, ''');') AS insert_stmt
FROM ghost ORDER BY sub;


-- ── [메인 DB 탭] ─────────────────────────────────────────────────────────────

CREATE TEMPORARY TABLE tmp_ghost (
    sub varchar(128) COLLATE utf8mb4_0900_ai_ci NOT NULL PRIMARY KEY
);
-- >>> 여기에 F-2 결과 INSERT문들 붙여넣고 실행 <<<
SELECT COUNT(*) AS loaded FROM tmp_ghost;

-- F-3. ★ 판정 ★
--   A_backfill_will_fix       = user.sub 에 있고 활성(delete_yn='N')
--                               → user_accounts 백필로 자동 해소. 조치 불필요.
--   C_withdrawn               = user.sub 에 있고 탈퇴(delete_yn='Y')
--                               → 병합 대상 아님. 파기 미연동의 실측 규모(별건 트랙).
--   B_2nd_provider_or_unknown = user.sub 에도 없음
--                               → 백필로도 해소 안 됨. 그 provider 재로그인 시 자가치유.
SELECT CASE
           WHEN u.id IS NULL      THEN 'B_2nd_provider_or_unknown'
           WHEN u.delete_yn = 'Y' THEN 'C_withdrawn'
           ELSE                        'A_backfill_will_fix'
       END AS verdict,
       COUNT(*) AS subs
FROM tmp_ghost g
LEFT JOIN `user` u ON u.sub = g.sub
GROUP BY verdict;

-- F-3b. A·C 의 개별 행(탈퇴 시기·provider). B 는 여기 안 나온다(user 에 없으므로).
SELECT g.sub, u.id AS user_id, u.delete_yn, u.exit_date, u.auth_provider, u.create_date
FROM tmp_ghost g
JOIN `user` u ON u.sub = g.sub
ORDER BY u.delete_yn, u.create_date;

-- F-3c. 교차 검증 — 정말 user_accounts 에 없는지(측정 오류 배제). 기대: 0.
--   ⚠️ COLLATE 명시 필수 — user_accounts.sub 은 utf8mb4_general_ci 인데 user.sub 은
--   utf8mb4_0900_ai_ci 다. 명시하지 않으면 "Illegal mix of collations"(1267) 가 난다.
--   F-3/F-3b/F-3d 가 통과하는 것은 그쪽이 user.sub 과만 조인하기 때문이다.
SELECT COUNT(*) AS should_be_zero
FROM tmp_ghost g
JOIN user_accounts ua ON ua.sub = g.sub COLLATE utf8mb4_general_ci;

-- F-3d. (A 가 나온 경우) 백필이 실제로 이들을 덮는지 — 백필 대상 조건(활성 + user_accounts
--       결손)과 같은 모집단인지 대사. 기대: A 건수와 일치.
SELECT COUNT(*) AS backfill_covered
FROM tmp_ghost g
JOIN `user` u ON u.sub = g.sub
LEFT JOIN user_accounts ua ON ua.user_id = u.id
WHERE u.delete_yn = 'N' AND ua.id IS NULL;
```

**F-4(선택)** — B가 유의미하게 나오면 DB만으로는 정체를 못 밝힌다. Cognito `admin-get-user`로 sub별 조회해 계정 존재 여부·`phone_number`·`identities` 클레임·`UserStatus`를 본다. `phone_number`로 `user`를 찾을 수 있으면 "그 사용자의 2nd provider sub"임이 확정되고, 그 provider로 로그인하는 순간 자가치유된다는 예측이 선다. **결과에 전화번호가 포함되므로 원문을 문서에 남기지 말고 건수와 판정만 기록한다.** 조치가 달라지지 않으므로 우선순위는 낮다.

### D-4. 케이론 실측 (M-1 / M-2)

> ⚠️ **API 키를 파일·명령줄에 쓰지 말 것.** `read -rs`로 env에만 주입한다. 키가 한 번이라도 파일·히스토리에 들어갔다면 **로테이션 대상**이다.
> ⚠️ **M-1은 쓰기(PATCH·DELETE)를 포함한다.** 운영계에서 실사용자 대화에 실행하면 복구 불가다. **M-1은 개발계에서** 하고, 운영계 확인이 꼭 필요하면 **본인 계정으로 그 자리에서 만든 대화에만** 한정한다. **M-2는 읽기 전용이라 운영계에서** 한다(실제 복수 sub 데이터가 필요).

```bash
export CHEIRON_URL=<케이론 API 주소>
export CHEIRON_TENANT=<테넌트>
 read -rs CHEIRON_KEY && export CHEIRON_KEY     # 명령 앞 공백 한 칸 = 히스토리 미기록
HDR=(-H "X-API-KEY: $CHEIRON_KEY" -H "X-Tenant-Domain: $CHEIRON_TENANT")
```

**M-1 — `X-API-UID`가 파티셔닝 전용인지** (`cheiron_sub` 설계의 전제)

검증 대상: "케이론은 `X-API-UID`로 파티션만 가르고 세션·발급 시각·토큰 주체는 검증하지 않는다."

1. 개발계 테스트 계정 A로 로그인해 채팅 1건을 완료하고 대화 strid를 확보한다.
2. **최소 익일 이후**(토큰 만료·세션 소멸을 확실히 지난 뒤) 아래를 순서대로. `STRID`는 **1에서 내가 만든 대화**여야 한다.

```bash
STRID=...   # 1 에서 확보한, 내가 만든 대화
# (a) 상세 — 읽기
curl -s -o /dev/null -w "detail %{http_code}\n" "${HDR[@]}" -H "X-API-UID: $SUB_A" \
  "$CHEIRON_URL/api/data_management/conversations/$STRID"
# (b) 제목 PATCH — 쓰기
curl -s -o /dev/null -w "patch %{http_code}\n" -X PATCH "${HDR[@]}" -H "X-API-UID: $SUB_A" \
  -H "Content-Type: application/json" -d '{"title":"gate0-측정"}' \
  "$CHEIRON_URL/api/data_management/conversations/$STRID"
# (c) 이어가기 스트림 — medcare 와 동일 경로·본문이어야 유효한 검증이다.
#     POST /api/service/conversations/{graphType} — graphType·본문 스키마는 개발계 medcare
#     로그(ChatService 요청 직전 INFO)에서 캡처해 재사용한다.
# (d) 삭제 — 파괴적, 반드시 마지막. 내가 만든 테스트 대화임을 재확인하고 실행.
curl -s -o /dev/null -w "delete %{http_code}\n" -X DELETE "${HDR[@]}" -H "X-API-UID: $SUB_A" \
  "$CHEIRON_URL/api/data_management/conversations/$STRID"
```

판정: (a)~(d) 전부 2xx → 전제 성립. 하나라도 401/403 등 세션성 거부 → **설계 재검토.**

**M-2 — sibling 파티션 간 strid 교차 유일성**

kmeta의 PK가 strid 단독이라, 서로 다른 sub 파티션에 같은 strid가 있으면 병합 시 PK 충돌이 난다.

```sql
-- M-2a. strid 형식 확인 — 이것만으로 대부분 판가름 난다.
--       전역 유일성이 깨지는 유일한 현실적 경로는 케이론이 파티션 로컬 일련번호를 쓰는 경우다.
--       UUID 면 서로 다른 파티션에서 같은 값이 나올 확률은 사실상 0 이다.
SELECT COUNT(*) AS total,
       SUM(strid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') AS uuid_form,
       MIN(LENGTH(strid)) AS min_len, MAX(LENGTH(strid)) AS max_len
FROM kmeta_streams;

-- M-2a-2. chat strid 가 stream_strid 를 접두로 갖는 복합 키인지 확정
--         (실측: 73자 = 36 + 구분자 1 + 36, 구분자는 언더스코어)
SELECT COUNT(*) AS total,
       SUM(LEFT(strid, 36) = stream_strid) AS prefixed_by_stream,
       SUM(SUBSTRING(strid, 38) REGEXP
           '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') AS tail_is_uuid
FROM kmeta_chats;
```

`uuid_form == total`이면 M-2 통과로 본다. 확인 사살이 필요하면 복수 sub 사용자 한 명을 골라 두 sub 각각으로 케이론 목록을 조회해 strid 교집합이 0인지 본다.

> ⚠️ **실측 함정**: 케이론 목록 응답의 배열 키는 `conversations`가 아니라 **`results`**다. 잘못된 jq 경로는 0줄을 반환하고, **빈 파일 두 개의 `comm -12`는 항상 공집합이라 파싱 실패가 "충돌 없음"으로 위장된다.** 판정 전에 반드시 `wc -l`로 줄 수를 확인하고 DB 값과 대조한다.
>
> ⚠️ **케이론 응답의 `user_strid`는 `skix_{sub}`**(테넌트 접두)다. 역이용하면 접두를 떼어 그 대화의 실제 파티션을 알 수 있어 병합 배치의 교차 검증 수단이 된다. **다만 그 값을 `cheiron_sub`에 넣으면 안 된다**(§5의 3번).
>
> ⚠️ M-2 실행 시 strid만 추출하고 **대화 제목·본문은 저장하지 않는다**(개인정보).

---

## 부록 E. 재키잉 SQL 전문 (P5)

**실행 위치: medcare DB.** 매핑은 `medcare_user_identity`가 이미 들고 있다(P2 backfill이 backend 권위를 복사해 뒀으므로 크로스 DB 조인이 필요 없다).

**대원칙**

1. **충돌 0건일 때만 apply를 실행한다.** 충돌 해소는 별도 SQL·별도 감사 기록으로 선행한다. journal은 UPDATE만 복원할 수 있고 DELETE는 복원하지 못한다.
2. 애플리케이션은 소유자를 바꾸지 않는다(§5의 1번) — 소유자 변경은 이 런북 전유다. 그래야 journal이 모든 변경을 추적한다.
3. 실행 중 유지보수 배치를 멈춘다(부록 C P5 선행 조건).

```sql
-- ─────────────────────────────────────────────────────────────────────────────
-- §0. 사전 점검 (읽기 전용)
-- ─────────────────────────────────────────────────────────────────────────────

-- 0-1. 매핑 적재 상태. NORMAL 이 아닌 행이 있으면 그 그룹은 재키잉 대상에서 빠진다(의도).
SELECT status, COUNT(*) AS rows_, COUNT(DISTINCT owner_key) AS owners
FROM medcare_user_identity GROUP BY status;

-- 0-2. 재키잉 대상 규모 — sub ≠ owner_key 인 NORMAL 매핑만이 대상이다.
SELECT COUNT(*) AS mappings_to_rekey
FROM medcare_user_identity
WHERE status = 'NORMAL' AND sub <> owner_key;

-- 0-3. 테이블별 영향 행 수(= dry-run). 이 수치가 §2 COMMIT 전 검증의 기대값이다.
SELECT 'kmeta_streams' AS tbl, COUNT(*) AS rows_
  FROM kmeta_streams t JOIN medcare_user_identity m ON m.sub = t.user_strid
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key
UNION ALL
SELECT 'kmeta_conversation_search', COUNT(*)
  FROM kmeta_conversation_search t JOIN medcare_user_identity m ON m.sub = t.auth_sub
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key
UNION ALL
SELECT 'customer_folders', COUNT(*)
  FROM customer_folders t JOIN medcare_user_identity m ON m.sub = t.folder_auth_sub
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key
UNION ALL
SELECT 'customer_folder_chat', COUNT(*)
  FROM customer_folder_chat t JOIN medcare_user_identity m ON m.sub = t.chat_auth_sub
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key
UNION ALL
SELECT 'customer_if_log', COUNT(*)
  FROM customer_if_log t JOIN medcare_user_identity m ON m.sub = t.customer_if_auth_sub
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

-- 0-4. ★ 유니크 충돌 점검 ★ — 유일한 위험 지점은 customer_folder_chat 하나다
--      (uk_folder_chat_sub_str = (chat_auth_sub, chat_str_id)). 나머지 4개 테이블은
--      소유자 컬럼이 유니크 키에 들어 있지 않다.
--      원리: 재키잉 후의 키를 지금 계산해 2행 이상이면 그게 곧 충돌이다.
--      기대: 0행. 0 이 아니면 §1 로 해소한 뒤 이 쿼리를 다시 돌려 0 을 확인한다.
SELECT COALESCE(m.owner_key, fc.chat_auth_sub) AS new_owner,
       fc.chat_str_id,
       COUNT(*)                        AS rows_,
       GROUP_CONCAT(fc.chat_id)        AS chat_ids,
       GROUP_CONCAT(fc.chat_auth_sub)  AS current_owners
FROM customer_folder_chat fc
LEFT JOIN medcare_user_identity m
       ON m.sub = fc.chat_auth_sub AND m.status = 'NORMAL'
GROUP BY new_owner, fc.chat_str_id
HAVING COUNT(*) > 1;

-- 0-5. cheiron_sub 백필 상태 — NULL 이 남아 있으면 P2 의 출처 백필이 누락된 것이다.
--      재키잉 후에는 user_strid 가 canonical 로 바뀌어 출처를 되살릴 수 없다(선행 필수).
SELECT COUNT(*) AS cheiron_sub_null FROM kmeta_streams WHERE cheiron_sub IS NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- §1. 충돌 해소 (0-4 가 0행이 아닐 때만, apply 전에)
-- ─────────────────────────────────────────────────────────────────────────────
-- 원칙: **최근에 담은 폴더를 남긴다.** 같은 대화를 sibling 두 계정에서 각각 폴더에 담은
-- 상태이므로, 사용자가 마지막으로 의도한 배치가 최근 행이다.
-- 삭제는 복원 불가라 quarantine 으로 옮긴 뒤 지운다.

-- 1-1. quarantine 테이블(원본과 같은 스키마 + 실행 메타)
CREATE TABLE IF NOT EXISTS `rekey_quarantine_folder_chat` LIKE `customer_folder_chat`;
ALTER TABLE `rekey_quarantine_folder_chat`
    ADD COLUMN IF NOT EXISTS `quarantined_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    ADD COLUMN IF NOT EXISTS `run_id` varchar(64) DEFAULT NULL;
-- ⚠️ 이 테이블에는 대화 제목·질문·답변 스냅샷이 복제된다. 접근 권한은 원 테이블과 동일하거나
--    더 제한하고, 검증·롤백 가능 기간이 지나면 삭제한 뒤 삭제 사실을 감사 로그로 남긴다.

-- 1-2. 남길 행을 고르고(그룹별 최신 create_date, 동률이면 chat_id 최대) 나머지를 격리
--
-- ⚠️ MAX(create_date) 와 MAX(chat_id) 를 따로 구하면 **서로 다른 행**을 가리킬 수 있다.
--    ROW_NUMBER 로 정렬 기준을 하나로 묶어 보존 행을 한 행으로 확정한다 — 그래야 정책
--    ("최근에 담은 폴더 유지")과 SQL 이 실제로 같은 행을 고른다.
SET @run_id = 'rekey-YYYYMMDD-1';   -- 실행마다 바꾼다

INSERT INTO rekey_quarantine_folder_chat
SELECT fc.*, CURRENT_TIMESTAMP(6), @run_id
FROM (
    SELECT x.chat_id,
           ROW_NUMBER() OVER (
               PARTITION BY COALESCE(m.owner_key, x.chat_auth_sub), x.chat_str_id
               ORDER BY x.create_date DESC, x.chat_id DESC
           ) AS rn
    FROM customer_folder_chat x
    LEFT JOIN medcare_user_identity m ON m.sub = x.chat_auth_sub AND m.status = 'NORMAL'
) ranked
JOIN customer_folder_chat fc ON fc.chat_id = ranked.chat_id
WHERE ranked.rn > 1;   -- rn = 1 이 보존 행. rn > 1 이 곧 중복이라 별도 HAVING 이 필요 없다.

-- 1-3. 격리한 행만 삭제 (건수가 1-2 의 INSERT 건수와 같은지 확인하고 실행)
DELETE fc FROM customer_folder_chat fc
JOIN rekey_quarantine_folder_chat q ON q.chat_id = fc.chat_id AND q.run_id = @run_id;

-- 1-4. §0-4 를 다시 실행해 **0행**을 확인한다. 아니면 여기서 멈춘다.


-- ─────────────────────────────────────────────────────────────────────────────
-- §2. 재키잉 apply — journal + UPDATE 를 **하나의 트랜잭션**으로
-- ─────────────────────────────────────────────────────────────────────────────
-- DDL 은 트랜잭션 밖에서 먼저 끝낸다(MySQL 의 DDL 은 암묵적 커밋을 일으킨다).

CREATE TABLE IF NOT EXISTS `rekey_journal` (
    `id`         bigint       NOT NULL AUTO_INCREMENT,
    `run_id`     varchar(64)  NOT NULL,
    `table_name` varchar(64)  NOT NULL,
    `pk_value`   varchar(500) NOT NULL COMMENT '행 식별자(문자열 PK 는 그대로, 정수 PK 는 문자열화)',
    `old_owner`  varchar(128) NOT NULL,
    `new_owner`  varchar(128) NOT NULL,
    `applied_at` datetime(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (`id`),
    KEY `idx_rekey_journal_run` (`run_id`),
    KEY `idx_rekey_journal_table_pk` (`table_name`, `pk_value`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SET @run_id = 'rekey-YYYYMMDD-1';   -- §1 과 같은 값

START TRANSACTION;

-- 2-1. 변경 전 값을 전부 기록한다(복원 근거).
INSERT INTO rekey_journal (run_id, table_name, pk_value, old_owner, new_owner)
SELECT @run_id, 'kmeta_streams', t.strid, t.user_strid, m.owner_key
  FROM kmeta_streams t JOIN medcare_user_identity m ON m.sub = t.user_strid
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

INSERT INTO rekey_journal (run_id, table_name, pk_value, old_owner, new_owner)
SELECT @run_id, 'kmeta_conversation_search', t.stream_strid, t.auth_sub, m.owner_key
  FROM kmeta_conversation_search t JOIN medcare_user_identity m ON m.sub = t.auth_sub
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

INSERT INTO rekey_journal (run_id, table_name, pk_value, old_owner, new_owner)
SELECT @run_id, 'customer_folders', CAST(t.folder_id AS CHAR), t.folder_auth_sub, m.owner_key
  FROM customer_folders t JOIN medcare_user_identity m ON m.sub = t.folder_auth_sub
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

INSERT INTO rekey_journal (run_id, table_name, pk_value, old_owner, new_owner)
SELECT @run_id, 'customer_folder_chat', CAST(t.chat_id AS CHAR), t.chat_auth_sub, m.owner_key
  FROM customer_folder_chat t JOIN medcare_user_identity m ON m.sub = t.chat_auth_sub
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

INSERT INTO rekey_journal (run_id, table_name, pk_value, old_owner, new_owner)
SELECT @run_id, 'customer_if_log', CAST(t.customer_if_id AS CHAR), t.customer_if_auth_sub, m.owner_key
  FROM customer_if_log t JOIN medcare_user_identity m ON m.sub = t.customer_if_auth_sub
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

-- 2-2. 실제 재키잉. cheiron_sub 는 **건드리지 않는다** — 파티션 출처는 그대로여야
--      이어가기·PATCH·DELETE 가 계속 동작한다.
UPDATE kmeta_streams t
  JOIN medcare_user_identity m ON m.sub = t.user_strid
   SET t.user_strid = m.owner_key
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

UPDATE kmeta_conversation_search t
  JOIN medcare_user_identity m ON m.sub = t.auth_sub
   SET t.auth_sub = m.owner_key
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

UPDATE customer_folders t
  JOIN medcare_user_identity m ON m.sub = t.folder_auth_sub
   SET t.folder_auth_sub = m.owner_key
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

UPDATE customer_folder_chat t
  JOIN medcare_user_identity m ON m.sub = t.chat_auth_sub
   SET t.chat_auth_sub = m.owner_key
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

UPDATE customer_if_log t
  JOIN medcare_user_identity m ON m.sub = t.customer_if_auth_sub
   SET t.customer_if_auth_sub = m.owner_key
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

-- 2-3. ★ COMMIT 전 검증 ★
--   (a) journal 건수가 §0-3 dry-run 과 일치하는가
SELECT table_name, COUNT(*) AS journaled
FROM rekey_journal WHERE run_id = @run_id GROUP BY table_name;

--   (b) 잔여 0 — 아직 sibling 키로 남은 행이 없어야 한다
SELECT 'kmeta_streams' AS tbl, COUNT(*) AS remaining
  FROM kmeta_streams t JOIN medcare_user_identity m ON m.sub = t.user_strid
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key
UNION ALL
SELECT 'customer_folder_chat', COUNT(*)
  FROM customer_folder_chat t JOIN medcare_user_identity m ON m.sub = t.chat_auth_sub
 WHERE m.status = 'NORMAL' AND m.sub <> m.owner_key;

--   (c) 파티션 출처가 보존됐는가 — NULL 이 늘지 않아야 한다(§0-5 와 같은 값)
SELECT COUNT(*) AS cheiron_sub_null FROM kmeta_streams WHERE cheiron_sub IS NULL;

-- 세 검증이 전부 기대값이면:
COMMIT;
-- 하나라도 어긋나면:
-- ROLLBACK;


-- ─────────────────────────────────────────────────────────────────────────────
-- §3. 사후 확인
-- ─────────────────────────────────────────────────────────────────────────────
-- 3-1. 미통합 잔여 — 레지스트리가 모르는 소유자 키로 남은 행.
--      0 이 아닌 것이 정상이다(운영계 실측 기준 소수). 추세만 본다.
SELECT COUNT(*) AS unmapped_owner_rows
FROM kmeta_streams s
LEFT JOIN medcare_user_identity i ON i.sub = s.user_strid OR i.owner_key = s.user_strid
WHERE s.user_strid IS NOT NULL AND i.sub IS NULL;

-- 3-2. 소유자별 대화 수 — 통합된 그룹이 실제로 합쳐졌는지 표본 확인.
--      partitions > 1 인 행이 곧 병합 성공 사례다(여러 파티션의 대화가 한 소유자 아래).
SELECT user_strid, COUNT(*) AS convs, COUNT(DISTINCT cheiron_sub) AS partitions
FROM kmeta_streams
GROUP BY user_strid
HAVING COUNT(DISTINCT cheiron_sub) > 1
ORDER BY convs DESC;

-- 3-3. 유지보수 배치 재개 후 owner 단위 convertAll → drift 1회 실행(부록 C P6).


-- ─────────────────────────────────────────────────────────────────────────────
-- §4. 롤백 (journal 기반 정밀 복원)
-- ─────────────────────────────────────────────────────────────────────────────
-- 전제: 플래그를 먼저 되돌린다 — canonical-write=false, **alias-read 는 ON 유지**.

SET @run_id = 'rekey-YYYYMMDD-1';
START TRANSACTION;

UPDATE kmeta_streams t JOIN rekey_journal j
    ON j.run_id = @run_id AND j.table_name = 'kmeta_streams' AND j.pk_value = t.strid
   SET t.user_strid = j.old_owner;

UPDATE kmeta_conversation_search t JOIN rekey_journal j
    ON j.run_id = @run_id AND j.table_name = 'kmeta_conversation_search' AND j.pk_value = t.stream_strid
   SET t.auth_sub = j.old_owner;

UPDATE customer_folders t JOIN rekey_journal j
    ON j.run_id = @run_id AND j.table_name = 'customer_folders' AND j.pk_value = CAST(t.folder_id AS CHAR)
   SET t.folder_auth_sub = j.old_owner;

UPDATE customer_folder_chat t JOIN rekey_journal j
    ON j.run_id = @run_id AND j.table_name = 'customer_folder_chat' AND j.pk_value = CAST(t.chat_id AS CHAR)
   SET t.chat_auth_sub = j.old_owner;

UPDATE customer_if_log t JOIN rekey_journal j
    ON j.run_id = @run_id AND j.table_name = 'customer_if_log' AND j.pk_value = CAST(t.customer_if_id AS CHAR)
   SET t.customer_if_auth_sub = j.old_owner;

-- 건수가 journal 과 일치하는지 확인 후 COMMIT / 아니면 ROLLBACK.
COMMIT;

-- §1 에서 격리한 행까지 되살리려면 quarantine 에서 원 컬럼만 골라 INSERT 한다
-- (quarantined_at·run_id 는 제외). 단 그 사이 같은 (owner, chat_str_id) 가 다시 생겼다면
-- 유니크 제약에 걸리므로 개별 판단이 필요하다.
```

**quarantine 운영 규칙**(대화 원문이 복제될 수 있다): 접근 권한은 원 테이블과 동일하거나 더 제한, 실행 ID·생성 시각 기록, 검증·롤백 가능 기간 확정, 기간 종료 후 quarantine 삭제 + 삭제 완료 감사 로그.
