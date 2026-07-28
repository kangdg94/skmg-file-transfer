---
description: 흐름 설명 생략, Input/Output 스펙만 간결하게
---
**$ARGUMENTS** 의 Input/Output만 답하라. 중간 흐름 설명은 생략.

- Input: 실제 JSON/파라미터 예시 + 각 필드 의미·필수 여부·제약
- Output: 실제 응답 JSON 예시 (ReponseResult 봉투 포함) 또는 발행 토픽+payload
- 부수효과 한 줄 목록 (DB 변경, 푸시, S3 등)
- 표와 JSON 코드블록만 사용, 산문 최소화

코드에서 검증한 것만. 못 찾은 필드는 "미확인" 표기.
