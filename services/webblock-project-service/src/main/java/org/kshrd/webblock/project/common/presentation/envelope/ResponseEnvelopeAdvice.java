package org.kshrd.webblock.project.common.presentation.envelope;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.core.MethodParameter;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.ProblemDetail;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.http.server.ServletServerHttpRequest;
import org.springframework.http.server.ServletServerHttpResponse;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyAdvice;

/**
 * Wraps every successful JSON body under /api and /internal in {@link ApiResponse}. A {@code ProblemDetail} (every error
 * body), any response with status 400 or more, binary bodies and Strings pass through unchanged; controllers never
 * return String. ArchitectureRulesTest keeps every other class away from ApiResponse.
 */
@RestControllerAdvice
public class ResponseEnvelopeAdvice implements ResponseBodyAdvice<Object> {

    @Override
    public boolean supports(MethodParameter returnType, Class<? extends HttpMessageConverter<?>> converterType) {
        return true;
    }

    @Override
    public Object beforeBodyWrite(
            Object body,
            MethodParameter returnType,
            MediaType selectedContentType,
            Class<? extends HttpMessageConverter<?>> selectedConverterType,
            ServerHttpRequest request,
            ServerHttpResponse response) {
        if (!(request instanceof ServletServerHttpRequest servletRequest)
                || !(response instanceof ServletServerHttpResponse servletResponse)) {
            return body;
        }
        HttpServletRequest httpRequest = servletRequest.getServletRequest();
        String path = httpRequest.getRequestURI();
        if (!path.startsWith("/api/") && !path.startsWith("/internal/")) {
            return body;
        }
        if (body instanceof byte[]
                || body instanceof Resource
                || body instanceof String
                || body instanceof ProblemDetail
                || !isJson(selectedContentType)) {
            return body;
        }
        int status = servletResponse.getServletResponse().getStatus();
        if (status >= 400) {
            // Never present an error status as success; errors are built by the exception handler.
            return body;
        }
        if (body instanceof PagedResult<?> paged) {
            return ApiResponse.success(
                    paged.items(), ApiMeta.of(httpRequest, status, new PageMeta(paged.limit(), paged.nextCursor())));
        }
        return ApiResponse.success(body, ApiMeta.of(httpRequest, status, null));
    }

    private static boolean isJson(MediaType type) {
        return MediaType.APPLICATION_JSON.isCompatibleWith(type)
                || type.getSubtype().endsWith("+json");
    }
}
