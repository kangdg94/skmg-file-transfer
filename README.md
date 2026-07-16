맞습니다. 첨부 가이드는 **ECS Fargate 환경에 Datadog Agent를 사이드카 컨테이너로 추가하는 방식**입니다.

구조는 다음과 같습니다.

```text
ECS 서비스
└─ Fargate Task
   ├─ app 컨테이너
   │  └─ dd-java-agent.jar
   │       └─ localhost:8126으로 Trace 전송
   │
   └─ datadog-agent 컨테이너
      └─ Datadog SaaS로 메트릭·Trace 전송
```

Fargate의 `awsvpc` 네트워크 모드에서는 같은 Task 안의 컨테이너끼리 `localhost`로 통신할 수 있으므로, 앱에서 `DD_AGENT_HOST=localhost`를 사용하는 것이 맞습니다. Datadog도 Fargate에서는 애플리케이션과 같은 Task Definition에 Agent 컨테이너를 추가하도록 안내합니다. ([AWS 문서][1])

첨부 가이드 기준으로 작업 순서는 아래와 같습니다. 

---

# 1. 먼저 담당 작업을 구분

가이드에서는 업무를 두 부분으로 나눕니다.

| 담당        | 작업                                                                       |
| --------- | ------------------------------------------------------------------------ |
| 인프라 담당    | Parameter Store, IAM 역할, Task Definition, Datadog Agent 사이드카, ECS 서비스 배포 |
| 애플리케이션 담당 | Dockerfile에 `dd-java-agent.jar` 포함, 실행 옵션 적용                             |
| 공동 확인     | `DD_SERVICE`, `DD_ENV`, `DD_VERSION`, `DD_SITE`, AAP 활성화 여부              |

즉, AWS 콘솔에서 Agent 컨테이너만 추가한다고 APM이 자동으로 동작하는 것은 아닙니다.

애플리케이션 이미지에도 반드시 다음이 들어가야 합니다.

```text
-javaagent:/dd-java-agent.jar
```

---

# 2. 내부망 네트워크부터 확인

내부망 Fargate에서 가장 많이 막히는 부분입니다.

Fargate Task는 최소한 다음 통신이 가능해야 합니다.

```text
애플리케이션 이미지 다운로드
Datadog Agent 이미지 다운로드
SSM Parameter Store에서 API Key 조회
CloudWatch Logs로 로그 전송
Datadog SaaS로 메트릭 및 Trace 전송
```

## 2.1 NAT Gateway가 있는 경우

AWS 콘솔에서 확인합니다.

1. **VPC**
2. 왼쪽 메뉴 **라우팅 테이블**
3. Fargate가 사용하는 Private Subnet의 라우팅 테이블 선택
4. **라우팅** 탭 확인

다음 라우팅이 있으면 NAT를 통한 외부 통신 구조입니다.

```text
대상: 0.0.0.0/0
대상 유형: NAT 게이트웨이
```

보안 그룹의 **아웃바운드 규칙**에서도 HTTPS `TCP 443`이 허용돼 있어야 합니다. Datadog Agent는 일반적으로 Agent에서 Datadog 방향으로 SSL/TLS `443` 통신을 시작하며, Datadog에서 Fargate로 들어오는 인바운드 연결은 필요하지 않습니다. ([docs.datadoghq.com][2])

## 2.2 NAT Gateway가 없는 완전한 내부망인 경우

AWS 서비스 접근용으로 다음 VPC 엔드포인트가 필요할 수 있습니다.

```text
com.amazonaws.ap-northeast-2.ecr.api
com.amazonaws.ap-northeast-2.ecr.dkr
com.amazonaws.ap-northeast-2.ssm
com.amazonaws.ap-northeast-2.logs
com.amazonaws.ap-northeast-2.kms
```

그리고 ECR 이미지 레이어 다운로드를 위한 **S3 게이트웨이 엔드포인트**가 필요합니다. AWS PrivateLink와 VPC 엔드포인트를 사용하면 NAT 없이 ECR 및 AWS 서비스에 접근할 수 있습니다. ([AWS 문서][3])

다만 다음 이미지는 **Public ECR**에 있습니다.

```text
public.ecr.aws/datadog/agent:latest
```

폐쇄망에서는 다음 방식이 더 안정적입니다.

