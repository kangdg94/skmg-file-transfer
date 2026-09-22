# [작업] 시크릿 평문 커밋 조치 — SSM Parameter Store 이관과 협력사 태스크 게이트

| 항목 | 내용 |
|---|---|
| **상태** | `backend-api-main` SSM 이관 **개발계·검증계 브랜치 반영 완료** · **운영계(`main`) 미반영** · skix 3종 로컬 시크릿 분리 **dev·stg·main 전부 반영** · **키 로테이션·git 이력 재작성 미착수** · 협력사 clone 게이트 **해소 안 됨** |
| **작업 기간** | 2026-08-10 ~ 2026-08-18 (2026-08-24 스트리밍 계열 추가 발견) |
| **직접 수정한 저장소** | `backend-api-main`, `skix-security`, `skix-openapi`, `skix-medcare` |
| **요청서를 전달한 대상** | 없음. 협력사 위임용 태스크 문서는 작성했으나 **clone 권한 게이트(§8)** 때문에 전달 보류 상태다 |
| **작성자** | Data플랫폼팀 백엔드 담당 |
| **기준일** | 2026-09-22 (Git 원격 브랜치 실측 기준) |
| **인수자가 첫날 할 일** | §10-1의 1번(운영계 EC2가 아직 구식 프로필로 도는지 확인)과 2번(개발계·검증계 SSM 파라미터 실재 확인). 둘 다 회사 AWS VDI에서만 가능하다 |

---

## 0. 세 줄 요약

1. 저장소에 **AWS 액세스 키·운영 DB 비밀번호·개인키 파일 14개·외부 API 키 20여 종이 평문으로 커밋**되어 있었다. `backend-api-main`의 런타임 시크릿을 **AWS SSM Parameter Store로 전부 옮기고**, 설정 파일에는 `${플레이스홀더}`만 남겼다. 이 변경은 개발계·검증계 브랜치에 반영됐고 **운영계 브랜치에는 없다.**
2. 이관 실행은 로컬 맥에서 AWS에 닿을 수 없어 **암호화 번들을 만들어 회사 VDI의 CloudShell에서 푸는 방식**으로 설계했다. 스크립트 5종과 환경별 암호화 번들 3개가 남아 있고, 전문은 §부록 B에 옮겨 적었다.
3. **코드에서 값을 빼냈을 뿐 값 자체는 그대로다.** git 이력에 옛 값이 남아 있고 로테이션을 하지 않았다. 그래서 **협력사에 저장소 clone 권한을 줄 수 없고**, 준비해 둔 협력사 태스크(버그 7건 등)가 이 게이트에 막혀 있다.

---

## 1. 배경

### 1-1. 용어

| 용어 | 뜻 |
|---|---|
| **SSM Parameter Store** | AWS Systems Manager의 설정값 저장소. 경로 형태(`/서비스/환경/키`)로 값을 넣고 꺼낸다. |
| **SecureString** | Parameter Store의 값 타입 중 하나. KMS로 암호화되어 저장되며, 읽으려면 `ssm:GetParameter*` 외에 `kms:Decrypt` 권한이 추가로 필요하다. |
| **`spring.config.import`** | Spring Boot 설정. `aws-parameterstore:/경로/`를 쓰면 기동 시 그 경로 아래 파라미터를 전부 읽어 설정값으로 얹는다. |
| **인스턴스 프로파일(Instance Profile)** | EC2에 붙이는 IAM 역할. 서버가 액세스 키 없이 AWS를 호출할 수 있게 해 준다. `backend-api-main`은 EC2에서 돌기 때문에 ECS Task Role 대신 이걸 쓴다. |
| **AssumeRole (역할 전환)** | 공유 계정에 로그인한 뒤 환경별 계정의 역할로 갈아타는 방식. 우리 회사 AWS는 SSO를 쓰지 않고 이 구조다(§1-4). |
| **프로필(profile)** | Spring의 환경 구분. 이 작업 전에는 `local`/`vdi`/`stag`/`prod`였고, 작업 후 `local`/`dev`/`stg`/`prd`로 통일했다. |
| **`AKIA…`** | AWS 장기 액세스 키 ID의 접두사. 저장소에서 평문 키를 찾을 때 이 문자열로 grep한다. |
| **CloudShell** | AWS 콘솔 안에서 바로 열리는 셸. VDI 브라우저에서 AWS CLI·python을 쓸 수 있는 유일한 경로였다. |
| **fail-fast / fail-closed** | 설정이 없으면 조용히 기본값으로 도는 대신 **기동을 실패시키는** 설계. 이 작업 전반의 기본 방침이다. |
| **V번호** | 최초 전수 조사 때 취약점에 붙인 일련번호(V1~V13, 이후 V14 추가). **원본 백로그 문서는 로컬에 남아 있지 않아** 번호와 내용의 대응은 일부만 복원된다(§3 머리말). |

### 1-2. 문제

`backend-api-main`은 표준이 생기기 전에 만들어진 레거시 서비스이고, 실행 환경도 ECS가 아니라 **EC2**다. 설정이 이렇게 되어 있었다.

- 환경별 설정이 `src/main/resources-{local,stag,prod,vdi}/application.yml` 4벌로 나뉘어 있고, **각 파일에 실제 값이 들어 있었다.** DB 계정·비밀번호, Cognito 클라이언트 시크릿, NICE PASS 시크릿, NHN SMS 키, OpenWeather·Google Geocoding 키, 얼굴인식 AES 키/IV, 관리자 JWT HMAC 키 등이다.
- `build.gradle`이 `sourceSets { resources { srcDirs "src/main/resources-${profile}" } }`로 빌드 시점에 디렉터리를 고르는 구조라, **환경별 시크릿이 jar 산출물에 그대로 구워졌다.** 같은 jar을 검증계→운영계로 승격할 수 없다.
- **개인키 파일 14개가 저장소에 추적되고 있었다** — Apple DeviceCheck `.p8` 1개, AWS IoT 디바이스 인증서/개인키 3쌍, CloudFront 서명 키쌍 3쌍, Firebase Admin SDK 서비스 계정 JSON 1개. (운영계 브랜치에는 지금도 그대로 있다. §2-1)
- 자바 소스에도 값이 박혀 있었다. `CryptoUtil`의 AES·PBE 개인키 상수, `SmsService`의 NHN appKey 리터럴 등이다.
- 코드 곳곳의 `local` 프로필 분기가 `StaticCredentialsProvider`로 **AKIA 장기 키를 직접 들고** AWS를 호출했다.

여기에 조사 중 세 가지가 더 드러났다.

1. **값 해시를 대조해 보니 `local`·`stag`·`prod`의 시크릿 다수가 같은 값이었다.** OpenWeather 키 2종, Google Geocoding 키, NICE PASS client-secret·site-password, `app.integrity-key`, `app.ble.security-key-sub`, 얼굴인식 AES 키·IV, MyHealthCare API 키(local=prod, stag만 상이). 즉 **로컬 개발용이라고 부르던 파일에 운영에 유효한 시크릿이 들어 있었다.** NHN SMS 키만 환경별로 달랐다. 최초 백로그의 "환경별 값은 다르다"는 서술은 틀렸고 이때 정정했다.
2. **`jwt.token.secret`(관리자 JWT HMAC)이 4개 프로필 전부 같은 리터럴이었고, 같은 값이 `skix-security`의 로컬 설정 파일에도 있었다.** `backend-api-main`이 발급자, `skix-security`가 검증자인 HMAC 쌍이다. 즉 **저장소를 읽을 수 있으면 운영 관리자 토큰을 위조할 수 있는 상태**였다. 파일 분리·gitignore로는 해결되지 않고 **값 로테이션이 있어야 닫힌다.**
3. 저장소에 커밋된 값들은 **git 이력에 그대로 남는다.** 현재 트리에서 지워도 과거 커밋을 체크아웃하면 나온다.

### 1-3. 목표

| # | 목표 | 달성 여부 |
|---|---|---|
| 1 | 저장소 현재 트리에서 런타임 시크릿 값을 전부 제거 | 개발계·검증계 달성, **운영계 미달성** |
| 2 | 시크릿 원본을 SSM Parameter Store(SecureString)로 일원화, 경로는 `/backend-api-main/<env>/` | 코드 준비 완료, **파라미터 실재는 미확인(§2-2)** |
| 3 | 환경별 jar이 아니라 **동일 jar + 실행 프로필**로 전환 | 달성(`build.gradle` sourceSets 제거) |
| 4 | 파라미터가 없으면 **기동 실패**(조용한 기본값 금지) | 달성 |
| 5 | 로컬 개발이 막히지 않게 할 것 | 달성(`config/local-secrets.yml` 방식) |
| 6 | 값 로테이션·git 이력 정리 | **미착수** |

### 1-4. 이 작업의 전제 — 로컬에서는 AWS를 쓸 수 없다

회사 AWS는 **SSO(Identity Center)를 쓰지 않는다.** 공유 계정의 IAM 사용자로 콘솔에 로그인한 뒤 환경 계정으로 역할 전환(AssumeRole)하는 허브-스포크 구조다.

| 계정 | 번호 |
|---|---|
| 공유(로그인) 계정 | `010382790647` |
| 개발계 | `010928196421` |
| 검증계 | `087432099373` |
| 운영계 | `779846811758` |

**콘솔 로그인은 회사 AWS VDI에서만 된다.** 로컬 맥에서는 같은 자격증명으로도 로그인이 실패한다(2026-08-11 실측, 소스 IP 조건 제한으로 추정). 로컬 맥에서 AWS API 엔드포인트와 IoT 8883 포트에는 닿지만 **자격증명을 발급받을 경로가 없다.**

그래서 두 가지가 결정됐다.

- **로컬 개발은 AWS 기능 없이 한다.** 로컬 CLI 프로필(`source_profile` + `role_arn`) 구성은 보류했고, 이 때문에 한때 추가했던 `awssdk:sts` 의존성도 폐기했다(현재 `origin/dev`의 `build.gradle`에 `sts`는 없다 — 실측).
- **AWS를 만져야 하는 단계는 전부 VDI에서** 한다. 이관 스크립트를 "로컬에서 번들을 만들고 VDI CloudShell에서 실행"하는 2단 구조로 짠 이유가 이것이다(§5).

로컬에서 AWS 호출이 어떻게 되는지 실측해 둔 결과다. 기동은 정상이다(AWS SDK v2는 요청 시점에 자격증명을 해석한다). IoT MQTT는 기기 인증서 인증이라 동작한다(다만 `local` 프로필은 토픽 접두사가 무작위 UUID라 실기기 메시지는 안 들어온다 — 원래 설계다). S3·Cognito·IoT Job은 호출 시점에 `Unable to load credentials`로 실패한다. 전환 전에도 폐기된 키로 403이 났으므로 **할 수 있는 일의 범위는 그대로다.**

---

## 2. 현재 상태 (2026-09-22 Git 실측)

### 2-1. 저장소별 반영

`git merge-base --is-ancestor <커밋> origin/<브랜치>`로 전부 실측했다.

| 저장소 | 내용 | dev | stg | main(운영) |
|---|---|---|---|---|
| `backend-api-main` | 정적 AWS 자격증명 제거 `6b2a4526` | 반영 | 반영 | **미반영** |
| `backend-api-main` | `.env` gitignore `88cd74f4` | 반영 | 반영 | **미반영** |
| `backend-api-main` | SSM 스타터 추가 `19e3508e` | 반영 | 반영 | **미반영** |
| `backend-api-main` | **SSM 런타임 로딩 전환 `b8c0f28e`** (프로필 yml 4벌·개인키 14파일 삭제) | 반영 | 반영 | **미반영** |
| `backend-api-main` | 프로필 `vdi/stag/prod` → `dev/stg/prd` `27555128` | 반영 | 반영 | **미반영** |
| `backend-api-main` | 로컬 시크릿 파일 주입 `3a63b6fe` | 반영 | 반영 | **미반영** |
| `skix-security` | 로컬 프로필 시크릿 분리 `18e69dd` | 반영 | 반영 | **반영** |
| `skix-security` | `.env` gitignore `3fd8693` | 반영 | 반영 | **반영** |
| `skix-openapi` | 로컬 RSA 키·DB 비밀번호 분리 `8163537` | 반영 | 반영 | **반영** |
| `skix-openapi` | `.env` gitignore `a574f97` | 반영 | 반영 | **반영** |
| `skix-medcare` | 로컬 프로필 `.env` 외부화 `25f740b` | 반영 | 반영 | **반영** |
| `skix-medcare` | `.env` import를 non-optional로 `4c7af66` | 반영 | 반영 | **반영** |

작업 브랜치 `fix/remove-static-aws-credentials`와 `fix/local-secret-file`은 **둘 다 원격에 push되어 있고 dev·stg에 머지됐다.** (작업 당시 기록에는 "전부 미push, 한 브랜치에 쌓아 마지막에 한 번에 push"로 남아 있으나, 실측 결과 이미 push·머지까지 끝났다.)

**`backend-api-main` 운영계 브랜치의 현재 모습 — 실측.**

- `src/main/resources-{local,stag,prod,vdi}/application.yml` 4벌이 **값과 함께 그대로 있다.**
- `AKIA` 문자열이 `src/main/resources-local/application.yml`, `src/main/resources-prod/application.yml`, 그리고 API 예제 파일(`.bru`) 4개에 **남아 있다.** (개발계·검증계 트리에는 `AKIA`가 0건이다.)
- 개인키 파일 **14개가 추적 중이다**: `DeviceCheck_Key/AuthKey_*.p8` 1, `IoTKeyFile`·`ProdIoTKeyFile`·`StagIoTKeyFile`의 인증서/개인키 6, `cloudfront-{dev,stg,prod}-key`의 private/public 6, `firebase/skMagicFirebaseAdminSdk.json` 1.
- `origin/main`은 `origin/stg`보다 **371커밋 뒤처져 있다**(`origin/dev` 기준 346커밋). `origin/dev`와 `origin/stg`는 차이가 0이다.

