package com.steamtracker.backend;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** The ML service's sample responses in contract/ml-service, which its Python tests check too. */
public final class ContractFixtures {

	private ContractFixtures() {
	}

	public static String read(String name) {
		try {
			return Files.readString(Path.of("..", "contract", "ml-service", name + ".json"));
		}
		catch (IOException e) {
			throw new UncheckedIOException(e);
		}
	}

}
