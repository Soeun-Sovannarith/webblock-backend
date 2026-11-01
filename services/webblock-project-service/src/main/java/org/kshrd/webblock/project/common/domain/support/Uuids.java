package org.kshrd.webblock.project.common.domain.support;

import com.fasterxml.uuid.Generators;
import com.fasterxml.uuid.impl.TimeBasedEpochGenerator;
import java.util.UUID;

/** UUIDv7 identifiers: time-ordered, so new rows land at the end of B-tree indexes. */
public final class Uuids {

    private static final TimeBasedEpochGenerator GENERATOR = Generators.timeBasedEpochGenerator();

    private Uuids() {}

    public static UUID v7() {
        return GENERATOR.generate();
    }
}
