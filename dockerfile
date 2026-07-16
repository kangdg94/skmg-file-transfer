# syntax=docker/dockerfile:1

# Build stage
FROM amazoncorretto:21-alpine AS builder
WORKDIR /app

COPY gradlew ./
COPY gradle ./gradle
RUN chmod +x gradlew

COPY build.gradle settings.gradle ./
RUN --mount=type=cache,target=/root/.gradle \
    ./gradlew dependencies --no-daemon

COPY src ./src
RUN --mount=type=cache,target=/root/.gradle \
    ./gradlew clean bootJar -x test --no-daemon


# Layer extraction
FROM amazoncorretto:21-alpine AS extractor
WORKDIR /app

COPY --from=builder /app/build/libs/*.jar app.jar

RUN java -Djarmode=layertools \
    -jar app.jar \
    extract


# Datadog Java Agent download
FROM alpine:3.20 AS datadog-tracer

ARG DD_JAVA_AGENT_URL=https://dtdg.co/latest-java-tracer

RUN apk add --no-cache curl ca-certificates \
    && curl \
        --fail \
        --silent \
        --show-error \
        --location \
        "${DD_JAVA_AGENT_URL}" \
        --output /dd-java-agent.jar \
    && chmod 0444 /dd-java-agent.jar


# Runtime
FROM gcr.io/distroless/java21-debian12
WORKDIR /app

COPY --from=extractor /app/dependencies ./
COPY --from=extractor /app/spring-boot-loader ./
COPY --from=extractor /app/snapshot-dependencies ./
COPY --from=extractor /app/application ./

# Datadog Java Agent
COPY --from=datadog-tracer /dd-java-agent.jar /dd-java-agent.jar

USER nonroot

EXPOSE 8080

ENTRYPOINT ["java", "-javaagent:/dd-java-agent.jar", "--enable-native-access=ALL-UNNAMED", "-XX:InitialRAMPercentage=50", "-XX:MaxRAMPercentage=70", "-XX:+ExitOnOutOfMemoryError", "-Djava.security.egd=file:/dev/./urandom", "org.springframework.boot.loader.launch.JarLauncher"]