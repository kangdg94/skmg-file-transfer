package com.skmagic.push.mapper;

import com.skmagic.push.model.DeviceConfig;
import com.skmagic.push.model.ProductDevice;
import org.apache.ibatis.annotations.Mapper;

import java.util.List;

/**
 * 디바이스 상태 가공에 필요한 조회 전용 매퍼. backend-api 의 동일 쿼리(id) 를 그대로 사용한다.
 */
@Mapper
public interface ProductDeviceMapper {

    String getCleanStartTime(String serial);

    String getChargeState(String serial);

    String getChargingYn(String serial);

    String getScheduleTypeCode(Long scheduleId);

    String getInstallYn(String serial);

    Long getDeviceIdBySerial(String serial);

    Long getMapIdBySerial(Long deviceId);

    List<String> getDeviceMapAreaIdByMapId(Long mapId);

    DeviceConfig getDeviceConfigMapper(String serial);
}
