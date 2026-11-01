package org.kshrd.webblock.project.application.generation.request;

import java.util.UUID;

/**
 * The capture body (spec 5.3.3): the contract the generation follows. Absent, the service uses (or makes) the contract
 * of all the active product types of the website's business type.
 */
public record CaptureGenerationRequest(UUID generationContractId) {}
