package com.skmagic.push.topic;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * 토픽 핸들러에 전달되는 단일 메시지 컨텍스트.
 *
 * @param topic  레코드에서 추출한 MQTT 토픽 (예: {@code skmg/airbot/{serial}/v1/status})
 * @param serial 토픽/페이로드에서 추출한 디바이스 식별자
 * @param source 레코드를 받은 Kinesis 스트림 이름 (로깅/디버깅용)
 * @param raw    UTF-8 디코딩 후 파싱한 원본 레코드 JSON
 */
public record TopicMessage(String topic, String serial, String source, JsonNode raw) {
}
