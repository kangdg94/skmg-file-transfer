# [작업] OASYS 계약번호(contract_no) 리팩토링 검토 · NMX 신방식 전환

| 항목 | 내용 |
|---|---|
| **상태** | (1) 계약번호 리팩토링 — **설계 수렴 완료 · 구현 미착수**(코드 변경 0). (2) NMX 신방식 전환 — **3개 백엔드 코드 완료 · dev/stg/main 전부 반영 · ECS 배포 여부 미확인** |
| **작업 기간** | NMX 전환 2026-06-16 ~ 2026-08-03 / 계약번호 구조 검토·실측·설계 수렴 2026-08-25 |
| **직접 수정한 저장소** | NMX 전환분: `backend-api-main`, `backend-scheduler-main`, `skix-security`. **계약번호 리팩토링은 저장소 변경 없음** (검토·실측·설계만) |
| **다른 담당자 작업과의 접점** | `backend-api-main` B015(오아시스 일 1회 계약정보 push) 수신·동기화는 **다른 백엔드 담당자**가 구현했다. 이 문서의 계약번호 검토가 그 구현의 가드 설계 근거가 됐다 |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §8-1의 1번(계약자명 검색 튜톨로지 버그 — 운영에서 이미 오동작 중)과 2번(B015 운영 승격 시 revert 사유 확인) |

---

## 0. 세 줄 요약

1. `contract.contract_no`는 **인덱스도 유니크도 없고**, `device.contract_id`가 NOT NULL+FK라 실계약이 없는 기기에도 계약 행이 강제된다. 그 결과 운영계에 **가계약번호 `T202ZEA19458` 하나가 3,760행으로 흩어져 6,301대를 물고 있고**(98%가 에어센서), 실계약번호도 약 1,200행이 중복이다. 2026-08-25에 dev·prd 실측과 3라운드 설계 검토를 마쳤고 **합의된 설계안까지 확정됐으나 구현은 한 줄도 시작하지 않았다.**
2. 검토 과정에서 **별건 버그 8곳을 확정**했다. 백오피스 통계 화면의 계약자명·모델명 검색 조건이 자기 자신과 비교하는 튜톨로지(`c.contract_no = contract_no`)라 **검색어를 무엇으로 넣든 전체가 반환된다.** 운영에서 지금도 오동작 중이며 이 리팩토링과 무관하게 고칠 수 있다.
3. OASYS 구방식 엔드포인트(`[FQDN]/api/{서비스명}/nmx/IF_NMX_xxx`) 중지에 대응해 3개 백엔드를 전부 신방식(`/api/nmx/interface` + body `interfaceid`)으로 옮겼고 **세 저장소 모두 dev·stg·main에 반영돼 있다.** 잔여 확인 4건 중 1건(구방식 URL이 남은 API 클라이언트 컬렉션)은 이번 실측에서 **이미 삭제된 것으로 확인**됐고, 나머지 3건은 로컬에서 볼 수 없는 값이라 미확인으로 남았다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **OASYS** | SK매직 계약·고객 시스템. 우리 백엔드가 HTTP 전문(인터페이스 ID별)으로 호출한다 |
| **NMX(나무엑스) 게이트웨이** | OASYS 앞단의 통합 게이트웨이. 신방식은 단일 엔드포인트 `/api/nmx/interface`에 body의 `interfaceid`로 전문을 분기한다. 구방식은 전문마다 URL이 달랐다(`/api/{서비스명}/nmx/IF_NMX_xxx`) |
| **IF_NMX_Rxxx** | OASYS 아웃바운드 전문 ID. R011=기본 계약정보 조회, R021=납입내역, R022=VOC 예약등록, R023=VOC 상세, R025=서비스 접수 증상, R045=멤버십 신청, R046=구독 여부 |
| **IF_NMX_B015** | OASYS **인바운드** 전문. 오아시스가 하루 1회 주문·계약 정보를 우리 쪽으로 push한다. 우리가 호출하는 게 아니라 받는 쪽이다 |
| **ordNo (주문번호)** | B015가 알려주는 주문번호. **우리 `contract.contract_no`와 같은 값이라고 전제하고 있으나 OASYS 측 확인이 안 됐다**(§5의 7번) |
| **ctrtNo / contract_no** | 계약번호. `contract` 테이블의 사실상 업서트 키 |
| **icstNo (통합고객번호)** | OASYS가 고객 한 명에게 부여하는 번호 |
| **가계약 (임시 계약)** | 실계약을 찾지 못한 기기를 등록할 때 붙이는 가짜 계약. 운영계는 고정값 `T202ZEA19458`, 비운영계는 랜덤 13자 |
| **ABT** | 에어봇(로봇). `device.device_type='ABT'` |
| **AQM / AQM_WLS** | 유선/무선 에어센서. `device.device_type` 값 |
| **공유 행** | 계약 행 1건에 기기가 2대 이상 매달린 상태. 그 행의 계약번호를 덮으면 무관한 기기까지 남의 계약을 가리킨다 |
| **중복 행** | 같은 `contract_no`가 `contract` 테이블에 여러 행으로 존재하는 상태. 유니크가 없어 가능하다 |
| **placeholder 계약** | 최종 설계에서 도입하기로 한 "실계약이 없음"을 뜻하는 단일 불변 계약 행(`INTERNAL-NO-OASYS`) |
| **shadow 기간** | 새 동기화를 실제 반영 없이 판정만 기록하며 돌리는 기간. 합의 3~7일 |
| **strangler 전환** | 기존 테이블을 제자리에서 고치는 대신 새 경로를 옆에 세우고 트래픽을 옮겨가는 방식 |
| **parity 검증** | 변경 전후로 같은 화면·통계가 같은 값을 내는지 대조하는 것 |

### 1-2. 문제

`contract` 테이블은 다음 구조다(`db-schema/prod.sql` 522행 부근. dev·stg·prod 3파일이 `AUTO_INCREMENT` 값 외에는 동일).

```sql
CREATE TABLE `contract` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint DEFAULT NULL,
  `company_id` int DEFAULT NULL,
  `icst_no` varchar(20) ... COLLATE utf8mb4_0900_ai_ci,
  `contract_no` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '계약번호',
  `contractor` varchar(300) COLLATE utf8mb4_general_ci,
  ...
  PRIMARY KEY (`id`),
  KEY `contract_user_FK` (`user_id`),
  KEY `contract_company_id_IDX` (`company_id`),
  KEY `contract_icstNo_IDX` (`icst_no`),
  KEY `contract_user_cust_grd_cd_id_IDX` (`user_cust_grd_cd`,`id`)
) ... COLLATE=utf8mb4_general_ci;
```

여기서 네 가지가 동시에 성립한다.

1. **`contract_no`에 인덱스도 유니크도 없다.** 위 DDL의 KEY 목록에 `contract_no`가 없다. 그런데 코드는 이 컬럼을 업서트 키로 쓴다 — `backend-api-main/src/main/resources/mapper/product/ProductDevice.xml` 67~73행:

   ```sql
   <select id="getContractId" resultType="Long">
       SELECT max(id) id FROM contract WHERE contract_no = #{contractNo}
   </select>
   ```

   `max(id)`를 고르는 것 자체가 "같은 번호가 여러 행일 수 있다"는 전제를 코드가 이미 인정하고 있다는 뜻이다.

2. **`device.contract_id`가 NOT NULL + FK다**(`db-schema/prod.sql` 594·615행). 실계약을 찾지 못한 기기도 어떤 계약 행을 반드시 가리켜야 한다 → **가계약이 구조적으로 강제된다.**

3. **가계약 생성 경로가 환경마다 다르다.**

   | 환경 | 경로 | 결과 |
   |---|---|---|
   | 운영(prd) | `OasysService.getProductOasysDeviceTmp()` (`src/main/java/com/skmagic/api/oasys/service/OasysService.java` 512~535행) | **고정값 `T202ZEA19458`**. 계약자명 `테스트`, `icstNo=201911282986`, 주소까지 전부 픽스처다 |
   | dev·stg | `ProductDeviceService.createProductDevice()` (`.../api/product/service/ProductDeviceService.java` 331~340행)에서 `SysUtil.getRandomId(13)` | 기기마다 다른 랜덤 13자 |

   운영은 호출 조건이 따로 있다 — R011로 실계약을 못 찾았을 때 **화이트리스트 사용자이거나 시리얼이 `SEN`으로 시작하면** 가계약으로 등록한다(같은 파일 319~329행). `SEN` 접두는 센서 제품군으로 보이며(스케줄러의 R025 증상 조회도 제품그룹을 `WRB`/`SEN`으로 나눠 부른다), 그렇다면 **센서는 화이트리스트와 무관하게 전부 가계약으로 들어간다.** 운영계 가계약 기기의 98%가 에어센서인 것(§3-1)은 이 분기의 결과로 보인다(코드 해석이며 데이터로 확인한 인과는 아님).

4. **`contract_no`가 25개 이상의 이력·통계 테이블에 스냅샷으로 복제돼 있다.** 그중 `history_error_action`은 `contract_no` + `serial` + `error_code`로 인덱스가 걸려 VOC 중복 방지에 쓰인다(`db-schema/prod.sql` 1664행 `history_error_action_contract_no_IDX`). **가계약 행을 공유하는 기기끼리 오류 이력을 공유하는 오동작이 성립한다.**

여기에 콜레이션이 갈린다. `contract.contract_no`는 `utf8mb4_general_ci`인데 `device.serial`과 이력 테이블의 `contract_no`는 `utf8mb4_0900_ai_ci`다. 두 컬럼을 직접 비교하면 `ERROR 1267 (Illegal mix of collations)`로 죽는다(실측). 이 때문에 "고쳐야 하는 줄 알고 조건을 제대로 붙이면 이번엔 쿼리가 깨지는" 자기은폐 구조가 된다(§3-6).

**계기는 B015였다.** 다른 백엔드 담당자가 오아시스 일 1회 계약정보 push 수신을 구현하면서, 그 동기화가 시리얼을 키로 `contract` 행을 갱신하게 된다. 공유된 가계약 행의 `contract_no`를 실주문번호로 덮으면 **형제 기기 전체가 남의 계약을 가리키게 된다.** 이 시나리오를 막을 수 있는지 확인하려다 구조 전체를 들여다보게 된 것이 이 검토다.

