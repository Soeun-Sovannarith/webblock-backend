package org.kshrd.webblock.project.domain.generation.model;

import static org.assertj.core.api.Assertions.assertThat;
import static org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus.captured;
import static org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus.failed;
import static org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus.generating;
import static org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus.published;
import static org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus.publishing;
import static org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus.values;

import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;

class GenerationRequestStatusTest {

    @Test
    void theTableIsExactlySpec535() {
        Map<GenerationRequestStatus, Set<GenerationRequestStatus>> allowed = Map.of(
                captured, Set.of(generating, failed),
                generating, Set.of(publishing, failed),
                publishing, Set.of(published, failed),
                published, Set.of(),
                failed, Set.of());
        for (var from : values()) {
            for (var to : values()) {
                assertThat(from.canMoveTo(to))
                        .as(from + "->" + to)
                        .isEqualTo(allowed.get(from).contains(to));
            }
        }
    }

    @Test
    void failedAndPublishedAreFinal() {
        for (var status : values()) {
            assertThat(status.isFinal()).as(status.name()).isEqualTo(status == failed || status == published);
        }
    }
}
