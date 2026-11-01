package org.kshrd.webblock.project.application.project.model;

import java.util.UUID;

public record NewProject(
        UUID projectId,
        UUID ownerUserId,
        String name,
        String subdomain,
        UUID businessTypeId,
        String componentCatalogVersion) {}
