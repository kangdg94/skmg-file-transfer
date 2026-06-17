package com.skmagic.push.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.Getter;
import lombok.Setter;

/**
 * 디바이스 설정값 모델. backend-api 의 {@code com.skmagic.api.product.model.DeviceConfig} 와 동일.
 */
@Getter
@Setter
@JsonInclude(JsonInclude.Include.NON_NULL)
public class DeviceConfig {
    private String homeLockYn;
    private String privacyLockYn;
    private String nightModeYn;
    private String autoCallYn;
    private String settingYn;
}