### 2-2. 인프라·배포 (로컬에서 확인 불가 — 확인 방법 포함)

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| 개발계·검증계 SSM 파라미터 실재 | **사실상 등록된 것으로 본다(강한 정황, 직접 확인은 못 함)** | 근거: `b8c0f28e`(8/13)가 `dev`·`stg`에 머지된 뒤에도 두 환경에 이 저장소의 다른 변경이 배포·검증된 기록이 있다(개발계는 9/8 OASYS 직접 구독 전체 흐름 검증, 검증계는 9/2 탈퇴 API 실패 응답 변경 배포). 파라미터가 없으면 `spring.config.import`가 기동을 실패시키므로 떠 있다는 것 자체가 등록의 방증이다. **확정하려면** VDI CloudShell에서 `aws ssm get-parameters-by-path --path /backend-api-main/dev/ --recursive --query 'Parameters[].Name'` |
| 운영계 SSM 파라미터 실재 | **미확인** | 위 명령에서 경로만 `/backend-api-main/prd/`로. 운영계 암호화 번들은 2026-08-12에 만들어 뒀다(§5) |
| EC2 인스턴스 프로파일의 `ssm:GetParameter*`·`kms:Decrypt` 권한 | 개발계·검증계는 **부여된 것으로 본다**(위와 같은 근거), **운영계는 미확인** | VDI 콘솔 IAM에서 해당 EC2의 역할 정책 확인. §부록 D에 필요한 최소 권한 형태를 적어 뒀다 |
| 운영계 EC2가 어떤 프로필로 기동 중인지 | **미확인.** 운영계 코드가 구버전이므로 아직 `-Pprofile=prod` 빌드 방식일 가능성이 높다 | VDI에서 운영 EC2 접속 후 실행 중 자바 프로세스 인자 확인 |
| 배포 파이프라인이 새 빌드 방식을 반영했는지 | **미확인.** 개발계·검증계는 돌고 있으니 반영된 것으로 보이나 파이프라인 정의를 못 봤다 | §9-1의 함정 항목 참고 |
| 키 로테이션(값 교체) | **전혀 안 함** | — |
| git 이력 재작성 | **안 함.** `backend-api-main`에서 `AKIA` 리터럴을 건드린 커밋이 이력에 14건 남아 있다(값은 확인하지 않고 존재만 확인) | — |
| AKIA 장기 키 유효성 | **이미 오래전 폐기 확인**(2026-08-11). 최초 백로그의 "유효한 장기 키" 평가는 과대였다. 로컬 프로필로 AWS를 써 온 사람이 없었다는 방증이라 이번 전환으로 깨지는 워크플로가 없다 | — |

### 2-3. 아직 손대지 않은 저장소 (실측)

| 저장소 | 남아 있는 것 |
|---|---|
| `skix-streaming` | `src/main/resources-{local,vdi,stag,prod}/application.yml` 4벌이 값과 함께 추적 중. `resources-local`·`resources-vdi`에 `AKIA` 문자열 잔존. IoT 인증서/개인키 4개, CloudFront 키쌍 4개 추적 중. `build_vdi.gradle`에 사내 Nexus 저장소 계정이 평문(사내망 빌드용 파일이며 환경 프로필이 아니다) |
| `skix-streaming-signal` | `signaling/.env.dev`·`.env.stg`·`.env.prd`가 **추적되고 있다.** `.gitignore`가 `.env`와 `.env.*.local`만 막고 환경별 파일은 놓친다. README상 필수 항목이 `SIGNING_SECRET`(HMAC)·`ADMIN_API_KEY`·`REDIS_CLUSTER_NODES`라 **관제용 API 키와 서명 키가 저장소에 있는 상태로 보인다**(값은 열어 보지 않았다). 저장소 루트의 `turnserver.conf`에도 고정 인증 시크릿과 계정이 평문이다 |
| `backend-scheduler-main` | `src/main/resources-{local,vdi,stag,prod}/application.yml` 4벌에 DB 비밀번호가 평문. `AKIA`는 0건 |
| `benjamin-lambda` | 설정 파일에 평문 시크릿 없음(실측). 조치 대상 아님 |

`skix-streaming-signal`은 조치 난도가 특히 높다. **저장소 디렉터리 자체가 배포 산출물**이고(`signaling/node_modules.tar.gz`를 커밋해 EC2로 옮긴다 — npm 레지스트리 미도달 대응), EC2에 `git`이 없어 산출물 복사 방식으로 배포한다. `.env`는 서버에서 손으로 관리된다. `turnserver.conf`의 coturn은 **실제 EC2에서 돌고 있지 않으므로**(프로세스·3478 포트 없음) 로테이션보다 **파일 삭제 검토가 먼저**다.

---

## 3. 취약점 항목표

**먼저 알아야 할 것.** 최초 전수 조사 산출물인 백로그 문서(V1~V13)는 **로컬에 남아 있지 않다.** 그래서 번호와 내용의 대응은 다른 기록으로 확인되는 것만 채웠고, 확인되지 않는 번호는 비워 뒀다. 아래 표는 **번호가 아니라 실제로 확인·조치된 항목** 기준으로 정리한 것이며, 각 행의 근거는 커밋 또는 이 문서를 쓰며 한 실측이다.

| 번호 | 항목 | 무엇이 문제였나 | 상태 | 근거 |
|---|---|---|---|---|
| **V1·V7** | 프로필 yml의 런타임 시크릿 평문 → SSM 이관 | DB 계정·외부 API 키 등이 4벌의 yml에 값째로 있었고 jar에 구워졌다 | **개발계·검증계 반영, 운영계 미반영.** 파라미터 등록은 §2-2 | `19e3508e`, `b8c0f28e`, `54e1013b` |
| **V5** | `local` 프로필 정적 AWS 자격증명 | 10개 파일이 `StaticCredentialsProvider`로 AKIA 키를 직접 사용. yml 4벌에 AKIA 7쌍 + 운영 yml 주석 속 실키 | **조치 완료**(개발계·검증계). 콘솔 조치는 키가 이미 폐기라 불필요 | `6b2a4526`. 문서상 6곳이었으나 실측 10개 파일이었다 |
| **V6** | 환경 간 시크릿 값 중복 | `local`/`stag`/`prod`의 다수 값이 동일 — 로컬 파일에 운영 유효 시크릿이 있었다 | **식별만 완료. 로테이션 미착수** | §1-2의 값 해시 대조 |
| **V10** | pre-commit 시크릿 스캐닝 도입 | 재발 방지 장치 없음 | **진행하지 않기로 결정**(2026-08-11) | 아래 주석 참고 |
| **V11** | `.env` 계열 gitignore 규칙 | 로컬 시크릿 파일이 실수로 커밋될 여지 | **3개 저장소 조치 완료, 전 브랜치 반영** | `88cd74f4`, `3fd8693`, `a574f97` |
| **V14** | Apple DeviceCheck 개인키 커밋 | `src/main/resources/DeviceCheck_Key/AuthKey_*.p8`. 최초 백로그에 없던 항목이라 조사 중 심각 등급으로 추가 | **개발계·검증계 트리에서 제거. 운영계·git 이력에 잔존** | `b8c0f28e` |
| (번호 미상) | 저장소 추적 개인키 파일 14개 | Apple p8 1, IoT 인증서/개인키 6, CloudFront 키쌍 6, Firebase Admin JSON 1 | **개발계·검증계 트리에서 제거. 운영계에 14개 전부 잔존**(실측) | `b8c0f28e`, `git ls-tree origin/main` |
| (번호 미상) | 관리자 JWT HMAC 키 공유 | `jwt.token.secret`이 4개 프로필 동일 + `skix-security` 로컬 설정에도 같은 값. 저장소를 읽으면 운영 관리자 토큰 위조 가능 | **SSM으로 옮겼고 로컬 파일에서 분리했으나 값은 그대로.** 실질 해소는 로테이션 | `b8c0f28e`, `18e69dd` |
| (번호 미상) | 자바 소스 하드코딩 | `CryptoUtil`의 AES·PBE 개인키 상수, `SmsService`의 NHN appKey 리터럴, 테스트·예제(`.bru`) 파일의 자격증명 흔적 | **조치 완료**(개발계·검증계) | `b8c0f28e`, `54e1013b`, `846a725b` |
| (번호 미상) | 민감정보 런타임 로그 | 요청 본문·외부 응답 본문·개인정보가 로그로 나가던 지점 다수 | **조치 완료**(개발계·검증계) | `bde60691`(64파일), `78225fcc`(70파일) |
| (번호 미상) | 운영 코드에 남은 테스트 엔드포인트·SSH 예제 | `api/test` 패키지, SSH 접속 예제 | **제거 완료** | `15d8b9bd` |
| (번호 미상) | git 이력에 남은 값 | 현재 트리에서 지워도 과거 커밋에 남는다 | **미착수** | `git log -S'AKIA' --all` 14건 |
| (번호 미상) | 스트리밍 계열 3종 | §2-3 | **미착수**(2026-08-24 발견) | §2-3 실측 |
| (번호 미상) | `backend-scheduler-main` 프로필 yml | DB 비밀번호 평문 4벌 | **미착수** | §2-3 실측 |

**V10에 대해.** 2026-08-11에 "진행하지 않는다"로 결정됐고 **결정 사유는 기록에 남아 있지 않다.** 한때 "다음 순서는 V10"이라는 요약이 돌았으나 같은 날 결정이 뒤집혔고, 실제로 그 뒤에 진행된 것은 SSM 이관이다. 인수자가 재도입을 검토한다면 이 문서의 의견은 이렇다 — 이력과 운영 값이 그대로인 지금 스캐너를 붙이면 이미 있는 값이 전부 걸려 곧바로 우회 옵션이 일상화되기 쉬우므로, **로테이션·이력 정리 이후**에 붙이는 편이 낫다.

---

## 4. `backend-api-main` SSM 이관 — 무엇을 바꿨나

### 4-1. 전후 구조

```
[이관 전]
  ./gradlew bootJar -Pprofile=prod
        └ build.gradle sourceSets 가 src/main/resources-prod 를 리소스로 선택
              └ application.yml (DB 비밀번호·API 키 실값)  ──> jar 안에 구워짐
              └ 개인키 파일 14개                            ──> jar 안에 구워짐
  java -jar air-bot-api.jar

[이관 후]
  ./gradlew clean bootJar                 ← 환경 구분 없음. 산출물 1개
        └ src/main/resources/application-{local,dev,stg,prd}.yml  (값은 ${플레이스홀더})
  java -jar build/libs/air-bot-api.jar --spring.profiles.active=prd
        └ application-prd.yml 의 spring.config.import:
               aws-parameterstore:/backend-api-main/prd/
        └ EC2 인스턴스 프로파일로 SSM 호출 → SecureString 복호화 → 설정값 주입
        └ 파라미터가 없으면 기동 실패
```

### 4-2. 프로필 재편

| 이전 디렉터리 | 이후 파일 | SSM 경로 |
|---|---|---|
| `src/main/resources-local/` | `src/main/resources/application-local.yml` | (없음 — 로컬 파일 주입) |
| `src/main/resources-vdi/` | `src/main/resources/application-dev.yml` | `/backend-api-main/dev/` |
| `src/main/resources-stag/` | `src/main/resources/application-stg.yml` | `/backend-api-main/stg/` |
| `src/main/resources-prod/` | `src/main/resources/application-prd.yml` | `/backend-api-main/prd/` |

**`vdi`라는 이름이 개발계였다.** 회사 VDI에서 쓰던 프로필이 그대로 개발계 배포 프로필이 된 것이라 이름과 실체가 어긋나 있었다. 이번에 `dev`로 바로잡았고, 코드에서 프로필 문자열을 비교하던 지점(`IoTConfiguration`, `RedisConfiguration`, `OasysService`, `SystemConstants` 등 11개 파일)도 함께 고쳤다. **프로필 이름을 문자열로 비교하는 코드가 아직 남아 있으니 프로필을 또 바꾸려면 `git grep`으로 전수 확인이 필요하다.**

`build.gradle`에서는 `ext.profile`과 `sourceSets` 블록을 통째로 지웠다. 이제 `-Pprofile=...`은 **아무 효과가 없다**(오류도 나지 않는다). §9-1의 함정 1번이 이것이다.

### 4-3. SSM 파라미터 체계

경로는 `/backend-api-main/<env>/<설정키>`이고 **전부 SecureString / Standard 티어**다. 값 크기 상한 4096바이트를 넘으면 등록 스크립트가 거절한다.

등록되는 파라미터는 **31개**다. 전체 목록은 §부록 D에 있다. 분류만 보면 이렇다.

| 분류 | 개수 | 예 |
|---|---|---|
| yml에서 그대로 옮긴 시크릿 | 22 | `aws.cognito.client.secret`, `jwt.token.secret`, `nice.pass.client-secret`, `openweather.api.key3_0` … |
| DB 계정 별칭 | 2 | `database.username`, `database.password` |
| 파일에서 읽어 넣는 것 | 5 | `firebase.service-account-json`, `app.apple.private-key`, `aws.iot.certificate`, `aws.iot.private-key`, `aws.cloudfront.private-key-pem` |
| 자바 소스에서 뽑아낸 것 | 2 | `crypto.aes-key`, `crypto.legacy-pbe-key` |

