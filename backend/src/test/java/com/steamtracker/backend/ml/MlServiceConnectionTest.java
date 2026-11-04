package com.steamtracker.backend.ml;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;

import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClient;

import com.sun.net.httpserver.HttpServer;

/** Over real sockets, since these failures come from the HTTP client. */
class MlServiceConnectionTest {

	private MlServiceClient client(int port, Duration readTimeout) {
		MlServiceProperties properties = new MlServiceProperties("http://localhost:" + port, Duration.ofSeconds(1),
				readTimeout);
		return new MlServiceClient(new MlServiceConfig().mlRestClient(RestClient.builder(), properties));
	}

	@Test
	void aServiceThatIsDownIsUnavailable() throws IOException {
		int freePort;
		try (ServerSocket socket = new ServerSocket(0)) {
			freePort = socket.getLocalPort();
		}
		MlServiceClient client = client(freePort, Duration.ofSeconds(1));
		assertThatThrownBy(() -> client.searchGames("prey", "US", false))
			.isInstanceOf(MlServiceException.Unavailable.class);
	}

	@Test
	void aServiceThatDoesntAnswerInTimeIsUnavailable() throws IOException {
		CountDownLatch finished = new CountDownLatch(1);
		HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
		ExecutorService executor = Executors.newSingleThreadExecutor();
		server.setExecutor(executor);
		server.createContext("/", exchange -> {
			try {
				finished.await();
			}
			catch (InterruptedException e) {
				Thread.currentThread().interrupt();
			}
			exchange.close();
		});
		server.start();
		try {
			MlServiceClient client = client(server.getAddress().getPort(), Duration.ofMillis(200));
			assertThatThrownBy(() -> client.searchGames("prey", "US", false))
				.isInstanceOf(MlServiceException.Unavailable.class);
		}
		finally {
			finished.countDown();
			server.stop(0);
			executor.shutdownNow();
		}
	}

}
