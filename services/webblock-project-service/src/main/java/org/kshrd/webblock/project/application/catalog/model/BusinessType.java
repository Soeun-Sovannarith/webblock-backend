package org.kshrd.webblock.project.application.catalog.model;

import java.util.UUID;

/** A row of catalog.business_types. */
public record BusinessType(UUID businessTypeId, String code, String name, boolean active) {}
