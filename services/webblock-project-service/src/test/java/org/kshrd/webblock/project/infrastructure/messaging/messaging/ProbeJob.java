package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import java.util.UUID;

/** Spike S1's test-only local job: handled in-process by {@link FlakyJobHandler}, never sent to the broker. */
record ProbeJob(UUID jobId, int failuresBeforeSuccess) {}
