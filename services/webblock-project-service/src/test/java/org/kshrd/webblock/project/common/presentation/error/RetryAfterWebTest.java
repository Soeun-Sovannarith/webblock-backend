package org.kshrd.webblock.project.common.presentation.error;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = DependencyProbeController.class)
@Import(WebSliceSupport.Imports.class)
class RetryAfterWebTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @Test
    void dependencyErrorsTellTheCallerWhenToRetry() throws Exception {
        mvc.perform(get("/api/test-dependency").with(Users.anyUser()))
                .andExpect(status().isServiceUnavailable())
                .andExpect(header().string("Retry-After", "5"))
                .andExpect(jsonPath("$.code").value("PAYMENT_UNAVAILABLE"));
    }

    @Test
    void aStatementTimeoutIsASanitized503() throws Exception {
        mvc.perform(get("/api/test-dependency/statement-timeout").with(Users.anyUser()))
                .andExpect(status().isServiceUnavailable())
                .andExpect(header().string("Retry-After", "5"))
                .andExpect(jsonPath("$.code").value("DEPENDENCY_UNAVAILABLE"))
                .andExpect(content().string(not(containsString("secret"))));
    }

    @Test
    @DisplayName("FR-5: no free pooled connection is a sanitized 503 DEPENDENCY_UNAVAILABLE with no ERROR log")
    void fr5_aPoolTimeoutIsASanitized503() throws Exception {
        Logger logger = (Logger) LoggerFactory.getLogger(GlobalExceptionHandler.class);
        ListAppender<ILoggingEvent> logs = new ListAppender<>();
        logs.start();
        logger.addAppender(logs);
        try {
            mvc.perform(get("/api/test-dependency/pool-timeout").with(Users.anyUser()))
                    .andExpect(status().isServiceUnavailable())
                    .andExpect(header().string("Retry-After", "5"))
                    .andExpect(jsonPath("$.code").value("DEPENDENCY_UNAVAILABLE"))
                    .andExpect(content().string(not(containsString("HikariPool"))));

            assertThat(logs.list).isNotEmpty().allSatisfy(event -> {
                assertThat(event.getLevel()).isEqualTo(Level.WARN);
                assertThat(event.getThrowableProxy()).isNull();
            });
        } finally {
            logger.detachAppender(logs);
        }
    }

    @Test
    @DisplayName("TR-24: a transaction that fails to start for another reason stays a 500 INTERNAL_ERROR")
    void tr24_anotherTransactionFailureIsInternal() throws Exception {
        mvc.perform(get("/api/test-dependency/transaction-failure").with(Users.anyUser()))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.code").value("INTERNAL_ERROR"))
                .andExpect(content().string(not(containsString("secret"))));
    }

    /** A pending admission release asks the client to repeat the same request shortly; no other conflict does. */
    @Test
    void anAdmissionReleasePendingConflictTellsTheCallerWhenToRetry() throws Exception {
        mvc.perform(get("/api/test-dependency/release-pending").with(Users.anyUser()))
                .andExpect(status().isConflict())
                .andExpect(header().string("Retry-After", "5"))
                .andExpect(jsonPath("$.code").value("ADMISSION_RELEASE_PENDING"));
        mvc.perform(get("/api/test-dependency/other-conflict").with(Users.anyUser()))
                .andExpect(status().isConflict())
                .andExpect(header().doesNotExist("Retry-After"));
    }

    @Test
    void aStreamConstraintThrownOutsideABodyReadIsMalformed() throws Exception {
        mvc.perform(get("/api/test-dependency/stream-constraints").with(Users.anyUser()))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("MALFORMED_REQUEST"))
                .andExpect(content().string(not(containsString("nesting"))));
    }
}
