package com.skmagic.push.controller;

import com.skmagic.push.store.SubscriptionStore;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * API Gateway WebSocket 의 {@code $connect}/{@code $disconnect} 라우트를 받는 HTTP 엔드포인트.
 *
 * <p>토큰 검증은 API Gateway(Authorizer)가 이미 수행하므로 이 서버는 검증하지 않는다.
 * API Gateway 는 검증으로 식별한 {@code loginId}(authorizer context)와 {@code connectionId}
 * ({@code $context.connectionId})를 통합 요청에 실어 호출한다.
 *
 * <ul>
 *     <li>{@code POST /connect}    : 연결을 사용자(loginId) 역인덱스에 등록</li>
 *     <li>{@code POST /disconnect} : 연결을 역인덱스에서 제거</li>
 * </ul>
 */
@Slf4j
@RestController
@RequiredArgsConstructor
public class ConnectionController {

    private final SubscriptionStore subscriptionStore;

    /**
     * {@code $connect} 처리. API Gateway 가 검증한 loginId 와 connectionId 를 받아 등록한다.
     */
    @PostMapping("/connect")
    public ResponseEntity<String> connect(@RequestParam("connectionId") String connectionId,
                                          @RequestParam("loginId") String loginId) {
        if (!StringUtils.hasText(connectionId) || !StringUtils.hasText(loginId)) {
            log.warn("[connect] 필수값 누락 connectionId={}, loginId={}", connectionId, loginId);
            return ResponseEntity.badRequest().body("connectionId/loginId required");
        }
        subscriptionStore.addConnection(loginId, connectionId);
        log.info("[connect] 등록 loginId={}, connectionId={}", loginId, connectionId);
        return ResponseEntity.ok("OK");
    }

    /**
     * {@code $disconnect} 처리. connectionId 로 연결을 제거한다.
     * loginId 가 함께 전달되면 직접 제거하고, 없으면 역방향 인덱스로 소유자를 찾아 제거한다.
     */
    @PostMapping("/disconnect")
    public ResponseEntity<String> disconnect(@RequestParam("connectionId") String connectionId,
                                             @RequestParam(value = "loginId", required = false) String loginId) {
        if (!StringUtils.hasText(connectionId)) {
            log.warn("[disconnect] connectionId 누락");
            return ResponseEntity.badRequest().body("connectionId required");
        }
        if (StringUtils.hasText(loginId)) {
            subscriptionStore.removeConnection(loginId, connectionId);
        } else {
            subscriptionStore.removeConnectionById(connectionId);
        }
        log.info("[disconnect] 제거 loginId={}, connectionId={}", loginId, connectionId);
        return ResponseEntity.ok("OK");
    }
}
