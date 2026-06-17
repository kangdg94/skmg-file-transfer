package com.skmagic.push.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.Getter;
import lombok.Setter;

import java.time.LocalDateTime;

/**
 * 디바이스 상태 응답 모델. backend-api 의 {@code com.skmagic.api.product.model.ProductDevice} 와
 * 동일한 필드 구성을 유지한다. (APP 이 REST 와 동일 구조로 수신하도록)
 */
@Getter
@Setter
@JsonInclude(JsonInclude.Include.NON_NULL)
public class ProductDevice {

	private Long id;
	private String loginId;
	private Long deviceId;
	private Long deviceMapId;
	private Long contractId;
	private Long buildingId;
	private Long mapId;
	private String buildingName;
	private Long modelId;
	private Long firmwareId;
	private Long lastFirmwareId;
	private Long categoryId;
	private String modelName;
	private String serial;
	private String contractNo;
	private String contractor;
	private String deviceName;
	private String deviceType;
	private String fileUrl;
	private String mapName;

	private String deviceOnOff;
	private String pm1Level;
	private String pm10Level;
	private String pm25Level;
	private String tvocsLevel;
	private String noxLevel;
	private String aqLevel;
	private Integer battery;
	private String chargingYn;
	private String chargeState;

	private String version;
	private String lastVersion;
	private String releaseYn;
	private String note;
	private String autoUpdateYn;
	private String areaId;
	private String aqmName;
	private String areaName;
	private String x;
	private String y;
	private String xImage;
	private String yImage;
	//private List<String> status;
	private String status;

	private String robotStatus;
	private String message;
	private String statusOnOff;
	private String statusCheck;

	private String percentage;
	private String filterReplacementDate;

	private String cleanModeCode;
	private String airFlowModeCode;

	private String scanningClean;

	private String isDocked;

	private String isLLM;
	private String installYn;
	private String isAqmCall;
	private String callReason;
	private String callType;

	private String AIModeTypeCode;
	private String isSchedule;
	private String scheduleTypeCode;
	private Long scheduleId;

	private String cleanStartTime;

	private String syncMode;

	private String errorCode;
	private String isNextArea;
	private String isVitalSign;
	private String isPaused;
	private String isNightMode;
	private String appYn;

	private LocalDateTime createDate;
	private LocalDateTime updateDate;

}
