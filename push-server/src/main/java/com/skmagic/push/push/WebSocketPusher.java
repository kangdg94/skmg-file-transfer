package com.skmagic.push.push;

import com.skmagic.push.store.SubscriptionStore;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.services.apigatewaymanagementapi.ApiGatewayManagementApiClient;
import software.amazon.awssdk.services.apigatewaymanagementapi.model.GoneException;
import software.amazon.awssdk.services.apigatewaymanagementapi.model.PostToConnectionRequest;

import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * API Gateway WebSocket(PostToConnection)을 통해 구독 클라이언트로 메시지를 push 한다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class WebSocketPusher {

    private final ApiGatewayManagementApiClient apiClient;
    private final SubscriptionStore subscriptionStore;

    /**
     * 해소된 (connectionId → 소유자 loginId) 매핑의 모든 연결로 payload 를 push 한다.
     * 이미 끊어진 연결(GoneException)은 해당 사용자 역인덱스에서 정리한다.
     *
     * @param serial              대상 디바이스 serial (로깅용)
     * @param connectionToLoginId push 대상 connectionId → 소유자 loginId
     * @param payloadJson         전송할 JSON payload
     */
    public void push(String serial, Map<String, String> connectionToLoginId, String payloadJson) {
        if (connectionToLoginId == null || connectionToLoginId.isEmpty()) {
            return;
        }
        for (Map.Entry<String, String> entry : connectionToLoginId.entrySet()) {
            send(serial, entry.getValue(), entry.getKey(), payloadJson);
        }
    }

    private void send(String serial, String loginId, String connectionId, String payloadJson) {
        try {
            apiClient.postToConnection(PostToConnectionRequest.builder()
                    .connectionId(connectionId)
                    .data(SdkBytes.fromString(payloadJson, StandardCharsets.UTF_8))
                    .build());
            log.debug("[push] 전송 성공 serial={}, loginId={}, connectionId={}", serial, loginId, connectionId);
        } catch (GoneException e) {
            // 클라이언트가 이미 연결을 종료함 → 사용자 역인덱스 정리
            log.info("[push] gone connection 정리 serial={}, loginId={}, connectionId={}", serial, loginId, connectionId);
            subscriptionStore.removeConnection(loginId, connectionId);
        } catch (Exception e) {
            log.warn("[push] 전송 실패 serial={}, loginId={}, connectionId={}, err={}", serial, loginId, connectionId, e.getMessage());
        }
    }
}
