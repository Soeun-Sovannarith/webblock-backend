package org.kshrd.webblock.project.application.generation.response;

import java.util.UUID;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;

/** The body of a capture's 202 and of its 200 replay (decisions J2-15, J2-26). */
public record CaptureResponse(UUID generationRequestId, GenerationRequestStatus status) {}
