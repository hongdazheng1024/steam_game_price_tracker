package com.steamtracker.backend;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** The real application over HTTP, against a fake ML service that sends the contract's sample responses. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class GameSearchFullStackTest {

	private static final JsonMapper JSON = JsonMapper.builder().build();

	private static final HttpServer ML_SERVICE = startMlService();

	private static volatile String searchFixture;

	private static volatile int searchStatus;

	private static final AtomicInteger priceCalls = new AtomicInteger();

	@LocalServerPort
	private int port;

	private static HttpServer startMlService() {
		try {
			HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
			server.createContext("/games/search", (exchange) -> {
				if (searchStatus == 200) {
					respond(exchange, 200, ContractFixtures.read(searchFixture));
				}
				else {
					respond(exchange, searchStatus, "{\"detail\": \"Search route failed: internal-host\"}");
				}
			});
			server.createContext("/prices", (exchange) -> {
				priceCalls.incrementAndGet();
				respond(exchange, 200, ContractFixtures.read("prices"));
			});
			server.start();
			return server;
		}
		catch (IOException e) {
			throw new IllegalStateException(e);
		}
	}

	private static void respond(HttpExchange exchange, int status, String body) throws IOException {
		byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
		exchange.getResponseHeaders().set("Content-Type", "application/json");
		exchange.sendResponseHeaders(status, bytes.length);
		exchange.getResponseBody().write(bytes);
		exchange.close();
	}

	@DynamicPropertySource
	static void mlServiceUrl(DynamicPropertyRegistry registry) {
		registry.add("ml-service.base-url", () -> "http://localhost:" + ML_SERVICE.getAddress().getPort());
	}

	@AfterAll
	static void stopMlService() {
		ML_SERVICE.stop(0);
	}

	@BeforeEach
	void reset() {
		searchStatus = 200;
		priceCalls.set(0);
	}

	private HttpResponse<String> get(String path) throws Exception {
		try (HttpClient client = HttpClient.newHttpClient()) {
			HttpRequest request = HttpRequest.newBuilder(URI.create("http://localhost:" + this.port + path)).build();
			return client.send(request, HttpResponse.BodyHandlers.ofString());
		}
	}

	@Test
	void aMatch() throws Exception {
		searchFixture = "search-match";
		JsonNode body = JSON.readTree(get("/api/v1/games/search?query=prey").body());

		assertThat(body.get("canSearchOnline").asBoolean()).isTrue();
		JsonNode result = body.get("results").get(0);
		assertThat(result.get("title").asString()).isEqualTo("Prey");
		assertThat(result.get("priceStatus").asString()).isEqualTo("AVAILABLE");
		assertThat(result.get("price").get("currency").asString()).isEqualTo("USD");
		assertThat(result.get("price").get("current").decimalValue()).isEqualByComparingTo("5.99");
		assertThat(result.get("price").get("regular").decimalValue()).isEqualByComparingTo("29.99");
		assertThat(result.get("price").get("discountPercent").asInt()).isEqualTo(80);
		assertThat(result.get("price").get("historicalLow").decimalValue()).isEqualByComparingTo("2.99");
		assertThat(priceCalls).hasValue(0);
	}

	@Test
	void choicesAreCombinedWithTheirPrices() throws Exception {
		searchFixture = "search-choices";
		JsonNode results = JSON.readTree(get("/api/v1/games/search?query=prey").body()).get("results");

		assertThat(priceCalls).hasValue(1);
		assertThat(results).hasSize(3);
		assertThat(results.get(0).get("priceStatus").asString()).isEqualTo("AVAILABLE");
		assertThat(results.get(1).get("priceStatus").asString()).isEqualTo("NOT_ON_STEAM");
		assertThat(results.get(2).get("priceStatus").asString()).isEqualTo("UNAVAILABLE");
	}

	@Test
	void notFoundIsAnEmptyList() throws Exception {
		searchFixture = "search-not-found";
		HttpResponse<String> response = get("/api/v1/games/search?query=zzqqxx");
		assertThat(response.statusCode()).isEqualTo(200);
		assertThat(JSON.readTree(response.body()).get("results")).isEmpty();
	}

	@Test
	void itsBadGatewayIsA502WithoutItsDetails() throws Exception {
		searchStatus = 502;
		HttpResponse<String> response = get("/api/v1/games/search?query=prey");
		assertThat(response.statusCode()).isEqualTo(502);
		assertThat(response.headers().firstValue("Content-Type")).hasValue("application/problem+json");
		assertThat(response.body()).doesNotContain("internal-host");
	}

	@Test
	void aBadRequestNeverReachesTheMlService() throws Exception {
		searchStatus = 500;
		assertThat(get("/api/v1/games/search?query=prey&country=XX").statusCode()).isEqualTo(400);
	}

	@Test
	void healthIsExposed() throws Exception {
		HttpResponse<String> response = get("/actuator/health");
		assertThat(response.statusCode()).isEqualTo(200);
		assertThat(JSON.readTree(response.body()).get("status").asString()).isEqualTo("UP");
	}

}
