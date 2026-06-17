package com.skmagic.push.topic;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.skmagic.push.model.ProductDevice;
import com.skmagic.push.service.ProductDeviceStatusService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

/**
 * 디바이스 상태({@code .../status}) 토픽 전용 핸들러.
 *
 * <p>backend-api 의 {@code GET /api/app/product/device/status} 가공 로직을 그대로 수행하여,
 * Kinesis 로 들어온 raw 상태 JSON 을 REST 응답과 동일한 {@link ProductDevice} 구조로 변환한다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
@Order(Ordered.HIGHEST_PRECEDENCE)
public class StatusTopicHandler implements TopicHandler {

    private final ProductDeviceStatusService productDeviceStatusService;
    private final ObjectMapper objectMapper;

    @Override
    public boolean supports(String topic) {
        return topic != null && topic.endsWith("/status");
    }

    @Override
    public JsonNode transform(TopicMessage message) {
        ProductDevice productDevice = productDeviceStatusService.buildStatus(message.serial(), message.raw());
        return objectMapper.valueToTree(productDevice);
    }
}
