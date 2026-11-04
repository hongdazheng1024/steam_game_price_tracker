package com.steamtracker.backend.ml;

import java.util.List;
import java.util.function.Supplier;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

import com.steamtracker.backend.ml.MlModels.PriceResult;
import com.steamtracker.backend.ml.MlModels.PricesRequest;
import com.steamtracker.backend.ml.MlModels.PricesResponse;
import com.steamtracker.backend.ml.MlModels.SearchResult;

@Component
public class MlServiceClient {

	private final RestClient restClient;

	public MlServiceClient(RestClient mlRestClient) {
		this.restClient = mlRestClient;
	}

	public SearchResult searchGames(String query, String country, boolean searchOnline) {
		return call(() -> restClient.get()
			.uri(uri -> uri.path("/games/search")
				.queryParam("query", "{query}")
				.queryParam("country", "{country}")
				.queryParam("search_online", "{searchOnline}")
				.build(query, country, searchOnline))
			.retrieve()
			.body(SearchResult.class));
	}

	public List<PriceResult> prices(List<String> gameIds, String country) {
		PricesResponse response = call(() -> restClient.post()
			.uri("/prices")
			.contentType(MediaType.APPLICATION_JSON)
			.body(new PricesRequest(gameIds, country))
			.retrieve()
			.body(PricesResponse.class));
		if (response.prices() == null) {
			throw new MlServiceException.BadGateway("The ML service's prices response has no prices", null);
		}
		return response.prices();
	}

	private <T> T call(Supplier<T> request) {
		T body;
		try {
			body = request.get();
		}
		catch (RestClientResponseException e) {
			if (e.getStatusCode().is5xxServerError() && !e.getStatusCode().isSameCodeAs(HttpStatus.BAD_GATEWAY)) {
				throw new MlServiceException.Unavailable("The ML service failed: " + e.getStatusCode(), e);
			}
			// Its 502 is IsThereAnyDeal failing. Anything else means this client sent a request it shouldn't have.
			throw new MlServiceException.BadGateway("The ML service answered " + e.getStatusCode(), e);
		}
		catch (ResourceAccessException e) {
			throw new MlServiceException.Unavailable("The ML service can't be reached: " + e.getMessage(), e);
		}
		catch (RestClientException e) {
			throw new MlServiceException.BadGateway("The ML service's response can't be read: " + e.getMessage(), e);
		}
		if (body == null) {
			throw new MlServiceException.BadGateway("The ML service's response is empty", null);
		}
		return body;
	}

}
