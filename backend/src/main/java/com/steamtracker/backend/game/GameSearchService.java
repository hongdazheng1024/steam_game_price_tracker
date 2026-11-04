package com.steamtracker.backend.game;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import com.steamtracker.backend.game.GameSearchResponse.GamePrice;
import com.steamtracker.backend.game.GameSearchResponse.GameResult;
import com.steamtracker.backend.game.GameSearchResponse.PriceStatus;
import com.steamtracker.backend.ml.MlModels.Choices;
import com.steamtracker.backend.ml.MlModels.Deal;
import com.steamtracker.backend.ml.MlModels.Game;
import com.steamtracker.backend.ml.MlModels.NoPrice;
import com.steamtracker.backend.ml.MlModels.NotFound;
import com.steamtracker.backend.ml.MlModels.NotInCatalog;
import com.steamtracker.backend.ml.MlModels.PriceFailed;
import com.steamtracker.backend.ml.MlModels.PriceMatch;
import com.steamtracker.backend.ml.MlModels.PriceResult;
import com.steamtracker.backend.ml.MlServiceClient;
import com.steamtracker.backend.ml.MlServiceException;

/**
 * Turns the ML service's search results, which differ by how many games matched, into one list of
 * games. Several matches come without prices, so those are looked up here.
 */
@Service
public class GameSearchService {

	private static final Logger log = LoggerFactory.getLogger(GameSearchService.class);

	private final MlServiceClient mlService;

	public GameSearchService(MlServiceClient mlService) {
		this.mlService = mlService;
	}

	public GameSearchResponse search(String query, String country, boolean searchOnline) {
		return switch (mlService.searchGames(query, country, searchOnline)) {
			// A single match may have come from the catalog, which can miss other games with the same title
			case PriceMatch match -> new GameSearchResponse(query, !searchOnline, List.of(result(match)));
			case NoPrice noPrice -> new GameSearchResponse(query, !searchOnline, List.of(notOnSteam(noPrice)));
			case Choices choices -> new GameSearchResponse(query, Choices.SOURCE_CATALOG.equals(choices.source()),
					withPrices(choices.choices(), country));
			// The ML service only answers this after searching IsThereAnyDeal, so there's nothing else to try
			case NotFound notFound -> new GameSearchResponse(query, false, List.of());
		};
	}

	private List<GameResult> withPrices(List<Game> games, String country) {
		Map<String, PriceResult> prices = lookUpPrices(games, country);
		return games.stream().map(game -> switch (prices.get(game.id())) {
			case PriceMatch match -> result(match);
			case NoPrice noPrice -> notOnSteam(noPrice);
			case NotInCatalog notInCatalog -> unavailable(game);
			case PriceFailed failed -> unavailable(game);
			case null -> unavailable(game);
		}).toList();
	}

	private Map<String, PriceResult> lookUpPrices(List<Game> games, String country) {
		List<String> ids = games.stream().map(Game::id).toList();
		try {
			return mlService.prices(ids, country).stream()
				.collect(Collectors.toMap(GameSearchService::gameId, Function.identity(), (first, repeat) -> first));
		}
		catch (MlServiceException e) {
			// The matches are still worth showing without prices
			log.warn("Couldn't look up the prices of {} games: {}", ids.size(), e.getMessage());
			return Map.of();
		}
	}

	private static String gameId(PriceResult result) {
		return switch (result) {
			case PriceMatch match -> match.id();
			case NoPrice noPrice -> noPrice.id();
			case NotInCatalog notInCatalog -> notInCatalog.id();
			case PriceFailed failed -> failed.id();
		};
	}

	private static GameResult result(PriceMatch match) {
		Deal current = match.currentDeal();
		if (current == null) {
			return new GameResult(match.id(), match.title(), PriceStatus.NOT_ON_STEAM, null);
		}
		Deal lowest = match.historicalLowest();
		GamePrice price = new GamePrice(current.price().currency(), current.price().amount(),
				current.regular().amount(), current.cut(), (lowest != null) ? lowest.price().amount() : null,
				current.url());
		return new GameResult(match.id(), match.title(), PriceStatus.AVAILABLE, price);
	}

	private static GameResult notOnSteam(NoPrice noPrice) {
		return new GameResult(noPrice.id(), noPrice.title(), PriceStatus.NOT_ON_STEAM, null);
	}

	private static GameResult unavailable(Game game) {
		return new GameResult(game.id(), game.title(), PriceStatus.UNAVAILABLE, null);
	}

}
