package com.steamtracker.backend.game;

import java.math.BigDecimal;
import java.util.List;

/**
 * @param canSearchOnline whether searching IsThereAnyDeal instead of the local catalog could find
 * other games, so the client can offer it
 */
public record GameSearchResponse(String query, boolean canSearchOnline, List<GameResult> results) {

	public enum PriceStatus {

		AVAILABLE,

		/** The game has no Steam price, so asking again won't help. */
		NOT_ON_STEAM,

		/** The price lookup failed, so it's worth trying again later. */
		UNAVAILABLE

	}

	/**
	 * @param price null unless the status is AVAILABLE
	 */
	public record GameResult(String id, String title, PriceStatus priceStatus, GamePrice price) {
	}

	/**
	 * @param discountPercent the current discount off the regular price
	 * @param historicalLow the all-time low on Steam, null if IsThereAnyDeal has none
	 * @param url where to buy
	 */
	public record GamePrice(String currency, BigDecimal current, BigDecimal regular, int discountPercent,
			BigDecimal historicalLow, String url) {
	}

}