```text
public.ecr.aws/datadog/agent 이미지를 외부에서 다운로드
→ 회사 AWS 계정의 Private ECR에 Push
→ Task Definition에서 Private ECR 주소 사용
```

Datadog 데이터 전송은 별도로 다음 중 하나가 필요합니다.

* NAT Gateway
* 사내 HTTP/HTTPS Proxy
* Datadog AWS PrivateLink

Datadog Agent는 인터넷에 직접 연결되지 않는 환경에서 HTTP/HTTPS Proxy를 사용할 수 있습니다. ([docs.datadoghq.com][4])

---

# 3. Datadog API Key를 Parameter Store에 저장

API Key를 Task Definition의 일반 환경변수에 직접 넣으면 안 됩니다.

## 3.1 AWS 콘솔 경로

1. AWS 콘솔 상단 검색창에서 **Systems Manager**
2. 왼쪽 메뉴 **애플리케이션 관리**
3. **파라미터 스토어**
4. 우측 **파라미터 생성**

다음처럼 입력합니다.

| 항목       | 입력값                     |
| -------- | ----------------------- |
| 이름       | `/datadog/api_key`      |
| 설명       | `Datadog Agent API Key` |
| 티어       | `표준`                    |
| 유형       | `SecureString`          |
| KMS 키 소스 | `현재 계정`                 |
| KMS 키 ID | 기본 키 또는 회사 관리형 키        |
| 값        | Datadog API Key         |

입력 후 **파라미터 생성**을 누릅니다.

생성된 파라미터를 선택하고 **ARN**을 복사합니다.

예:

```text
arn:aws:ssm:ap-northeast-2:123456789012:parameter/datadog/api_key
```

ECS Task Definition에서는 이 ARN을 `DD_API_KEY` 환경변수의 보안 값으로 사용합니다. AWS는 ECS 컨테이너에 민감 정보를 전달할 때 Parameter Store나 Secrets Manager를 사용하도록 안내합니다. ([AWS 문서][5])

---

# 4. ECS 작업 실행 역할에 API Key 조회 권한 추가

여기서 가장 많이 혼동하는 부분이 있습니다.

## 두 역할의 차이

| AWS 콘솔 항목    | 용도                                                       |
| ------------ | -------------------------------------------------------- |
| **작업 역할**    | 애플리케이션 코드가 S3, DynamoDB 등의 AWS API를 호출할 때 사용             |
| **작업 실행 역할** | ECS가 이미지 다운로드, CloudWatch 로그 전송, Parameter Store 조회 시 사용 |

`DD_API_KEY`를 Parameter Store에서 가져오는 권한은 **작업 실행 역할**에 추가해야 합니다. ([AWS 문서][6])

---

## 4.1 현재 작업 실행 역할 확인

1. AWS 콘솔에서 **Elastic Container Service**
2. 왼쪽 메뉴 **작업 정의**
3. 현재 애플리케이션의 작업 정의 선택
4. 최신 개정 선택
5. **작업 실행 역할** 확인

일반적으로 다음과 같은 이름입니다.

```text
ecsTaskExecutionRole
```

---

## 4.2 IAM 권한 추가

