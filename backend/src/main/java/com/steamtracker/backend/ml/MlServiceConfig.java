package com.steamtracker.backend.ml;

import java.net.http.HttpClient;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

@Configuration
@EnableConfigurationProperties(MlServiceProperties.class)
public class MlServiceConfig {

	@Bean
	RestClient mlRestClient(RestClient.Builder builder, MlServiceProperties properties) {
		// HTTP/1.1, since uvicorn doesn't speak HTTP/2 and the JDK client would otherwise try to upgrade
		HttpClient httpClient = HttpClient.newBuilder()
			.version(HttpClient.Version.HTTP_1_1)
			.connectTimeout(properties.connectTimeout())
			.build();
		JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(httpClient);
		requestFactory.setReadTimeout(properties.readTimeout());
		return builder.baseUrl(properties.baseUrl()).requestFactory(requestFactory).build();
	}

}
