package com.skmagic.push.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.AwsCredentialsProvider;
import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.apigatewaymanagementapi.ApiGatewayManagementApiClient;
import software.amazon.awssdk.services.apigatewaymanagementapi.ApiGatewayManagementApiClientBuilder;
import software.amazon.awssdk.services.cloudwatch.CloudWatchAsyncClient;
import software.amazon.awssdk.services.dynamodb.DynamoDbAsyncClient;
import software.amazon.awssdk.services.kinesis.KinesisAsyncClient;

import java.net.URI;

/**
 * AWS 클라이언트 빈 구성.
 *
 * <p>KCL 은 Kinesis(레코드 소비), DynamoDB(샤드 리스 관리), CloudWatch(메트릭) 세 가지 클라이언트를 필요로 한다.
 * 또한 서버→클라이언트 push 를 위해 API Gateway Management API 클라이언트를 구성한다.
 */
@Configuration
public class AwsConfig {

    private final PushProperties props;

    public AwsConfig(PushProperties props) {
        this.props = props;
    }

    private AwsCredentialsProvider credentialsProvider() {
        String ak = props.getAws().getAccessKeyId();
        String sk = props.getAws().getSecretAccessKey();
        if (ak != null && !ak.isBlank() && sk != null && !sk.isBlank()) {
            return StaticCredentialsProvider.create(AwsBasicCredentials.create(ak, sk));
        }
        // ECS Task Role / 환경변수 등에서 자동 탐색
        return DefaultCredentialsProvider.create();
    }

    private Region region() {
        return Region.of(props.getAws().getRegion());
    }

    @Bean
    public KinesisAsyncClient kinesisAsyncClient() {
        return KinesisAsyncClient.builder()
                .region(region())
                .credentialsProvider(credentialsProvider())
                .build();
    }

    @Bean
    public DynamoDbAsyncClient dynamoDbAsyncClient() {
        return DynamoDbAsyncClient.builder()
                .region(region())
                .credentialsProvider(credentialsProvider())
                .build();
    }

    @Bean
    public CloudWatchAsyncClient cloudWatchAsyncClient() {
        return CloudWatchAsyncClient.builder()
                .region(region())
                .credentialsProvider(credentialsProvider())
                .build();
    }

    /**
     * API Gateway WebSocket 으로 메시지를 push 하기 위한 클라이언트.
     * endpointOverride 에 WebSocket 관리 endpoint 를 지정해야 한다.
     */
    @Bean
    public ApiGatewayManagementApiClient apiGatewayManagementApiClient() {
        ApiGatewayManagementApiClientBuilder builder = ApiGatewayManagementApiClient.builder()
                .region(region())
                .credentialsProvider(credentialsProvider());
        String endpoint = props.getWebsocket().getEndpoint();
        if (endpoint != null && !endpoint.isBlank()) {
            builder.endpointOverride(URI.create(endpoint));
        }
        return builder.build();
    }
}