1. AWS 콘솔에서 **IAM**
2. 왼쪽 메뉴 **액세스 관리 → 역할**
3. 위에서 확인한 `ecsTaskExecutionRole` 선택
4. **권한 추가**
5. **인라인 정책 생성**
6. **JSON** 탭 선택
7. 다음 정책 입력

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadDatadogApiKey",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameters"
      ],
      "Resource": "arn:aws:ssm:ap-northeast-2:123456789012:parameter/datadog/api_key"
    }
  ]
}
```

다음 값은 실제 AWS 계정 ID로 변경합니다.

```text
123456789012
```

8. **다음**
9. 정책 이름 입력

```text
DatadogApiKeyParameterReadPolicy
```

10. **정책 생성**

Parameter Store의 SecureString에 **고객 관리형 KMS 키**를 사용했다면 다음 권한도 추가해야 합니다.

```json
{
  "Sid": "DecryptDatadogApiKey",
  "Effect": "Allow",
  "Action": [
    "kms:Decrypt"
  ],
  "Resource": "arn:aws:kms:ap-northeast-2:123456789012:key/키-ID"
}
```

기본 AWS 관리형 키를 사용했다면 일반적으로 별도의 `kms:Decrypt` 권한 추가는 필요하지 않습니다. ECS가 Parameter Store 값을 주입하려면 실행 역할에 `ssm:GetParameters`가 필요하고, 고객 관리형 KMS 키를 쓴 경우 `kms:Decrypt`도 필요합니다. ([AWS 문서][6])

---

# 5. CloudWatch 로그 그룹 준비

애플리케이션과 Datadog Agent의 시작 로그를 확인할 수 있도록 로그 그룹을 준비합니다.

1. AWS 콘솔에서 **CloudWatch**
2. 왼쪽 메뉴 **로그 → 로그 그룹**
3. **로그 그룹 생성**
4. 로그 그룹 이름 입력

```text
/ecs/my-springboot-app
```

5. 보존 기간 설정
6. **생성**

Task Definition에서 다음 로그 그룹 이름을 동일하게 사용합니다.

Agent 로그도 같은 그룹에 보내고 `awslogs-stream-prefix`만 다르게 설정할 수 있습니다.

---

# 6. 애플리케이션 이미지에 Java Agent 적용

이 단계는 AWS 콘솔이 아니라 애플리케이션 소스의 `Dockerfile`에서 수행합니다.

첨부 가이드는 Java 21, Spring Boot 3.3.6을 기준으로 다음 구성을 사용합니다. 

```dockerfile
FROM eclipse-temurin:21-jre AS builder

WORKDIR /application

ARG JAR_FILE=build/libs/my-app.jar
COPY ${JAR_FILE} application.jar

RUN java -Djarmode=layertools -jar application.jar extract


FROM eclipse-temurin:21-jre

WORKDIR /application

ADD https://dtdg.co/latest-java-tracer /dd-java-agent.jar

COPY --from=builder /application/dependencies/ ./
COPY --from=builder /application/spring-boot-loader/ ./
COPY --from=builder /application/snapshot-dependencies/ ./
COPY --from=builder /application/application/ ./

ENTRYPOINT ["java", "-javaagent:/dd-java-agent.jar", "--enable-native-access=ALL-UNNAMED", "org.springframework.boot.loader.launch.JarLauncher"]
```

Datadog 공식 Java 문서도 `latest-java-tracer`를 사용해 `dd-java-agent.jar`를 이미지에 추가하는 방식을 제공합니다. ([docs.datadoghq.com][7])

## 주의사항

다음처럼 `ENTRYPOINT` JSON 배열 안에 백슬래시를 넣지 않습니다.

```dockerfile
ENTRYPOINT ["java", \
  "-javaagent:/dd-java-agent.jar", \
  "org.springframework.boot.loader.launch.JarLauncher"]
