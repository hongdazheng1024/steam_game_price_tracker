package com.steamtracker.backend.game;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

@RestController
@RequestMapping("/api/v1/games")
public class GameController {

	private final GameSearchService gameSearchService;

	public GameController(GameSearchService gameSearchService) {
		this.gameSearchService = gameSearchService;
	}

	@GetMapping("/search")
	public GameSearchResponse search(
			@RequestParam @NotBlank(message = "query must not be blank") @Size(max = 100,
					message = "query must be at most 100 characters") String query,
			@RequestParam(defaultValue = "US") @Pattern(regexp = "US|JP|GB|CA|AU|DE|FR",
					message = "country must be one of US, JP, GB, CA, AU, DE, FR") String country,
			@RequestParam(defaultValue = "false") boolean searchOnline) {
		return gameSearchService.search(query, country, searchOnline);
	}

}
