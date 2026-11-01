package org.kshrd.webblock.project.application.page.model;

import tools.jackson.databind.JsonNode;

/** A page the wizard creates from a template. */
public record NewPage(
        String name, String path, JsonNode puckTree, String seoTitle, String seoDescription, int sortOrder) {}