**DB 계정을 `database.username`/`password` 별칭으로 합친 이유.** 원본 yml은 읽기/쓰기 데이터소스에 같은 계정을 두 번 적어 두었다. 파라미터를 4개로 두면 한쪽만 바꾸는 사고가 나므로 하나로 묶고, yml의 네 자리를 전부 `${database.username}` / `${database.password}`로 참조하게 했다. **이관 스크립트는 읽기/쓰기 계정이 다르면 별칭을 쓸 수 없다고 판단해 중단한다.** 앞으로 읽기 전용 계정을 분리한다면 이 별칭부터 풀어야 한다.

**폐기한 설정(SSM에 넣지 않고 yml에서도 삭제).** 15개 키 + `aws.s3.*.accessKey`/`secretKey` 패턴 전체 + `aws.s3.ai.*` 하위 전부다. 정적 자격증명이거나(`aws.iot.awsAccessKeyId`, `aws.cognito.credentials.*`) 참조 코드가 사라진 죽은 설정이다(`aws.s3.map/recommend/ai`). 파일 경로 설정(`app.apple.key-path`, `aws.iot.certificate-locations` 등)은 파일을 안 읽게 됐으므로 함께 지웠다. 전체 목록은 §부록 D.

**남아 있는 껄끄러운 점 하나.** `app.ble.masking-key`, `masking-key-sub`, `masking-secret-key` 3개는 조사 당시 **사용처가 없는 죽은 설정**으로 분류했는데, 실제로는 삭제되지 않고 SSM으로 함께 옮겨졌다(등록 스크립트의 대상 목록에 들어 있다). 개발계 코드에서 이 세 키를 읽는 곳은 없다(실측 — `User.java`의 `maskingKey` 필드는 DB 컬럼이라 무관하다). 급하지 않지만 **파라미터 3개를 정리하면 SSM이 그만큼 깨끗해진다**(§10-3).

### 4-4. 코드에서 바뀐 지점

| 대상 | 이전 | 이후 |
|---|---|---|
| `build.gradle` | `sourceSets`로 환경별 리소스 디렉터리 선택 | 제거. `spring-cloud-aws-starter-parameter-store` 추가 (버전은 이미 있던 `spring-cloud-aws-dependencies:3.1.1` BOM이 관리) |
| `IoTConfiguration` | `aws.iot.certificate-locations` 패턴으로 **파일**을 읽음 | `aws.iot.certificate` / `private-key` **문자열 프로퍼티**를 받음 |
| `CloudFrontConfig`, `CfUrlUtils`, `CloudFrontService`, `CfUrlController` | CloudFront 개인키를 파일 경로로 읽음 | `aws.cloudfront.private-key-pem` 문자열 |
| `FCMConfig` | Firebase 서비스 계정 JSON 파일을 읽음 | `firebase.service-account-json` 문자열 |
| `AppIntegrityService` | Apple `.p8` 파일 경로 | `app.apple.private-key` 문자열 |
| `CryptoUtil` | `AES_PRIVATE_KEY`·`PBE_PRIVATE_KEY` static 상수 리터럴 | `@Component` + `@Value("${crypto.aes-key}")` / `${crypto.legacy-pbe-key}` 세터 주입 |
| `SmsService` | `sendAuthSms`가 NHN appKey를 **소스에 박아** 호출 | `smsUrl` + `smsAppKey` 설정값 사용. 실패 시 응답 본문 대신 HTTP 상태만 예외에 싣는다 |
| 정적 자격증명 10개 파일 | `local` 분기에서 `StaticCredentialsProvider`(AKIA) | `DefaultCredentialsProvider`. **운영 분기(`InstanceProfileCredentialsProvider`)는 무변경** |

정적 자격증명을 바꾼 10개 파일: `RequestControlRobotService`, `FirmwareService`, `IotJobService`, `JobApprovalService`, `ProductDeviceService`, `FileFirmwareService`, `AWSCognitoConfiguration`, `AWSS3Configuration`, `BeanMapper`, `HttpPublishTransport`.

### 4-5. 로컬 개발 방법 (인수자가 처음 겪을 부분)

세 저장소 모두 같은 형태다. **저장소를 clone하면 로컬 기동이 바로 실패한다. 정상이다.**

```bash
# backend-api-main / skix-security / skix-openapi 공통
cp config/local-secrets.yml.example config/local-secrets.yml
# 편집기로 값 채우기
./gradlew clean bootJar
java -Dspring.profiles.active=local -jar build/libs/air-bot-api.jar
```

`skix-medcare`만 `.env` 파일 방식이다(`.env.example` 복사).

- `config/local-secrets.yml`은 gitignore 대상이고 `.example`만 커밋된다.
- `spring.config.import`에 **`optional:`을 붙이지 않았다.** 파일이 없으면 `ConfigDataResourceNotFoundException`으로 **파일 경로를 찍으며 즉시 죽는다.** 키 하나가 비어 엉뚱한 지점에서 죽는 것보다 새로 오는 사람에게 친절하고, 이 프로젝트 전반의 fail-closed 방침과도 맞다.
- 값을 어디서 구하나: 개발계 값이 꼭 필요한 항목은 VDI CloudShell에서 `/backend-api-main/dev/` 경로를 읽어 온다. **`.example` 파일에는 값을 적지 않는다.** JWT 키쌍·KVS lease 시크릿처럼 로컬에서 새로 만들어도 되는 것은 `.example`의 주석에 생성 명령(`openssl genpkey …`, `openssl rand -base64 32`)을 적어 뒀다.
- `backend-api-main` 로컬에서 필요한 키는 31개 파라미터와 같은 이름이다(§부록 D).
- `skix-security` 로컬에서 필요한 시크릿은 6종이다: `app.oasys-api.username`/`password`(개발계 OASYS 서버 계정이다 — 로컬 전용이 아니다), `app.kvs.lease-secret`, `app.jwt.private-key`/`public-key`, `aws.iot.certificate`/`private-key`, `admin.jwt.secret`. `spring.datasource.password`는 로컬 docker 전용이라 커밋된 채로 뒀다.

---

## 5. 이관 실행 절차

### 5-1. 왜 암호화 번들인가

값을 읽으려면 저장소 파일이 필요하고, 값을 쓰려면 AWS가 필요한데, **그 둘이 같은 장소에 없다.** 저장소는 로컬 맥에 있고 AWS 콘솔은 VDI에서만 열린다(§1-4). VDI에 저장소를 통째로 옮기면 평문 시크릿을 한 번 더 복제하는 셈이다.

그래서 이렇게 짰다.

```
[로컬 맥]
  build_encrypted_bundle.sh --env prd
      └ git archive 로 필요한 파일만 꺼냄 (작업 트리를 건드리지 않는다)
      └ 등록 스크립트 + 입력 파일 + 환경 표식을 tar
      └ openssl aes-256-cbc / pbkdf2 200000회 로 암호화
      → backend-api-ssm-prd.tar.gz.enc  (이 파일 하나만 옮긴다)

[VDI CloudShell]
  cloudshell_run_encrypted_bundle.sh --bundle ...enc --env prd
      └ 임시 디렉터리(700)에만 복호화, 종료 시 trap 으로 삭제
      └ 번들 안의 환경 표식과 --env 가 다르면 중단
      └ execute_bundle.sh → import_parameters.py
             └ 기본이 dry-run. --apply 를 붙여야 실제로 쓴다
             └ sts:GetCallerIdentity 로 계정번호가 맞는지 먼저 확인
             └ 값은 stdout 에도, CLI 인자에도 절대 실리지 않는다 (boto3 로 직접 전송)
```

설계에서 지킨 것.

- **값을 화면에 찍지 않는다.** dry-run은 파라미터 **이름만** 출력한다. `--apply`도 `OK <이름>` 만 찍는다. CloudShell 세션 로그에 값이 남지 않게 하려는 것이다.
- **AWS CLI 인자로 값을 넘기지 않는다.** boto3로 직접 보낸다. 프로세스 목록에 값이 노출되는 경로를 없앴다.
- **계정을 잘못 고르면 시작조차 못 한다.** 환경별 계정번호를 스크립트가 알고 있고 `sts:GetCallerIdentity` 결과와 다르면 중단한다. 역할 전환을 깜빡한 채 공유 계정에서 운영 파라미터를 만드는 사고를 막는다.
- **덮어쓰기는 명시해야 한다.** 기본은 `Overwrite=False`라 이미 있는 파라미터는 `SKIP`으로 지나간다. 갱신하려면 `--overwrite`를 `--apply`와 함께 준다.
- **부분 실패는 부분 실패로 보고한다.** 중간에 끊기면 `created/updated/skipped` 개수를 남기고 멈춘다. 재실행하면 이미 만든 것은 SKIP이므로 이어서 진행된다.

### 5-2. 현재 남아 있는 산출물

작성자 로컬에만 있던 파일이라 인수자는 부록 B의 전문을 같은 파일명으로 저장해 쓴다. 이전에 만든 암호화 번들(`.tar.gz.enc`)은 암호구문이 기록되지 않아 재사용할 수 없으므로, 필요하면 §5-4 순서로 새로 만든다.

| 파일 | 역할 |
|---|---|
| `build_encrypted_bundle.sh` | 로컬에서 암호화 번들 생성 |
| `cloudshell_run_encrypted_bundle.sh` | VDI CloudShell에서 번들 복호화·실행 |
| `execute_bundle.sh` | 번들 내부 진입점 (사람이 직접 부르지 않는다) |
| `import_parameters.py` | 실제 등록 로직. 분류 목록도 여기 있다 |
| `render_sanitized_profiles.rb` | 옛 yml에서 시크릿을 걷어내고 새 프로필 yml을 생성 (일회성. 이미 적용됨) |
| `run_migration.sh` | 로컬에 AWS 자격증명이 있을 때 쓰는 단축 경로. **로컬에서는 쓸 수 없다**(§1-4) |
| `backend-api-ssm-{dev,stg,prd}.tar.gz.enc` | 2026-08-12에 만든 환경별 암호화 번들 |

전문은 §부록 B에 옮겨 적었다.

**암호화 번들의 암호구문(passphrase)은 어디에도 기록되어 있지 않다.** 모르면 번들을 버리고 `build_encrypted_bundle.sh`로 새로 만들면 된다. 다만 아래 주의가 있다.

### 5-3. ⚠ 지금 다시 만들 때 반드시 바꿔야 하는 것

`build_encrypted_bundle.sh`와 `run_migration.sh`는 **기본 소스 ref가 `dev`** 다. 번들을 만든 2026-08-12에는 `dev`에 옛 파일이 아직 있었지만, **`b8c0f28e`(8/13)가 `resources-{local,stag,prod,vdi}`와 개인키 14개를 지웠다.** 지금 기본값 그대로 실행하면 `git archive`가 파일을 못 찾고 실패한다.

**옛 파일이 그대로 남아 있는 ref는 `origin/main`이다**(실측). 따라서:

```bash
./build_encrypted_bundle.sh --env prd --source-ref origin/main
# 또는 삭제 직전 커밋
./build_encrypted_bundle.sh --env prd --source-ref b8c0f28e^
```

단, **`origin/main`은 검증계보다 371커밋 뒤처진 트리**라 그동안 운영 값이 바뀌었다면 반영되어 있지 않다. 운영계 파라미터를 새로 만들 때는 **번들 값과 현재 운영 EC2가 실제로 쓰는 값이 같은지** 먼저 대조해야 한다(§10-2 게이트 1번).

### 5-4. 실행 순서 (운영계 예시)

```bash
# ── 로컬 맥 ──────────────────────────────────────────────
cd <부록 B 스크립트를 저장한 작업 폴더>
./build_encrypted_bundle.sh --env prd --source-ref origin/main
#   → 새 암호구문을 입력한다. 출력: backend-api-ssm-prd.tar.gz.enc

# 이 .enc 파일과 cloudshell_run_encrypted_bundle.sh 둘만 VDI 로 옮긴다.
# 저장소나 평문 파일은 옮기지 않는다.

# ── VDI CloudShell (운영계 계정으로 역할 전환한 상태) ────
python3 -m pip install --user PyYAML      # CloudShell 에 없을 수 있다

chmod +x cloudshell_run_encrypted_bundle.sh
./cloudshell_run_encrypted_bundle.sh --bundle backend-api-ssm-prd.tar.gz.enc --env prd
#   → dry-run. 만들 파라미터 31개의 "이름"과 폐기 목록이 나온다. 값은 안 나온다.
#   → 계정번호가 779846811758 로 찍히는지 확인한다.

./cloudshell_run_encrypted_bundle.sh --bundle backend-api-ssm-prd.tar.gz.enc --env prd --apply
#   → 실제 등록. 이미 있는 파라미터는 SKIP.
#   → 값을 갱신해야 하면 --apply --overwrite

# ── 등록 확인 (이름만) ───────────────────────────────────
aws ssm get-parameters-by-path --path /backend-api-main/prd/ --recursive \
  --query 'Parameters[].Name' --output text | tr '\t' '\n' | sort

# ── 번들 파기 ────────────────────────────────────────────
shred -u backend-api-ssm-prd.tar.gz.enc    # CloudShell 홈은 세션 간 유지된다
```

dry-run 출력은 이렇게 읽는다.

| 줄 | 의미 |
|---|---|
| `Mode: dry-run` / `apply` | `--apply` 없이 돌았는지 |
| `Expected AWS account:` | 이 환경에 기대되는 계정번호. `--apply` 시 실제 계정과 다르면 중단 |
| `Parameters to create: 31` | 31이 아니면 입력 yml이 기대와 다른 것이다. 중단하고 원인부터 본다 |
| `Non-secret properties retained in profile YAML:` | yml에 그대로 남는 비-시크릿 설정 수 |
| `Obsolete/static credential properties discarded:` | 폐기 대상 수 |

