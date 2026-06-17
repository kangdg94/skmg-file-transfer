package com.skmagic.push.topic;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

/**
 * 전용 핸들러가 없는 토픽을 위한 기본 핸들러. 원본 데이터를 가공 없이 그대로 전달한다.
 * 항상 마지막 순위로 평가되도록 가장 낮은 우선순위를 갖는다.
 */
@Component
@Order(Ordered.LOWEST_PRECEDENCE)
public class DefaultTopicHandler implements TopicHandler {

    @Override
    public boolean supports(String topic) {
        return true;
    }

    @Override
    public JsonNode transform(TopicMessage message) {
        return message.raw();
    }
}
