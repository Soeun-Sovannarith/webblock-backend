package org.kshrd.webblock.project.application.generation.model;

import java.util.UUID;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;

/**
 * A capture's answer: the request, its status now, and whether this call created it (false for an idempotent replay).
 */
public record CaptureOutcome(UUID generationRequestId, GenerationRequestStatus status, boolean created) {}