---

## 6. 되돌리면 안 되는 설계 결정

| # | 결정 | 바꾸면 생기는 일 |
|---|---|---|
| 1 | **`spring.config.import`에 `optional:`을 붙이지 않는다** (운영 3환경) | optional로 만들면 SSM이 안 읽혀도 앱이 뜬다. 그러면 DB 비밀번호가 빈 값인 채로 기동해 **첫 요청에서야** 죽거나, 더 나쁘게는 기본값으로 엉뚱하게 동작한다. 지금은 파라미터 하나만 없어도 기동이 실패하고 어떤 키가 없는지 로그에 남는다 |
| 2 | **yml에 SSM과 같은 키를 남기지 않는다** | 이 저장소에서는 `spring.config.import`로 들어온 값이 **공통 `application.yml`에 박힌 값에 덮여 조용히 무시된** 실제 사례가 있다. 그 기록이 `application.yml`의 `app.internal.enforce-api-key` 주석에 남아 있다. 우선순위 규칙을 따지기보다 **같은 키를 양쪽에 두지 않는 것**이 유일하게 안전한 규칙이다. 어기면 SSM 값을 바꿨는데 반영이 안 되는, 가장 찾기 어려운 버그가 된다. 같은 이유로 비-시크릿 운영 스위치(`enforce-api-key` 등)는 SSM에 넣지 않고 프로필 yml에 둔다 |
| 3 | **`app.internal.api-key`의 빈 기본값 `${app.myHealthCare.api.api-key:}`를 유지한다** | 콜론 뒤 빈 기본값을 빼면 파라미터 하나 누락이 앱 전체 기동 실패가 된다. 이 키는 medcare 연동 공유 시크릿의 **별칭**이라 SSM 파라미터를 늘리지 않는다. 공통 `application.yml`에 둔 것은 `local` 프로필까지 상속시키기 위해서다 |
| 4 | **`facial.AES_SECRET_KEY`·`facial.AES_IV`는 값을 유지한 채로만 이관한다** | **DB에 저장된 데이터를 푸는 키**다. 로테이션하면 기존 얼굴인식 데이터를 복호화할 수 없다. "옮기는 김에 새로 만들자"는 판단이 데이터 유실로 직결된다 |
| 5 | **`app.integrity-key`는 앱 배포 없이 바꾸지 않는다** | iOS 앱에 내장된 값과 문자열을 비교한다. 서버만 바꾸면 그 앱 버전 전체가 무결성 검사에서 떨어진다 |
| 6 | **`crypto.aes-key`·`crypto.legacy-pbe-key`는 값을 유지한 채로만 이관하고, legacy 쪽도 지우지 않는다** | `EncryptAspect`가 DB 컬럼 암·복호화에 `CryptoUtil`을 쓴다. `decryptAES`는 AES-GCM 형식이면 `aes-key`로, 아니면 옛 PBE 방식(`legacy-pbe-key`)으로 푼다. 즉 legacy 키는 **옛 방식으로 저장된 기존 데이터를 읽는 데 지금도 쓰인다.** 더 위험한 것은 **키가 틀려도 오류가 나지 않는다**는 점이다. AES 복호화 실패와 PBE 복호화 불가 모두 **암호문을 그대로 반환**한다. 값이 잘못 이관되면 기동도 되고 예외도 없이 화면에 암호문이 나온다. `aes-key`는 Base64로 디코딩해 정확히 32바이트여야 하며 등록 스크립트가 이를 검증한다 |
| 7 | **`otp.key`는 로테이션해도 안전하다** | 분 단위 TOTP라 교체 영향이 다음 분에 끝난다. 로테이션 대상 중 가장 먼저 손대기 좋은 항목이라는 뜻이다(4·5번과 대비) |
| 8 | **정적 자격증명 교체는 `local` 분기만 건드렸다. 운영 분기(`InstanceProfileCredentialsProvider`)는 그대로 둔다** | 작업 범위를 "운영 경로 무변경"으로 정한 것이다. 운영 분기를 `DefaultCredentialsProvider`로 "통일"하면 자격증명 해석 순서가 바뀐다. EC2에 환경변수나 `~/.aws`가 있으면 그쪽이 먼저 잡혀 **의도치 않은 권한으로 동작**할 수 있다. 명시적으로 인스턴스 프로파일만 보게 두는 편이 안전하다 |
| 9 | **등록 스크립트는 기본이 dry-run이고 값을 출력하지 않는다** | 편하자고 값을 찍게 고치면 CloudShell 세션 스크롤백과 브라우저 로그에 운영 시크릿이 남는다. 같은 이유로 AWS CLI 인자로 값을 넘기지 않는다 |
| 10 | **`--apply` 전에 계정번호를 대조한다** | 역할 전환을 잊은 채 실행하면 엉뚱한 계정에 운영 시크릿이 생긴다. 지우는 것보다 안 만드는 것이 낫다 |
| 11 | **`config/local-secrets.yml.example`에 실제 값을 채워 두지 않는다** | 예제 파일은 커밋된다. 편의로 개발계 값을 채우는 순간 이 작업이 원위치된다 |
| 12 | **`skix-security`의 `spring.datasource.password: 1234`는 지우지 않아도 된다** | 로컬 docker 전용 값이다. 이것까지 분리하면 새로 오는 사람이 채워야 할 항목만 늘고 얻는 게 없다. 반대로 같은 파일에 있던 `app.oasys-api.username/password`는 **개발계 실서버 계정**이라 반드시 분리 대상이다 |

---

## 7. skix 3종 저장소 조치 (완료)

`skix-security`·`skix-openapi`·`skix-medcare`는 이미 SSM + ECS Task Definition 주입 표준을 쓰고 있어서 **배포 경로에는 문제가 없었다.** 남아 있던 것은 로컬 프로필 파일이었고, 여기에 로컬 전용이 아닌 값이 섞여 있는 것이 문제였다.

| 저장소 | 분리한 것 | 남긴 것 | 커밋 |
|---|---|---|---|
| `skix-security` | `app.oasys-api.username`/`password`(개발계 OASYS 실서버 계정), `app.kvs.lease-secret`, `app.jwt.private-key`/`public-key`, `aws.iot.certificate`/`private-key`(개발계 IoT 실 인증서), `admin.jwt.secret` | DB URL·Redis·각 base-url·KVS 역할 ARN·Cognito 풀 ID·로깅·`datasource.password`(로컬 docker) | `18e69dd` |
| `skix-openapi` | RSA 개인키 전문, DB 비밀번호 | 비-시크릿 설정 전부 | `8163537` |
| `skix-medcare` | 로컬 프로필 시크릿을 `.env`로 외부화하고, 이후 `.env` import를 non-optional로 바꿔 **평문 기동 경로를 닫았다** | — | `25f740b`, `4c7af66` |

세 저장소 모두 `.env` / `config/local-secrets.yml`을 gitignore하고 `.example`만 커밋한다(`3fd8693`, `a574f97`, `8a8ac1a`). **세 커밋 모두 dev·stg·main 전부에 반영되어 있다**(실측).

`skix-openapi` 작업 중 남긴 메모 하나. 분리한 로컬 RSA 개인키는 **git 이력에 남아 있다.** 로컬 전용이라 이력 재작성은 하지 않았지만, **SSM의 `JWT_PRIVATE_KEY`와 값이 다른지는 확인이 필요하다.** 같으면 로컬 전용이 아니었던 것이고 로테이션 대상이 된다. 아직 확인 못 했다.

`skix-security`의 `admin.jwt.secret`은 분리만으로 끝나지 않는다. §1-2의 2번 그대로, `backend-api-main`의 `jwt.token.secret`과 같은 값이고 **양쪽 저장소 이력에 남아 있다.** 해소는 로테이션뿐이며, 로테이션하면 두 서비스를 **같은 시점에** 바꿔야 한다(§10-2).

---

## 8. 협력사 태스크 조사와 clone 게이트

2026-08-10에 `backend-api-main`을 **부채·안정성·테스트·보안 4축으로 전수 조사**해 협력사에 위임할 태스크 목록을 만들었다. 이 작업이 시크릿 조치와 한 문서에 묶인 이유는 **조사 결론이 "지금은 저장소를 넘길 수 없다"였기 때문**이다.

### 8-1. 최대 게이트

> 저장소에 **운영 개인키 14개와 IAM 액세스 키, 운영 DB 비밀번호가 평문으로 커밋**되어 있다. clone 권한을 주는 순간 이 값 전부가 사외로 나간다. **재발급(로테이션)과 git 이력 재작성 전에는 협력사 clone 권한을 부여할 수 없다.**

현재 트리의 값은 개발계·검증계 브랜치에서 사라졌지만 **이력과 운영계 브랜치에는 그대로다.** 즉 이 게이트는 **아직 유효하다.** 게이트를 여는 조건은 §10-2에 정리했다.

### 8-2. 조사에서 나온 것 (게이트가 열리면 착수할 내용)

P0으로 분류하고 실물 코드로 재검증한 버그다.

| 항목 | 증상 |
|---|---|
| `javax` → `jakarta` 미전환 2건 | 안면인식 Feature 등록이 100% 실패 중 |
| SQS 핸들러 7개가 예외를 삼킴 | 실패해도 ack가 나가 **DLQ가 무력화**된다 |
| `SmsService`가 `@Async` + Map 반환 | 실패가 성공 응답으로 나간다 |
| MyBatis `default-statement-timeout: 3000` | 단위가 초라 **50분**이다 |
| `AwsTokenUtil` NPE | 500 응답 |
| CallCenter 페이로드 키가 `"xxxx"` | 그대로 운영에 있음 |

테스트는 **assert가 0건**이고, 과거 테스트 54개가 커밋 `b0abfb56`에서 일괄 삭제됐다(복원 가능).

블라인드로 위임하면 다칠 지점도 확인해 뒀다. AWS SDK v1은 `IotJobService`·`MapService`가 **실사용 중**이라 그냥 걷어내면 안 된다. `embedded-redis`가 main 소스에서 쓰이고 있어 테스트 스코프로 옮기면 컴파일이 깨진다. 바이탈 알람 중복 컨트롤러는 **양쪽 다 라이브**다. `PdfUtil`의 `chartCount`는 static 메서드 2개를 관통하는 시그니처 변경이라 diff 형태로 지시할 수 없다. `IotJobService`·`FirmwareService` 관련 항목은 **별도 OTA 트랙과 파일이 겹치므로 이중 배정 금지**다. SQS 핸들러 예외 로그와 slow query는 **사내에서 실측한 뒤** 넘겨야 방향이 잡힌다.

### 8-3. 협력사 문서를 쓰는 방식 (인수자가 이어받을 때 지킬 것)

처음에는 before/after diff와 금지 조항 위주의 **지시서** 형태로 썼다가 반려됐다. 협력사 인원을 판단력 있는 동료로 대하는 관계이고, 검토해서 더 나은 방법이 있으면 그쪽으로 가도 되는 사이다. 리터럴 실행을 강제하는 문서는 그 관계에 맞지 않는다.

**각 항목을 ① 문제 상황(위치·영향) ② 제안하는 해결 방향 ③ 주의점(조사에서 확인한 함정을 '참고 정보'로)** 구조로 쓰고, "검토 후 더 나은 방법이 있으면 제안해 달라"는 톤을 유지한다. 방향이 크게 갈릴 수 있는 항목만 "착수 전 공유"를 요청한다. §8-2의 함정 정보는 **금지 명령이 아니라 도움말로** 전달한다.

조사 산출물 문서(태스크 백로그와 협업형 요청서)는 **로컬에 남아 있지 않다.** 위 §8-2가 이 문서에 남은 전부이며, 다시 쓸 때는 §8-2를 출발점으로 삼되 **9월 형상으로 재검증**해야 한다(8월 조사 이후 커밋이 많다).

---

## 9. 배포 방법

### 9-1. 배포 전에 반드시 아는 함정

| # | 함정 | 결과 |
|---|---|---|
| 1 | **빌드 명령이 바뀌었다.** `-Pprofile=<env>`는 이제 **아무 효과가 없다**(오류도 안 난다) | 옛 파이프라인이 `-Pprofile=prod`로 빌드하면 빌드는 성공하고, 실행 시 프로필이 없어 SSM을 못 읽고 기동이 실패한다. 조용한 오작동은 아니지만 **"빌드는 됐는데 안 뜬다"**로 보인다 |
| 2 | **실행 인자가 필수다.** `java -jar air-bot-api.jar`만으로는 뜨지 않는다 | `--spring.profiles.active={dev\|stg\|prd}`가 있어야 한다 |
| 3 | **EC2 인스턴스 프로파일 권한이 선행 조건이다** | `ssm:GetParameter*` + SecureString 복호화용 `kms:Decrypt`가 없으면 기동 실패. 코드 배포 전에 권한부터 붙인다 |
| 4 | **SSM 파라미터가 먼저 있어야 한다** | 코드가 먼저 올라가면 그 순간 서비스가 죽는다. 순서는 파라미터 → 권한 → 코드다 |
| 5 | **저장소에 배포 스크립트가 없다** | 프로필 재편 때 함께 쓴 EC2 기동 스크립트를 저장소 추적에서 뺐다(`d7eb1fbe`). 전문을 §부록 C에 옮겨 뒀다. 서버에 이미 놓여 있는지는 **미확인** |

### 9-2. 운영계 승격 순서 (역순 금지)

