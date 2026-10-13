# Communicator 연동 API 인터페이스

> 기준: backend-api-main `dev` 브랜치 (커밋 `cb12a00d`)

## 1. App → WAS : 음성 명령 발화 전달

`POST /api/app/voice/utterances` — STT 확정 문장과 현재 맵의 등록 공간 전체를 보내면 Communicator 판단 결과를 `data` 로 받는다.

### Request Header

| Header | 필수 | 값 |
|---|---|---|
| `IdToken` | Y | Cognito IdToken (JWT) |
| `Content-Type` | Y | `application/json` |

### Request Body

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `request_id` | String | Y | App 이 발급하는 요청 식별자. WAS·엔진 로그 상관관계 추적용 |
| `text` | String | Y | 호출어 + STT 확정 명령어. 예: `"하이나무 박종문방으로 이동해"` |
| `space_personalization` | Object | Y | 현재 맵의 등록 공간 정보 (발화에 언급된 공간만이 아니라 **전체 목록**) |
| `space_personalization.parent_entity` | String | - | Map ID 와 관계없이 `"<SPACE>"` 고정 |
| `space_personalization.child_entity_list` | Array | Y | 공간 목록. `null` 이면 실패, 빈 배열은 허용 |
| `child_entity_list[].child_entity` | String | - | 엔진 공간 슬롯 ID. `<SPACE_SPACE0>`(스테이션) ~ `<SPACE_SPACE7>` |
| `child_entity_list[].aliases` | Array\<String\> | - | 공간 이름·별칭 |

실제 공간 ID ↔ `<SPACE_SPACEn>` 슬롯 변환은 **App 이 담당**한다. WAS 는 값을 바꾸지 않고 그대로 엔진에 넘긴다. 필수 여부 `-` 항목은 WAS 가 검증하지 않는 필드다.

```json
{
  "request_id": "remote-test-1",
  "text": "하이나무 박종문방으로 이동해",
  "space_personalization": {
    "parent_entity": "<SPACE>",
    "child_entity_list": [
      { "child_entity": "<SPACE_SPACE1>", "aliases": ["박종문방"] },
      { "child_entity": "<SPACE_SPACE2>", "aliases": ["거실"] }
    ]
  }
}
```

### Response

HTTP 200. 응답은 WAS 공통 봉투(`result`·`message`·`success`·`error`·`data`)이며, 성공 시 `data` = 2장 Communicator 응답 바디 원문이다(필드 정의는 2장 참고).

```json
{
  "result": 200,
  "message": "OK",
  "success": true,
  "data": {
    "schema_version": 1,
    "event": "utterance_result",
    "input_text": "박종문방으로 이동해",
    "decision": {
      "type": "execute",
      "function": "공간이동",
      "ask_type": null,
      "fallback_reason": null,
      "noop_reason": null,
      "spaces": [ { "space_id": "<SPACE_SPACE1>", "display_name": "박종문방" } ],
      "unknown_spaces": []
    },
    "reply": { "text": "박종문방으로 이동할게요.", "action": "speak" }
  }
}
```

`data` 내부 `null` 값은 빠지지 않고 엔진이 준 대로 `null` 로 내려간다(NON_NULL 은 봉투 최상위에만 적용).

## 2. WAS → Communicator : 발화 판단 요청

`POST {app.communicator.api.url}/v1/communicator/utterances` — WAS 는 App 이 보낸 바디를 그대로 보내고, 엔진의 `utterance_result` 를 받아 App 에 그대로 내려준다.

### Request Header

| Header | 값 |
|---|---|
| `Content-Type` | `application/json` |
| `Accept` | `application/json, application/*+json` |
| 인증 헤더 | 없음 (WAS 의 Oasys Basic 인증 인터셉터는 Oasys URL 에만 적용) |

### Request Body

1장 Request Body 와 필드·키(snake_case)가 같다. WAS 는 값을 추가·변환하지 않지만 모델로 역직렬화한 뒤 다시 직렬화하므로, **1장에 없는 필드를 App 이 보내면 엔진까지 가지 않는다.**

### Response Body (`event = utterance_result`)

