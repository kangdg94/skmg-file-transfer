package com.skmagic.push.topic;

import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 등록된 {@link TopicHandler} 들 중 토픽을 처리할 수 있는 핸들러를 찾아준다.
 *
 * <p>핸들러는 {@code @Order} 우선순위 순으로 평가되며, 가장 먼저 {@code supports} 를 만족하는 핸들러를 사용한다.
 * {@link DefaultTopicHandler} 가 항상 마지막에서 모든 토픽을 받으므로 최소 하나는 매칭된다.
 */
@Component
public class TopicHandlerRegistry {

    private final List<TopicHandler> handlers;

    public TopicHandlerRegistry(List<TopicHandler> handlers) {
        this.handlers = handlers;
    }

    public TopicHandler resolve(String topic) {
        for (TopicHandler handler : handlers) {
            if (handler.supports(topic)) {
                return handler;
            }
        }
        // DefaultTopicHandler 가 모든 토픽을 받으므로 정상적으로는 도달하지 않는다.
        throw new IllegalStateException("처리할 수 있는 TopicHandler 가 없습니다. topic=" + topic);
    }
}