```
1. 운영계 SSM 파라미터 31개 등록            (§5-4)  ← 코드보다 먼저
2. 운영 EC2 인스턴스 프로파일에 권한 부여     (§부록 D)
3. 등록 확인 (이름만 나열해 31개인지)
4. stg → main 승격 + 배포                    ← 여기서 처음으로 코드가 SSM 을 읽는다
5. 기동 로그 확인 → 스모크
```

4번이 **되돌리기 가장 어려운 지점**이다. `origin/main`이 `origin/stg`보다 371커밋 뒤처져 있어 이 승격은 시크릿 조치만 넘어가는 게 아니라 **여름 이후의 모든 변경이 함께 넘어간다.** 시크릿 조치만 떼어 운영에 올릴 수는 없다(프로필 재편·yml 삭제가 한 덩어리다). 즉 이 작업의 운영 반영은 **9월 운영 릴리스와 같은 배에 탄다.**

### 9-3. 배포 후 검증

```bash
# 1) 기동했는가 — 가장 확실한 신호는 뜨는 것 자체다.
#    파라미터가 하나라도 없으면 애초에 안 뜬다.
#    이 저장소는 actuator 를 쓰지 않아 헬스 엔드포인트가 없다(실측). 로그로 본다.
grep 'Started ' <로그>/app.log | tail -1

# 2) 활성 프로필 확인 (EC2 에서)
ps -ef | grep 'air-bot-api.jar' | grep -o 'spring.profiles.active=[a-z]*'
#    기대: spring.profiles.active=prd

# 3) 기동 로그에서 SSM 로딩 확인
grep -i 'parameterstore\|ConfigData' <로그>/app.log | head

# 4) 실제로 값이 붙었는지 — 시크릿을 쓰는 경로를 하나씩
#    - DB: 아무 조회 API 1건 (database.username/password)
#    - IoT: MQTT 연결 로그 (aws.iot.certificate / private-key)
#    - FCM: 푸시 1건 (firebase.service-account-json)
#    - CloudFront: 펌웨어 서명 URL 발급 1건 (aws.cloudfront.private-key-pem)
#    - 얼굴인식: 기존 데이터 조회 1건 (facial.AES_SECRET_KEY / AES_IV)  ← 값 유지 이관 검증
#    - 암호화 컬럼: 기존 데이터 복호화 1건 (crypto.aes-key / legacy-pbe-key)
#      → 평문이 나와야 한다. 암호문 같은 문자열이 그대로 보이면 키가 틀린 것이다
#    - SMS: 인증번호 발송 1건 (app.nhn.sms.*)  ← 소스 하드코딩을 걷어낸 경로다
#    - 관리자 로그인: 백오피스 로그인 1건 (jwt.token.secret)
```

**4번의 얼굴인식·암호화 컬럼 항목이 이 배포의 핵심 회귀 지점이다.** 값이 하나라도 다르게 들어가면 기존 데이터를 못 읽는다. 기동이 성공해도 이건 안 걸리고, `CryptoUtil.decryptAES`는 실패 시 **예외 없이 암호문을 그대로 돌려주므로** 로그에도 안 남는다. 눈으로 평문인지 확인해야 한다(§6의 6번).

### 9-4. 롤백

- **코드**: 이전 jar로 되돌린다. 단 이전 jar은 **환경별로 구워진 옛 산출물**이므로 자기 안에 설정을 갖고 있다. 즉 롤백은 그 자체로 동작한다.
- **SSM 파라미터**: 삭제하지 않는다. 코드를 되돌려도 파라미터가 남아 있어 해롭지 않고, 다시 올릴 때 재등록이 필요 없다.
- **IAM 권한**: 회수하지 않는다. 읽기 권한이 경로 한정으로 붙어 있을 뿐이다.
- **주의**: 롤백한 옛 jar에는 **평문 시크릿이 들어 있다.** 롤백 산출물을 아무 데나 보관하지 않는다.

---

## 10. 남은 일

### 10-1. 즉시 (인수 후 첫 주, VDI 필요)

| # | 할 일 | 담당 | 비고 |
|---|---|---|---|
| 1 | **운영 EC2가 어떤 방식으로 도는지 확인.** 실행 인자에 `spring.profiles.active`가 있는지, 없으면 아직 구식 `-Pprofile` jar이다 | 백엔드 | §9-1 |
| 2 | **개발계·검증계 SSM 파라미터 실재 확인.** `aws ssm get-parameters-by-path --path /backend-api-main/dev/ --recursive --query 'Parameters[].Name'` 로 31개인지 | 백엔드 | §2-2. 강한 정황은 있으나 직접 본 적 없다 |
| 3 | 운영계 파라미터 존재 여부 확인 (없으면 §5-4 절차로 등록) | 백엔드 | 번들 재생성 시 §5-3 필독 |
| 4 | 개발계·검증계·운영계 **EC2 인스턴스 프로파일 정책 확인.** 경로 한정인지, 와일드카드로 과하게 열려 있지 않은지 | 백엔드 | §부록 D |
| 5 | `skix-openapi` 로컬 RSA 개인키가 SSM `JWT_PRIVATE_KEY`와 **같은 값인지** 확인. 같으면 로컬 전용이 아니었던 것이라 로테이션 대상 | 백엔드 | §7 |

### 10-2. 게이트 (통과 전 금지)

**A. 운영계 배포 게이트**

| # | 확인할 것 |
|---|---|
| 1 | 번들에 담긴 운영 값이 **현재 운영 EC2가 실제로 쓰는 값과 같은지.** 번들 소스가 `origin/main`(검증계보다 371커밋 뒤처진 트리)이라 그 사이에 값이 바뀌었을 수 있다 |
| 2 | 파라미터 등록 → 권한 부여 → 코드 승격 **순서 준수**(§9-2) |
| 3 | 9월 운영 릴리스와 **한 배**라는 점을 릴리스 담당과 합의. 시크릿 조치만 떼어 올릴 수 없다 |
| 4 | §9-3의 4번 검증 항목 전부(특히 얼굴인식·암호화 컬럼) |

**B. 협력사 clone 게이트** (§8-1)

| # | 조건 | 현재 |
|---|---|---|
| 5 | 저장소 이력에 남은 값 **전부 로테이션** | 미착수 |
| 6 | git 이력 재작성 또는 그에 준하는 조치 | 미착수 |
| 7 | 운영계 브랜치에서 개인키 14개·`AKIA` 제거 (= A 게이트 통과) | 미착수 |

**로테이션 순서 제안.** 영향이 작은 것부터 간다. ① `otp.key`(분 단위 TOTP, 즉시 안전) → ② 외부 API 키류(OpenWeather·Google Geocoding·MyHealthCare·NHN SMS — 발급처에서 새로 받고 SSM만 갈아끼우면 된다) → ③ DB 비밀번호(재기동 필요) → ④ `jwt.token.secret`/`admin.jwt.secret`(**`backend-api-main`과 `skix-security`를 같은 시점에** 바꿔야 한다. 어긋나면 관제 백오피스 전체가 인증 실패) → ⑤ IoT·CloudFront·Firebase·Apple 개인키(발급 절차와 소비처 확인이 필요) → ⑥ `app.integrity-key`(**앱 배포 동반**) → ⑦ `facial.AES_*`(**로테이션 불가. 값 유지**).

### 10-3. 후속 (막지는 않음)

| # | 항목 |
|---|---|
| 8 | SSM에서 죽은 파라미터 3개 제거: `app.ble.masking-key`, `masking-key-sub`, `masking-secret-key`(§4-3) |
| 9 | `skix-streaming` 프로필 yml 4벌·IoT/CloudFront 키·`build_vdi.gradle` Nexus 계정 조치(§2-3). `backend-api-main`과 같은 패턴을 그대로 쓸 수 있다 |
| 10 | `skix-streaming-signal`: `signaling/.env.{dev,stg,prd}` 추적 해제 + `.gitignore` 보완(`.env.*` 전체를 막고 `.env.example`만 예외). 배포가 저장소 디렉터리 복사 방식이라 **추적 해제만 하면 배포가 깨진다** — 서버의 `.env`를 먼저 확정해야 한다 |
| 11 | `skix-streaming-signal/turnserver.conf`: coturn이 실제로 돌지 않으므로 **파일 삭제 검토가 먼저** |
| 12 | `backend-scheduler-main` 프로필 yml 4벌의 DB 비밀번호(§2-3) |
| 13 | V10(pre-commit 시크릿 스캐닝) 재검토. **로테이션·이력 정리 이후**가 순서다(§3 주석) |
| 14 | `backend-api-main` 로컬 CLI 프로필 구성. 지금은 보류 상태이며, 재개하려면 소스 IP 제한을 우회할 방법이 먼저 필요하다(§1-4) |
| 15 | 개발자별 최소 권한 IAM 정책. SSO 미사용이 확인됐으므로 IAM 사용자 단위로 초안을 잡아야 한다 |

---

## 11. 운영 중 자주 만날 상황

| 상황 | 확인 방법 |
|---|---|
| **앱이 안 뜬다 / `ConfigDataResourceNotFoundException`** | 로컬이면 `config/local-secrets.yml`이 없는 것이다. `.example`을 복사한다. 운영이면 SSM 경로 접근 실패다 — 인스턴스 프로파일 권한과 파라미터 존재를 본다 |
| **`Could not resolve placeholder 'xxx'`** | 그 이름의 SSM 파라미터가 없다. 이름은 `/backend-api-main/<env>/` + 플레이스홀더 이름 그대로다 |
| **SSM에서 값을 바꿨는데 반영이 안 된다** | 두 가지다. ① 앱을 재기동하지 않았다(기동 시 1회 로드다. 핫 리로드 없음). ② **같은 키가 yml에도 있다** — 이 저장소에서는 yml 값이 이긴 전례가 있다(§6의 2번) |
| **로컬에서 S3·Cognito 호출이 `Unable to load credentials`** | 정상이다. 로컬은 AWS 기능 없이 개발한다(§1-4). 전환 전에도 폐기된 키로 403이 났다 |
| **로컬에서 실기기 MQTT 메시지가 안 온다** | 원래 그렇다. `local` 프로필은 IoT 토픽 접두사가 무작위 UUID다 |
| **화면·응답에 암호문 같은 문자열이 그대로 나온다** | `crypto.aes-key` / `legacy-pbe-key` 값이 원본과 다르다. 복호화 실패가 예외 없이 원문 반환으로 처리되는 구조다(§6의 6번) |
| **"빌드는 됐는데 서버가 안 뜬다"** | `--spring.profiles.active`가 빠졌을 가능성이 가장 높다(§9-1의 2번) |
| **파라미터 등록이 중간에 끊겼다** | 재실행한다. 이미 만든 것은 `SKIP`으로 넘어간다. 값을 갱신해야 하면 `--apply --overwrite` |
| **`AWS account mismatch`로 스크립트가 멈춘다** | 역할 전환을 안 한 것이다. 의도된 방어다(§6의 10번) |
| **`Read/write DB credentials differ`로 멈춘다** | 원본 yml의 읽기/쓰기 DB 계정이 달라졌다는 뜻이다. `database.*` 별칭 설계를 먼저 손봐야 한다(§4-3) |
| **저장소에서 평문 시크릿을 또 찾고 싶다** | `git grep -nI 'AKIA' <ref>` · `git log -S'AKIA' --all --oneline` · `git ls-tree -r --name-only <ref> \| grep -E '\.pem$\|\.p8$\|\.env'` |


---

## 부록 A. 핵심 커밋 (시간순)

전부 작성자 커밋이며 반영 상태는 2026-09-22 실측이다. `backend-api-main` 커밋은 **dev·stg 반영, main 미반영**, skix 3종 커밋은 **dev·stg·main 전부 반영**이다.

| 날짜 | 저장소 | 커밋 | 내용 |
|---|---|---|---|
| 07-31 | skix-medcare | `25f740b` | 로컬 프로필 시크릿을 `.env` 주입으로 외부화 |
| 08-11 | backend-api-main | `6b2a4526` | V5. `local` 분기 정적 AWS 자격증명 → `DefaultCredentialsProvider`(10개 파일), yml 4벌에서 AKIA 7쌍·운영 yml 주석 속 실키 제거 |
| 08-11 | backend-api-main / skix-security / skix-openapi | `88cd74f4` / `3fd8693` / `a574f97` | V11. `.env`, `.env.*`, `!.env.example` gitignore |
| 08-11 | backend-api-main | `19e3508e` | `spring-cloud-aws-starter-parameter-store` 추가. `config.import` 전까지 비활성 |
| 08-13 | backend-api-main | `b8c0f28e` | **SSM 런타임 로딩 전환.** `build.gradle` sourceSets 제거, `resources-{local,stag,prod,vdi}` 삭제, 개인키 14파일 삭제, IoT·CloudFront·Firebase·Apple·Crypto를 문자열 프로퍼티로 전환 (38파일, −1629) |
| 08-13 | backend-api-main | `846a725b` | 예제(`.bru`)·테스트·주석의 자격증명 흔적 제거 |
| 08-13 | backend-api-main | `27555128` | 프로필 `vdi/stag/prod` → `dev/stg/prd`, 코드의 프로필 문자열 비교 11곳 정정 |
| 08-13 | backend-api-main | `54e1013b` | SSM 이관 대상을 런타임 시크릿으로 한정해 프로필 yml 4벌 재생성, `SmsService` NHN appKey 하드코딩 제거 |
| 08-13 | backend-api-main | `bde60691` | 민감정보 런타임 로그 제거 (64파일) |
| 08-13 | backend-api-main | `15d8b9bd` | 운영 코드의 테스트 엔드포인트·SSH 예제 제거 |
| 08-13 | backend-api-main | `622b3d62` | 운영 디버그 코드·불필요 리소스 제거 (EC2 기동 스크립트 `deploy/air-bot-api.sh` 추가도 이 커밋) |
| 08-13 | backend-api-main | `2739f4c9` → `d2e38849` | 앞선 정리에서 과하게 지운 **외부 연동 가능성이 있는 API를 일단 전부 복원**한 뒤, 범위를 다시 잡아 미사용 API만 제거 |
| 08-13 | backend-api-main | `78225fcc` | 민감정보 로그 추가 정리(70파일)·로컬 JDBC 설정 정리 |
| 08-13 | skix-security | `18e69dd` | 로컬 프로필에서 시크릿 6종 분리, `config/local-secrets.yml` non-optional import |
| 08-13 | skix-openapi | `8163537` | 로컬 RSA 개인키·DB 비밀번호 분리 |
| 08-13 | skix-medcare | `4c7af66` | `.env` import를 non-optional로 — 평문 기동 경로 차단 |
| 08-14 | backend-api-main | `d7eb1fbe` | EC2 기동 스크립트 저장소 추적 제거 (전문은 부록 C) |
| 08-14 | backend-api-main | `ecaaf55f` | 검증·운영 로그 경로 정정 |
| 08-18 | backend-api-main | `3a63b6fe` | 로컬 시크릿 파일 주입 방식 복구 (`config/local-secrets.yml` + `.example`, non-optional) |