| 필드 | 타입 | 설명 |
|---|---|---|
| `schema_version` | Integer | 현재 `1` |
| `event` | String | `utterance_result` |
| `input_text` | String | 엔진이 처리한 명령어. 정상 입력이면 호출어가 제거되어 있음 |
| `decision.type` | String | `execute`, `fallback`, `defer`, `cancel`, `noop`, `silent_ignore`, `silent_observe` |
| `decision.function` | String | 이동 실행 시 `"공간이동"` |
| `decision.ask_type` | String | 모바일 이동 모드에서는 `null` |
| `decision.fallback_reason` | String | `unsupported_request`, `unknown_space`, `multiple_spaces` |
| `decision.noop_reason` | String | `already_active`, `already_starting` |
| `decision.spaces[]` | Array | 목적지 목록. 없으면 빈 배열 |
| `decision.spaces[].space_id` | String | 요청의 `child_entity` 와 같은 슬롯 ID. App 이 실제 공간 ID 로 변환 |
| `decision.spaces[].display_name` | String | 표시용 공간명 |
| `decision.unknown_spaces[]` | Array\<String\> | 미등록으로 판정한 공간명 |
| `reply.text` | String | 안내 문구 |
| `reply.action` | String | `speak` TTS 수행 / `none` TTS 없음 |

`decision.type = execute` 는 이동 요청에 대한 **판단**일 뿐 기기 이동 완료가 아니다. `fallback` 등 실행하지 않는 판단도 엔진의 정상 결과이므로 WAS 는 `success: true` 로 내려준다.

```json
// 미등록 공간 fallback 예시 (정상 응답)
{
  "schema_version": 1,
  "event": "utterance_result",
  "input_text": "회의실로 이동해",
  "decision": {
    "type": "fallback", "function": null, "ask_type": null,
    "fallback_reason": "unknown_space", "noop_reason": null,
    "spaces": [], "unknown_spaces": ["회의실"]
  },
  "reply": { "text": "음성을 이해하지 못했어요. 다시 시도해 주세요.", "action": "speak" }
}
```

엔진이 위 표에 없는 필드를 추가하면 WAS 모델(`UtteranceResult`)에도 추가해야 App 까지 전달된다. 모르는 필드는 오류 없이 버려진다.

## 3. 에러 코드 및 타임아웃·재시도

엔진 호출 실패는 모두 HTTP 200 + `success: false` + `VOIC` 코드로 내려가고, 토큰·JSON 형식 오류만 HTTP 500 으로 내려간다.

| 코드 | HTTP | 발생 조건 | message |
|---|---|---|---|
| `VOIC0001` | 200 | `request_id`·`text` 가 비었거나, `space_personalization` 또는 `child_entity_list` 가 `null` (엔진 호출 안 함) | 음성 명령 필수 입력값이 누락되었습니다. |
| `VOIC0002` | 200 | 엔진 연결 실패, 응답 타임아웃, WAS 커넥션 풀 대기 초과 | 음성 명령 처리 서버에 연결할 수 없거나 응답이 지연되었습니다. |
| `VOIC0003` | 200 | 엔진 4xx·5xx 응답, 빈 응답·`decision` 없음, JSON 아닌 응답, WAS 에 엔진 주소 미설정 | 음성 명령 처리 중 오류가 발생했습니다. |
| (없음) | 500 | `IdToken` 누락·위조·만료, 요청 바디 JSON 파싱 실패 | 바디 `{"result":500,"message":"INTERNAL_SERVER_ERROR"}` |

### 타임아웃·재시도 (WAS → Communicator)

| 항목 | 값 | 비고 |
|---|---|---|
| 연결 타임아웃 | 5초 | WAS 공용값 |
| 커넥션 풀 대기 | 5초 | 풀 최대 100, 대상 호스트당 20 |
| 응답 타임아웃 | 30초 | `app.communicator.api.response-timeout-ms` (엔진이 다단계 처리라 공용 10초보다 길게) |
| 자동 재시도 | 없음 | `request_id` 한 건이 엔진에 두 번 들어가지 않도록 끔 |

WAS 가 한 요청에 최대 약 40초(5 + 5 + 30)까지 붙잡힐 수 있으므로, App 의 HTTP 타임아웃은 이보다 길게 잡는 것을 권장한다. 재시도는 App 판단이며, 재시도 시 새 `request_id` 를 쓸지는 미정(협의 필요)이다.
