---
description: 진입점(MQTT 토픽/REST 경로/SQS/WebSocket/클래스#메서드)을 자동 판별해 전체 로직을 추적
---
다음 진입점의 로직을 끝까지 추적하라: **$ARGUMENTS**

진입점 유형을 먼저 판별하라:
- `skmg/...` 또는 토픽 끝 단어 하나 (예: getS3URL, cleanResult) → **MQTT/SQS**. CLAUDE.md의 리플렉션 매핑 규칙 적용: 토픽 마지막 세그먼트 == `ResponseDeviceService`의 메서드명. `VALID_TOPIC_ENDS` 포함 여부부터 확인. MQTT(비동기·예외 스왈로잉)와 SQS(동기·DLQ) 두 경로 차이를 모두 답할 것.
- `/`로 시작하거나 HTTP 메서드 포함 (예: GET /building/list) → **REST**. context-path `/api` 감안. 컨트롤러→서비스→매퍼→XML SQL까지.
- `클래스#메서드` → 해당 메서드부터 호출 체인 추적 (역방향: 누가 호출하는지도).

규칙:
- 거대 파일은 rg로 줄번호 특정 후 sed로 범위 발췌만 (전체 읽기 금지)
- 흐름의 끝(응답 JSON / MQTT 발행 토픽 / DB 변경 / FCM 푸시 / S3 등 부수효과)까지 완주
- 횡단 관심사(인증·@CheckWritePermission·@Encrypt·readOnly 라우팅·마스킹) 해당 여부 확인
- CLAUDE.md의 "로직 추적 답변 형식" 7항목으로 답하라. `파일:줄번호` 필수.