---

## 부록 B. SSM 이관 스크립트 전문

작성자가 사용한 원본 스크립트 그대로다. 시크릿 값은 원래 스크립트에 들어 있지 않다. **바꿔 적은 곳은 한 군데**다 — `build_encrypted_bundle.sh`와 `run_migration.sh`가 Apple DeviceCheck 키 파일명(키 ID가 들어간 이름)을 리터럴로 적고 있어서, 저장소 `src/main/resources/DeviceCheck_Key/`의 유일한 `.p8` 파일을 찾는 코드로 바꿔 적었다. 동작은 같다.

**다시 강조: 두 스크립트의 `source_ref` 기본값 `dev`로는 지금 동작하지 않는다.** `--source-ref origin/main` 또는 `--source-ref b8c0f28e^`를 준다(§5-3).

필요 도구: 로컬은 `git`·`openssl`·`tar`, CloudShell은 `python3`·`PyYAML`·`boto3`(CloudShell 기본 포함)·`openssl`.

### B-1. `build_encrypted_bundle.sh` — 로컬 맥에서 실행

```bash
#!/usr/bin/env bash

set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_REPO=$(cd -- "${SCRIPT_DIR}/../../backend-api-main" && pwd)

environment=""
repository="${DEFAULT_REPO}"
source_ref="dev"
output=""

usage() {
  echo "Usage: $0 --env dev|stg|prd [--repo PATH] [--source-ref REF] [--output FILE.enc]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      environment="${2:-}"
      shift 2
      ;;
    --repo)
      repository="${2:-}"
      shift 2
      ;;
    --source-ref)
      source_ref="${2:-}"
      shift 2
      ;;
    --output)
      output="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "${environment}" in
  dev)
    legacy_profile="vdi"
    iot_directory="IoTKeyFile"
    cloudfront_directory="cloudfront-dev-key"
    ;;
  stg)
    legacy_profile="stag"
    iot_directory="StagIoTKeyFile"
    cloudfront_directory="cloudfront-stg-key"
    ;;
  prd)
    legacy_profile="prod"
    iot_directory="ProdIoTKeyFile"
    cloudfront_directory="cloudfront-prod-key"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

if [[ -z "${output}" ]]; then
  output="${SCRIPT_DIR}/backend-api-ssm-${environment}.tar.gz.enc"
fi
if [[ -e "${output}" || -e "${output}.partial" ]]; then
  echo "Output already exists; refusing to overwrite: ${output}" >&2
  exit 1
fi

git -C "${repository}" rev-parse --verify "${source_ref}^{commit}" >/dev/null

bundle_temp_dir=$(mktemp -d /tmp/backend-api-ssm-bundle.XXXXXX)
legacy_temp_dir=$(mktemp -d /tmp/backend-api-ssm-legacy.XXXXXX)
chmod 700 "${bundle_temp_dir}" "${legacy_temp_dir}"
cleanup() {
  rm -rf -- "${bundle_temp_dir}" "${legacy_temp_dir}"
  rm -f -- "${output}.partial"
}
trap cleanup EXIT INT TERM

bundle_root="${bundle_temp_dir}/backend-api-ssm-bundle"
mkdir -p "${bundle_root}/inputs"

git -C "${repository}" archive "${source_ref}" \
  "src/main/resources-${legacy_profile}/application.yml" \
  src/main/resources/DeviceCheck_Key \
  "src/main/resources/${iot_directory}" \
  "src/main/resources/${cloudfront_directory}/private_key.pem" \
  src/main/resources/firebase/skMagicFirebaseAdminSdk.json \
  src/main/java/com/skmagic/core/utils/CryptoUtil.java | \
  tar -x -C "${legacy_temp_dir}"

iot_certificate=$(find \
  "${legacy_temp_dir}/src/main/resources/${iot_directory}" \
  -type f -name '*-certificate.pem' -print -quit)
iot_private_key=$(find \
  "${legacy_temp_dir}/src/main/resources/${iot_directory}" \
  -type f -name '*-private.pem' -print -quit)

if [[ -z "${iot_certificate}" || -z "${iot_private_key}" ]]; then
  echo "IoT certificate or private key was not found" >&2
  exit 1
fi

# [인수인계 문서 표기] 원본은 Apple 키 파일명을 리터럴로 적었다. 키 ID 노출을 피하려고 탐색으로 바꿔 적었다.
apple_private_key=$(find \
  "${legacy_temp_dir}/src/main/resources/DeviceCheck_Key" \
  -type f -name '*.p8' -print -quit)
if [[ -z "${apple_private_key}" ]]; then
  echo "Apple DeviceCheck private key was not found" >&2
  exit 1
fi

install -m 700 "${SCRIPT_DIR}/import_parameters.py" "${bundle_root}/import_parameters.py"
install -m 700 "${SCRIPT_DIR}/execute_bundle.sh" "${bundle_root}/execute_bundle.sh"
printf '%s\n' "${environment}" > "${bundle_root}/environment"
install -m 600 \
  "${legacy_temp_dir}/src/main/resources-${legacy_profile}/application.yml" \
  "${bundle_root}/inputs/application.yml"
install -m 600 \
  "${legacy_temp_dir}/src/main/resources/firebase/skMagicFirebaseAdminSdk.json" \
  "${bundle_root}/inputs/firebase-service-account.json"
install -m 600 \
  "${apple_private_key}" \
  "${bundle_root}/inputs/apple-private-key.p8"
install -m 600 "${iot_certificate}" "${bundle_root}/inputs/iot-certificate.pem"
install -m 600 "${iot_private_key}" "${bundle_root}/inputs/iot-private-key.pem"
install -m 600 \
  "${legacy_temp_dir}/src/main/resources/${cloudfront_directory}/private_key.pem" \
  "${bundle_root}/inputs/cloudfront-private-key.pem"
install -m 600 \
  "${legacy_temp_dir}/src/main/java/com/skmagic/core/utils/CryptoUtil.java" \
  "${bundle_root}/inputs/CryptoUtil.java"

passphrase_args=()
if [[ -n "${BUNDLE_PASSPHRASE_FILE:-}" ]]; then
  passphrase_args=(-pass "file:${BUNDLE_PASSPHRASE_FILE}")
fi

echo "Creating encrypted ${environment} bundle. Enter a new passphrase when prompted."
tar -czf - -C "${bundle_temp_dir}" backend-api-ssm-bundle | \
  openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -md sha256 \
    "${passphrase_args[@]}" \
    -out "${output}.partial"
mv -- "${output}.partial" "${output}"
chmod 600 "${output}"

echo "Encrypted bundle created: ${output}"
echo "Only the encrypted bundle and cloudshell_run_encrypted_bundle.sh should be transferred."
```

### B-2. `cloudshell_run_encrypted_bundle.sh` — VDI CloudShell에서 실행

```bash
#!/usr/bin/env bash

set -euo pipefail
umask 077

bundle=""
environment=""
forward_args=()

usage() {
  echo "Usage: $0 --bundle FILE.enc --env dev|stg|prd [--apply] [--overwrite] [--kms-key-id KEY]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)
      bundle="${2:-}"
      shift 2
      ;;
    --env)
      environment="${2:-}"
      shift 2
      ;;
    --apply|--overwrite)
      forward_args+=("$1")
      shift
      ;;
    --kms-key-id)
      forward_args+=("$1" "${2:-}")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "${environment}" in
  dev|stg|prd) ;;
  *)
    usage >&2
    exit 2
    ;;
esac
if [[ ! -f "${bundle}" ]]; then
  echo "Encrypted bundle not found: ${bundle}" >&2
  exit 1
fi

runtime_temp_dir=$(mktemp -d /tmp/backend-api-ssm-runtime.XXXXXX)
chmod 700 "${runtime_temp_dir}"
cleanup() {
  rm -rf -- "${runtime_temp_dir}"
}
trap cleanup EXIT INT TERM

passphrase_args=()
if [[ -n "${BUNDLE_PASSPHRASE_FILE:-}" ]]; then
  passphrase_args=(-pass "file:${BUNDLE_PASSPHRASE_FILE}")
fi

echo "Decrypting bundle into a temporary directory. Enter its passphrase when prompted."
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -md sha256 \
  "${passphrase_args[@]}" \
  -in "${bundle}" | tar -xzf - -C "${runtime_temp_dir}"

bundle_root="${runtime_temp_dir}/backend-api-ssm-bundle"
if [[ ! -x "${bundle_root}/execute_bundle.sh" || ! -f "${bundle_root}/environment" ]]; then
  echo "Invalid encrypted bundle contents" >&2
  exit 1
fi

bundle_environment=$(<"${bundle_root}/environment")
if [[ "${bundle_environment}" != "${environment}" ]]; then
  echo "Bundle environment mismatch: expected ${environment}, bundle is ${bundle_environment}" >&2
  exit 1
fi

"${bundle_root}/execute_bundle.sh" "${forward_args[@]}"
```

### B-3. `execute_bundle.sh` — 번들 내부 진입점 (직접 부르지 않는다)

```bash
#!/usr/bin/env bash

set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
environment=$(<"${SCRIPT_DIR}/environment")
forward_args=()

usage() {
  echo "Usage: $0 [--apply] [--overwrite] [--kms-key-id KEY]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply|--overwrite)
      forward_args+=("$1")
      shift
      ;;
    --kms-key-id)
      forward_args+=("$1" "${2:-}")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "${environment}" in
  dev|stg|prd) ;;
  *)
    echo "Invalid bundle environment" >&2
    exit 1
    ;;
esac

python3 "${SCRIPT_DIR}/import_parameters.py" \
  --env "${environment}" \
  --config "${SCRIPT_DIR}/inputs/application.yml" \
  --firebase-service-account "${SCRIPT_DIR}/inputs/firebase-service-account.json" \
  --apple-private-key "${SCRIPT_DIR}/inputs/apple-private-key.p8" \
  --iot-certificate "${SCRIPT_DIR}/inputs/iot-certificate.pem" \
  --iot-private-key "${SCRIPT_DIR}/inputs/iot-private-key.pem" \
  --cloudfront-private-key "${SCRIPT_DIR}/inputs/cloudfront-private-key.pem" \
  --legacy-crypto-source "${SCRIPT_DIR}/inputs/CryptoUtil.java" \
  "${forward_args[@]}"
```

### B-4. `import_parameters.py` — 등록 로직

계정번호·경로 규약·분류 목록(`SECRET_EXACT`, `DISCARDED_EXACT`)이 여기 있다. **분류를 바꾸려면 B-6의 `render_sanitized_profiles.rb`도 같이 바꿔야 한다**(두 파일이 같은 목록을 따로 들고 있다).

