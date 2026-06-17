package com.skmagic.push.topic;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * 토픽별 처리 로직(전략). 토픽마다 데이터 변환/가공 규칙이 다를 수 있으므로 구현체를 추가해 확장한다.
 *
 * <p>변경 감지 및 WebSocket push 는 공통 파이프라인이 담당하므로, 구현체는
 * "APP 으로 보낼 데이터(JSON)" 를 만드는 일에만 집중한다.
 *
 * <p>새 토픽 로직 추가 방법:
 * <ol>
 *     <li>{@link TopicHandler} 를 구현한 {@code @Component} 클래스를 만든다.</li>
 *     <li>{@link #supports(String)} 에서 처리할 토픽 패턴을 판별한다.</li>
 *     <li>{@link #transform(TopicMessage)} 에서 가공 로직을 구현하고 push 할 데이터 노드를 반환한다.</li>
 * </ol>
 */
public interface TopicHandler {

    /** 이 핸들러가 해당 토픽을 처리할 수 있는지 여부. */
    boolean supports(String topic);

    /**
     * 토픽별 로직을 수행하고 APP 으로 push 할 데이터 노드를 만든다.
     *
     * @return push 할 데이터 JSON. {@code null} 을 반환하면 이 레코드는 push 하지 않고 스킵한다.
     */
    JsonNode transform(TopicMessage message);
}
