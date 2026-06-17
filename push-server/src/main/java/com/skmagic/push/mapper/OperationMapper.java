package com.skmagic.push.mapper;

import com.skmagic.push.model.ProductDevice;
import org.apache.ibatis.annotations.Mapper;

import java.util.List;

/**
 * 운영(맵/AQM) 조회 매퍼(부분). backend-api 의 Operation.xml 와 동일 쿼리(id).
 */
@Mapper
public interface OperationMapper {

    String getOperationMapData(Long mapId);

    List<ProductDevice> getAqmList(Long mapId);
}
