package com.skmagic.push.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.ArrayList;
import java.util.List;

/**
 * push 서버 설정 프로퍼티 (prefix: {@code push}).
 */
@Getter
@Setter
@ConfigurationProperties(prefix = "push")
public class PushProperties {

    private final Aws aws = new Aws();
    private final Kinesis kinesis = new Kinesis();
    private final WebSocket websocket = new WebSocket();

    @Getter
    @Setter
    public static class Aws {
        /** AWS 리전 (예: ap-northeast-2) */
        private String region = "ap-northeast-2";
        /** 정적 자격증명. 비워두면 DefaultCredentialsProvider(IAM Role 등) 사용 */
        private String accessKeyId;
        private String secretAccessKey;
    }

    @Getter
    @Setter
    public static class Kinesis {
        /** 소비할 Kinesis 데이터 스트림 이름 목록 (콤마 구분 또는 YAML 리스트). 토픽마다 1개씩 지정한다. */
        private List<String> streamNames = new ArrayList<>();
        /** KCL 애플리케이션 이름 접두사. 스트림별로 "-{streamName}" 이 붙어 DynamoDB 리스 테이블이 분리된다. */
        private String applicationName = "air-bot-push-server";
        /** 최초 시작 위치: LATEST 또는 TRIM_HORIZON */
        private String initialPosition = "LATEST";
    }

    @Getter
    @Setter
    public static class WebSocket {
        /** API Gateway WebSocket 관리 endpoint (예: https://{api-id}.execute-api.{region}.amazonaws.com/{stage}) */
        private String endpoint;
    }
}
