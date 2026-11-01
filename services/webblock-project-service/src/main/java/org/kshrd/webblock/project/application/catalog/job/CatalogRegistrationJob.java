package org.kshrd.webblock.project.application.catalog.job;

import java.util.UUID;

/** The outbox payload of the catalog registration job: the project ID only, no personal data. */
public record CatalogRegistrationJob(UUID projectId) {}