```

반드시 다음처럼 작성합니다.

```dockerfile
ENTRYPOINT ["java", "-javaagent:/dd-java-agent.jar", "--enable-native-access=ALL-UNNAMED", "org.springframework.boot.loader.launch.JarLauncher"]
```

Docker 이미지를 다시 빌드하고 ECR에 Push한 뒤, 새 이미지 태그를 Task Definition에 반영해야 합니다.

```text
예:
123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/my-springboot-app:1.0.0
```

---

# 7. ECS Task Definition에 Datadog Agent 추가

기존 ECS 서비스가 있다면 새로운 Task Definition을 만들기보다는 **기존 작업 정의의 새 개정**을 만드는 것이 안전합니다.

## 7.1 AWS 콘솔 경로

1. AWS 콘솔에서 **Elastic Container Service**
2. 왼쪽 메뉴 **작업 정의**
3. 현재 작업 정의 패밀리 선택
4. 최신 개정 선택
5. 우측 상단 **새 개정 생성**
6. 가능하면 **JSON을 사용하여 새 개정 생성** 선택

화면에 따라 다음처럼 표시될 수 있습니다.

```text
새 개정 생성
JSON으로 새 개정 생성
JSON을 사용하여 새 개정 생성
```

기존 JSON은 삭제하지 말고, 기존 값을 유지한 상태에서 Datadog 설정을 추가합니다.

---

## 7.2 애플리케이션 컨테이너에 환경변수 추가

기존 `app` 컨테이너의 `environment`에 다음 값을 추가합니다.

```json
"environment": [
  {
    "name": "DD_SERVICE",
    "value": "my-springboot-app"
  },
  {
    "name": "DD_ENV",
    "value": "prod"
  },
  {
    "name": "DD_VERSION",
    "value": "1.0.0"
  },
  {
    "name": "DD_AGENT_HOST",
    "value": "localhost"
  },
  {
    "name": "DD_TRACE_AGENT_PORT",
    "value": "8126"
  },
  {
    "name": "DD_TRACE_AGENT_URL",
    "value": "http://localhost:8126"
  },
  {
    "name": "DD_APPSEC_ENABLED",
    "value": "true"
  },
  {
    "name": "DD_LOGS_INJECTION",
    "value": "true"
  },
  {
    "name": "DD_TRACE_STARTUP_LOGS",
    "value": "true"
  }
]
```

각 값은 다음 의미입니다.

| 변수                      | 의미                            |
| ----------------------- | ----------------------------- |
| `DD_SERVICE`            | Datadog에서 표시될 서비스 이름          |
| `DD_ENV`                | `dev`, `staging`, `prod` 등 환경 |
| `DD_VERSION`            | 배포 버전                         |
| `DD_AGENT_HOST`         | 같은 Task의 Agent 주소             |
| `DD_TRACE_AGENT_PORT`   | APM 포트                        |
| `DD_TRACE_AGENT_URL`    | Trace 전송 주소를 명시적으로 고정         |
| `DD_APPSEC_ENABLED`     | App & API Protection 활성화      |
| `DD_LOGS_INJECTION`     | 애플리케이션 로그에 Trace ID 삽입        |
| `DD_TRACE_STARTUP_LOGS` | 시작 시 Datadog 설정 출력            |

`DD_AGENT_HOST=localhost`는 같은 Fargate Task 안의 컨테이너가 localhost 인터페이스를 공유하기 때문에 가능합니다. ([AWS 문서][8])

---

## 7.3 Datadog Agent 컨테이너 추가

기존 `containerDefinitions` 배열에 다음 컨테이너를 추가합니다.

```json
{
  "name": "datadog-agent",
  "image": "public.ecr.aws/datadog/agent:latest",
  "essential": true,
  "environment": [
    {
      "name": "DD_SITE",
      "value": "datadoghq.com"
    },
    {
      "name": "ECS_FARGATE",
      "value": "true"
    },
    {
      "name": "DD_APM_ENABLED",
      "value": "true"
    },
    {
      "name": "DD_APM_NON_LOCAL_TRAFFIC",
      "value": "true"
    },
    {
      "name": "DD_PROCESS_AGENT_ENABLED",
      "value": "true"
    },
    {
      "name": "DD_DOGSTATSD_NON_LOCAL_TRAFFIC",
      "value": "true"
    },
    {
      "name": "DD_REMOTE_CONFIGURATION_ENABLED",
      "value": "true"
    }
  ],
  "secrets": [
    {
      "name": "DD_API_KEY",
      "valueFrom": "arn:aws:ssm:ap-northeast-2:123456789012:parameter/datadog/api_key"
    }
  ],
  "portMappings": [
    {
      "containerPort": 8126,
      "hostPort": 8126,
      "protocol": "tcp"
    }
  ],
  "healthCheck": {
    "command": [
      "CMD-SHELL",
      "agent health"
    ],
    "interval": 30,
    "timeout": 5,
    "retries": 3,
    "startPeriod": 15
  },
  "logConfiguration": {
    "logDriver": "awslogs",
    "options": {
      "awslogs-group": "/ecs/my-springboot-app",
      "awslogs-region": "ap-northeast-2",
      "awslogs-stream-prefix": "datadog-agent"
    }
  }
}
```

다음 값은 실제 환경으로 변경합니다.

```text
123456789012       → AWS 계정 ID
datadoghq.com      → 실제 Datadog Site
/ecs/my-springboot-app → 실제 로그 그룹
```

Datadog 공식 문서에서도 Fargate Task Definition 안에 `datadog-agent` 컨테이너를 추가하고, 이미지로 `public.ecr.aws/datadog/agent:latest`, 환경변수로 `ECS_FARGATE=true`를 설정하도록 안내합니다. ([docs.datadoghq.com][9])

---

# 8. Task Definition 전체에서 확인할 항목

Task Definition 상단 설정은 다음 구조여야 합니다.

```json
{
  "family": "my-springboot-task",
  "networkMode": "awsvpc",
  "requiresCompatibilities": [
    "FARGATE"
  ],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::123456789012:role/ecsTaskExecutionRole",
  "containerDefinitions": [
    {
      "name": "app"
    },
    {
      "name": "datadog-agent"
    }
  ]
}
```

## CPU와 메모리 주의

가이드의 다음 값은 예시입니다.

```json
"cpu": "512",
"memory": "1024"
```

기존 Spring Boot 애플리케이션이 이미 1GB에 가깝게 사용한다면 Agent를 추가하면서 메모리 부족이 발생할 수 있습니다.

따라서 다음 순서로 판단합니다.

1. 기존 Task CPU/Memory 확인
2. 기존 앱의 최대 메모리 사용량 확인
3. Datadog Agent 추가 여유분 확보
4. 개발환경에서 먼저 배포
5. 안정화 후 운영 반영

Agent 컨테이너가 `essential: true`이므로 Agent가 OOM으로 종료되면 Task 전체가 중지되고 ECS 서비스가 새 Task를 실행합니다. 모니터링이 끊긴 상태에서 앱만 계속 동작하는 것을 방지하기 위한 설정입니다. 

---

# 9. 보안 그룹에서 8126을 외부에 열 필요 없음

다음 포트는 같은 Task 내부 통신용입니다.

```text
8126: Datadog APM Trace
```

앱 컨테이너가 `localhost:8126`으로 접근하므로 다음 작업은 하지 않습니다.

```text
ALB에 8126 등록
보안 그룹 인바운드에 8126 개방
인터넷에 8126 공개
```

Fargate Task의 보안 그룹에는 기존 애플리케이션 포트만 ALB에서 허용하면 됩니다.

예:

| 방향    |   포트 | 소스                        |
| ----- | ---: | ------------------------- |
| 인바운드  | 8080 | ALB 보안 그룹                 |
| 아웃바운드 |  443 | Datadog, AWS 서비스 또는 Proxy |
| 인바운드  | 8126 | 추가하지 않음                   |

같은 `awsvpc` Task 내부 컨테이너는 localhost로 직접 통신할 수 있습니다. ([AWS 문서][1])

---

# 10. 새 Task Definition 개정 등록

JSON 수정을 완료한 후:

1. 화면 하단 **생성**
2. 또는 **등록**
3. 새 개정 번호 확인

예:

```text
my-springboot-task:15
```

기존 개정이 `:14`였다면 새 Datadog 설정은 `:15`에 등록됩니다.

Task Definition을 새로 등록했다고 기존 서비스가 자동으로 새 개정을 사용하는 것은 아닙니다. 서비스 업데이트가 필요합니다.

---

# 11. ECS 서비스에 새 개정 적용

## AWS 콘솔 경로

1. **Elastic Container Service**
2. 왼쪽 메뉴 **클러스터**
3. 대상 클러스터 선택
4. **서비스** 탭
5. 대상 서비스 선택
6. 우측 상단 **서비스 업데이트**

다음 값을 확인합니다.

| 항목        | 설정                   |
| --------- | -------------------- |
| 컴퓨팅 옵션    | 기존 설정 유지             |
| 작업 정의 패밀리 | 기존 패밀리               |
| 작업 정의 개정  | 방금 생성한 최신 개정         |
| 원하는 작업 수  | 기존 값 유지              |
| 강제 새 배포   | 최신 개정 선택 시 일반적으로 불필요 |

화면 하단에서 **업데이트**를 누릅니다.

---

# 12. ECS에서 배포 상태 확인

서비스 화면에서 확인합니다.

## 12.1 배포 확인

1. ECS 클러스터
2. 서비스 선택
3. **배포** 탭

정상적으로 다음 상태가 되어야 합니다.

```text
롤아웃 상태: 완료
실행 중 작업 수 = 원하는 작업 수
```

## 12.2 Task 내부 컨테이너 확인

1. 서비스 화면의 **작업** 탭
2. 새로 생성된 Task 선택
3. **컨테이너** 영역 확인

다음 두 컨테이너가 모두 실행 중이어야 합니다.

```text
app              실행 중
datadog-agent    실행 중 / 정상
```

## 12.3 실패한 경우

**중지된 작업**을 선택하고 다음 항목을 확인합니다.

* 중지 이유
* 컨테이너 종료 코드
* `ResourceInitializationError`
* `CannotPullContainerError`
* `Essential container in task exited`
* `AccessDeniedException`
* `unable to pull secrets`

`ResourceInitializationError` 또는 `unable to pull secrets`는 Task가 ECR, Parameter Store 또는 Secrets Manager에 연결하지 못할 때 주로 발생합니다. ([AWS 문서][10])

---

# 13. CloudWatch에서 로그 확인

AWS 콘솔에서:

1. **CloudWatch**
2. **로그 → 로그 그룹**
3. `/ecs/my-springboot-app`
4. 최신 로그 스트림 확인

스트림은 대략 다음처럼 구분됩니다.

```text
ecs/app/...
datadog-agent/datadog-agent/...
```

## 앱 로그에서 확인할 내용

`DD_TRACE_STARTUP_LOGS=true`를 설정했다면 시작할 때 Datadog Java Tracer 설정이 출력됩니다.

확인할 값:

```text
service: my-springboot-app
env: prod
agent_url: http://localhost:8126
appsec_enabled: true
```

## Agent 로그에서 확인할 내용

다음과 같은 오류가 없어야 합니다.

```text
API key invalid
Could not reach intake
Connection timed out
No route to host
```

---

# 14. Datadog 화면에서 검증

## 14.1 Infra Monitoring

Datadog에서:

```text
Infrastructure
→ Containers
```

또는:

```text
Infrastructure
→ Host Map
```

Fargate Task와 컨테이너가 표시되는지 확인합니다.

Agent가 포함된 Fargate Task는 상세 컨테이너 메트릭, Autodiscovery, Trace 등의 수집이 가능합니다. ([docs.datadoghq.com][9])

## 14.2 APM

애플리케이션 API를 몇 번 호출합니다.

```bash
curl https://애플리케이션주소/existing-route
```

Datadog에서:

```text
APM
→ Traces
```

필터:

```text
service:my-springboot-app
env:prod
```

다음 정보가 보여야 합니다.

* API 경로
* 처리 시간
* HTTP 상태 코드
* 오류
* DB 호출
* 외부 API 호출

## 14.3 App & API Protection

Datadog에서:

```text
Security
→ Application Security
```

가이드의 테스트 예시는 다음과 같습니다.

```bash
for i in $(seq 1 250); do
  curl https://애플리케이션주소/existing-route \
    -A 'dd-test-scanner-log'
