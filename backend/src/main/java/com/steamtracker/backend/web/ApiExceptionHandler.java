package com.steamtracker.backend.web;

import java.util.stream.Collectors;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.MessageSourceResolvable;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

import com.steamtracker.backend.ml.MlServiceException;

/** Errors as RFC 9457 problem details. ML service details stay in the log, since they name its upstream calls. */
@RestControllerAdvice
public class ApiExceptionHandler extends ResponseEntityExceptionHandler {

	private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

	@ExceptionHandler(MlServiceException.BadGateway.class)
	ProblemDetail badGateway(MlServiceException.BadGateway e) {
		log.warn("{}", e.getMessage());
		return ProblemDetail.forStatusAndDetail(HttpStatus.BAD_GATEWAY,
				"The game price provider failed. Please try again later.");
	}

	@ExceptionHandler(MlServiceException.Unavailable.class)
	ProblemDetail unavailable(MlServiceException.Unavailable e) {
		log.warn("{}", e.getMessage());
		return ProblemDetail.forStatusAndDetail(HttpStatus.SERVICE_UNAVAILABLE,
				"Game search is unavailable right now. Please try again later.");
	}

	@Override
	protected ResponseEntity<Object> handleHandlerMethodValidationException(HandlerMethodValidationException ex,
			HttpHeaders headers, HttpStatusCode status, WebRequest request) {
		ProblemDetail body = ex.getBody();
		body.setDetail(ex.getAllErrors()
			.stream()
			.map(MessageSourceResolvable::getDefaultMessage)
			.collect(Collectors.joining("; ")));
		return handleExceptionInternal(ex, body, headers, status, request);
	}

}