### 1-3. 검토 목표

- 지금 구조가 실제로 어떤 상태인지 dev·운영계에서 **수치로** 확인한다(추정 금지).
- B015 동기화가 오염을 일으킬 수 있는지, 있다면 무엇을 선행해야 막히는지 정한다.
- 계약번호를 정상화하는 경로를 **되돌릴 수 없는 실수 없이** 밟을 수 있는 순서로 확정한다.
- 정리 과정에서 드러나는 별건 결함은 분리해서 따로 고칠 수 있게 남긴다.

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 계약번호 리팩토링

| 항목 | 상태 |
|---|---|
| 설계 | **수렴 완료**(2026-08-25). 3라운드 교차 검토 후 합의본 확정. §3-3 |
| 구현 | **미착수.** 저장소 어디에도 이 리팩토링을 위한 커밋이 없다. `contract_type`·`usage_type`으로 grep 하면 3개 저장소 모두 0건 |
| DDL | 미작성. `db-schema`의 dev/stg/prod 3파일 모두 `contract` 정의가 §1-2와 동일 |
| dev 실측 | 완료(2026-08-25). §3-1 |
| 운영계 실측 | 1차·2차 완료(2026-08-25). §3-1 |
| 검증계(stg) 실측 | **미실시** |
| 별건 버그 수정 | **미착수.** §3-6의 8개 지점(계약자명 4 + 모델명 4)이 `origin/dev` 현재 코드에 그대로 있다(실측) |

### 2-2. B015 (다른 담당자 구현) — 브랜치 반영

`backend-api-main` 기준. 각 행은 `git merge-base --is-ancestor`로 확인했다.

| 커밋 | 날짜 | 내용 | dev | stg | main(운영) |
|---|---|---|---|---|---|
| `cbb9c4d5` | 08-21 | 계약 동기화·인바운드 처리 테스트 | 반영 | 반영 | **미반영** |
| `33223587` / `c2a30632` | 08-26 | 동기화 리팩터 + 공유 판정(`isShared`) | 반영 | 반영 | **미반영** |
| `e7e49d75` | 08-26 | `feature/oasys-b015-contract-sync` → dev 머지 | 반영 | 반영 | **미반영** |
| `414a07c0` | 09-07 | `S` 접두 계약번호는 `contract` 갱신 제외 | 반영 | 반영 | **미반영** |
| `7d9ac170` | 09-01 | **`hotfix/20260827_oasys_b015`를 main에서 revert** (37파일, 4,088줄 삭제) | 반영 | 반영 | **반영(=main 최신)** |

파일 단위 실측:

| 파일 | dev | stg | main |
|---|---|---|---|
| `src/main/java/com/skmagic/api/oasys/service/OasysOrderIngestService.java` | 있음 | 있음 | **없음** |
| `src/main/java/com/skmagic/api/oasys/service/OasysContractSyncService.java` | 있음 | 있음 | **없음** |
| `src/main/java/com/skmagic/api/oasys/model/OasysContractRef.java` | 있음 | 있음 | **없음** |
| `src/main/resources/mapper/oasys/OasysOrderDeviceDay.xml` | 있음 | 있음 | **없음** |

브랜치 tip(2026-09-22): `origin/dev` = `3fdd8447`(09-18), `origin/stg` = `ed6dc871`(09-18), `origin/main` = `7d9ac170`(09-01).

**`origin/main`의 트리 해시는 `60f0643e`(2026-08-06)와 바이트 단위로 같다**(둘 다 `3736b22f15b56ba3b509982358627557aaa9d373`). 즉 운영계 코드는 8/6 상태이고 8월 하순 핫픽스가 전부 되돌려져 있다.

**여기서 나오는 함정**: dev·stg를 main으로 승격하면 **의도적으로 걷어낸 B015가 그대로 복귀한다.** base에도 main에도 없고 dev에만 추가된 형태라 3-way 머지가 dev 쪽을 채택하기 때문이다. 역머지(`2f0358e8`, 09-03)를 이미 했든 안 했든 결과는 같다. **운영 승격 전에 왜 revert 했는지부터 확인해야 한다.**

수신 테이블 `oasys_order_device_day`의 DDL은 **`db-schema`의 dev·stg·prod 3파일 모두에 존재한다**(예: `prod.sql` 4197행). 즉 테이블은 운영계에도 있고 코드만 없다.

### 2-3. NMX 신방식 전환 — 브랜치 반영

| 저장소 | 핵심 커밋 | 날짜 | dev | stg | main |
|---|---|---|---|---|---|
| `backend-api-main` | `157356c0` 게이트웨이 리팩터(단일 통로 + 봉투 자동정규화) | 07-01 | 반영 | 반영 | **반영** |
| `backend-api-main` | `00f4de6f` CallCenterService 게이트웨이 이관 | 07-01 | 반영 | 반영 | **반영** |
| `backend-api-main` | `f7fd8b9d` 상담 계열 이관 | 07-01 | 반영 | 반영 | **반영** |
| `backend-scheduler-main` | `0c15abe` 오아시스 게이트웨이 추가 | 07-01 | 반영 | 반영 | **반영** |
| `skix-security` | `98b0d67` 나무엑스 호출 게이트웨이 통일 + 봉투 언래핑 | 07-01 | 반영 | 반영 | **반영** |
| `skix-security` | `11ff2fe` 전문 구분 라벨 + R011 DTO 정합성 | 07-01 | 반영 | 반영 | **반영** |

`backend-scheduler-main` tip: `origin/dev` = `405302a`(08-25), `origin/stg` = `origin/main` = `a800341`(08-26). **stg와 main이 같은 커밋**이라 스케줄러는 검증계·운영계가 같은 형상이다.

`skix-security` tip: `origin/dev` = `0fa25b5`(09-14), `origin/stg` = `04b28dc`(09-14), `origin/main` = `f19e9d7`(08-31).

### 2-4. 배포·확인 상태

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| NMX 코드의 ECS 실제 배포 | **미확인.** 로컬 맥에서는 ECS를 볼 수 없다 | VDI에서 3개 서비스의 태스크 정의 이미지 태그를 브랜치 tip과 대조 |
| `skix-security` 운영계 `OASYS_API_URL` 실제 값 | **미확인.** 코드상 `application-ecs.yml` 34행이 `${OASYS_API_URL}`이라 값이 저장소에 없다 | VDI에서 운영 태스크 정의 환경변수 확인. 기대값은 `https://oasys.skmagic.com` |
| R026 전문 미사용 | **코드상 재확인 완료** — `backend-api-main`·`skix-security`·`backend-scheduler-main` 3저장소에 `R026` 문자열 0건(빌드 산출물 제외 grep) | OASYS 측에 "우리는 R026을 쓰지 않는다" 회신은 **미결** |
| 구방식 URL이 남은 API 클라이언트 컬렉션(Bruno) | **해소됨.** `622b3d62`(08-13)가 컬렉션 파일 278개를 포함해 347파일·57,609줄을 걷어냈고 dev·stg에 반영돼 있다. `origin/dev`·`origin/stg`·`origin/main` 어느 트리에도 `.bru`가 0건이다(main은 애초에 없었음) | — |
| 구방식 엔드포인트 중지 기한 | 공지상 **2026-08-07**. 기준일 기준 6주 경과 | 기한 이후 OASYS 연동 장애 보고는 확인하지 못했다. 장애가 없었다면 전환이 실제로 유효했다는 방증이지만 **로그로 확인한 사실은 아니다** |

---

## 3. 무엇을 검토·확정했나

이 절은 코드가 아니라 **판단**을 남긴 것이다. 구현이 시작되면 이 절이 스펙 역할을 한다.

### 3-1. 실측 결과 (2026-08-25)

재실행 가능한 SQL은 **부록 B**에 있다. 아래는 그 결과다.

**개발계(dev)**

| 관측 | 값 |
|---|---|
| 지배 병리 | **공유가 아니라 중복 행이다.** `T202ZEA19437` 211행, `CN00000001` 108행, `abcdefghijk` 55행 |
| 실형식 번호도 중복 | `T253RA*` 최대 41행 |
| 중복의 시점 | 전부 2025-03~05 이전 레거시. **현행 업서트 코드는 결백하다.** 다만 `contract_no`에 인덱스가 없어 동시 등록 레이스 여지는 남아 있다 |
| 실계약번호 형식 | **현행은 13자 `[A-Z0-9]`**, 접두 `T` 4,724건 / `M` 99건 / `F` 84건 |
| 구형식 | 12자 `T202ZEA*`는 2020년경 형식. **즉 계약번호 형식은 시간에 따라 진화한다** |
| B015 오염 벡터 | **진성 0건.** 오탐 2건은 13자 실번호였다 |
| 22자 랜덤 더미(`g*`) | 전부 `icst_no=201911282986` — `getProductOasysDeviceTmp()` 하드코딩값이다. 구버전 생성기 산물로 확정 |
| `G253GB158594`(12자, `icst_no` NULL) | **미확정.** 어느 경로 산물인지 판별 못 함 |

**운영계(prd) 1차**

| 관측 | 값 |
|---|---|
| 가계약 | `T202ZEA19458` 단일 값이 **3,760행** |
| 가계약에 물린 기기 | **6,301대**, 여러 행에 분산. 최대 행(id 10555) 하나에 **203대** |
| 랜덤 더미 | **없음**(코드와 정합 — 운영은 고정값 경로만 탄다) |
| 실번호 | 전부 13자. **5,695개 번호가 6,897행** → 약 1,200행 중복. 상위는 `G257GB1065*` 계열로 11~27행 |
| 고아 행(기기가 안 매달린 계약) | **23개뿐** → 단순 DELETE로는 정리 불가. **병합·재지정이 필수** |
| 콜레이션 | `contract_no`(general_ci) vs `serial`·이력테이블(0900_ai_ci) 상이. 직접 문자열 비교 시 `ERROR 1267` 실증 |

**운영계(prd) 2차**

