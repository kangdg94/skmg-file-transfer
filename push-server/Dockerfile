# ---- Build stage ----
FROM gradle:8.7-jdk17 AS build
WORKDIR /workspace

# 의존성 캐시 최적화: 빌드 스크립트 먼저 복사
COPY settings.gradle build.gradle ./
COPY gradle ./gradle
# (선택) 래퍼로 빌드하려면 gradlew 도 복사. 여기서는 이미지의 gradle 사용.

# 소스 복사 후 빌드
COPY src ./src
RUN gradle clean bootJar --no-daemon

# ---- Runtime stage ----
FROM eclipse-temurin:17-jre
WORKDIR /app

# - curl       : ECS healthCheck(actuator) 용
# - libgl/glib : OpenCV(openpnp) 네이티브 로딩에 필요한 시스템 라이브러리
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 비루트 사용자로 실행
RUN useradd -r -u 1001 appuser
COPY --from=build /workspace/build/libs/*.jar app.jar
USER appuser

# 활성 프로파일 기본값 (task-definition 의 SPRING_PROFILES_ACTIVE 로 override)
ENV SPRING_PROFILES_ACTIVE=dev

EXPOSE 8081
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75.0", "-jar", "/app/app.jar"]