```python
#!/usr/bin/env python3
"""Import legacy backend-api-main configuration into SSM Parameter Store.

Dry-run is the default. Values are never written to stdout or passed as command-line
arguments to the AWS CLI. With --apply, boto3 sends them directly to SSM.
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment check
    raise SystemExit("PyYAML is required: python3 -m pip install --user PyYAML") from exc


REGION = "ap-northeast-2"
ACCOUNT_BY_ENV = {
    "dev": "010928196421",
    "stg": "087432099373",
    "prd": "779846811758",
}

SECRET_EXACT = {
    "aws.cognito.client.secret",
    "jwt.token.secret",
    "app.integrity-key",
    "app.myHealthCare.api.api-key",
    "app.nhn.sms.appKey",
    "app.nhn.sms.secretKey",
    "app.oasys.api.username",
    "app.oasys.api.password",
    "app.ble.google-geocode-key",
    "app.ble.masking-key",
    "app.ble.masking-key-sub",
    "app.ble.masking-secret-key",
    "app.ble.security-key-sub",
    "facial.AES_SECRET_KEY",
    "facial.AES_IV",
    "googleGeocoding.api.key",
    "map.convert.key",
    "openweather.api.key2_5",
    "openweather.api.key3_0",
    "otp.key",
    "nice.pass.client-secret",
    "nice.pass.site-password",
}

DISCARDED_EXACT = {
    "spring.profiles.active",
    "app.apple.key-path",
    "aws.cloudfront.privateKeyPath",
    "aws.iot.certificate-locations",
    "aws.iot.private-locations",
    "aws.iot.awsAccessKeyId",
    "aws.iot.awsSecreatAccessKey",
    "aws.iot.job.credentials.access-key-id",
    "aws.iot.job.credentials.secret-access-key",
    "aws.cognito.credentials.access-key-id",
    "aws.cognito.credentials.secret-access-key",
    "aws.kms.cmkArn",
    "aws.iot.job.thingName",
    "encypt.field.key",
}

S3_STATIC_CREDENTIAL = re.compile(
    r"^aws\.s3\.[^.]+\.(?:accessKey|secretKey)$"
)

VALID_PARAMETER_NAME = re.compile(r"^[A-Za-z0-9_.\-/]+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import one backend-api-main environment into SSM (dry-run by default)."
    )
    parser.add_argument("--env", required=True, choices=ACCOUNT_BY_ENV)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--firebase-service-account", required=True, type=Path)
    parser.add_argument("--apple-private-key", required=True, type=Path)
    parser.add_argument("--iot-certificate", required=True, type=Path)
    parser.add_argument("--iot-private-key", required=True, type=Path)
    parser.add_argument("--cloudfront-private-key", required=True, type=Path)
    parser.add_argument("--legacy-crypto-source", required=True, type=Path)
    parser.add_argument("--region", default=REGION)
    parser.add_argument(
        "--kms-key-id",
        help="Optional customer-managed KMS key ARN or alias. Omit to use alias/aws/ssm.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create parameters. Without this flag only a dry-run is performed.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Update existing parameters. Requires --apply.",
    )
    return parser.parse_args()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Unable to read required input: {path}: {exc}") from exc


def scalar_to_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def flatten(value: Any, path: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            result.update(flatten(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            # Spring Cloud AWS maps _0_, _1_, ... to indexed properties.
            child_path = f"{path}_{index}_"
            result.update(flatten(child, child_path))
    else:
        scalar = scalar_to_string(value)
        if scalar is not None:
            result[path] = scalar
    return result


def is_discarded(key: str) -> bool:
    return (
        key in DISCARDED_EXACT
        or key.startswith("aws.s3.ai.")
        or S3_STATIC_CREDENTIAL.fullmatch(key) is not None
    )


def require_property(flattened: dict[str, str], key: str) -> str:
    try:
        return flattened[key]
    except KeyError as exc:
        raise SystemExit(f"Required secret property is missing: {key}") from exc


def extract_legacy_crypto_keys(source: str) -> tuple[str, str]:
    aes_match = re.search(r'AES_PRIVATE_KEY\s*=\s*"([^"]+)"', source)
    pbe_match = re.search(r'PBE_PRIVATE_KEY\s*=\s*"([^"]+)"', source)
    if not aes_match or not pbe_match:
        raise SystemExit("Could not locate legacy AES/PBE keys in CryptoUtil source")

    aes_key = aes_match.group(1)
    try:
        decoded = base64.b64decode(aes_key, validate=True)
    except ValueError as exc:
        raise SystemExit("Legacy AES key is not valid Base64") from exc
    if len(decoded) != 32:
        raise SystemExit("Legacy AES key must decode to exactly 32 bytes")
    return aes_key, pbe_match.group(1)


def build_properties(
    args: argparse.Namespace,
) -> tuple[dict[str, str], list[str], int]:
    raw = yaml.safe_load(read_text(args.config)) or {}
    if not isinstance(raw, dict):
        raise SystemExit("The legacy application YAML must contain a mapping at its root")

    flattened = flatten(raw)
    discarded = sorted(key for key in flattened if is_discarded(key))
    properties = {
        key: require_property(flattened, key) for key in sorted(SECRET_EXACT)
    }

    read_username = require_property(flattened, "spring.datasource.read.username")
    write_username = require_property(flattened, "spring.datasource.write.username")
    read_password = require_property(flattened, "spring.datasource.read.password")
    write_password = require_property(flattened, "spring.datasource.write.password")
    if read_username != write_username or read_password != write_password:
        raise SystemExit(
            "Read/write DB credentials differ; database.username/password aliases "
            "cannot be shared"
        )
    properties["database.username"] = read_username
    properties["database.password"] = read_password

    aes_key, pbe_key = extract_legacy_crypto_keys(
        read_text(args.legacy_crypto_source)
    )
    properties.update(
        {
            "firebase.service-account-json": read_text(args.firebase_service_account),
            "app.apple.private-key": read_text(args.apple_private_key),
            "aws.iot.certificate": read_text(args.iot_certificate),
            "aws.iot.private-key": read_text(args.iot_private_key),
            "aws.cloudfront.private-key-pem": read_text(args.cloudfront_private_key),
            "crypto.aes-key": aes_key,
            "crypto.legacy-pbe-key": pbe_key,
        }
    )
    selected_legacy_keys = SECRET_EXACT | {
        "spring.datasource.read.username",
        "spring.datasource.read.password",
        "spring.datasource.write.username",
        "spring.datasource.write.password",
    }
    retained_count = len(set(flattened) - selected_legacy_keys - set(discarded))
    return properties, discarded, retained_count


def validate_properties(env: str, properties: dict[str, str]) -> None:
    prefix = f"/backend-api-main/{env}/"
    errors: list[str] = []
    for key, value in properties.items():
        name = prefix + key
        if not key or not VALID_PARAMETER_NAME.fullmatch(name):
            errors.append(f"invalid parameter name: {name}")
        if value == "":
            errors.append(f"empty value: {name}")
        if len(value.encode("utf-8")) > 4096:
            errors.append(f"Standard tier value exceeds 4096 bytes: {name}")
    if errors:
        raise SystemExit("Validation failed:\n- " + "\n- ".join(errors))


def print_plan(
    env: str,
    properties: dict[str, str],
    discarded: list[str],
    retained_count: int,
    apply: bool,
) -> None:
    prefix = f"/backend-api-main/{env}/"
    print(f"Mode: {'apply' if apply else 'dry-run'}")
    print(f"Environment: {env}")
    print(f"Expected AWS account: {ACCOUNT_BY_ENV[env]}")
    print(f"Parameters to create: {len(properties)} (all SecureString / Standard)")
    print(f"Non-secret properties retained in profile YAML: {retained_count}")
    print(f"Obsolete/static credential properties discarded: {len(discarded)}")
    print("\nParameter names (values intentionally hidden):")
    for key in sorted(properties):
        print(f"  {prefix}{key}")
    print("\nDiscarded legacy properties:")
    for key in discarded:
        print(f"  {key}")


def apply_parameters(args: argparse.Namespace, properties: dict[str, str]) -> None:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:  # pragma: no cover - CloudShell environment check
        raise SystemExit("boto3 is required for --apply") from exc

    session = boto3.Session(region_name=args.region)
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    actual_account = identity["Account"]
    expected_account = ACCOUNT_BY_ENV[args.env]
    if actual_account != expected_account:
        raise SystemExit(
            f"AWS account mismatch: expected {expected_account}, got {actual_account}"
        )

    ssm = session.client("ssm")
    prefix = f"/backend-api-main/{args.env}/"
    created = 0
    updated = 0
    skipped = 0
    try:
        for key in sorted(properties):
            name = prefix + key
            request: dict[str, Any] = {
                "Name": name,
                "Description": "backend-api-main runtime configuration",
                "Value": properties[key],
                "Type": "SecureString",
                "Tier": "Standard",
                "Overwrite": args.overwrite,
            }
            if args.kms_key_id:
                request["KeyId"] = args.kms_key_id
            try:
                ssm.put_parameter(**request)
                if args.overwrite:
                    updated += 1
                else:
                    created += 1
                print(f"OK  {name}")
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code == "ParameterAlreadyExists" and not args.overwrite:
                    skipped += 1
                    print(f"SKIP {name} (already exists)")
                    continue
                raise
    except (BotoCoreError, ClientError) as exc:
        raise SystemExit(
            "SSM import stopped after a partial result. Values were not printed. "
            f"created={created}, updated={updated}, skipped={skipped}; error={type(exc).__name__}"
        ) from exc

    print(
        f"Completed: created={created}, updated={updated}, skipped={skipped}, "
        f"total={len(properties)}"
    )


def main() -> None:
    args = parse_args()
    if args.overwrite and not args.apply:
        raise SystemExit("--overwrite requires --apply")

    properties, discarded, retained_count = build_properties(args)
    validate_properties(args.env, properties)
    print_plan(args.env, properties, discarded, retained_count, args.apply)
    if not args.apply:
        print("\nNo AWS changes made. Add --apply only after reviewing this plan.")
        return

    print("\nApplying parameters. Values will remain hidden.")
    apply_parameters(args, properties)


if __name__ == "__main__":
    main()
```

### B-5. `run_migration.sh` — 로컬에 AWS 자격증명이 있을 때만 쓰는 단축 경로

번들을 거치지 않고 저장소에서 바로 등록한다. **로컬 맥에서는 자격증명이 없어 `--apply`가 불가능하다**(dry-run은 된다). AWS 자격증명과 저장소가 같은 곳에 있는 환경이 생기면 쓴다.

```bash
#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_REPO=$(cd -- "${SCRIPT_DIR}/../../backend-api-main" && pwd)

environment=""
repository="${DEFAULT_REPO}"
source_ref="dev"
forward_args=()

usage() {
  echo "Usage: $0 --env dev|stg|prd [--repo PATH] [--source-ref REF] [--apply] [--overwrite] [--kms-key-id KEY]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      environment="${2:-}"
      shift 2
      ;;
    --repo)
      repository="${2:-}"
      shift 2
      ;;
    --source-ref)
      source_ref="${2:-}"
      shift 2
      ;;
    --apply|--overwrite)
      forward_args+=("$1")
      shift
      ;;
    --kms-key-id)
      forward_args+=("$1" "${2:-}")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "${environment}" in
  dev)
    legacy_profile="vdi"
    iot_directory="IoTKeyFile"
    cloudfront_directory="cloudfront-dev-key"
    ;;
  stg)
    legacy_profile="stag"
    iot_directory="StagIoTKeyFile"
    cloudfront_directory="cloudfront-stg-key"
    ;;
  prd)
    legacy_profile="prod"
    iot_directory="ProdIoTKeyFile"
    cloudfront_directory="cloudfront-prod-key"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

git -C "${repository}" rev-parse --verify "${source_ref}^{commit}" >/dev/null

migration_temp_dir=$(mktemp -d /tmp/backend-api-ssm-migration.XXXXXX)
chmod 700 "${migration_temp_dir}"
cleanup() {
  rm -rf -- "${migration_temp_dir}"
}
trap cleanup EXIT INT TERM

git -C "${repository}" archive "${source_ref}" \
  "src/main/resources-${legacy_profile}/application.yml" \
  src/main/resources/DeviceCheck_Key \
  "src/main/resources/${iot_directory}" \
  "src/main/resources/${cloudfront_directory}/private_key.pem" \
  src/main/resources/firebase/skMagicFirebaseAdminSdk.json \
  src/main/java/com/skmagic/core/utils/CryptoUtil.java | \
  tar -x -C "${migration_temp_dir}"

iot_certificate=$(find \
  "${migration_temp_dir}/src/main/resources/${iot_directory}" \
  -type f -name '*-certificate.pem' -print -quit)
iot_private_key=$(find \
  "${migration_temp_dir}/src/main/resources/${iot_directory}" \
  -type f -name '*-private.pem' -print -quit)

if [[ -z "${iot_certificate}" || -z "${iot_private_key}" ]]; then
  echo "IoT certificate or private key was not found" >&2
  exit 1
fi

# [인수인계 문서 표기] 원본은 Apple 키 파일명을 리터럴로 적었다. 키 ID 노출을 피하려고 탐색으로 바꿔 적었다.
apple_private_key=$(find \
  "${migration_temp_dir}/src/main/resources/DeviceCheck_Key" \
  -type f -name '*.p8' -print -quit)

python3 "${SCRIPT_DIR}/import_parameters.py" \
  --env "${environment}" \
  --config "${migration_temp_dir}/src/main/resources-${legacy_profile}/application.yml" \
  --firebase-service-account "${migration_temp_dir}/src/main/resources/firebase/skMagicFirebaseAdminSdk.json" \
  --apple-private-key "${apple_private_key}" \
  --iot-certificate "${iot_certificate}" \
  --iot-private-key "${iot_private_key}" \
  --cloudfront-private-key "${migration_temp_dir}/src/main/resources/${cloudfront_directory}/private_key.pem" \
  --legacy-crypto-source "${migration_temp_dir}/src/main/java/com/skmagic/core/utils/CryptoUtil.java" \
  "${forward_args[@]}"
```

### B-6. `render_sanitized_profiles.rb` — 새 프로필 yml 생성 (일회성, 이미 적용됨)

옛 `resources-*/application.yml`에서 시크릿·폐기 키를 걷어내고 `${database.*}` 별칭을 넣고 `spring.config.import`를 붙여 `src/main/resources/application-{local,dev,stg,prd}.yml`을 만든다. `54e1013b`의 yml이 이 결과물을 손본 것이다. **다시 돌리면 현재 yml을 덮어쓰므로 돌리지 않는다.** 이관 규칙을 이해하는 참고용으로만 남긴다. (`local`에 `optional:aws-parameterstore:` 를 쓰는 부분은 이후 `3a63b6fe`에서 `file:./config/local-secrets.yml` non-optional로 바뀌었다.)

