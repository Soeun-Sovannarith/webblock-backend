package org.kshrd.webblock.project.infrastructure.config.page;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;

class PageConfigTest {

    @Test
    void mapsThePathConstraints() {
        assertThat(new PageConfig().pageConstraints().constraints())
                .containsEntry("pages_project_path_key", ErrorCode.PAGE_PATH_TAKEN)
                .containsEntry("pages_path_check", ErrorCode.PAGE_PATH_INVALID);
    }
}
