package com.skmagic.push.config;

import jakarta.annotation.PostConstruct;
import lombok.extern.slf4j.Slf4j;
import nu.pattern.OpenCV;
import org.springframework.context.annotation.Configuration;

/**
 * OpenCV 네이티브 라이브러리 로더.
 *
 * <p>backend-api 는 플랫폼별 경로({@code opencv.library.path})로 {@code System.load} 하지만,
 * push-server 는 openpnp 배포본에 번들된 네이티브를 {@link OpenCV#loadLocally()} 로 추출/로딩한다.
 * (OS/아키텍처에 맞는 네이티브를 자동 선택하므로 별도 경로 설정이 필요 없다.)
 */
@Slf4j
@Configuration
public class OpenCvConfig {

    @PostConstruct
    public void loadNativeLibrary() {
        try {
            OpenCV.loadLocally();
            log.info("[opencv] 네이티브 라이브러리 로딩 완료");
        } catch (Throwable t) {
            // 로딩 실패 시 syncMode 계산만 비활성(스킵)되도록 두고 서버는 계속 기동한다.
            log.error("[opencv] 네이티브 라이브러리 로딩 실패: {}", t.getMessage());
        }
    }
}
