package com.steamtracker.backend.game;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.math.BigDecimal;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import com.steamtracker.backend.game.GameSearchResponse.GamePrice;
import com.steamtracker.backend.game.GameSearchResponse.GameResult;
import com.steamtracker.backend.game.GameSearchResponse.PriceStatus;
import com.steamtracker.backend.ml.MlServiceException;

@WebMvcTest(GameController.class)
class GameControllerTest {

	private static final String URL = "/api/v1/games/search";

	@Autowired
	private MockMvc mvc;

	@MockitoBean
	private GameSearchService service;

	@Test
	void returnsTheResponseAsJson() throws Exception {
		GamePrice price = new GamePrice("USD", new BigDecimal("5.99"), new BigDecimal("29.99"), 80, null, "https://x");
		when(this.service.search("prey", "JP", true)).thenReturn(new GameSearchResponse("prey", false,
				List.of(new GameResult("a", "Prey", PriceStatus.AVAILABLE, price),
						new GameResult("b", "Prey 2", PriceStatus.NOT_ON_STEAM, null))));

		this.mvc.perform(get(URL).param("query", "prey").param("country", "JP").param("searchOnline", "true"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.query").value("prey"))
			.andExpect(jsonPath("$.canSearchOnline").value(false))
			.andExpect(jsonPath("$.results[0].id").value("a"))
			.andExpect(jsonPath("$.results[0].priceStatus").value("AVAILABLE"))
			.andExpect(jsonPath("$.results[0].price.current").value(5.99))
			.andExpect(jsonPath("$.results[0].price.discountPercent").value(80))
			.andExpect(jsonPath("$.results[0].price.historicalLow").isEmpty())
			.andExpect(jsonPath("$.results[1].priceStatus").value("NOT_ON_STEAM"))
			.andExpect(jsonPath("$.results[1].price").isEmpty());
	}

	@Test
	void defaultsToTheUsAndTheCatalog() throws Exception {
		when(this.service.search("prey", "US", false)).thenReturn(new GameSearchResponse("prey", true, List.of()));
		this.mvc.perform(get(URL).param("query", "prey"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$.results").isEmpty());
	}

	@Test
	void anEmptyCountryMeansTheDefault() throws Exception {
		when(this.service.search("prey", "US", false)).thenReturn(new GameSearchResponse("prey", true, List.of()));
		this.mvc.perform(get(URL).param("query", "prey").param("country", "")).andExpect(status().isOk());
		verify(this.service).search("prey", "US", false);
	}

	@Test
	void rejectsAMissingQuery() throws Exception {
		this.mvc.perform(get(URL)).andExpect(status().isBadRequest());
		verify(this.service, never()).search(anyString(), anyString(), anyBoolean());
	}

	@ParameterizedTest
	@ValueSource(strings = { "", "   " })
	void rejectsABlankQuery(String query) throws Exception {
		this.mvc.perform(get(URL).param("query", query))
			.andExpect(status().isBadRequest())
			.andExpect(content().contentTypeCompatibleWith("application/problem+json"))
			.andExpect(jsonPath("$.detail").value("query must not be blank"));
		verify(this.service, never()).search(anyString(), anyString(), anyBoolean());
	}

	@Test
	void acceptsAQueryOfTheMaximumLength() throws Exception {
		String query = "x".repeat(100);
		when(this.service.search(query, "US", false)).thenReturn(new GameSearchResponse(query, false, List.of()));
		this.mvc.perform(get(URL).param("query", query)).andExpect(status().isOk());
	}

	@Test
	void rejectsAQueryThatIsTooLong() throws Exception {
		this.mvc.perform(get(URL).param("query", "x".repeat(101)))
			.andExpect(status().isBadRequest())
			.andExpect(jsonPath("$.detail").value("query must be at most 100 characters"));
	}

	@ParameterizedTest
	@ValueSource(strings = { "XX", "us", "USA" })
	void rejectsAnUnsupportedCountry(String country) throws Exception {
		this.mvc.perform(get(URL).param("query", "prey").param("country", country))
			.andExpect(status().isBadRequest());
		verify(this.service, never()).search(anyString(), anyString(), anyBoolean());
	}

	@Test
	void rejectsABadSearchOnlineFlag() throws Exception {
		this.mvc.perform(get(URL).param("query", "prey").param("searchOnline", "maybe"))
			.andExpect(status().isBadRequest());
	}

	@Test
	void aProviderFailureIsA502WithoutItsDetails() throws Exception {
		when(this.service.search("prey", "US", false))
			.thenThrow(new MlServiceException.BadGateway("https://internal-host/secret failed", null));
		this.mvc.perform(get(URL).param("query", "prey"))
			.andExpect(status().isBadGateway())
			.andExpect(content().contentTypeCompatibleWith("application/problem+json"))
			.andExpect(content().string(not(containsString("internal-host"))));
	}

	@Test
	void anUnavailableServiceIsA503WithoutItsDetails() throws Exception {
		when(this.service.search("prey", "US", false))
			.thenThrow(new MlServiceException.Unavailable("http://internal-host:8000 refused", null));
		this.mvc.perform(get(URL).param("query", "prey"))
			.andExpect(status().isServiceUnavailable())
			.andExpect(content().contentTypeCompatibleWith("application/problem+json"))
			.andExpect(content().string(not(containsString("internal-host"))));
	}

}
