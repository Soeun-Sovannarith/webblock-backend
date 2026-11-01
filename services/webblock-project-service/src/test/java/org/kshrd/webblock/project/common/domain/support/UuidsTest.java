package org.kshrd.webblock.project.common.domain.support;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class UuidsTest {

    @Test
    void generatesVersion7UuidsInIncreasingOrderEvenWithinOneMillisecond() {
        List<String> ids = new ArrayList<>();
        for (int i = 0; i < 1_000; i++) {
            UUID id = Uuids.v7();
            assertThat(id.version()).isEqualTo(7);
            assertThat(id.variant()).isEqualTo(2);
            ids.add(id.toString());
        }
        assertThat(ids).isSorted().doesNotHaveDuplicates();
    }
}
