---
description: REST API 경로의 컨트롤러→서비스→매퍼→SQL→응답까지 추적
---
REST API **$ARGUMENTS** 의 처리 로직을 추적하라.

절차:
1. context-path가 `/api`이므로 이를 뺀 경로로 `rg -n --iglob '*Controller.java'` 검색 (클래스 @RequestMapping + 메서드 매핑 조합 주의)
2. 컨트롤러 → 서비스 → 매퍼 인터페이스 → `resources/mapper/{도메인}/*.xml`의 해당 SQL id까지. 테이블명·조인·조건 요지 포함
3. 횡단 관심사 확인:
   - `SystemConstants.getExcludeUrls()` 포함 여부 (인증 필요한지)
   - `@CheckWritePermission` 유무
   - 요청/응답 모델의 `@Encrypt` 필드 (DB값≠자바값), 마스킹 처리
   - `@Transactional(readOnly=true)` 유무 (read 레플리카 vs write DB)
4. 응답은 `ReponseResult` 봉투/`PageUtil` 페이징 감안한 **실제 응답 JSON 구조**로

Input(파라미터/바디 JSON 예시, 필수/선택), 처리 분기, Output(성공/실패 응답), 부수효과(DB 쓰기·MQTT 발행·FCM) 전부. CLAUDE.md 답변 형식으로.
