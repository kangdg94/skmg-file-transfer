package com.skmagic.push.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.google.gson.Gson;
import com.skmagic.push.mapper.DeviceMapMappingMapper;
import com.skmagic.push.mapper.OperationMapper;
import com.skmagic.push.mapper.ProductDeviceMapper;
import com.skmagic.push.model.DeviceConfig;
import com.skmagic.push.model.ProductDevice;
import com.skmagic.push.util.RoomChecker;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * backend-api 의 {@code ProductDeviceService.productDeviceStatus()} 가공 로직을 push 서버로 포팅한 것.
 *
 * <p>차이점: 원본은 Redis {@code operation:{serial}} 에서 상태 JSON 을 읽지만,
 * push 서버는 Kinesis 레코드로 들어온 상태 JSON 을 직접 가공한다. (DB 보강/Redis power 조회는 동일)
 *
 * <p>결과 {@link ProductDevice} 필드 구성은 REST 응답과 동일하게 유지한다.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class ProductDeviceStatusService {

    private final ProductDeviceMapper productDeviceMapper;
    private final DeviceMapMappingMapper deviceMapMappingMapper;
    private final OperationMapper operationMapper;
    private final StringRedisTemplate stringRedisTemplate;

    /**
     * 디바이스 상태 값 가공 (APP 전용 응답과 동일 구조).
     *
     * @param serial   디바이스 시리얼
     * @param jsonNode Kinesis 레코드로 전달된 Airbot 상태 JSON
     * @return 가공된 {@link ProductDevice}
     */
    public ProductDevice buildStatus(String serial, JsonNode jsonNode) {

        ProductDevice productDevice = new ProductDevice();
        productDevice.setSerial(serial);

        if (jsonNode == null) {
            productDevice.setMessage("Operation Data is NULL");
            log.info("Operation Data is NULL");
            return productDevice;
        }

        String deviceType = deviceMapMappingMapper.getDeviceType(productDevice.getSerial());
        productDevice.setDeviceType(deviceType);

        // 조회한 Airbot 상태값 정보 설정
        if ("ABT".equals(deviceType)) {
            productDevice.setRobotStatus(jsonNode.get("robotStatus").asText()); // 로봇 상태

            // 디바이스 상태가 청정 중일 경우 청정 시작 시간 설정
            if (productDevice.getRobotStatus().equals("CLEANING")) {
                productDevice.setCleanStartTime(productDeviceMapper.getCleanStartTime(productDevice.getSerial()));
            }

            productDevice.setCleanModeCode(jsonNode.get("cleanType").asText());
            productDevice.setAirFlowModeCode(jsonNode.get("windType").asText());

            if (jsonNode.get("scanningClean") == null || jsonNode.get("scanningClean").asText().equals("OFF")) {
                productDevice.setScanningClean("N");
            } else {
                productDevice.setScanningClean("Y");
            }

            // 로봇 동작 상태가 에러 일 경우 에러코드 조회
            if (productDevice.getRobotStatus().equals("ERROR")) {
                if (jsonNode.get("errorCode") != null) {
                    productDevice.setErrorCode(jsonNode.get("errorCode").asText());
                } else {
                    productDevice.setErrorCode("");
                }
            }
        }

        productDevice.setChargeState(productDeviceMapper.getChargeState(productDevice.getSerial()));
        productDevice.setChargingYn(productDeviceMapper.getChargingYn(productDevice.getSerial()));

        // 도킹 여부
        if (jsonNode.get("isDocked") != null) {
            if (jsonNode.get("isDocked").asText().equals("true")) {
                productDevice.setIsDocked("Y");
            } else {
                productDevice.setIsDocked("N");
            }
        } else {
            productDevice.setIsDocked("N");
            log.warn("=== isDocked is NULL ===");
        }

        // LLM 여부
        if (jsonNode.get("isLLM") != null) {
            productDevice.setIsLLM("true".equals(jsonNode.get("isLLM").asText()) ? "Y" : "N");
        } else {
            productDevice.setIsLLM("N");
        }

        // AQM 청정 호출 여부 / 호출 사유
        productDevice.setIsAqmCall("N");

        // AQM 호출 청정 여부를 조회하여 AQM 호출 청정일 경우
        if (jsonNode.get("isAQMCall") != null) {
            if (jsonNode.get("isAQMCall").asText().equals("true")) {

                productDevice.setIsAqmCall("Y");

                // 자동 호출 일 경우 - 자동 호출 일 경우는 청정 수행
                if (jsonNode.get("callType") != null) {

                    productDevice.setCallType(jsonNode.get("callType").asText());

                    if (jsonNode.get("callType").asText().equals("AUTO")) {

                        // 자동 호출 사유
                        if (jsonNode.get("callReason") != null) {
                            productDevice.setCallReason(jsonNode.get("callReason").asText());
                        }
                    }
                }
            }
        }

        // AIModeTypeCode - AI 모드 동작 유형 코드
        if (jsonNode.get("AIModeType") != null) {
            productDevice.setAIModeTypeCode(jsonNode.get("AIModeType").asText());
        } else {
            productDevice.setAIModeTypeCode("AIMT0001");
        }

        // isSchedule - 스케줄 실행 여부
        if (jsonNode.get("isSchedule") != null) {
            if (jsonNode.get("isSchedule").asText().equals("true")) {
                productDevice.setIsSchedule("Y");
            } else {
                productDevice.setIsSchedule("N");
            }
        } else {
            productDevice.setIsSchedule("N");
        }

        // scheduleId - 스케줄 아이디
        if (jsonNode.get("scheduleID") != null) {
            productDevice.setScheduleId(jsonNode.get("scheduleID").asLong());

            // scheduleTypeCode - 스케줄 유형 코드
            productDevice.setScheduleTypeCode(productDeviceMapper.getScheduleTypeCode(productDevice.getScheduleId()));
        } else {
            productDevice.setScheduleId(0L);
        }

        // installYn - AQM 전체 공간 설치 유무
        productDevice.setInstallYn(productDeviceMapper.getInstallYn(productDevice.getSerial()));

        // 다음 공간 청정 공간 유무
        if (jsonNode.get("isNextArea") != null) {
            if (jsonNode.get("isNextArea").asText().equals("true")) {
                productDevice.setIsNextArea("Y");
            } else {
                productDevice.setIsNextArea("N");
            }
        } else {
            productDevice.setIsNextArea("N");
        }

        // 바이탈 사인 기능 동작 여부
        if (jsonNode.get("isVitalSign") != null) {
            if (jsonNode.get("isVitalSign").asText().equals("true")) {
                productDevice.setIsVitalSign("Y");
            } else {
                productDevice.setIsVitalSign("N");
            }
        } else {
            productDevice.setIsVitalSign("N");
        }

        // 기능 일시정지 여부
        if (jsonNode.get("isPaused") != null) {
            if (jsonNode.get("isPaused").asText().equals("true")) {
                productDevice.setIsPaused("Y");
            } else {
                productDevice.setIsPaused("N");
            }
        } else {
            productDevice.setIsPaused("N");
        }

        // 나이트모드 여부
        if (jsonNode.get("isNightMode") != null) {
            if (jsonNode.get("isNightMode").asText().equals("true")) {
                productDevice.setIsNightMode("Y");
            } else {
                productDevice.setIsNightMode("N");
            }
        }

        // SyncMode 상태 조회
        // 해당 디바이스의 ID 조회
        Long deviceId = productDeviceMapper.getDeviceIdBySerial(productDevice.getSerial());
        productDevice.setSyncMode("N");
        if (deviceId != null) {

            // 해당 디바이스의 mapId 조회
            Long mapId = productDeviceMapper.getMapIdBySerial(deviceId);
            if (mapId != null) {
                // syncmode 체크방식 변경 //2025_12_04 AXDO-25
                // 2. mapData 조회 (JSON 문자열)
                String mapData = operationMapper.getOperationMapData(mapId);
                Gson gson = new Gson();
                if (mapData != null) {
                    RoomChecker.RoomListJson data = gson.fromJson(mapData, RoomChecker.RoomListJson.class);
                    // 현재 로봇이 있는 방 찾기
                    String currentRoomId = RoomChecker.findFirstRoomIdContainingRobot(data);
                    // 다른 로봇이 있는 방 (assign_info)
                    List<String> assignedRoomIds = data.assign_info.get(productDevice.getSerial());
                    if (assignedRoomIds != null) {
                        // robot 방 제거
                        assignedRoomIds.remove(currentRoomId);
                        List<String> areaList = productDeviceMapper.getDeviceMapAreaIdByMapId(mapId);
                        areaList.forEach(assignedRoomIds::remove);
                        if (assignedRoomIds.size() > 0) {
                            productDevice.setSyncMode("N");
                        } else {
                            productDevice.setSyncMode("Y");
                            List<ProductDevice> aqmList = operationMapper.getAqmList(mapId);
                            if (aqmList == null || aqmList.isEmpty()) {
                                productDevice.setSyncMode("N");
                            } else {
                                for (ProductDevice aqmDevice : aqmList) {
                                    DeviceConfig deviceConfig = getDeviceConfig(aqmDevice.getSerial());
                                    if (null != deviceConfig && "N".equals(deviceConfig.getAutoCallYn())) {
                                        productDevice.setSyncMode("N");
                                    }
                                }
                            }
                        }
                    }
                }
            } else {
                productDevice.setMapId(0L);
            }

            // [수정 후] Redis 조회 방식 (DB 부하 제로!)
            // 1. DTO 객체에서 시리얼 번호를 꺼냅니다.
            String redisSerial = productDevice.getSerial();
            // 2. 시리얼 번호가 존재할 때만 Redis를 조회합니다.
            if (redisSerial != null) {
                String powerVal = stringRedisTemplate.opsForValue().get("power:" + redisSerial);

                // 3. Redis에 "ON" 값이 있으면 "on", 없거나 만료되었으면 "Off"를 세팅합니다.
                productDevice.setDeviceOnOff("ON".equals(powerVal) ? "on" : "Off");
            } else {
                // 시리얼 번호 자체가 없는 예외적인 경우 기본값 처리
                productDevice.setDeviceOnOff("Off");
            }
        }

        productDevice.setLoginId(null);
        productDevice.setSerial(null);

        return productDevice;
    }

    /**
     * 디바이스 설정값 조회. backend-api 의 {@code getDeviceConfig} 와 동일.
     */
    public DeviceConfig getDeviceConfig(String serial) {
        return productDeviceMapper.getDeviceConfigMapper(serial);
    }
}
