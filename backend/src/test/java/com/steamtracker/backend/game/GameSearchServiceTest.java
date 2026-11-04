package com.steamtracker.backend.game;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.util.List;

import org.junit.jupiter.api.Test;

import com.steamtracker.backend.game.GameSearchResponse.GamePrice;
import com.steamtracker.backend.game.GameSearchResponse.GameResult;
import com.steamtracker.backend.game.GameSearchResponse.PriceStatus;
import com.steamtracker.backend.ml.MlModels.Choices;
import com.steamtracker.backend.ml.MlModels.Deal;
import com.steamtracker.backend.ml.MlModels.Game;
import com.steamtracker.backend.ml.MlModels.Money;
import com.steamtracker.backend.ml.MlModels.NoPrice;
import com.steamtracker.backend.ml.MlModels.NotFound;
import com.steamtracker.backend.ml.MlModels.NotInCatalog;
import com.steamtracker.backend.ml.MlModels.PriceFailed;
import com.steamtracker.backend.ml.MlModels.PriceMatch;
import com.steamtracker.backend.ml.MlServiceClient;
import com.steamtracker.backend.ml.MlServiceException;

class GameSearchServiceTest {

	private static final Deal CURRENT = new Deal(usd("5.99"), usd("29.99"), 80, "https://itad.link/prey");

	private static final Deal LOWEST = new Deal(usd("2.99"), usd("29.99"), 90, null);

	private static final GamePrice PRICE = new GamePrice("USD", new BigDecimal("5.99"), new BigDecimal("29.99"), 80,
			new BigDecimal("2.99"), "https://itad.link/prey");

	private final MlServiceClient mlService = mock(MlServiceClient.class);

	private final GameSearchService service = new GameSearchService(this.mlService);

	private static Money usd(String amount) {
		return new Money(new BigDecimal(amount), "USD");
	}

	private static PriceMatch match(String id, String title) {
		return new PriceMatch(id, title, CURRENT, LOWEST);
	}

	private static Choices choices(String source, String... ids) {
		return new Choices(List.of(ids).stream().map((id) -> new Game(id, "Game " + id)).toList(), source, "prey");
	}

	@Test
	void aMatchIsOneResultWithItsPrice() {
		when(this.mlService.searchGames("prey", "GB", false)).thenReturn(match("a", "Prey"));

		GameSearchResponse response = this.service.search("prey", "GB", false);

		assertThat(response.query()).isEqualTo("prey");
		assertThat(response.results())
			.containsExactly(new GameResult("a", "Prey", PriceStatus.AVAILABLE, PRICE));
		verify(this.mlService, never()).prices(any(), anyString());
	}

	@Test
	void aMatchWithoutAnAllTimeLowStillHasAPrice() {
		when(this.mlService.searchGames("prey", "US", false)).thenReturn(new PriceMatch("a", "Prey", CURRENT, null));
		GamePrice price = this.service.search("prey", "US", false).results().get(0).price();
		assertThat(price.current()).isEqualByComparingTo("5.99");
		assertThat(price.historicalLow()).isNull();
	}

	@Test
	void aMatchWithoutACurrentDealIsNotOnSteam() {
		when(this.mlService.searchGames("prey", "US", false)).thenReturn(new PriceMatch("a", "Prey", null, LOWEST));
		assertThat(this.service.search("prey", "US", false).results())
			.containsExactly(new GameResult("a", "Prey", PriceStatus.NOT_ON_STEAM, null));
	}

	@Test
	void noPriceIsOneResultNotOnSteam() {
		when(this.mlService.searchGames("prey", "US", false)).thenReturn(new NoPrice("a", "Prey"));
		assertThat(this.service.search("prey", "US", false).results())
			.containsExactly(new GameResult("a", "Prey", PriceStatus.NOT_ON_STEAM, null));
	}

	@Test
	void notFoundIsAnEmptyListWithNothingElseToTry() {
		when(this.mlService.searchGames("prey", "US", false)).thenReturn(new NotFound("prey"));
		GameSearchResponse response = this.service.search("prey", "US", false);
		assertThat(response.results()).isEmpty();
		assertThat(response.canSearchOnline()).isFalse();
	}

	@Test
	void choicesGetTheirPricesInTheChoicesOrder() {
		when(this.mlService.searchGames("prey", "JP", false)).thenReturn(choices("catalog", "a", "b", "c", "d"));
		// Not in the choices' order, to show results are matched by id
		when(this.mlService.prices(List.of("a", "b", "c", "d"), "JP"))
			.thenReturn(List.of(new PriceFailed("c", "Game c", "timeout"), new NoPrice("b", "Game b"),
					new NotInCatalog("d"), match("a", "Game a")));

		assertThat(this.service.search("prey", "JP", false).results()).containsExactly(
				new GameResult("a", "Game a", PriceStatus.AVAILABLE, PRICE),
				new GameResult("b", "Game b", PriceStatus.NOT_ON_STEAM, null),
				new GameResult("c", "Game c", PriceStatus.UNAVAILABLE, null),
				new GameResult("d", "Game d", PriceStatus.UNAVAILABLE, null));
	}

	@Test
	void aChoiceMissingFromThePricesIsUnavailable() {
		when(this.mlService.searchGames("prey", "US", false)).thenReturn(choices("api", "a"));
		when(this.mlService.prices(List.of("a"), "US")).thenReturn(List.of());
		assertThat(this.service.search("prey", "US", false).results())
			.containsExactly(new GameResult("a", "Game a", PriceStatus.UNAVAILABLE, null));
	}

	@Test
	void choicesAreStillShownWhenThePriceCallFails() {
		when(this.mlService.searchGames("prey", "US", false)).thenReturn(choices("api", "a", "b"));
		when(this.mlService.prices(any(), anyString()))
			.thenThrow(new MlServiceException.Unavailable("down", null));

		assertThat(this.service.search("prey", "US", false).results()).containsExactly(
				new GameResult("a", "Game a", PriceStatus.UNAVAILABLE, null),
				new GameResult("b", "Game b", PriceStatus.UNAVAILABLE, null));
	}

	@Test
	void canSearchOnlineUnlessTheSearchAlreadyWentOnline() {
		when(this.mlService.prices(any(), anyString())).thenReturn(List.of());

		when(this.mlService.searchGames("prey", "US", false)).thenReturn(match("a", "Prey"));
		assertThat(this.service.search("prey", "US", false).canSearchOnline()).isTrue();

		when(this.mlService.searchGames("prey", "US", true)).thenReturn(match("a", "Prey"));
		assertThat(this.service.search("prey", "US", true).canSearchOnline()).isFalse();

		when(this.mlService.searchGames("none", "US", false)).thenReturn(new NoPrice("a", "None"));
		assertThat(this.service.search("none", "US", false).canSearchOnline()).isTrue();

		when(this.mlService.searchGames("cat", "US", false)).thenReturn(choices("catalog", "a"));
		assertThat(this.service.search("cat", "US", false).canSearchOnline()).isTrue();

		when(this.mlService.searchGames("api", "US", false)).thenReturn(choices("api", "a"));
		assertThat(this.service.search("api", "US", false).canSearchOnline()).isFalse();
	}

	@Test
	void aFailedSearchIsPassedOn() {
		when(this.mlService.searchGames("prey", "US", false))
			.thenThrow(new MlServiceException.BadGateway("upstream", null));
		assertThatThrownBy(() -> this.service.search("prey", "US", false))
			.isInstanceOf(MlServiceException.BadGateway.class);
	}

}