| 관측 | 값 |
|---|---|
| 가계약 6,301대의 구성 | **98%가 에어센서** — AQM 1,877 + AQM_WLS 4,303, ABT는 **122대뿐** |
| AQM 분리 후 잔여 더미 | ABT 122대 + 미연동 ABT 8,171대 재지정 |
| 실계약에 물린 센서 | 2,212대. AQM 계약 연동을 제거하면 이들도 함께 NULL이 된다 |
| `G` 접두 계약 | 시리얼 복사본이 아니고, 고객번호가 실재하며, 계약자명을 갖고 있다 → **실계약으로 확정** |
| `G` 계약의 구조 | 로봇 1대 + 센서 6대 패키지. 연동 해제 버그가 아니라 정상 상품 구성이다(별건 해제) |
| `G` 계약과 B015 피드 | dev B015 피드(T/M/F 4,907건)에 **G가 없다** |
| `device_type` 빈 값 | 2~3행. 별도 정리 대상 |

> **주의 — 수치 재확인이 필요한 지점.** 가계약 구성 합(1,877 + 4,303 + 122 = 6,302)이 기록된 총계 6,301과 1 어긋난다. 정리 규모를 산정할 때는 부록 B의 쿼리를 다시 돌려 수치를 새로 잡을 것. 미연동 ABT 8,171대도 기록된 값이며 재실행으로 확인해야 한다.

**`G` 계약 가설 2개 (미판별)**

1. 재발번 전의 스테일 구계약 — 그렇다면 B015 동기화가 바로잡을 대상이다.
2. B015 피드에 포함되지 않는 별도 상품군 — 그렇다면 동기화 대상에서 빼야 한다.

판별법은 부록 B의 `B-10`이다. 운영계 `G` 계약에 물린 로봇 시리얼(예: `WRBA1M10KRNGJ0225F00059`)을 dev의 `oasys_order_device_day`에서 조회해 나오는지 본다. **아직 실행하지 않았다.**

### 3-2. 지배 병리 4종 (정리 대상)

| # | 병리 | 왜 문제인가 |
|---|---|---|
| 1 | **중복 행** (같은 번호가 여러 `contract` 행) | 업서트가 `max(id)`를 고르므로 어떤 행이 선택될지 시점에 따라 달라진다. 화면·통계가 행마다 다른 `user_id`·`company_id`를 보게 된다 |
| 2 | **공유 행** (한 계약 행에 기기 다수, 최대 203대) | 그 행의 `contract_no`를 덮으면 무관한 기기 전부가 남의 계약을 가리킨다. 이력·통계에도 그대로 전파된다 |
| 3 | **가계약 픽스처 오염** | `계약자명=테스트`, 하드코딩 `icst_no`, 고정 주소가 정식 계약처럼 취급된다. 기기가 1대만 남는 순간 "제자리 갱신"으로 새면 **픽스처 값을 그대로 단 정식 계약**이 만들어진다 |
| 4 | **콜레이션 상이** | `contract_no`(general_ci) ↔ `serial`/이력(0900_ai_ci). 조인·비교를 제대로 쓰려는 순간 1267로 깨진다. 잘못된 쿼리(§3-6)가 오히려 "동작하는" 상태를 유지하는 원인이다 |

### 3-3. 확정 설계 (3라운드 수렴본, 2026-08-25)

별도 설계 검토 세션과 서로의 안을 교차 반박하는 방식으로 3라운드를 돌려 합의한 결과다. 핵심 4가지.

**① 동기화는 기존 행을 고치지 않는다 — 실계약번호로 `contract` UPSERT + `device.contract_id` 재지정**

수신한 실계약번호로 계약 행을 만들거나 찾고, 그 기기의 `contract_id`만 옮긴다. 기존 행의 `contract_no`를 덮는 일이 아예 없으므로 공유 행 오염이 **원천 차단**된다. 부수 효과로 동기화가 돌 때마다 기기가 canonical 계약으로 조금씩 이주하므로 **나중에 병합할 대상이 스스로 줄어든다.**

**② 비고객 ABT는 `TEST-{serial}`이 아니라 단일 불변 placeholder `INTERNAL-NO-OASYS`**

| 속성 | 값 |
|---|---|
| `contract_no` | `INTERNAL-NO-OASYS` (3환경 동일, **절대 수정 금지**) |
| `contract_type` | `INTERNAL_PLACEHOLDER` |
| PII 컬럼 (`contractor`, `icst_no`, 주소 등) | 전부 NULL |
| 기기 성격 분류 | 계약이 아니라 **`device.usage_type`**이 맡는다. 값은 `CUSTOMER` / `TEST` / `PV` / `UNKNOWN`, **초기값은 전부 `UNKNOWN`**이고 명시적 인벤토리 작업으로만 분류한다 |

기기당 1행(`TEST-{serial}`)을 만드는 초안은 **철회했다.** 기기 수만큼 계약 행이 늘어나고, 계약이 아닌 것을 계약 테이블로 표현하는 문제가 그대로 남기 때문이다. 최종 모델은 **"미할당이 정상 상태"**다.

> `contract_type` 컬럼의 전체 값 목록은 검토 기록에 `INTERNAL_PLACEHOLDER` 외에는 확정돼 있지 않다. 구현 시 `OASYS`(실계약)와 `INTERNAL_PLACEHOLDER` 둘로 시작하고 필요할 때 늘리는 것이 검토 취지에 맞다.

**③ 중복 행 in-place 병합은 조건부 — Phase D 게이트를 통과할 때만**

shadow 기간과는 **별개의** 게이트다. 네 가지를 전부 통과해야 in-place 병합을 확정한다.

| 게이트 | 내용 |
|---|---|
| payload conflict 실측 | 병합할 행들의 컬럼값이 실제로 충돌하는지, 충돌 시 어느 쪽을 남길지 규칙이 데이터로 결정되는지 |
| dry-run | 실제 UPDATE/DELETE 없이 결과만 산출해 건수·영향 기기를 확인 |
| parity | 변경 전후 화면·통계가 같은 값을 내는지 대조 |
| rollback | 되돌릴 수 있는지, 되돌리는 절차가 실제로 돈다는 확인 |

**하나라도 실패하면 in-place 병합을 포기하고 strangler 전환으로 간다.**

**④ `ordNo` = 계약번호가 확인되기 전에는 자동 반영 금지**

이것은 **릴리스 블로커**다. B015의 `ordNo`를 `contract.contract_no`로 쓰는 전제가 OASYS 측 확인 없이 성립하고 있다. 확인 전까지는 **수신·적재(receive)만 켜고 반영(apply)은 끈다.**

**추가 보완 4건**

| # | 보완 | 이유 |
|---|---|---|
| a | placeholder 재지정 **전에** `history_error_action` 키 전환과 스케줄러 `user_id` 전환을 **선행**한다 | 안 하면 이력 공유가 오히려 확대되고 운영시간 통계가 누락된다 |
| b | 기기 등록 경로가 placeholder 행을 **UPDATE 하지 못하게** 막는다 | `ProductDevice.xml` 288~310행 `updateProductContract`는 `contract_no`를 포함해 **전 컬럼을 덮어쓴다.** placeholder 행에 이게 걸리면 불변 전제가 즉시 깨진다 |
| c | `contract_no` UNIQUE를 **병합 직후 · 동기화 ON 이전**으로 앞당긴다 | UPSERT 동시성 안전의 전제다. UNIQUE 없이 UPSERT를 켜면 중복 행이 다시 생긴다 |
| d | parity 검증 대상 화면·통계 목록을 **Phase 0에서** 확정한다 | 나중에 정하면 "무엇이 깨졌는지 모르는 상태"로 병합을 진행하게 된다 |

**합의된 공통 원칙** — 동기화 플래그 OFF로 시작, shadow 3~7일, 수신 원문 전량 저장, 판정코드 기록, 할당 이력 테이블, PII 축소, 인바운드 전용키, **형식 판별 전면 금지**(§5의 3번).

### 3-4. AQM(에어센서) 계약 연동 제거 — 결정 사항

운영계 가계약 기기의 98%가 에어센서라는 실측(§3-1)에 따른 결정이다. **에어센서는 계약과 연결할 이유가 없다.**

| 작업 | 내용 |
|---|---|
| 스키마 | `device.contract_id`를 **NULL 허용**으로 변경 |
| 등록 경로 | AQM 등록 시 `contract` 생성·연결을 생략 |
| 백필 | 기존 AQM·AQM_WLS 행의 `contract_id`를 NULL로 |
| 뒷정리 | 참조를 잃은 가계약 행 삭제 |

**검증한 부작용 2건**

- 백오피스 control/sensor 화면에 계약번호·계약자명 검색이 있지만 **운영계 AQM이 전부 가계약이라 이미 죽은 기능이다.** 검색 옵션 제거를 함께 한다.
- `history_aqm_*` 적재 서브쿼리는 NULL 적재로 무해하다.

**선행해야 하는 NOT NULL 전제 3곳 (실측)**

| 위치 | 내용 |
|---|---|
| `skix-streaming/src/main/java/com/skix/streaming/module/device/adapter/out/persistence/jpa/Device.java` 33행 | `@Column(name = "contract_id", nullable = false, ...)`. JPA 엔티티가 NOT NULL을 선언하고 있다 |
| `backend-api-main/src/main/resources/mapper/device/DeviceConfig.xml` 400행 | `INNER JOIN contract con ON dev.contract_id = con.id`. NULL이 되면 기기가 목록에서 사라진다 |
| `backend-scheduler-main/src/main/resources/mapper/Collect.xml` 402·434행 | `join contract c on c.id = abt.contract_id` (에어봇·AQM 전력상태 수집). 두 쿼리 모두 **로봇의** `contract_id`로 조인한다. 센서만 NULL이 되는 동안은 영향이 없지만, NULL 허용 이후 로봇이 미할당(NULL)이 되면 그 로봇과 딸린 센서의 전력상태 이력이 **에러 없이 조용히 빠진다** |

### 3-5. B015 가드는 이미 있다 — 단 `contract_type` 컬럼이 아니다

검토 시점 표현은 "`contract_type` 가드 선행 필수"였다. **구현은 컬럼 없이 판정 메서드로 수렴했다.** `contract_type`으로 grep 하면 0건이라 "가드가 없다"고 오판하기 쉽다.

`backend-api-main/src/main/java/com/skmagic/api/oasys/model/OasysContractRef.java` 49~72행:

