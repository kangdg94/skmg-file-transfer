package com.skmagic.push.kinesis;

import com.skmagic.push.config.PushProperties;
import com.skmagic.push.service.DeviceStatusChangeService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.SmartLifecycle;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.services.cloudwatch.CloudWatchAsyncClient;
import software.amazon.awssdk.services.dynamodb.DynamoDbAsyncClient;
import software.amazon.awssdk.services.kinesis.KinesisAsyncClient;
import software.amazon.kinesis.common.ConfigsBuilder;
import software.amazon.kinesis.common.InitialPositionInStream;
import software.amazon.kinesis.common.InitialPositionInStreamExtended;
import software.amazon.kinesis.coordinator.Scheduler;
import software.amazon.kinesis.retrieval.polling.PollingConfig;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * 애플리케이션 생명주기에 맞춰 스트림(토픽)마다 KCL {@link Scheduler} 를 별도 스레드에서 기동/종료한다.
 * 스트림별로 KCL 애플리케이션 이름이 분리되므로 DynamoDB 리스 테이블/체크포인트도 독립적이다.
 */
@Slf4j
@Component
public class KinesisConsumerRunner implements SmartLifecycle {

    private final PushProperties props;
    private final KinesisAsyncClient kinesisClient;
    private final DynamoDbAsyncClient dynamoDbClient;
    private final CloudWatchAsyncClient cloudWatchClient;
    private final DeviceStatusChangeService changeService;

    private final List<Scheduler> schedulers = new CopyOnWriteArrayList<>();
    private final List<Thread> schedulerThreads = new CopyOnWriteArrayList<>();
    private volatile boolean running = false;

    public KinesisConsumerRunner(PushProperties props,
                                 KinesisAsyncClient kinesisClient,
                                 DynamoDbAsyncClient dynamoDbClient,
                                 CloudWatchAsyncClient cloudWatchClient,
                                 DeviceStatusChangeService changeService) {
        this.props = props;
        this.kinesisClient = kinesisClient;
        this.dynamoDbClient = dynamoDbClient;
        this.cloudWatchClient = cloudWatchClient;
        this.changeService = changeService;
    }

    @Override
    public void start() {
        List<String> configured = props.getKinesis().getStreamNames();
        List<String> streamNames = configured == null ? List.of()
                : configured.stream()
                        .filter(s -> s != null && !s.isBlank())
                        .map(String::trim)
                        .distinct()
                        .toList();

        if (streamNames.isEmpty()) {
            log.warn("[kinesis] push.kinesis.stream-names 미설정 → 컨슈머를 시작하지 않습니다.");
            return;
        }

        for (String streamName : streamNames) {
            Scheduler scheduler = buildScheduler(streamName);
            Thread thread = new Thread(scheduler, "kcl-" + streamName);
            thread.setDaemon(true);
            thread.start();
            schedulers.add(scheduler);
            schedulerThreads.add(thread);
        }
        this.running = true;
        log.info("[kinesis] KCL 컨슈머 {}개 스트림 시작 streams={}", streamNames.size(), streamNames);
    }

    private Scheduler buildScheduler(String streamName) {
        // 스트림별로 애플리케이션 이름을 분리해 DynamoDB 리스 테이블/체크포인트가 충돌하지 않도록 한다.
        String appName = props.getKinesis().getApplicationName() + "-" + streamName;
        String workerId = appName + "-" + UUID.randomUUID();

        ConfigsBuilder configsBuilder = new ConfigsBuilder(
                streamName,
                appName,
                kinesisClient,
                dynamoDbClient,
                cloudWatchClient,
                workerId,
                new DeviceStatusRecordProcessorFactory(streamName, changeService)
        );

        InitialPositionInStream initialPosition =
                "TRIM_HORIZON".equalsIgnoreCase(props.getKinesis().getInitialPosition())
                        ? InitialPositionInStream.TRIM_HORIZON
                        : InitialPositionInStream.LATEST;

        return new Scheduler(
                configsBuilder.checkpointConfig(),
                configsBuilder.coordinatorConfig(),
                configsBuilder.leaseManagementConfig(),
                configsBuilder.lifecycleConfig(),
                configsBuilder.metricsConfig(),
                configsBuilder.processorConfig(),
                configsBuilder.retrievalConfig()
                        .initialPositionInStreamExtended(
                                InitialPositionInStreamExtended.newInitialPosition(initialPosition))
                        .retrievalSpecificConfig(new PollingConfig(streamName, kinesisClient))
        );
    }

    @Override
    public void stop() {
        if (schedulers.isEmpty()) {
            running = false;
            return;
        }
        log.info("[kinesis] KCL 컨슈머 종료 시작... count={}", schedulers.size());
        for (Scheduler scheduler : schedulers) {
            try {
                Future<Boolean> gracefulShutdown = scheduler.startGracefulShutdown();
                gracefulShutdown.get(20, TimeUnit.SECONDS);
            } catch (Exception e) {
                log.warn("[kinesis] graceful 종료 실패: {}", e.getMessage());
            }
        }
        for (Thread thread : schedulerThreads) {
            thread.interrupt();
        }
        schedulers.clear();
        schedulerThreads.clear();
        running = false;
        log.info("[kinesis] KCL 컨슈머 종료 완료");
    }

    @Override
    public boolean isRunning() {
        return running;
    }

    @Override
    public int getPhase() {
        // 다른 빈들이 준비된 뒤 마지막에 시작되도록 늦은 phase 사용
        return Integer.MAX_VALUE - 100;
    }
}
