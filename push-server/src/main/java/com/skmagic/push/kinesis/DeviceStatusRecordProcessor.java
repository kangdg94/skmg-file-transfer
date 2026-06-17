package com.skmagic.push.kinesis;

import com.skmagic.push.service.DeviceStatusChangeService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import software.amazon.kinesis.lifecycle.events.InitializationInput;
import software.amazon.kinesis.lifecycle.events.LeaseLostInput;
import software.amazon.kinesis.lifecycle.events.ProcessRecordsInput;
import software.amazon.kinesis.lifecycle.events.ShardEndedInput;
import software.amazon.kinesis.lifecycle.events.ShutdownRequestedInput;
import software.amazon.kinesis.processor.ShardRecordProcessor;
import software.amazon.kinesis.retrieval.KinesisClientRecord;

import java.nio.charset.StandardCharsets;

/**
 * KCL 샤드 레코드 프로세서. 샤드별로 인스턴스가 생성되며 레코드를 순차 처리한다.
 */
@Slf4j
@RequiredArgsConstructor
public class DeviceStatusRecordProcessor implements ShardRecordProcessor {

    private final String streamName;
    private final DeviceStatusChangeService changeService;

    private String shardId;

    @Override
    public void initialize(InitializationInput initializationInput) {
        this.shardId = initializationInput.shardId();
        log.info("[kinesis] 샤드 처리 시작 stream={}, shardId={}", streamName, shardId);
    }

    @Override
    public void processRecords(ProcessRecordsInput processRecordsInput) {
        for (KinesisClientRecord record : processRecordsInput.records()) {
            try {
                String json = StandardCharsets.UTF_8.decode(record.data()).toString();
                changeService.handleRecord(streamName, json);
            } catch (Exception e) {
                // 단일 레코드 실패가 샤드 전체 처리를 막지 않도록 격리
                log.warn("[kinesis] 레코드 처리 실패 shardId={}, err={}", shardId, e.getMessage());
            }
        }
        try {
            // 처리 완료 지점 체크포인트 (재시작 시 중복/유실 최소화)
            processRecordsInput.checkpointer().checkpoint();
        } catch (Exception e) {
            log.warn("[kinesis] 체크포인트 실패 shardId={}, err={}", shardId, e.getMessage());
        }
    }

    @Override
    public void leaseLost(LeaseLostInput leaseLostInput) {
        log.info("[kinesis] 리스 상실 shardId={}", shardId);
    }

    @Override
    public void shardEnded(ShardEndedInput shardEndedInput) {
        log.info("[kinesis] 샤드 종료 shardId={}", shardId);
        try {
            shardEndedInput.checkpointer().checkpoint();
        } catch (Exception e) {
            log.warn("[kinesis] 샤드 종료 체크포인트 실패 shardId={}, err={}", shardId, e.getMessage());
        }
    }

    @Override
    public void shutdownRequested(ShutdownRequestedInput shutdownRequestedInput) {
        log.info("[kinesis] 셧다운 요청 shardId={}", shardId);
        try {
            shutdownRequestedInput.checkpointer().checkpoint();
        } catch (Exception e) {
            log.warn("[kinesis] 셧다운 체크포인트 실패 shardId={}, err={}", shardId, e.getMessage());
        }
    }
}
