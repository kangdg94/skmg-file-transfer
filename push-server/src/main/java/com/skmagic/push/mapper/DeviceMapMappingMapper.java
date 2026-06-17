package com.skmagic.push.mapper;

import org.apache.ibatis.annotations.Mapper;

/**
 * 디바이스 매핑 조회 매퍼(부분). backend-api 의 DeviceMapMapping.xml getDeviceType 와 동일.
 */
@Mapper
public interface DeviceMapMappingMapper {

    String getDeviceType(String serial);
}
