package org.kshrd.webblock.project.common.presentation.filter;

import org.springframework.http.ProblemDetail;
import org.springframework.http.converter.json.ProblemDetailJacksonMixin;
import tools.jackson.databind.json.JsonMapper;

public final class WebTestSupport {

    public static final String UUID_V7 = "^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";

    private WebTestSupport() {}

    /**
     * A JSON mapper that renders {@link ProblemDetail} as Spring Boot's does: its properties as top-level members. A
     * plain {@code JsonMapper} lacks the mixin and nests them under {@code properties}.
     */
    public static JsonMapper problemMapper() {
        return JsonMapper.builder()
                .addMixIn(ProblemDetail.class, ProblemDetailJacksonMixin.class)
                .build();
    }
}
