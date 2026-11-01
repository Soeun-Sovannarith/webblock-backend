package org.kshrd.webblock.project.application.generation.model;

import java.time.Instant;
import java.util.UUID;

/**
 * One deploy of a website: its published generation request. versionId is the generationRequestId, editRevision the
 * revision the request froze, publishedAt the time the request reached published; current marks the newest deploy.
 */
public record DeployVersion(UUID versionId, long editRevision, Instant publishedAt, boolean current) {}
