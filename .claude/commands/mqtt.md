---
description: MQTT 토픽 수신→처리→응답발행 전체 체인 추적 (리플렉션 매핑 반영)
---
MQTT 토픽 **$1** 의 처리 로직을 추적하라.

이 프로젝트의 MQTT 라우팅 (grep으로 핸들러가 안 나오는 이유):
1. `IoTConfiguration.java` — `$share/{group}/{type}/airbot/+/+/{토픽끝}` 구독 확인
2. `IotInboundMessageHandler.VALID_TOPIC_ENDS` — 화이트리스트 포함 확인 (IN_PROGRESS/succeeded/failed/rejected → jobUpdate 특례)
3. 핸들러 = `ResponseDeviceService.$1(String topic, String payload)` — 토픽 끝 == 메서드명 리플렉션 호출
4. 응답 발행 시 `RequestDeviceService.*Response()` → `Topic.java` 상수에서 발행 토픽 확인

반드시 포함: payload 역직렬화 모델(대개 HistoryControl)의 실제 JSON 예시, correlationId 흐름, MQTT 경로(비동기·예외 스왈로잉→기기 타임아웃) vs SQS 경로(동기·재시도/DLQ) 차이, DB 변경·FCM·S3 등 부수효과, 발행 응답 토픽과 payload.

거대 파일(ResponseDeviceService 4.6k줄, RequestDeviceService 3.3k줄)은 rg→sed 발췌만. CLAUDE.md 답변 형식으로.