```java
public static final String PROVISIONAL_CONTRACT_NO = "T202ZEA19458";
private static final int SHARED_DEVICE_CNT = 2;

public boolean isShared() {
    return deviceCnt >= SHARED_DEVICE_CNT
            || PROVISIONAL_CONTRACT_NO.equals(contractNo);
}
```

- 공유 행이면 제자리 갱신이 아니라 **새 계약을 만들고 그 기기 1대만 옮긴다**(`forkContract`).
- **가계약은 대수와 무관하게 항상 공유로 본다.** 대수 조건만 쓰면 공유 기기가 하나씩 빠져나가 1대가 남는 순간 제자리 갱신으로 새어, 픽스처 값을 그대로 단 정식 계약이 만들어지기 때문이다.
- 이 판정을 B015 수신 경로(`OasysContractSyncService`)와 관제 재등록 경로(`ProductDeviceService.createProductDeviceRenewal`, 1312행)가 **함께 쓴다.** 규칙이 갈리면 같은 계약을 한쪽은 덮고 한쪽은 새로 만드는 상태가 된다.

추가로 `OasysContractSyncService`는 B015의 `ordNo`와 R011 응답의 `ordNo`를 **교차검증**해 불일치·미확정이면 보류(`skippedNotReady`)한다. 정식 계약번호가 아닌 `S` 접두 번호는 대상 선정과 쓰기 직전 **두 지점에서** 걸러진다(`OasysContractSyncTarget.isExcludedContractNo`, 34~37행).

**결론**: §1-2에서 경고한 "공유 가계약 행을 실주문번호로 덮어 형제 기기가 남의 계약을 가리킴" 시나리오는 **현재 dev·stg 코드에서 차단돼 있다.** 다만 이 판정은 값 비교라서 **비운영계의 랜덤 13자 가계약은 판별하지 못한다**(코드 주석도 그렇게 적혀 있다). 그래서 §3-3 ②의 placeholder가 여전히 필요하다.

### 3-6. 별건 버그: 계약자명·모델명 검색 튜톨로지 (운영에서 오동작 중)

**이 리팩토링과 독립적으로 지금 고칠 수 있고, 지금 고쳐야 한다.**

`backend-api-main/src/main/resources/mapper/statistics/` 아래 4개 지점(기준일 `origin/dev` 실측 행 번호):

| 파일 | 행 | 소속 SQL 조각 |
|---|---|---|
| `ErrorStatistics.xml` | **122** | `<sql id="searchStatisicsCondition">` (104행 시작) |
| `ErrorStatistics.xml` | **166** | `<sql id="searchStatisicsConditionGetErrorLevelStatistics">` (148행 시작) |
| `ErrorStatistics.xml` | **343** | `<sql id="searchCondition">` (328행 시작) |
| `OperatingMode.xml` | **22** | `<sql id="searchCondition">` (7행 시작) |

네 곳 모두 똑같다.

```sql
<if test="searchKey == 'contractor' and searchValue !=null ">
    AND EXISTS (select 1 from contract c
    where c.contract_no = contract_no and c.contractor like CONCAT('%', #{searchValue}, '%')) /* 계약자명 */
</if>
```

**무슨 일이 일어나는가**: 서브쿼리 안의 한정 없는 `contract_no`는 MySQL 이름 해석 규칙상 **내부 테이블 `contract c`의 컬럼으로 먼저 잡힌다.** 즉 `c.contract_no = c.contract_no`가 되어 항상 참이다(NULL 제외). 바깥 테이블과의 상관 조건이 사라지므로 **계약자명을 아무거나 넣어도 그 이름을 가진 계약이 DB에 하나라도 있으면 전체 행이 반환된다.** 필터가 아예 없는 것과 같다.

**같은 클래스의 버그가 모델명 검색에도 4곳 있다**: `where m.id = d.model_id and d.serial = serial and ...` — `d.serial = d.serial`로 해석된다. 위치는 `ErrorStatistics.xml` **130·174·351행**, `OperatingMode.xml` **30행**. 계약자명 검색과 같은 SQL 조각 안에 짝으로 들어 있다.

**영향 범위**: 이 SQL 조각들은 백오피스 오류 통계(일·주·월·기간), 제품별 오류 목록, 오류 이력 목록·건수, 운영모드 사용량(현황·일·주·월·기간) 쿼리가 `<include refid=...>`로 공유한다. 프론트에서 해당 검색 옵션은 `frontend-web-backoffice/src/pages/main/analysis/` 아래 오류·운영 화면에 노출돼 있다.

**고칠 때 반드시 함께 처리해야 하는 것**: 한정만 붙여 `c.contract_no = he.contract_no`로 고치면 **이번에는 콜레이션 1267이 터진다**(`contract.contract_no`=general_ci vs 이력 테이블=0900_ai_ci). 이 자기은폐 구조가 버그를 오래 살려둔 원인이다.

**권장 수정 방향**: 문자열 조인이 아니라 **`contract_id` 경유 조인**으로 바꾼다. `device.contract_id → contract.id`는 BIGINT 비교라 콜레이션 문제가 없고 인덱스도 탄다. 통계 테이블에 `device_id`가 있으면 `device`를 한 번 거치면 된다. 임시 회피가 필요하면 `COLLATE utf8mb4_0900_ai_ci`를 명시할 수 있으나 인덱스를 못 타므로 정공법이 아니다.

### 3-7. `contract.user_id` 의존 잔존 3건 (최종)

계약 테이블에서 앱 사용자 아이디를 떼내는 작업(`384e647a`, 06-01 외 6커밋)은 대부분 끝났다. 남은 3건을 확정했다.

| # | 위치 | 상태 | 처리 |
|---|---|---|---|
| 1 | `backend-scheduler-main/src/main/resources/mapper/Collect.xml` **380~381행** (운영시간 일집계 적재) | **라이브.** `FROM user u, contract c, device d, model m, statistics_operation so WHERE u.id = c.user_id` — INNER 조인이라 `user_id`가 NULL이면 그 기기의 운영시간이 통계에서 통째로 빠진다 | 전환 필요 |
| 2 | `backend-api-main/src/main/resources/mapper/oasys/OasysOrderDeviceDay.xml` **45행** (B015 enrich — 앱 버전 조회) | **라이브.** `LEFT JOIN user u ON u.id = c.user_id`. 파일 주석(109~110행)도 "비면 앱버전 조회가 끊긴다"고 적고 있다. dev·stg에만 있는 파일이다 | 전환 필요 |
| 3 | `backend-api-main/src/main/resources/mapper/callcenter/CallCenter.xml` **153행** `getIcstNo` | **dead query 실증.** `CallCenterMapper.java` 25행에 선언은 있으나 `callCenterMapper.getIcstNo(` 호출부가 서비스 코드 어디에도 없다(기준일 재확인) | **삭제** |

**교차 검토에서 뒤집힌 주장 1건**: `CallCenterService`의 `getContractInfo`가 `contract.user_id`에 의존한다는 지적이 있었으나 **틀렸다.** `CallCenter.xml` 62~71행은 `user_building → device → contract` 경로로 조인하며 `user_id`를 쓰지 않는다(실측).

### 3-8. 계약번호가 사는 곳이 세 군데라는 사실

정리를 설계할 때 놓치기 쉬운 지점이다.

| 위치 | 성격 |
|---|---|
| `contract.contract_no` (backend-api-main DB) | 원본 |
| 이력·통계 25+ 테이블의 `contract_no` 컬럼 | 그 시점 스냅샷. **소급 재작성하지 않기로 확정**(§5의 8번) |
| `security.device_current_contract.ctrt_no` (skix-security 별도 스키마) | 시리얼→계약번호 캐시. R011 성공과 R047 웹훅만 갱신한다. `backend-api-main`의 `contract` 정리와 **자동으로 맞춰지지 않는다** |

---

## 4. OASYS NMX 신방식 전환

### 4-1. 무엇이 바뀌었나

| | 구방식 | 신방식 |
|---|---|---|
| URL | `{FQDN}/api/{서비스명}/nmx/IF_NMX_xxx` — 전문마다 다름 | **`{FQDN}/api/nmx/interface` 단일** |
| 전문 지정 | URL 경로 | **request body의 `interfaceid` 필드** |
| 응답 봉투 | 전문별로 1겹 또는 2겹(`payload.payload`) | 동일. 저장소마다 **자동 언래핑 단일 지점**을 둬서 호출부가 전문별로 분기하지 않게 했다 |

전환의 핵심은 URL 교체가 아니라 **호출 통로를 저장소마다 하나로 모은 것**이다. 통로가 하나면 다음 규격 변경 때 고칠 곳이 한 곳이고, 전환 누락을 grep 한 번으로 확인할 수 있다.

### 4-2. 저장소별 적용 상태

**OASYS를 직접 호출하는 주체는 3개 백엔드뿐이다.** 프론트(백오피스)·앱(iOS/Android)·`skix-streaming`은 전부 자사 백엔드(`*.namuhx.com`)를 경유하며 OASYS 직접 호출이 없다.

| 저장소 | 단일 통로 | 경로 상수 위치 | 사용 전문 |
|---|---|---|---|
| `backend-api-main` | `src/main/java/com/skmagic/api/oasys/service/OasysGateway.java` (봉투 정규화는 `OasysEnvelopeUnwrapper`) | 전문 목록은 `.../service/OasysInterface.java` | R002, R008, R011, R020, R022, R023, R033, R034, R042 |
| `backend-scheduler-main` | `src/main/java/com/skmagic/batch/oasys/OasysGateway.java` **27행** `INTERFACE_PATH = "/api/nmx/interface"` | `.../oasys/OasysInterface.java` | R023, R025 |
| `skix-security` | `src/main/java/com/skix/security/infra/http/oasys/OasysApiClient.java` **43행** `NMX_INTERFACE = "/api/nmx/interface"` (봉투는 `OasysResponseReader`) | 같은 파일 45~48행 | R011, R021, R045, R046 |

**인바운드(B015)는 이 전환과 별개다.** 오아시스가 우리를 호출하는 방향이라 엔드포인트는 우리 쪽 `OasysInboundController`이고, 봉투는 `{ "data": [...], "interfaceId": "IF_NMX_B015" }` 형태다(필드명이 아웃바운드의 `interfaceid`와 대소문자가 다르다).