done
```

운영 서비스에서 바로 실행하지 말고 개발·검증 환경에서 먼저 실행하는 것이 안전합니다.

AAP는 Agent 자체가 아니라 Java Tracer에서 활성화됩니다.

```text
앱 컨테이너:
DD_APPSEC_ENABLED=true

Agent 컨테이너:
DD_REMOTE_CONFIGURATION_ENABLED=true
```

`DD_REMOTE_CONFIGURATION_ENABLED=true`를 사용하면 지원되는 Tracer 버전에서 Datadog UI를 통한 원격 설정 기능을 사용할 수 있습니다. 

---

# 15. 로그 수집과 로그 연계는 다른 기능

다음 설정은 로그를 Datadog으로 전송하는 설정이 아닙니다.

```text
DD_LOGS_INJECTION=true
```

이 설정은 기존 로그에 다음 정보를 삽입합니다.

```text
trace_id
span_id
service
env
version
```

실제 애플리케이션 로그가 현재 `awslogs` 드라이버로 CloudWatch에 기록된다면, Datadog 로그 수집을 위해서는 별도로 다음 중 하나가 필요합니다.

1. **CloudWatch Logs → Datadog Forwarder Lambda**
2. **FireLens Fluent Bit 사이드카 → Datadog**

현재 목표가 Infra Monitoring, APM, AAP라면 로그 전달 구성은 별도 단계로 처리해도 됩니다. 첨부 가이드도 로그 수집 방식으로 FireLens 또는 CloudWatch Logs와 Datadog Forwarder 연동을 제시합니다. 

---

# 16. 가장 자주 발생하는 오류

## `AccessDeniedException: ssm:GetParameters`

원인:

```text
작업 실행 역할에 ssm:GetParameters 권한 없음
```

해결:

```text
IAM
→ 역할
→ ecsTaskExecutionRole
→ 인라인 정책
→ ssm:GetParameters 추가
```

---

## `ResourceInitializationError: unable to pull secrets`

원인 후보:

* SSM VPC 엔드포인트 없음
* NAT Gateway 없음
* 실행 역할 권한 없음
* Parameter Store ARN 오류
* 고객 관리형 KMS 키 권한 없음

---

## `CannotPullContainerError`

원인 후보:

* `public.ecr.aws` 접근 불가
* NAT Gateway 없음
* DNS 해석 실패
* 보안 그룹 아웃바운드 차단

폐쇄망이면 Datadog Agent 이미지를 Private ECR로 이관하는 방식이 적절합니다.

---

## Agent는 실행되는데 Datadog에 아무것도 안 보임

확인 순서:

1. `DD_SITE`가 실제 Datadog 조직 Site와 같은지
2. API Key가 올바른지
3. 아웃바운드 443 통신 가능한지
4. Proxy 설정이 필요한 환경인지
5. Agent CloudWatch 로그에 연결 오류가 있는지

---

## Infra는 보이지만 APM이 안 보임

확인 순서:

1. 애플리케이션 이미지에 `/dd-java-agent.jar`가 있는지
2. Java 실행 명령에 `-javaagent:/dd-java-agent.jar`가 있는지
3. `DD_AGENT_HOST=localhost`인지
4. `DD_TRACE_AGENT_PORT=8126`인지
5. Agent에 `DD_APM_ENABLED=true`가 있는지
6. 앱 컨테이너가 실제 요청을 받았는지
7. 앱 시작 로그에 Datadog Tracer 설정이 출력되는지

---

# 최종 작업 체크리스트

```text
[ ] 내부망에서 Datadog 방향 HTTPS 443 통신 가능
[ ] public.ecr.aws 접근 가능 또는 Agent 이미지를 Private ECR로 이관
[ ] Parameter Store에 /datadog/api_key SecureString 생성
[ ] 작업 실행 역할에 ssm:GetParameters 추가
[ ] CloudWatch 로그 그룹 생성
[ ] Dockerfile에 dd-java-agent.jar 추가
[ ] Java ENTRYPOINT에 -javaagent 적용
[ ] 앱 이미지를 다시 빌드하고 ECR에 Push
[ ] 기존 Task Definition의 새 개정 생성
[ ] 앱 컨테이너에 DD_SERVICE/DD_ENV/DD_VERSION 추가
[ ] datadog-agent 사이드카 컨테이너 추가
[ ] ECS 서비스에 새 Task Definition 개정 적용
[ ] app, datadog-agent 컨테이너 모두 RUNNING 확인
[ ] Datadog Infrastructure에서 Fargate Task 확인
[ ] Datadog APM Traces 확인
[ ] Application Security에서 AAP 확인
```

현재 사용 중인 **ECS Task Definition JSON**을 제공하면, 기존 설정을 유지하면서 Datadog 부분만 병합한 복사·붙여넣기용 JSON으로 정리할 수 있습니다.

[1]: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-networking-awsvpc.html?utm_source=chatgpt.com "Allocate a network interface for an Amazon ECS task"
[2]: https://docs.datadoghq.com/partners/getting_started/data-intake/?utm_source=chatgpt.com "Data intake"
[3]: https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html?utm_source=chatgpt.com "Amazon ECR interface VPC endpoints (AWS PrivateLink)"
[4]: https://docs.datadoghq.com/agent/configuration/proxy/?utm_source=chatgpt.com "Datadog Agent Proxy Configuration"
[5]: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data.html?utm_source=chatgpt.com "Pass sensitive data to an Amazon ECS container"
[6]: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_execution_IAM_role.html "Amazon ECS task execution IAM role - Amazon Elastic Container Service"
[7]: https://docs.datadoghq.com/tracing/trace_collection/dd_libraries/java/?utm_source=chatgpt.com "Tracing Java Applications"
[8]: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-networking.html?utm_source=chatgpt.com "Amazon ECS task networking options for Fargate"
[9]: https://docs.datadoghq.com/integrations/aws-fargate/ "Amazon ECS on AWS Fargate"
[10]: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/resource-initialization-error.html?utm_source=chatgpt.com "Troubleshooting Amazon ECS ResourceInitializationError ..."
