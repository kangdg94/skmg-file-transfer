package com.skmagic.push.kinesis;

import com.skmagic.push.service.DeviceStatusChangeService;
import lombok.RequiredArgsConstructor;
import software.amazon.kinesis.processor.ShardRecordProcessor;
import software.amazon.kinesis.processor.ShardRecordProcessorFactory;

/**
 * 샤드마다 {@link DeviceStatusRecordProcessor} 인스턴스를 생성하는 팩토리.
 * 스트림(토픽) 단위로 생성되므로 해당 스트림 이름을 프로세서에 전달한다.
 */
@RequiredArgsConstructor
public class DeviceStatusRecordProcessorFactory implements ShardRecordProcessorFactory {

    private final String streamName;
    private final DeviceStatusChangeService changeService;

    @Override
    public ShardRecordProcessor shardRecordProcessor() {
        return new DeviceStatusRecordProcessor(streamName, changeService);
    }
}
