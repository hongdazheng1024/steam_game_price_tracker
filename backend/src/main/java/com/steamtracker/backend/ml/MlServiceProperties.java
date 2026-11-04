package com.steamtracker.backend.ml;

import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("ml-service")
public record MlServiceProperties(String baseUrl, Duration connectTimeout, Duration readTimeout) {
}
