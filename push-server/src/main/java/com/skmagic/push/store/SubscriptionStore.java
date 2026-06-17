package com.skmagic.push.store;

import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.Collections;
import java.util.Set;

/**
 * WebSocket 연결 정보 + 디바이스 직전 상태를 저장하는 Redis 저장소(읽기/쓰기).
 *
 * <p>토픽에는 serial 만 있고 WebSocket 연결에는 serial 정보가 없다(앱은 $connect 시 토큰으로
 * 사용자만 식별된다). 따라서 연결 역인덱스는 <b>사용자(loginId) 기준</b>으로 관리한다.
 *
 * <p>키 규칙(이 서버의 {@code /connect}/{@code /disconnect} 핸들러가 기록/제거한다):
 * <ul>
 *     <li>{@code ws:user:{loginId}:connections} (Set) : 해당 사용자의 활성 connectionId 집합
 *         ($connect 핸들러가 기록, $disconnect 시 제거)</li>
 *     <li>{@code ws:conn:{connectionId}} (String) : connectionId → 소유자 loginId 역방향 인덱스
 *         ($disconnect 가 connectionId 만 알 때 소유자를 역추적하기 위함)</li>
 *     <li>{@code ws:laststate:{topic}:{serial}} (String) : 해당 토픽(스트림)에서 마지막으로 push 한
 *         상태 JSON (이 서버가 기록, 변경 감지에 사용)</li>
 * </ul>
 */
@Component
@RequiredArgsConstructor
public class SubscriptionStore {

    private static final String USER_PREFIX = "ws:user:";
    private static final String CONN_PREFIX = "ws:conn:";
    private static final String LAST_STATE_PREFIX = "ws:laststate:";
    private static final Duration LAST_STATE_TTL = Duration.ofHours(24);
    /** 연결 정보 TTL. $disconnect 누락 시 좀비 연결을 자동 만료시키는 안전장치. */
    private static final Duration CONNECTION_TTL = Duration.ofHours(12);

    private final StringRedisTemplate redis;

    private String userConnectionsKey(String loginId) {
        return USER_PREFIX + loginId + ":connections";
    }

    private String connOwnerKey(String connectionId) {
        return CONN_PREFIX + connectionId;
    }

    private String lastStateKey(String topic, String serial) {
        return LAST_STATE_PREFIX + topic + ":" + serial;
    }

    /**
     * 새 WebSocket 연결을 사용자(loginId) 역인덱스에 등록한다. ($connect)
     * connectionId → loginId 역방향 인덱스도 함께 기록한다.
     */
    public void addConnection(String loginId, String connectionId) {
        redis.opsForSet().add(userConnectionsKey(loginId), connectionId);
        redis.expire(userConnectionsKey(loginId), CONNECTION_TTL);
        redis.opsForValue().set(connOwnerKey(connectionId), loginId, CONNECTION_TTL);
    }

    /** 해당 사용자(loginId)의 활성 connectionId 집합을 반환한다. */
    public Set<String> getConnectionsByLoginId(String loginId) {
        Set<String> members = redis.opsForSet().members(userConnectionsKey(loginId));
        return members == null ? Collections.emptySet() : members;
    }

    /** 연결을 역인덱스에서 제거한다. (loginId 를 아는 경우: $disconnect / Gone 정리) */
    public void removeConnection(String loginId, String connectionId) {
        redis.opsForSet().remove(userConnectionsKey(loginId), connectionId);
        redis.delete(connOwnerKey(connectionId));
    }

    /**
     * connectionId 만으로 연결을 제거한다. ($disconnect 에서 loginId 가 없을 때)
     * 역방향 인덱스로 소유자(loginId)를 찾아 정리한다.
     */
    public void removeConnectionById(String connectionId) {
        String loginId = redis.opsForValue().get(connOwnerKey(connectionId));
        if (loginId != null && !loginId.isBlank()) {
            redis.opsForSet().remove(userConnectionsKey(loginId), connectionId);
        }
        redis.delete(connOwnerKey(connectionId));
    }

    /** 해당 토픽에서 마지막으로 push 한 상태 JSON 을 반환한다. (없으면 null) */
    public String getLastState(String topic, String serial) {
        return redis.opsForValue().get(lastStateKey(topic, serial));
    }

    /** 해당 토픽의 마지막 상태 JSON 을 저장한다. */
    public void setLastState(String topic, String serial, String stateJson) {
        redis.opsForValue().set(lastStateKey(topic, serial), stateJson, LAST_STATE_TTL);
    }
}