### 4-3. OASYS 베이스 URL이 어디에 있는가 (환경별)

| 저장소 | 설정 위치 | dev/개발 | stg/검증 | prd/운영 |
|---|---|---|---|---|
| `backend-api-main` | 프로필별 yml 리터럴 (`application-{dev,stg,prd}.yml`의 `oasys.api.url`) | `https://oasysdev.skmagic.com` (dev.yml 161행) | `https://oasysqa.skmagic.com` (stg.yml 158행) | `https://oasys.skmagic.com` (prd.yml 158행) |
| `backend-scheduler-main` | **빌드 시점에 선택되는 리소스 디렉터리** (`build.gradle` 45~53행, `-Pprofile=` 값으로 `src/main/resources-{profile}`을 소스셋에 넣는다) | `resources-vdi/application.yml` 50행 → `https://oasysdev.skmagic.com` | `resources-stag/application.yml` 50행 → `https://oasysqa.skmagic.com` | `resources-prod/application.yml` 50행 → `https://oasys.skmagic.com` |
| `skix-security` | 런타임 환경변수 | (로컬 프로필은 `application-localhost.yml` 29행에 `https://oasysdev.skmagic.com`) | — | `application-ecs.yml` 34행 `base-url: ${OASYS_API_URL}` |

**스케줄러의 함정 2가지.**
1. **프로필 이름이 관례와 다르다.** 개발계가 `dev`가 아니라 **`vdi`**, 검증계가 `stg`가 아니라 **`stag`**다. `-Pprofile=dev`로 빌드하면 `src/main/resources-dev`가 없어 설정이 통째로 비는 형태가 된다.
2. **OASYS URL이 빌드 타임에 jar에 박힌다.** 런타임 환경변수로 못 바꾼다. URL을 바꾸려면 재빌드·재배포가 필요하다.

또 `backend-scheduler-main`의 프로필별 yml에는 **OASYS 접속 자격증명이 평문으로 들어 있다**(운영 포함). 이 문서에는 값을 옮기지 않는다. 시크릿 정리 작업의 대상이다(§8-3).

### 4-4. 잔여 확인 4건 — 기준일 재실측 결과

| # | 항목 | 2026-08-03 기록 | 2026-09-22 실측 |
|---|---|---|---|
| 1 | `skix-security` 운영계 `OASYS_API_URL` 실제 값 | 확인 필요 | **여전히 미확인.** 값이 저장소에 없다(태스크 정의 환경변수). 다만 **경로 `/api/nmx/interface`는 코드 상수**(`OasysApiClient.java` 43행)라 환경변수가 무엇이든 구방식 경로로 돌아갈 수는 없다. 위험은 "잘못된 호스트"뿐이다 |
| 2 | `backend-scheduler-main` 신방식 코드의 배포 여부 | 확인 필요 | **브랜치는 확인됨** — `0c15abe`(07-01)가 dev·stg·main 전부에 있고 stg와 main은 같은 커밋(`a800341`, 08-26)이다. **ECS 실배포는 미확인** |
| 3 | R026 전문 미사용 확인 | OASYS 측 확인 필요 | **코드 재확인 완료** — 3저장소 grep 0건. **OASYS 회신은 미결** |
| 4 | API 클라이언트 컬렉션(Bruno)에 구방식 URL 잔존 | 정리 필요(런타임 무관) | **해소됨.** `622b3d62`(08-13)가 컬렉션 파일 278개 포함 347파일을 걷어냈고 dev·stg 반영. 세 브랜치 어디에도 `.bru`가 없다 |

---

## 5. 되돌리면 안 되는 설계 결정

구현 시작 시 "더 단순해 보인다"는 이유로 바꾸면 사고가 나는 것만 남겼다.

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **동기화는 기존 계약 행의 `contract_no`를 덮지 않는다.** 실계약번호로 UPSERT하고 `device.contract_id`만 재지정한다 | 제자리 갱신은 공유 행에서 곧바로 오염이 된다. 운영계 최대 행 하나에 203대가 물려 있어, 한 번의 잘못된 UPDATE가 203대를 남의 계약으로 만든다. 계약 정보는 이력·통계로 계속 복제되므로 되돌리기도 어렵다 |
| 2 | **가계약은 대수와 무관하게 항상 "공유"로 판정한다** (`OasysContractRef.isShared()`) | 대수 조건만 남기면, 공유 기기가 하나씩 빠져나가 1대가 남는 순간 제자리 갱신으로 새어 **픽스처 값(계약자명 `테스트`, 하드코딩 통합고객번호, 고정 주소)을 그대로 단 "정식 계약"**이 만들어진다 |
| 3 | **계약번호를 형식으로 판별하지 않는다** (`startsWith("T")`, 자릿수, 정규식) | dev 실측에서 실계약 접두가 `T`(4,724) 외에 **`M`(99)·`F`(84)**로 존재하고, 12자는 2020년경 구형식이며 현행은 13자다. **형식은 시간에 따라 진화한다.** 현재 `skix-security/src/main/java/com/skix/security/domain/service/OasysService.java` 97~102행 `isCallableContractNo()`가 `startsWith("T")`를 쓰고 있어 M/F 실계약을 막는다 — 실증된 모순이며 컬럼 기준 판정으로 대체 대상이다. 단, **지금 그냥 풀면** 가계약·비정상 번호가 R045(계약 체결)로 나가므로 **placeholder·`contract_type` 도입과 같은 릴리스에서만** 바꾼다 |
| 4 | **placeholder 행(`INTERNAL-NO-OASYS`)은 어떤 경로로도 UPDATE 하지 않는다** | `ProductDevice.xml` 288~310행 `updateProductContract`는 `contract_no`를 포함해 **전 컬럼을 덮어쓴다.** 등록·재등록 경로가 placeholder 행에 이걸 걸면 불변 전제가 즉시 깨지고, 그 순간 placeholder를 공유하던 기기 전부가 한 고객의 계약을 가리킨다 |
| 5 | **placeholder 재지정보다 `history_error_action` 키 전환과 스케줄러 `user_id` 전환을 먼저 한다** | 순서를 뒤집으면 placeholder를 공유하는 기기끼리 **VOC 오류 이력이 섞이는 범위가 오히려 넓어지고**(`history_error_action`은 `contract_no`+`serial`+`error_code`로 조회한다), `Collect.xml` 380~381행의 INNER 조인 때문에 운영시간 통계에서 기기가 통째로 빠진다 |
| 6 | **`contract_no` UNIQUE는 병합 직후 · 동기화 ON 이전에 건다** | UNIQUE 없이 UPSERT를 켜면 동시 요청이 중복 행을 다시 만든다. 지금 막 정리한 것을 새로 오염시키는 순서다. 반대로 병합 전에 걸면 기존 중복 때문에 DDL 자체가 실패한다. 창이 정확히 그 사이다 |
| 7 | **`ordNo` = `contract_no` 확인 전에는 자동 반영을 켜지 않는다.** 수신·적재만 켠다 | 전제가 틀리면 전 기기의 계약번호를 주문번호로 덮어쓰는 결과가 된다. 계약 체결·과금과 연결된 값이라 사후 복구가 사실상 불가능하다. **릴리스 블로커다** |
| 8 | **이력·통계의 `contract_no` 스냅샷은 소급 재작성하지 않는다** | 그 시점의 사실이다. 이후 쌓이는 이력은 `device → contract`를 타고 새 번호로 들어간다. 소급 재작성은 25개 이상 테이블에 대한 대규모 UPDATE이며, 되돌릴 수 없고 과거 조회 결과를 바꾼다 |
| 9 | **AQM 계약 연동 제거 전에 NOT NULL 전제 3곳을 먼저 푼다** (§3-4) | `skix-streaming`의 JPA 엔티티는 부팅·매핑 단계에서 깨지고, `DeviceConfig.xml` 400행 INNER 조인은 기기를 목록에서 사라지게 하며, `Collect.xml` 402·434행은 **전력상태 이력을 조용히 빠뜨린다**(에러 없이 행이 줄어드는 형태라 눈치채기 어렵다) |
| 10 | **B015를 운영에 올리기 전에 왜 revert 했는지 먼저 확인한다** | `origin/main`은 8/6 트리이고 B015는 `7d9ac170`으로 걷어내진 상태다. dev·stg를 그냥 승격하면 **3-way 머지가 dev 쪽을 채택해 B015가 통째로 복귀한다.** 역머지 여부와 무관하다 |
| 11 | **`contract_type`으로 grep 해서 0건이어도 "가드가 없다"고 판단하지 않는다** | 가드는 컬럼이 아니라 `OasysContractRef.isShared()` + `OasysContractSyncService`의 `ordNo` 교차검증으로 구현돼 있다(§3-5). "없으니 만들자"고 중복 구현하면 두 규칙이 갈려 같은 계약을 한쪽은 덮고 한쪽은 새로 만드는 상태가 된다 |

---

## 6. 실행 계획 (Phase별)

합의된 설계(§3-3)를 실행 순서로 편 것이다. **Phase 간 순서에는 §5의 근거가 붙어 있다. 순서를 바꾸지 말 것.**

