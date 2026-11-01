package org.kshrd.webblock.project.application.project.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;

class ProjectCursorTest {

    @Test
    void roundTripsToTheMicrosecond() {
        ProjectCursor cursor = new ProjectCursor(Instant.parse("2026-10-15T01:02:03.123456Z"), UUID.randomUUID());
        assertThat(ProjectCursor.decode(cursor.encode())).isEqualTo(cursor);
    }

    @Test
    void refusesAnythingElse() {
        for (String bad : new String[] {"", "x", "bm90IGEgY3Vyc29y", "a".repeat(300), "MTIzOm5vdC1hLXV1aWQ"}) {
            assertThatThrownBy(() -> ProjectCursor.decode(bad))
                    .as(bad)
                    .isInstanceOf(WebBlockException.class)
                    .satisfies(e ->
                            assertThat(((WebBlockException) e).errorCode()).isEqualTo(ErrorCode.VALIDATION_FAILED));
        }
    }
}
