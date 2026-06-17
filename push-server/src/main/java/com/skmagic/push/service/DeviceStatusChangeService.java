package com.skmagic.push.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.skmagic.push.mapper.BuildingMemberMapper;
import com.skmagic.push.push.WebSocketPusher;
import com.skmagic.push.store.SubscriptionStore;
import com.skmagic.push.topic.TopicHandler;
import com.skmagic.push.topic.TopicHandlerRegistry;
import com.skmagic.push.topic.TopicMessage;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Kinesis 레코드 → 토픽별 로직 → 변경 감지 → push 파이프라인의 핵심 로직.
 *
 * <p>흐름:
 * <ol>
 *     <li>레코드에서 MQTT 토픽({@code skmg/airbot/{serial}/v1/status})과 serial 을 추출한다.</li>
 *     <li>토픽에 맞는 {@link TopicHandler} 로 데이터를 가공한다. (토픽별 로직)</li>
 *     <li>가공 결과를 직전 상태와 비교해 변경점이 있을 때만 push 한다. (공통)</li>
 * </ol>
 *
 * <p>토픽별 로직은 {@link TopicHandler} 구현체를 추가해 확장한다. 변경 감지/push 는 모든 토픽 공통이다.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DeviceStatusChangeService {

    /** 변경 비교 시 무시할 필드 (식별자/타임스탬프 등 매번 바뀌는 값). 스키마 확정 시 조정. */
    private static final List<String> VOLATILE_FIELDS = List.of(
            "timestamp", "ts", "eventTime", "receivedAt", "messageId", "requestId"
    );

    /** 레코드에서 MQTT 토픽이 담기는 후보 필드명 (IoT Rule 의 {@code topic() as topic} 등). */
    private static final String[] TOPIC_FIELDS = {"topic", "mqttTopic", "_topic"};

    private final ObjectMapper objectMapper;
    private final SubscriptionStore subscriptionStore;
    private final WebSocketPusher pusher;
    private final TopicHandlerRegistry topicHandlerRegistry;
    private final BuildingMemberMapper buildingMemberMapper;

    /**
     * Kinesis 레코드 1건을 처리한다. 토픽별 로직 수행 후 변경점이 있을 때만 구독자에게 push 한다.
     *
     * @param source     레코드를 받은 Kinesis 스트림 이름 (로깅용)
     * @param recordJson 레코드 본문(UTF-8 디코딩된 JSON 문자열)
     */
    public void handleRecord(String source, String recordJson) {
        if (recordJson == null || recordJson.isBlank()) {
            log.debug("[change] 빈 레코드 (스킵) source={}", source);
            return;
        }
        log.debug("[change] 레코드 수신 source={}, len={}", source, recordJson.length());

        final JsonNode root;
        try {
            root = objectMapper.readTree(recordJson);
        } catch (Exception e) {
            log.warn("[change] JSON 파싱 실패 (스킵): {}", e.getMessage());
            return;
        }

        String topic = extractTopic(root);
        if (topic == null || topic.isBlank()) {
            log.debug("[change] topic 없음 (스킵) source={}", source);
            return;
        }

        String serial = extractSerial(topic, root);
        if (serial == null || serial.isBlank()) {
            log.debug("[change] serial 없음 (스킵) topic={}", topic);
            return;
        }

        // 토픽에는 serial 만 있으므로, DB 매핑(serial → 건물 멤버 loginId)으로 push 대상 사용자를 찾고
        // 각 사용자의 활성 WebSocket 연결(connectionId)을 수집한다. 연결된 멤버가 없으면
        // 가공/비교 비용을 들이지 않고 종료한다.
        Map<String, String> connectionToLoginId = resolveConnections(serial);
        if (connectionToLoginId.isEmpty()) {
            log.debug("[change] 활성 연결 없음 (push 생략) topic={}, serial={}", topic, serial);
            return;
        }

        // 토픽별 로직 수행 (데이터 변환/가공)
        final JsonNode processed;
        try {
            TopicHandler handler = topicHandlerRegistry.resolve(topic);
            processed = handler.transform(new TopicMessage(topic, serial, source, root));
        } catch (Exception e) {
            log.warn("[change] 토픽 로직 처리 실패 (스킵) topic={}, serial={}, err={}", topic, serial, e.getMessage());
            return;
        }
        if (processed == null) {
            // 핸들러가 push 불필요로 판단
            log.debug("[change] 핸들러 push 불필요 판단 (스킵) topic={}, serial={}", topic, serial);
            return;
        }

        String comparable = buildComparable(processed);
        String previous = subscriptionStore.getLastState(topic, serial);

        if (comparable.equals(previous)) {
            log.debug("[change] 변경점 없음 (push 생략) topic={}, serial={}, connections={}", topic, serial, connectionToLoginId.size());
            return; // 변경점 없음
        }
        log.debug("[change] 변경 감지 topic={}, serial={}", topic, serial);

        // 변경 감지 → 마지막 상태 갱신 후 push
        subscriptionStore.setLastState(topic, serial, comparable);

        String payload = buildPushPayload(topic, serial, processed);
        pusher.push(serial, connectionToLoginId, payload);
        log.info("[change] push 완료 topic={}, serial={}, connections={}", topic, serial, connectionToLoginId.size());
    }

    /**
     * serial 에 매핑된 건물 멤버(loginId) 들의 활성 WebSocket 연결을 수집한다.
     *
     * @return connectionId → 소유자 loginId 매핑 (연결이 없으면 빈 맵)
     */
    private Map<String, String> resolveConnections(String serial) {
        List<String> loginIds = buildingMemberMapper.getBuildingMemberLoginIdListBySerial(serial);
        if (loginIds == null || loginIds.isEmpty()) {
            log.debug("[change] serial → 멤버 매핑 없음 serial={}", serial);
            return Map.of();
        }
        Map<String, String> connectionToLoginId = new HashMap<>();
        for (String loginId : loginIds) {
            if (loginId == null || loginId.isBlank()) {
                continue;
            }
            Set<String> connections = subscriptionStore.getConnectionsByLoginId(loginId);
            for (String connectionId : connections) {
                connectionToLoginId.put(connectionId, loginId);
            }
        }
        log.debug("[change] 연결 해소 serial={}, members={}, connections={}", serial, loginIds.size(), connectionToLoginId.size());
        return connectionToLoginId;
    }

    /**
     * 레코드에서 MQTT 토픽을 추출한다. (IoT Rule 이 topic 을 레코드에 주입했다고 가정)
     */
    private String extractTopic(JsonNode root) {
        for (String candidate : TOPIC_FIELDS) {
            if (root.hasNonNull(candidate)) {
                return root.get(candidate).asText();
            }
        }
        return null;
    }

    /**
     * 디바이스 serial 을 추출한다.
     * 우선 토픽 경로({@code skmg/airbot/{serial}/...})에서 추출하고, 없으면 페이로드 필드로 폴백한다.
     */
    private String extractSerial(String topic, JsonNode root) {
        String fromTopic = extractSerialFromTopic(topic);
        if (fromTopic != null && !fromTopic.isBlank()) {
            return fromTopic;
        }
        if (root.hasNonNull("serial")) {
            return root.get("serial").asText();
        }
        for (String candidate : new String[] {"deviceSerial", "deviceId", "thingName"}) {
            if (root.hasNonNull(candidate)) {
                return root.get(candidate).asText();
            }
        }
        return null;
    }

    /**
     * {@code skmg/airbot/{serial}/v1/status} 형태의 토픽 경로에서 serial 세그먼트를 추출한다.
     */
    private String extractSerialFromTopic(String topic) {
        if (topic == null) {
            return null;
        }
        String[] parts = topic.split("/");
        if (parts.length >= 3 && "skmg".equals(parts[0]) && "airbot".equals(parts[1])) {
            return parts[2];
        }
        return null;
    }

    /**
     * 변경 비교에 사용할 정규화된 문자열을 만든다. 휘발성 필드를 제거한 JSON 의 문자열 표현.
     */
    private String buildComparable(JsonNode root) {
        try {
            if (root.isObject()) {
                ObjectNode copy = ((ObjectNode) root).deepCopy();
                for (String f : VOLATILE_FIELDS) {
                    copy.remove(f);
                }
                return objectMapper.writeValueAsString(copy);
            }
            return objectMapper.writeValueAsString(root);
        } catch (Exception e) {
            // 직렬화 실패 시 원본 문자열로 대체
            return root.toString();
        }
    }

    /**
     * 클라이언트로 보낼 push payload 를 구성한다.
     */
    private String buildPushPayload(String topic, String serial, JsonNode root) {
        try {
            ObjectNode out = objectMapper.createObjectNode();
            out.put("type", "deviceStatus");
            out.put("topic", topic);
            out.put("serial", serial);
            out.set("data", root);
            return objectMapper.writeValueAsString(out);
        } catch (Exception e) {
            return root.toString();
        }
    }
}
