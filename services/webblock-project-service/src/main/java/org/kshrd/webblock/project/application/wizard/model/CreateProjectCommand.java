package org.kshrd.webblock.project.application.wizard.model;

import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;

/** The wizard's request; the business profile goes to the catalog through the pending_catalog_registrations table, never the outbox. */
public record CreateProjectCommand(
        String name, String subdomain, String businessTypeCode, String templateCode, BusinessProfile businessProfile) {}
