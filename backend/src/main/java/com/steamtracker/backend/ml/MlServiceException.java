package com.steamtracker.backend.ml;

public class MlServiceException extends RuntimeException {

	public MlServiceException(String message, Throwable cause) {
		super(message, cause);
	}

	/** The ML service answered, but IsThereAnyDeal failed or the response made no sense. */
	public static class BadGateway extends MlServiceException {

		public BadGateway(String message, Throwable cause) {
			super(message, cause);
		}

	}

	/** The ML service couldn't be reached, timed out or is failing. */
	public static class Unavailable extends MlServiceException {

		public Unavailable(String message, Throwable cause) {
			super(message, cause);
		}

	}

}
