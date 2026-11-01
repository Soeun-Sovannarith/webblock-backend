package org.kshrd.webblock.project.application.messaging.handler;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Arrays;
import org.junit.jupiter.api.Test;

/** The status names this service reads are Namastack's own (namastack-outbox-core is on the runtime classpath only). */
class OutboxRecordsTest {

    @Test
    void theStatusesAreNamastacks() throws Exception {
        Class<?> status = Class.forName("io.namastack.outbox.OutboxRecordStatus");
        assertThat(Arrays.stream(status.getEnumConstants()).map(Object::toString))
                .containsExactlyInAnyOrder(OutboxRecords.NEW, OutboxRecords.COMPLETED, OutboxRecords.FAILED);
    }
}
