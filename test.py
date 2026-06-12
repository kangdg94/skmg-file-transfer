각 토픽의 의미입니다. 토픽 끝자리(액션명) 기준으로 정리했습니다.

### 기기 상태/공통
| 토픽 | 의미 |
|------|------|
| started | 기기 부팅/시작 알림 |
| initStatus | 기기 초기 상태 정보 |
| error | **에러** 관련 (기기 오류 발생) |
| event | 기기 이벤트 발생 알림 |
| factoryReset | 공장 초기화 결과 |

### 공기질(Air Quality) 관련
| 토픽 | 의미 |
|------|------|
| aq_map | **공기질 맵** 데이터 (airbot) |
| aqmCall | **AQM**(공기질 측정) 호출 (aqm) |
| aqtarget | 공기질 목표값 설정 (aqm) |

### 청정/제어(airbot 로봇 제어)
| 토픽 | 의미 |
|------|------|
| statusClean | 청정 상태 |
| setClean | 청정 설정 결과 |
| cleanResult | 청정 결과 |
| control | 로봇 제어 응답 |
| moveTo | 특정 위치 이동 응답 |
| comeback | 복귀(충전대 등) 응답 |

### 맵/매핑
| 토픽 | 의미 |
|------|------|
| mapping | 맵 생성(슬램) 응답 |
| setMap | 맵 설정 응답 |
| mapUpload | 맵 업로드 응답 |
| getMapInformation | 맵 정보 조회 |
| getHomeInformation | 홈(건물) 정보 조회 |

### 스케줄
| 토픽 | 의미 |
|------|------|
| setSchedule | 스케줄 설정 응답 |
| getSchedule | 스케줄 조회 |
| toggleSchedule | 스케줄 on/off 토글 |
| getHoliday | 공휴일 정보 조회 |

### 설정(Config)
| 토픽 | 의미 |
|------|------|
| getConfig | 설정 조회 응답 |
| setConfig | 설정 변경 응답 |

### 외기/날씨(Outdoor / Weather)
| 토픽 | 의미 |
|------|------|
| setOutdoorWI | 외기 정보 설정 응답 |
| getOutdoorWI | 외기 정보 조회 |
| getLLMOutdoorWI | **LLM(AI) 기반** 외기 정보 조회 |
| getLLMWF | **LLM(AI) 기반** 날씨 예보 조회 |

### 펌웨어/FOTA(OTA 업데이트)
| 토픽 | 의미 |
|------|------|
| jobUpdate | AWS IoT Job 실행 상태(FOTA 진행/성공/실패) |
| otaEvent | OTA(펌웨어 업데이트) 이벤트 |
| getFirmwareVersion | 현재 펌웨어 버전 조회 |
| getLatestVersion | 최신 펌웨어 버전 조회 |

### 기타(데이터/로그/리소스)
| 토픽 | 의미 |
|------|------|
| getAQMList | AQM(공기질 측정기) 목록 조회 |
| getDeviceLogResponse | 기기 로그 조회 응답 |
| AILogData | AI 로그 데이터 |
| getS3URL | S3 업로드/다운로드 URL 발급 |

크게 보면 **기기 상태/이벤트 · 공기질 · 청정/로봇제어 · 맵 · 스케줄 · 설정 · 외기날씨 · 펌웨어(FOTA) · 데이터/로그** 영역으로 나뉩니다.