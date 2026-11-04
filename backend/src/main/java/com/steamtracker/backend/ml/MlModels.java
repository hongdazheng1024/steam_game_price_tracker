package com.steamtracker.backend.ml;

import java.math.BigDecimal;
import java.util.List;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

/**
 * The ML service's responses, limited to the fields this backend uses. The Python side is
 * agent/api.py, and contract/ml-service holds sample responses that both sides' tests check.
 */
public final class MlModels {

	private MlModels() {
	}

	public record Game(String id, String title) {
	}

	public record Money(BigDecimal amount, String currency) {
	}

	public record Deal(Money price, Money regular, int cut, String url) {
	}

	@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = "status")
	@JsonSubTypes({ @JsonSubTypes.Type(value = PriceMatch.class, name = "match"),
			@JsonSubTypes.Type(value = NoPrice.class, name = "no_price"),
			@JsonSubTypes.Type(value = Choices.class, name = "choices"),
			@JsonSubTypes.Type(value = NotFound.class, name = "not_found") })
	public sealed interface SearchResult permits PriceMatch, NoPrice, Choices, NotFound {
	}

	@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = "status")
	@JsonSubTypes({ @JsonSubTypes.Type(value = PriceMatch.class, name = "match"),
			@JsonSubTypes.Type(value = NoPrice.class, name = "no_price"),
			@JsonSubTypes.Type(value = NotInCatalog.class, name = "not_in_catalog"),
			@JsonSubTypes.Type(value = PriceFailed.class, name = "error") })
	public sealed interface PriceResult permits PriceMatch, NoPrice, NotInCatalog, PriceFailed {
	}

	public record PriceMatch(String id, String title, @JsonProperty("current_deal") Deal currentDeal,
			@JsonProperty("historical_lowest") Deal historicalLowest) implements SearchResult, PriceResult {
	}

	public record NoPrice(String id, String title) implements SearchResult, PriceResult {
	}

	public record Choices(List<Game> choices, String source, String query) implements SearchResult {

		public static final String SOURCE_CATALOG = "catalog";

	}

	public record NotFound(String query) implements SearchResult {
	}

	public record NotInCatalog(String id) implements PriceResult {
	}

	public record PriceFailed(String id, String title, String error) implements PriceResult {
	}

	public record PricesRequest(@JsonProperty("game_ids") List<String> gameIds, String country) {
	}

	public record PricesResponse(List<PriceResult> prices) {
	}

}