```ruby
#!/usr/bin/env ruby

require "yaml"

ROOT = File.expand_path("../../backend-api-main", __dir__)

PROFILE_MAP = {
  "local" => ["local", "local"],
  "dev" => ["vdi", "dev"],
  "stg" => ["stag", "stg"],
  "prd" => ["prod", "prd"]
}.freeze

SECRET_PATHS = %w[
  aws.cognito.client.secret
  jwt.token.secret
  app.integrity-key
  app.myHealthCare.api.api-key
  app.nhn.sms.appKey
  app.nhn.sms.secretKey
  app.oasys.api.username
  app.oasys.api.password
  app.ble.google-geocode-key
  app.ble.masking-key
  app.ble.masking-key-sub
  app.ble.masking-secret-key
  app.ble.security-key-sub
  facial.AES_SECRET_KEY
  facial.AES_IV
  googleGeocoding.api.key
  map.convert.key
  openweather.api.key2_5
  openweather.api.key3_0
  otp.key
  nice.pass.client-secret
  nice.pass.site-password
].freeze

REMOVED_PATHS = %w[
  spring.profiles.active
  app.apple.key-path
  aws.cloudfront.privateKeyPath
  aws.iot.certificate-locations
  aws.iot.private-locations
  aws.iot.awsAccessKeyId
  aws.iot.awsSecreatAccessKey
  aws.iot.job.credentials.access-key-id
  aws.iot.job.credentials.secret-access-key
  aws.iot.job.thingName
  aws.cognito.credentials.access-key-id
  aws.cognito.credentials.secret-access-key
  aws.kms.cmkArn
  encypt.field.key
].freeze

def fetch_legacy_yaml(old_profile)
  content = IO.popen(
    ["git", "-C", ROOT, "show", "dev:src/main/resources-#{old_profile}/application.yml"],
    &:read
  )
  raise "Unable to read legacy #{old_profile} configuration" unless $?.success?

  YAML.safe_load(content, aliases: true) || {}
end

def delete_path(root, dotted_path)
  parts = dotted_path.split(".")
  leaf = parts.pop
  parent = parts.reduce(root) do |current, part|
    break nil unless current.is_a?(Hash)

    current[part]
  end
  parent.delete(leaf) if parent.is_a?(Hash)
end

def delete_s3_static_credentials(root)
  s3 = root.dig("aws", "s3")
  return unless s3.is_a?(Hash)

  s3.delete("ai")
  s3.each_value do |config|
    next unless config.is_a?(Hash)

    config.delete("accessKey")
    config.delete("secretKey")
  end
end

def prune_empty_hashes(value)
  return unless value.is_a?(Hash)

  value.each_value { |child| prune_empty_hashes(child) }
  value.delete_if { |_key, child| child.is_a?(Hash) && child.empty? }
end

def sanitize(old_profile, ssm_environment)
  config = fetch_legacy_yaml(old_profile)

  read_db = config.dig("spring", "datasource", "read") || {}
  write_db = config.dig("spring", "datasource", "write") || {}
  unless read_db["username"] == write_db["username"] &&
         read_db["password"] == write_db["password"]
    raise "Read/write DB credentials differ for #{old_profile}; aliases cannot be shared"
  end

  read_db["username"] = "${database.username}"
  read_db["password"] = "${database.password}"
  write_db["username"] = "${database.username}"
  write_db["password"] = "${database.password}"

  mybatis = config.dig("mybatis", "configuration")
  if mybatis.is_a?(Hash) && mybatis.key?("jdbc-type-for-null")
    mybatis["jdbc-type-for-null"] = "NULL"
  end

  SECRET_PATHS.each { |path| delete_path(config, path) }
  REMOVED_PATHS.each { |path| delete_path(config, path) }
  delete_s3_static_credentials(config)

  config["spring"] ||= {}
  config["spring"]["config"] = {
    "import" => if ssm_environment == "local"
                  "optional:aws-parameterstore:/backend-api-main/local/"
                else
                  "aws-parameterstore:/backend-api-main/#{ssm_environment}/"
                end
  }

  prune_empty_hashes(config)
  config
end

PROFILE_MAP.each do |new_profile, (old_profile, ssm_environment)|
  output = File.join(ROOT, "src/main/resources/application-#{new_profile}.yml")
  File.write(output, YAML.dump(sanitize(old_profile, ssm_environment)))
  File.chmod(0o644, output)
end
```

---

## 부록 C. EC2 기동 스크립트 (`deploy/air-bot-api.sh`, 저장소에서 추적 제거됨)

프로필 재편 때 작성했다가(`622b3d62`) 다음 날 저장소 추적에서 뺐다(`d7eb1fbe`). 서버에 이 파일이 놓여 있는지는 **미확인**이다. 사용법: `./air-bot-api.sh {start|stop|restart|status} [dev|stg|prd]`. 핵심은 `--spring.profiles.active`를 반드시 받는다는 것이고, 프로필 없이 `start`하면 거절한다.

```bash
#!/usr/bin/env bash

set -Eeuo pipefail
umask 027

readonly BASE_DIR="/opt/www/airbot/back-end"
readonly JAR_FILE="${BASE_DIR}/air-bot-api.jar"
readonly PID_FILE="${BASE_DIR}/air-bot-api.pid"
readonly LOG_DIR="${BASE_DIR}/logs"
readonly JAVA_BIN="${JAVA_BIN:-java}"

is_running() {
    [[ -f "${PID_FILE}" ]] || return 1

    local pid
    pid="$(<"${PID_FILE}")"
    [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

start() {
    local profile="${1:-${SPRING_PROFILES_ACTIVE:-}}"

    case "${profile}" in
        dev|stg|prd) ;;
        *)
            echo "Profile must be one of: dev, stg, prd" >&2
            exit 2
            ;;
    esac

    if is_running; then
        echo "Application is already running (PID $(<"${PID_FILE}"))."
        return 0
    fi

    if [[ ! -r "${JAR_FILE}" ]]; then
        echo "JAR file not found or unreadable: ${JAR_FILE}" >&2
        exit 1
    fi

    mkdir -p "${LOG_DIR}"
    chmod 750 "${LOG_DIR}"

    nohup "${JAVA_BIN}" \
        -Djava.net.preferIPv4Stack=true \
        -Xms4076m \
        -Xmx8192m \
        -jar "${JAR_FILE}" \
        --spring.profiles.active="${profile}" \
        >/dev/null 2>&1 &

    local pid=$!
    printf '%s\n' "${pid}" > "${PID_FILE}"
    sleep 3

    if ! is_running; then
        rm -f "${PID_FILE}"
        echo "Application failed to start. Check ${LOG_DIR}/app.log." >&2
        exit 1
    fi

    echo "Application started with profile ${profile} (PID ${pid})."
}

stop() {
    if ! is_running; then
        rm -f "${PID_FILE}"
        echo "Application is not running."
        return 0
    fi

    local pid
    pid="$(<"${PID_FILE}")"
    kill "${pid}"

    local attempt
    for attempt in {1..30}; do
        if ! kill -0 "${pid}" 2>/dev/null; then
            rm -f "${PID_FILE}"
            echo "Application stopped."
            return 0
        fi
        sleep 1
    done

    echo "Application did not stop within 30 seconds (PID ${pid})." >&2
    exit 1
}

status() {
    if is_running; then
        echo "Application is running (PID $(<"${PID_FILE}"))."
    else
        echo "Application is stopped."
        return 1
    fi
}

case "${1:-}" in
    start) start "${2:-}" ;;
    stop) stop ;;
    restart)
        stop
        start "${2:-}"
        ;;
    status) status ;;
    *)
        echo "Usage: $0 {start|stop|restart|status} [dev|stg|prd]" >&2
        exit 2
        ;;
esac
```

---

## 부록 D. SSM 파라미터 목록과 필요 권한

### D-1. 등록 대상 31개 (`/backend-api-main/<env>/` 아래, 전부 SecureString / Standard)

| # | 파라미터 이름 | 출처 | 비고 |
|---|---|---|---|
| 1 | `aws.cognito.client.secret` | yml | |
| 2 | `jwt.token.secret` | yml | 관리자 JWT HMAC. `skix-security` `ADMIN_JWT_SECRET`과 같은 값이어야 한다. 로테이션 시 동시 교체 |
| 3 | `app.integrity-key` | yml | iOS 앱 내장값과 비교. 교체 시 앱 배포 동반 |
| 4 | `app.myHealthCare.api.api-key` | yml | medcare 연동. `app.internal.api-key`가 이 값을 별칭으로 참조 |
| 5 | `app.nhn.sms.appKey` | yml | |
| 6 | `app.nhn.sms.secretKey` | yml | |
| 7 | `app.oasys.api.username` | yml | |
| 8 | `app.oasys.api.password` | yml | |
| 9 | `app.ble.google-geocode-key` | yml | |
| 10 | `app.ble.masking-key` | yml | **사용처 없음.** 정리 대상 |
| 11 | `app.ble.masking-key-sub` | yml | **사용처 없음.** 정리 대상 |
| 12 | `app.ble.masking-secret-key` | yml | **사용처 없음.** 정리 대상 |
| 13 | `app.ble.security-key-sub` | yml | |
| 14 | `facial.AES_SECRET_KEY` | yml | **DB 데이터 암호화 키. 로테이션 불가** |
| 15 | `facial.AES_IV` | yml | 위와 같음 |
| 16 | `googleGeocoding.api.key` | yml | |
| 17 | `map.convert.key` | yml | |
| 18 | `openweather.api.key2_5` | yml | |
| 19 | `openweather.api.key3_0` | yml | |
| 20 | `otp.key` | yml | 분 단위 TOTP. 로테이션 안전 |
| 21 | `nice.pass.client-secret` | yml | |
| 22 | `nice.pass.site-password` | yml | |
| 23 | `database.username` | yml `spring.datasource.{read,write}.username` | 읽기/쓰기 공통 별칭 |
| 24 | `database.password` | yml `spring.datasource.{read,write}.password` | 읽기/쓰기 공통 별칭 |
| 25 | `firebase.service-account-json` | `src/main/resources/firebase/skMagicFirebaseAdminSdk.json` | JSON 전체 |
| 26 | `app.apple.private-key` | `src/main/resources/DeviceCheck_Key/*.p8` | PEM 전체 |
| 27 | `aws.iot.certificate` | `src/main/resources/{IoTKeyFile,StagIoTKeyFile,ProdIoTKeyFile}/*-certificate.pem` | 환경별 디렉터리가 다르다(dev=`IoTKeyFile`) |
| 28 | `aws.iot.private-key` | 위 디렉터리의 `*-private.pem` | |
| 29 | `aws.cloudfront.private-key-pem` | `src/main/resources/cloudfront-{dev,stg,prod}-key/private_key.pem` | public_key.pem은 비밀이 아니라 옮기지 않았다 |
| 30 | `crypto.aes-key` | `CryptoUtil.java`의 옛 상수 | Base64, 디코딩 32바이트. **DB 데이터 암호화 키. 값 유지 필수** |
| 31 | `crypto.legacy-pbe-key` | `CryptoUtil.java`의 옛 상수 | 옛 PBE 방식 데이터 복호화용. **값 유지 필수** |

### D-2. 폐기한 설정 (SSM에 넣지 않고 yml에서도 삭제)

`spring.profiles.active`, `app.apple.key-path`, `aws.cloudfront.privateKeyPath`, `aws.iot.certificate-locations`, `aws.iot.private-locations`, `aws.iot.awsAccessKeyId`, `aws.iot.awsSecreatAccessKey`, `aws.iot.job.credentials.access-key-id`, `aws.iot.job.credentials.secret-access-key`, `aws.cognito.credentials.access-key-id`, `aws.cognito.credentials.secret-access-key`, `aws.kms.cmkArn`, `aws.iot.job.thingName`, `encypt.field.key`, 그리고 `aws.s3.ai.*` 전체와 `aws.s3.*.accessKey` / `aws.s3.*.secretKey` 패턴 전체.

파일 경로 설정은 파일을 안 읽게 되어 지웠고, 정적 자격증명은 인스턴스 프로파일로 대체되어 지웠고, 나머지는 참조 코드가 없는 죽은 설정이다.

### D-3. EC2 인스턴스 프로파일에 필요한 권한

원래 정책 초안이 있었으나 로컬에 남아 있지 않다. 아래는 **등록 스크립트와 `spring.config.import`가 요구하는 동작에서 역산한 최소 형태**다. 실제 적용된 정책과 대조하는 기준으로 쓴다. `<계정>`은 §1-4 표의 환경 계정, `<env>`는 `dev`/`stg`/`prd`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadBackendApiMainParameters",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParametersByPath",
        "ssm:GetParameters",
        "ssm:GetParameter"
      ],
      "Resource": [
        "arn:aws:ssm:ap-northeast-2:<계정>:parameter/backend-api-main/<env>",
        "arn:aws:ssm:ap-northeast-2:<계정>:parameter/backend-api-main/<env>/*"
      ]
    },
    {
      "Sid": "DecryptSecureStringWithSsmKey",
      "Effect": "Allow",
      "Action": "kms:Decrypt",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": "ssm.ap-northeast-2.amazonaws.com"
        }
      }
    }
  ]
}
```

- `spring.config.import: aws-parameterstore:/backend-api-main/<env>/`는 **경로 단위 조회**(`GetParametersByPath`)를 한다. 경로 자체 ARN과 하위 와일드카드를 둘 다 넣는 이유다.
- 등록 시 `--kms-key-id`를 주지 않았다면 기본 키 `alias/aws/ssm`으로 암호화된다. 이 경우 `kms:ViaService` 조건만으로 충분하다. 고객 관리형 키를 썼다면 `Resource`를 그 키 ARN으로 좁힌다.
- **다른 환경 경로를 읽을 수 없게** 계정·경로를 환경별로 고정한다. 운영 EC2가 `/backend-api-main/dev/`를 읽을 이유가 없다.
- 등록하는 사람(CloudShell)에게는 별도로 `ssm:PutParameter`와 `sts:GetCallerIdentity`가 필요하다. 이건 사람 역할의 권한이지 EC2 권한이 아니다.
