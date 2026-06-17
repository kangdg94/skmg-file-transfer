package com.skmagic.push;

import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * Air-bot Push Server.
 *
 * <p>독립 실행형(ECS) 이벤트 기반 push 서버.
 * <ol>
 *     <li>Kinesis 스트림을 KCL 로 소비한다.</li>
 *     <li>레코드에서 디바이스 상태를 파싱하고, Redis 에 저장된 이전 상태와 비교해 변경점을 감지한다.</li>
 *     <li>변경이 있으면 토픽의 serial 을 DB(device → building → member)로 매핑해 대상 사용자(loginId)를 찾고,
 *         각 사용자의 활성 WebSocket 연결(connectionId)을 Redis 역인덱스에서 조회한다.</li>
 *     <li>API Gateway WebSocket(PostToConnection)으로 즉시 push 한다.</li>
 * </ol>
 *
 * <p>연결 정보(ws:user:{loginId}:connections)는 이 서버의 {@code /connect}/{@code /disconnect} HTTP 엔드포인트가
 * 기록/제거한다. API Gateway WebSocket 의 $connect/$disconnect 라우트가 이 엔드포인트를 호출하며,
 * 토큰 검증은 API Gateway(Authorizer)가 수행해 loginId 를 전달한다.
 */
@Slf4j
@ConfigurationPropertiesScan
@SpringBootApplication
public class PushServerApplication {

    public static void main(String[] args) {
        SpringApplication.run(PushServerApplication.class, args);
    }
}
