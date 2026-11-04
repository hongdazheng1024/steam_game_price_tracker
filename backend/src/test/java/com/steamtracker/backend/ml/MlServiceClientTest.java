package com.steamtracker.backend.ml;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

import java.math.BigDecimal;
import java.util.List;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import com.steamtracker.backend.ContractFixtures;
import com.steamtracker.backend.ml.MlModels.Choices;
import com.steamtracker.backend.ml.MlModels.Game;
import com.steamtracker.backend.ml.MlModels.NoPrice;
import com.steamtracker.backend.ml.MlModels.NotFound;
import com.steamtracker.backend.ml.MlModels.NotInCatalog;
import com.steamtracker.backend.ml.MlModels.PriceFailed;
import com.steamtracker.backend.ml.MlModels.PriceMatch;
import com.steamtracker.backend.ml.MlModels.PriceResult;

class MlServiceClientTest {

	private static final String SEARCH_URL = "http://ml/games/search?query=prey&country=US&search_online=false";

	private MockRestServiceServer server;

	private MlServiceClient client;

	@BeforeEach
	void setUp() {
		RestClient.Builder builder = RestClient.builder().baseUrl("http://ml");
		this.server = MockRestServiceServer.bindTo(builder).build();
		this.client = new MlServiceClient(builder.build());
	}

	private void respondToSearch(String fixture) {
		this.server.expect(requestTo(SEARCH_URL))
			.andRespond(withSuccess(ContractFixtures.read(fixture), MediaType.APPLICATION_JSON));
	}

	@Test
	void readsAMatch() {
		respondToSearch("search-match");
		PriceMatch match = (PriceMatch) this.client.searchGames("prey", "US", false);
		assertThat(match.title()).isEqualTo("Prey");
		assertThat(match.currentDeal().price().amount()).isEqualByComparingTo(new BigDecimal("5.99"));
		assertThat(match.currentDeal().price().currency()).isEqualTo("USD");
		assertThat(match.currentDeal().regular().amount()).isEqualByComparingTo(new BigDecimal("29.99"));
		assertThat(match.currentDeal().cut()).isEqualTo(80);
		assertThat(match.currentDeal().url()).startsWith("https://itad.link/");
		assertThat(match.historicalLowest().price().amount()).isEqualByComparingTo(new BigDecimal("2.99"));
	}

	@Test
	void readsNoPrice() {
		respondToSearch("search-no-price");
		assertThat(this.client.searchGames("prey", "US", false)).isInstanceOf(NoPrice.class);
	}

	@Test
	void readsChoices() {
		respondToSearch("search-choices");
		Choices choices = (Choices) this.client.searchGames("prey", "US", false);
		assertThat(choices.source()).isEqualTo(Choices.SOURCE_CATALOG);
		assertThat(choices.choices()).hasSize(3).first().extracting(Game::title).isEqualTo("Prey");
	}

	@Test
	void readsNotFound() {
		respondToSearch("search-not-found");
		assertThat(this.client.searchGames("prey", "US", false)).isEqualTo(new NotFound("zzqqxx"));
	}

	@Test
	void encodesSearchParameters() {
		this.server.expect(requestTo("http://ml/games/search?query=a%26b%2Bc%20d&country=GB&search_online=true"))
			.andRespond(withSuccess(ContractFixtures.read("search-not-found"), MediaType.APPLICATION_JSON));
		this.client.searchGames("a&b+c d", "GB", true);
		this.server.verify();
	}

	@Test
	void sendsAndReadsPrices() {
		this.server.expect(requestTo("http://ml/prices"))
			.andExpect(method(HttpMethod.POST))
			.andExpect(content().json("{\"game_ids\": [\"a\", \"b\"], \"country\": \"JP\"}"))
			.andRespond(withSuccess(ContractFixtures.read("prices"), MediaType.APPLICATION_JSON));

		List<PriceResult> prices = this.client.prices(List.of("a", "b"), "JP");

		assertThat(prices).hasSize(4);
		assertThat(prices.get(0)).isInstanceOf(PriceMatch.class);
		assertThat(((PriceMatch) prices.get(0)).historicalLowest()).isNull();
		assertThat(prices.get(1)).isInstanceOf(NoPrice.class);
		assertThat(prices.get(2)).isInstanceOf(PriceFailed.class);
		assertThat(prices.get(3)).isInstanceOf(NotInCatalog.class);
	}

	@Test
	void itsBadGatewayIsABadGateway() {
		this.server.expect(requestTo(SEARCH_URL)).andRespond(withStatus(HttpStatus.BAD_GATEWAY));
		assertThatThrownBy(() -> this.client.searchGames("prey", "US", false))
			.isInstanceOf(MlServiceException.BadGateway.class);
	}

	@ParameterizedTest
	@ValueSource(ints = { 500, 503, 504 })
	void itsOtherServerErrorsMeanUnavailable(int status) {
		this.server.expect(requestTo(SEARCH_URL)).andRespond(withStatus(HttpStatus.valueOf(status)));
		assertThatThrownBy(() -> this.client.searchGames("prey", "US", false))
			.isInstanceOf(MlServiceException.Unavailable.class);
	}

	@Test
	void aRejectedRequestIsABadGateway() {
		this.server.expect(requestTo(SEARCH_URL)).andRespond(withStatus(HttpStatus.UNPROCESSABLE_CONTENT));
		assertThatThrownBy(() -> this.client.searchGames("prey", "US", false))
			.isInstanceOf(MlServiceException.BadGateway.class);
	}

	@ParameterizedTest
	@ValueSource(strings = { "not json", "{\"status\": \"new_status\"}", "{\"no\": \"status\"}", "" })
	void anUnreadableResponseIsABadGateway(String body) {
		this.server.expect(requestTo(SEARCH_URL)).andRespond(withSuccess(body, MediaType.APPLICATION_JSON));
		assertThatThrownBy(() -> this.client.searchGames("prey", "US", false))
			.isInstanceOf(MlServiceException.BadGateway.class);
	}

	@Test
	void pricesWithoutAListIsABadGateway() {
		this.server.expect(requestTo("http://ml/prices")).andRespond(withSuccess("{}", MediaType.APPLICATION_JSON));
		assertThatThrownBy(() -> this.client.prices(List.of("a"), "US"))
			.isInstanceOf(MlServiceException.BadGateway.class);
	}

}