| Phase | 내용 | 산출물 | 완료 판정 | 선행 조건 |
|---|---|---|---|---|
| **0. 준비** | parity 검증 대상 화면·통계 목록 확정(보완 d). 판정코드 체계·할당 이력 테이블 설계. 수신 원문 전량 저장 확인. 동기화 플래그 OFF 고정. stg 실측(부록 B 전체를 검증계에서 재실행) | 대상 목록 문서, DDL 초안, stg 실측표 | 목록이 확정되고 stg 수치가 dev·prd와 대조됨 | 없음 — **여기부터 시작한다** |
| **A. 선행 의존 제거** | ① `history_error_action` 조회 키를 `contract_no` → `contract_id`(또는 `device_id`) 기준으로 전환. ② `Collect.xml` 380~381행 `u.id = c.user_id` 의존 제거. ③ `backend-api-main` B015 enrich의 `user_id` 의존 제거. ④ `CallCenter.xml` 153행 `getIcstNo` dead query 삭제 | 3저장소 커밋 | 4건 전환 완료, 운영시간·전력상태 통계 parity 통과 | Phase 0 |
| **B. placeholder·분류 도입** | `contract.contract_type` 컬럼 추가. `INTERNAL-NO-OASYS` 행 3환경 동일 생성. `device.usage_type` 컬럼 추가(전부 `UNKNOWN`). 등록 경로가 placeholder를 UPDATE 하지 못하게 차단(§5의 4번). 가계약 생성 경로를 placeholder로 일원화 | DDL + `backend-api-main` 커밋 | 신규 등록이 랜덤 더미·고정 가계약을 더 이상 만들지 않음 | **Phase A 필수**(§5의 5번) |
| **C. AQM 계약 연동 제거** | NOT NULL 전제 3곳 해제(§3-4) → `device.contract_id` NULL 허용 → AQM 등록 경로에서 contract 생략 → 기존 AQM·AQM_WLS 백필 NULL → 참조 잃은 가계약 행 삭제 → 백오피스 계약 검색 옵션 제거 | DDL + 3저장소 커밋 + 백필 스크립트 | 운영계 가계약 연동 기기가 6,301대 → ABT만 남음. 전력상태·기기목록 parity 통과 | Phase B |
| **D. 중복 병합 게이트** | payload conflict 실측 → dry-run → parity → rollback 절차 확인. **4개 전부 통과 시에만** in-place 병합 확정. 하나라도 실패하면 strangler 전환으로 전환 | 게이트 판정 기록, 병합 스크립트 | 4개 게이트 통과 또는 strangler 결정 | Phase C |
| **E. UNIQUE** | 중복 병합 직후 `contract_no`에 UNIQUE 부여 | DDL | 제약 적용, 등록·재등록 회귀 통과 | **Phase D 직후, Phase F 이전**(§5의 6번) |
| **F. 동기화 단계적 ON** | ① 수신·적재만 ON. ② shadow 3~7일 — 판정만 기록하고 반영 안 함. ③ 판정 결과 검토. ④ **`ordNo`=`contract_no` OASYS 확인 완료 후** 자동 반영 ON | 플래그 전환 기록, shadow 판정 리포트 | shadow 기간 오판정 0건 + OASYS 회신 확보 | Phase E + **§5의 7번 해소** |
| **G. 형식 판별 제거** | `skix-security` `isCallableContractNo()`의 `startsWith("T")`를 `contract_type` 기준으로 교체. `PROVISIONAL_CONTRACT_NO`·`DUMMY_CONTRACT_NOS` 하드코딩 제거 | `skix-security` 커밋 | M/F 실계약으로 R045·R046 정상 호출, placeholder는 차단 | Phase B(placeholder 실재) — **B와 같은 릴리스가 아니면 위험**(§5의 3번) |

**별건(어느 Phase에도 종속되지 않음)**: §3-6의 튜톨로지 버그 8곳(계약자명 4 + 모델명 4). **지금 바로 고칠 수 있고, 운영에서 이미 오동작 중이므로 먼저 고치는 게 맞다.**

---

## 7. 남은 실행 항목 (검토 종료 시점 기준)

검토 종료 시점(2026-08-25)에 "남은 건 실행"으로 정리한 4가지다.

| # | 항목 | 상태 | 비고 |
|---|---|---|---|
| 1 | OASYS 문의 10건 | **미발송** | 문의 항목 10건의 **원문 목록은 남아 있지 않다.** 설계가 실제로 의존하는 미확정 전제는 부록 C에 정리했다 |
| 2 | `G` 시리얼 dev B015 대조 쿼리 | **미실행** | 부록 B의 `B-10`. `G` 계약이 스테일 구계약인지 별도 상품군인지 판별하는 유일한 수단 |
| 3 | stg 실측 | **미실시** | 부록 B 전체를 검증계에서 재실행. 환경 간 스키마 drift가 실재하므로(부록 B-14) 환경별로 각각 봐야 한다 |
| 4 | Phase 0 구현 착수 | **미착수** | §6 |

---

## 8. 남은 일

### 8-1. 즉시 (인수 후 첫 주)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **§3-6 튜톨로지 버그 수정.** 계약자명 검색 `ErrorStatistics.xml` 122·166·343행, `OperatingMode.xml` 22행 + 모델명 검색 `ErrorStatistics.xml` 130·174·351행, `OperatingMode.xml` 30행(총 8곳). 한정만 붙이면 콜레이션 1267이 터지므로 `contract_id` 경유 조인으로 고친다 | 백엔드 | 운영에서 이미 오동작 중. 리팩토링과 독립 |
| 2 | **B015 운영 승격 판단.** `7d9ac170`으로 왜 revert 했는지 확인. 확인 전에는 `backend-api-main` stg→main 승격 금지 | 백엔드 + revert 수행자 | §5의 10번 |
| 3 | NMX 잔여 3건 확인 — 운영 `OASYS_API_URL` 값, 3개 서비스 ECS 배포 여부, OASYS에 R026 미사용 회신 | 백엔드 | VDI 필요. §4-4 |
| 4 | `CallCenter.xml` 153행 `getIcstNo` dead query 삭제 | 백엔드 | 단독으로 안전하다. 호출부 없음 실측 |

### 8-2. 착수 게이트 (계약번호 리팩토링을 시작하기 전에 확정할 것)

| # | 확인할 것 | 상대 |
|---|---|---|
| 5 | **`ordNo` = `contract_no`인가.** B015 주문번호를 계약번호로 써도 되는지 | OASYS |
| 6 | **`G` 접두 계약의 정체.** 스테일 구계약인가 별도 상품군인가 (부록 B `B-10` 실행 후 문의) | OASYS |
| 7 | 계약번호 **재발번 정책** — 교체구독 등으로 번호가 바뀌면 옛 번호는 어떻게 되는가 | OASYS |
| 8 | 부록 C의 나머지 전제 | OASYS · 기획 |
| 9 | stg 실측 완료 (부록 B) | 백엔드 |
| 10 | parity 검증 대상 화면·통계 목록 확정 | 백엔드 · 기획 |

### 8-3. 후속 (이 작업을 막지는 않음)

| # | 항목 |
|---|---|
| 11 | `backend-scheduler-main`의 프로필별 yml에 OASYS 접속 자격증명이 평문으로 들어 있다(운영 포함). 시크릿 정리 작업 대상 |
| 12 | `backend-scheduler-main`의 프로필 이름이 관례와 다르다(개발계 `vdi`, 검증계 `stag`). 다른 저장소와 맞추거나, 최소한 README·배포 스크립트에 명시 |
| 13 | `OasysService.getProductOasysDeviceTmp()`(512행)는 주석에 "운영 임시 삭제 대상"이라고 적혀 있다. placeholder 도입(Phase B)과 함께 제거 |
| 14 | `OasysService.OasysSearchInfoCtrtNo()`(772~790행)의 dev 전용 분기 — 계약번호가 12자가 아니면 `T202ZEA19458`로 바꿔치기한다. 개발계 전용 하드코딩이며 Phase B 대상 |
| 15 | `dev` 환경의 `G253GB158594`(12자, `icst_no` NULL) 정체 미확정. 어느 경로 산물인지 판별 |
| 16 | `device.device_type` 빈 값 2~3행(운영계) 정리 |
| 17 | `skix-security`의 `security.device_current_contract` 캐시와 `backend-api-main`의 `contract` 정리가 자동으로 맞춰지지 않는다(§3-8). Phase C~F에서 캐시 무효화 절차 필요 |

---

## 9. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| "백오피스에서 계약자명으로 검색했는데 전부 나와요" | **버그다**(§3-6). 오동작이지 데이터 문제가 아니다. 수정 전까지는 계약번호로 검색하도록 안내한다(계약번호 검색은 정상 동작한다) |
| "이 기기 계약자가 `테스트`로 나와요" | 가계약(`T202ZEA19458`)에 물린 기기다. 부록 B `B-3`으로 확인. 운영계에서 정상적으로 발생하는 상태이며 실계약이 잡히면 바뀐다 |
| "같은 계약번호인데 화면마다 값이 달라요" | 중복 행이다(§3-2의 1번). 업서트가 `max(id)`를 고르므로 어느 행을 보느냐에 따라 `user_id`·`company_id`가 다르다. 부록 B `B-2`로 행 수를 본다 |
| 계약번호 관련 쿼리가 `ERROR 1267`로 죽음 | 콜레이션 상이다(§3-2의 4번). `contract.contract_no`는 `utf8mb4_general_ci`, `device.serial`·이력 테이블은 `utf8mb4_0900_ai_ci`. **문자열 조인 대신 `contract_id` 경유**로 바꾼다 |
| B015 수신 결과 확인 | `SELECT base_date, COUNT(*) FROM oasys_order_device_day GROUP BY base_date ORDER BY base_date DESC LIMIT 7;` — 하루 1회 수신이라 날짜당 1묶음이 정상 |
| B015 동기화가 아무것도 안 바꿨을 때 | 정상일 수 있다. 계약번호가 그대로면 R011도 부르지 않고 넘어가는 설계다. 로그 키워드 `[OASYS-IN]`. `skippedNotReady`는 `ordNo` 교차검증 불일치로 **의도적으로 보류한 것**이지 실패가 아니다 |
| R045·R046이 `OASYS_INVALID_CONTRACT`로 거절됨 | `skix-security` `OasysService.isCallableContractNo()`가 막은 것이다. 가계약(`T202ZEA19458`)이거나, `T`로 시작하지 않는 번호(**M·F 실계약 포함 — 알려진 결함**)이거나, 대문자·숫자 외 문자가 섞였을 때다. 로그: `Blocking R045 for serial=..., non-callable ctrtNo=...` |
| OASYS 호출이 전부 실패 | 저장소별 베이스 URL 위치가 다르다(§4-3). 스케줄러는 **빌드 타임에 jar에 박히므로 환경변수로 못 고친다** |
| 에어센서 기기 목록에서 기기가 사라짐 | AQM 계약 연동 제거(Phase C)를 하는 중이라면 `DeviceConfig.xml` 400행 INNER 조인이 원인이다(§3-4) |

---

## 부록 A. 핵심 커밋 (시간순)

### A-1. NMX 전환 (작성자 작업)

| 날짜 | 저장소 | 커밋 | 내용 | dev/stg/main |
|---|---|---|---|---|
| 06-16 | backend-api-main | `d9def788` | 통합고객번호(R008)·세이프키(R002) 연동 추가 | 전부 반영 |
| 06-16 | backend-api-main | `bbbc52e2` | R002/R008 사이트코드 및 호출 URL 수정 | 전부 반영 |
| 06-18 | backend-api-main | `fde7460b` | R002/R008 응답 payload 2단 중첩 파싱 수정 | 전부 반영 |
| 06-23 | skix-security | `0c46696` | R045·R046 계약번호 검증 일원화 — **`T` 접두 규칙 추가**, R045 ResolveService 경유 | 전부 반영 |
| 07-01 | backend-api-main | `157356c0` | **OASYS 게이트웨이 리팩터 — 단일 통로 + 봉투 자동정규화** | 전부 반영 |
| 07-01 | backend-api-main | `00f4de6f` / `f7fd8b9d` / `25c8d032` | CallCenter·상담 계열 게이트웨이 이관, 앱 미사용 스텁 정리 | 전부 반영 |
| 07-01 | backend-scheduler-main | `0c15abe` | **오아시스 게이트웨이 추가** (R023·R025) | 전부 반영 |
| 07-01 | skix-security | `98b0d67` | **나무엑스 호출 게이트웨이 통일 + 봉투 자동 언래핑** | 전부 반영 |
| 07-01 | skix-security | `11ff2fe` | 전문 구분(operation 라벨) + R011 응답 DTO 정합성 | 전부 반영 |
| 08-03 | skix-security | `4942d5f` | R011 호출량 감축 — 현재계약 테이블·계약정보 캐시 도입 | 전부 반영 |
| 08-13 | backend-api-main | `622b3d62` | 운영 디버그 코드·불필요 리소스 제거 (**구방식 URL이 남아 있던 API 클라이언트 컬렉션 파일 278개 포함 347파일 제거**) | dev·stg 반영, main 해당 없음 |

### A-2. `contract.user_id` 제거 (작성자 작업, 계약번호 검토의 전사)

| 날짜 | 저장소 | 커밋 | 내용 |
|---|---|---|---|
| 06-01 | backend-api-main | `384e647a` | 계약정보에서 앱 사용자 아이디 제거 |
| 06-30 ~ 07-01 | backend-api-main | `4648a17a`, `25c75af5`, `119a7574`, `3588e15f`, `0a4dab26`, `da333158` | `contract.user_id` 사용하지 않도록 수정 (6커밋) |

잔존 3건의 최종 판정은 §3-7.

### A-3. B015 (다른 담당자 작업 — 이 검토가 가드 설계 근거)

| 날짜 | 커밋 | 내용 | dev | stg | main |
|---|---|---|---|---|---|
| 08-21 | `cbb9c4d5` | 계약 동기화·인바운드 처리 테스트 추가 | 반영 | 반영 | 미반영 |
| 08-26 | `33223587` | 계약 동기화·갱신 로직 리팩터 | 반영 | 반영 | 미반영 |
| 08-26 | `c2a30632` | **공유 판정(`isShared`) 구현 + 단위 테스트** | 반영 | 반영 | 미반영 |
| 08-26 | `e7e49d75` | `feature/oasys-b015-contract-sync` → dev 머지 | 반영 | 반영 | 미반영 |
| 09-01 | `7d9ac170` | **`hotfix/20260827_oasys_b015` revert** (37파일 4,088줄 삭제) | 반영 | 반영 | 반영(main tip) |
| 09-03 | `2f0358e8` | 운영계 핫픽스를 dev로 역머지 (내용상 no-op) | 반영 | 반영 | 미반영 |
| 09-07 | `414a07c0` | `S` 접두 계약번호는 contract 갱신 제외 | 반영 | 반영 | 미반영 |

---

## 부록 B. 실측 SQL 팩 (재실행용)

**실행 위치**: `backend-api-main` DB. 로컬 맥에서는 DB에 직접 붙을 수 없으므로 **회사 VDI에서** 실행한다.

**주의 2가지.**
- 컨테이너로 붙는 경우 `docker exec -i ... mysql --default-character-set=utf8mb4`로 실행한다. `-i`와 문자셋 지정이 없으면 한글이 깨지거나 입력이 전달되지 않는다.
- **출력 없음(=0건)과 오류를 혼동하지 말 것.** 0건은 0건이다.

주석의 `-- 실측:` 줄은 2026-08-25에 관측된 값이다. 재실행 결과와 다르면 **재실행 결과가 맞다.**

### B-1. 한 계약 행에 몇 대가 매달려 있나 (공유 행 규모)

```sql
SELECT c.id            AS contract_id,
       c.contract_no,
       c.contractor,
       COUNT(d.id)     AS device_cnt
  FROM contract c
  JOIN device   d ON d.contract_id = c.id
 GROUP BY c.id, c.contract_no, c.contractor
 ORDER BY device_cnt DESC
 LIMIT 20;
-- 실측(prd): 최상위 행 id=10555 하나에 203대
```

### B-2. 같은 계약번호가 몇 행으로 흩어져 있나 (중복 행 규모)

```sql
SELECT contract_no,
       COUNT(*)            AS row_cnt,
       MIN(create_date)    AS first_seen,
       MAX(create_date)    AS last_seen
  FROM contract
 GROUP BY contract_no
HAVING COUNT(*) > 1
 ORDER BY row_cnt DESC
 LIMIT 30;
-- 실측(dev): T202ZEA19437 211행 / CN00000001 108행 / abcdefghijk 55행 / T253RA* 최대 41행
--            (전부 2025-03~05 이전 생성 — 현행 업서트 코드 산물이 아님)
-- 실측(prd): T202ZEA19458 3,760행 / 실번호 5,695개가 6,897행(약 1,200행 중복),
--            상위는 G257GB1065* 계열 11~27행
```

### B-3. 가계약에 물린 기기 수와 그 구성

```sql
-- B-3a. 총량
SELECT COUNT(DISTINCT c.id) AS contract_rows,
       COUNT(d.id)          AS device_cnt
  FROM contract c
  JOIN device   d ON d.contract_id = c.id
 WHERE c.contract_no = 'T202ZEA19458';
-- 실측(prd): contract_rows=3,760 / device_cnt=6,301

-- B-3b. 기기 종류별 구성
SELECT d.device_type, d.linked_yn, COUNT(*) AS cnt
  FROM contract c
  JOIN device   d ON d.contract_id = c.id
 WHERE c.contract_no = 'T202ZEA19458'
 GROUP BY d.device_type, d.linked_yn
 ORDER BY cnt DESC;
-- 실측(prd): AQM 1,877 / AQM_WLS 4,303 / ABT 122  (합 6,302 — 기록된 총계 6,301과 1 어긋난다.
--            정리 규모 산정 시 이 쿼리 결과를 기준으로 삼을 것)
-- 참고: 미연동 ABT 8,171대가 재지정 대상으로 기록돼 있다. linked_yn 분해로 재확인할 것
```

### B-4. 계약번호 형식 분포 (형식 판별을 금지하는 근거)

```sql
SELECT LEFT(contract_no, 1)     AS prefix,
       CHAR_LENGTH(contract_no) AS len,
       COUNT(*)                 AS cnt,
       MIN(contract_no)         AS sample_min,
       MAX(contract_no)         AS sample_max
  FROM contract
 GROUP BY prefix, len
 ORDER BY cnt DESC;
-- 실측(dev): 현행은 13자 [A-Z0-9]. 접두 T=4,724 / M=99 / F=84
--            12자 T202ZEA* 는 2020년경 구형식
-- → skix-security 의 startsWith("T") 게이트는 M/F 실계약과 모순한다
```

### B-5. 고아 계약 행 (기기가 하나도 안 매달린 계약)

```sql
SELECT COUNT(*) AS orphan_cnt
  FROM contract c
  LEFT JOIN device d ON d.contract_id = c.id
 WHERE d.id IS NULL;
-- 실측(prd): 23건 → 단순 DELETE 로는 정리 불가. 병합·재지정이 필수
```

### B-6. 콜레이션 상이 확인과 1267 재현

```sql
-- B-6a. 어느 컬럼이 어떤 콜레이션인가
SELECT TABLE_NAME, COLUMN_NAME, COLLATION_NAME
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND COLUMN_NAME IN ('contract_no', 'serial')
 ORDER BY COLLATION_NAME, TABLE_NAME;
-- 실측: contract.contract_no = utf8mb4_general_ci
--       device.serial, history_*.contract_no = utf8mb4_0900_ai_ci

-- B-6b. 직접 비교하면 죽는다 (의도적으로 실패시키는 쿼리)
SELECT 1
  FROM contract c
  JOIN history_error_action h ON h.contract_no = c.contract_no
 LIMIT 1;
-- 기대: ERROR 1267 (HY000): Illegal mix of collations
-- 이 오류가 안 나면 환경 간 스키마 drift 를 의심할 것
```

### B-7. B015 오염 벡터 — 공유 행이 1대 매칭으로 뚫리는 건이 있는가

가드가 도입되기 전 위험도를 재는 쿼리다. 지금도 회귀 확인용으로 쓸 수 있다.

```sql
SELECT o.ord_no,
       o.install_qr_info AS serial,
       c.id              AS contract_id,
       c.contract_no     AS current_contract_no,
       cnt.device_cnt
  FROM oasys_order_device_day o
  JOIN device   d ON d.serial = o.install_qr_info COLLATE utf8mb4_0900_ai_ci
  JOIN contract c ON c.id     = d.contract_id
  JOIN (SELECT contract_id, COUNT(*) AS device_cnt
          FROM device
         GROUP BY contract_id) cnt ON cnt.contract_id = c.id
 WHERE o.base_date  = (SELECT MAX(base_date) FROM oasys_order_device_day)
   AND cnt.device_cnt = 1                    -- 1대 매칭이면 제자리 갱신으로 뚫린다
   AND c.contract_no <> o.ord_no             -- 번호가 다를 때만 갱신 대상이 된다
   AND CHAR_LENGTH(c.contract_no) <> 13      -- 13자 실번호는 오탐이므로 제외
 ORDER BY o.ord_no;
-- 실측(dev): 진성 0건 (위 마지막 조건 없이 돌리면 오탐 2건이 섞여 나온다 — 둘 다 13자 실번호)
```

### B-8. 22자 랜덤 더미의 출처 확인

```sql
SELECT contract_no, icst_no, contractor, COUNT(*) AS cnt
  FROM contract
 WHERE CHAR_LENGTH(contract_no) = 22
 GROUP BY contract_no, icst_no, contractor
 ORDER BY cnt DESC
 LIMIT 20;
-- 실측(dev): 전부 icst_no = 201911282986
--            → OasysService.getProductOasysDeviceTmp() 하드코딩값이므로 구버전 생성기 산물로 확정
--            (현행 비운영 코드는 SysUtil.getRandomId(13) 이라 13자를 만든다)
```

### B-9. `G` 접두 계약이 실계약인지 확인

```sql
SELECT c.contract_no,
       c.contractor,
       c.icst_no,
       COUNT(d.id)                        AS device_cnt,
       GROUP_CONCAT(DISTINCT d.device_type) AS device_types,
       GROUP_CONCAT(d.serial SEPARATOR ' ') AS serials
  FROM contract c
  JOIN device   d ON d.contract_id = c.id
 WHERE c.contract_no LIKE 'G%'
 GROUP BY c.contract_no, c.contractor, c.icst_no
 ORDER BY device_cnt DESC
 LIMIT 30;
-- 실측(prd): contract_no 가 시리얼 복사본이 아니고, icst_no 실재, contractor 보유
--            → 실계약으로 확정. 로봇 1대 + 센서 6대 패키지 구조 (연동 해제 버그가 아님)
```

### B-10. `G` 계약 판별 — 운영계 로봇 시리얼을 dev B015 피드에서 조회 (**미실행**)

`G` 계약이 "재발번 전 스테일 구계약"인지 "B015에 안 오는 별도 상품군"인지 가르는 유일한 수단이다.

```sql
-- 1단계 (운영계에서): G 계약에 물린 로봇 시리얼 목록을 뽑는다
SELECT DISTINCT d.serial
  FROM contract c
  JOIN device   d ON d.contract_id = c.id
 WHERE c.contract_no LIKE 'G%'
   AND d.device_type = 'ABT'
 ORDER BY d.serial;
-- 예시로 기록된 시리얼: WRBA1M10KRNGJ0225F00059

-- 2단계 (개발계에서): 그 시리얼이 B015 피드에 존재하는지 본다
--   아래 IN 목록에 1단계 결과를 붙여 넣는다
SELECT base_date, ord_no, install_qr_info, ord_prdt_qcrs_no,
       ctrt_type_nm, ord_sts_nm, cttr_icst_no
  FROM oasys_order_device_day
 WHERE install_qr_info  IN ('WRBA1M10KRNGJ0225F00059' /* , ... */)
    OR ord_prdt_qcrs_no IN ('WRBA1M10KRNGJ0225F00059' /* , ... */)
 ORDER BY base_date DESC;

-- 판정:
--   행이 나오고 ord_no 가 T/M/F 계열 → G 는 재발번 전 스테일 구계약.
--                                      B015 동기화가 바로잡을 대상이다.
--   행이 없음                        → G 는 B015 에 오지 않는 별도 상품군.
--                                      동기화 대상에서 제외해야 한다.
-- 참고: dev B015 피드에는 T/M/F 4,907건이 있고 G 는 하나도 없다(실측)
```

### B-11. 가계약 공유로 인한 오류 이력 섞임 확인

```sql
SELECT contract_no,
       error_code,
       COUNT(DISTINCT serial) AS serial_cnt,
       COUNT(*)               AS row_cnt
  FROM history_error_action
 WHERE contract_no = 'T202ZEA19458'
 GROUP BY contract_no, error_code
HAVING serial_cnt > 1
 ORDER BY row_cnt DESC
 LIMIT 20;
-- serial_cnt > 1 인 행이 나오면, 서로 무관한 기기가 같은 (contract_no, error_code) 키를
-- 공유하고 있다는 뜻이다. VOC 중복 방지가 이 키로 도는 탓에 오동작이 성립한다.
```

### B-12. AQM 계약 연동 제거의 영향 범위

```sql
-- B-12a. 실계약에 물려 있는 센서 (분리하면 함께 NULL 이 된다)
SELECT COUNT(*) AS sensors_on_real_contract
  FROM device   d
  JOIN contract c ON c.id = d.contract_id
 WHERE d.device_type IN ('AQM', 'AQM_WLS')
   AND c.contract_no <> 'T202ZEA19458';
-- 실측(prd): 2,212대

-- B-12b. 분리 후 가계약에 남는 것
SELECT d.device_type, d.linked_yn, COUNT(*) AS cnt
  FROM device   d
  JOIN contract c ON c.id = d.contract_id
 WHERE c.contract_no = 'T202ZEA19458'
   AND d.device_type = 'ABT'
 GROUP BY d.device_type, d.linked_yn;
-- 실측(prd): 연동 ABT 122대. 그 밖에 미연동 ABT 8,171대가 재지정 대상으로 기록돼 있다
```

### B-13. `device_type` 빈 값 정리 대상

```sql
SELECT id, serial, device_type, contract_id, linked_yn, create_date
  FROM device
 WHERE device_type IS NULL OR device_type = '';
-- 실측(prd): 2~3행
```

### B-14. 스키마 drift 확인 (환경별로 각각 실행)

```sql
SHOW CREATE TABLE contract;
SHOW CREATE TABLE device;
SHOW CREATE TABLE history_error_action;
-- db-schema 의 dev.sql / stg.sql / prod.sql 과 실제 DB 가 다를 수 있다.
-- 제약(NOT NULL 여부)은 반드시 해당 환경에서 확인할 것.
-- 실례: history_control.contract_no 는 db-schema 3파일 모두 DEFAULT NULL 인데
--       실제 DB 는 dev 만 NOT NULL 이었다. 같은 코드가 dev 에서는 예외로 터지고
--       stg/prd 에서는 조용히 NULL 을 기록했다. 한 환경의 결과를 다른 환경에 일반화하지 말 것
```

---

## 부록 C. OASYS에 확인해야 할 전제

검토 종료 시점에 "문의 10건"으로 기록됐으나 **항목 원문 목록은 남아 있지 않다.** 아래는 **확정된 설계(§3-3)가 실제로 의존하고 있는 미확정 전제를 코드·실측에서 역산한 것**이며, 원래 10건과 일대일 대응한다는 보장은 없다. 문의서를 다시 쓸 때 출발점으로 쓴다.

| # | 확인할 것 | 왜 필요한가 | 미확인 시 결과 |
|---|---|---|---|
| 1 | **B015의 `ordNo`가 우리가 아는 계약번호(`ctrtNo`)와 같은 값인가.** 다르다면 대응 관계는 무엇인가 | 동기화 전체가 이 전제 위에 서 있다 | **릴리스 블로커.** 전제가 틀리면 전 기기의 계약번호를 주문번호로 덮는다(§5의 7번) |
| 2 | **`G` 접두 계약번호는 무엇인가.** 현행 발번 체계인가, 폐기된 구번호인가, 별도 상품군인가 | 운영계 중복 상위가 `G257GB1065*` 계열이다. 병합 대상인지 아닌지가 갈린다 | 병합 범위를 정할 수 없다. 부록 B `B-10` 선행 |
| 3 | **계약번호 형식 규칙과 그 이력.** 현행 13자 `[A-Z0-9]`, 접두 `T`/`M`/`F`가 전부인가. 앞으로 늘어날 수 있는가 | `skix-security`의 `startsWith("T")` 게이트가 M/F를 막고 있다(실증) | 형식 판별을 안전하게 제거할 근거가 없다(§5의 3번) |
| 4 | **`S` 접두 번호("세이프계약번호")의 정의.** 왜 `contract`에 남기면 안 되는가, 언제 오는가 | 현재 코드가 접두 문자열로 제외하고 있다(`OasysContractSyncTarget`). 형식 판별 금지 원칙과 충돌한다 | 컬럼 기준 판정으로 바꿀 수 없다 |
| 5 | **계약번호 재발번 정책.** 교체구독 등으로 번호가 바뀌면 옛 번호는 폐기되는가, 재사용될 수 있는가 | `contract_no` UNIQUE를 걸어도 되는지가 여기서 갈린다 | Phase E(UNIQUE)를 확정할 수 없다 |
| 6 | **한 계약에 기기가 여러 대 붙는 정상 케이스가 있는가.** 있다면 그 구조는 무엇인가 | 운영계에서 로봇 1 + 센서 6 패키지를 관측했다(§3-1). "공유 = 비정상"이라는 판정 규칙의 전제 | `isShared()` 판정이 정상 패키지를 오분류한다 |
| 7 | **B015에 포함되지 않는 계약·기기가 있는가.** 있다면 무엇이 빠지는가 | dev 피드에 `G`가 하나도 없다(실측) | 동기화 커버리지를 알 수 없고, "안 온다 = 해지"로 오해할 위험 |
| 8 | **같은 기준일에 B015를 두 번 보내는 경우가 있는가.** 있다면 뒤엣것이 전량 교체인가 증분인가 | 수신 테이블 UNIQUE가 `(base_date, ord_no)`다 | 재수신 처리가 데이터를 잃거나 중복시킬 수 있다 |
| 9 | **R011 응답의 `ordNo`와 B015의 `ordNo`가 항상 같은가.** 다를 수 있다면 어느 쪽이 권위인가 | 현재 동기화가 둘을 교차검증해 불일치면 보류한다. 정상적으로 다를 수 있으면 보류가 쌓인다 | `skippedNotReady`가 계속 쌓여도 정상인지 이상인지 판단 불가 |
| 10 | **계약이 해지되면 B015에 어떻게 나타나는가** (`cnlt_dt` 채움 / 목록 제외 / 상태코드 변경) | 해지 기기의 계약을 어떻게 처리할지가 정해지지 않았다 | 해지 기기가 계약을 계속 가리키거나, 반대로 멀쩡한 기기를 해지로 오판한다 |
| 11 | **가계약(`T202ZEA19458`)을 OASYS도 인지하고 있는가.** 이 번호로 전문을 보내면 어떻게 응답하는가 | 현재 우리가 로컬에서 차단하고 있다(`DUMMY_CONTRACT_NOS`) | placeholder 전환 후 차단 방식을 바꿀 근거가 없다 |
