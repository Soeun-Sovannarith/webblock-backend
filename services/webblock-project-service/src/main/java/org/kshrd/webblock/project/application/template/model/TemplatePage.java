package org.kshrd.webblock.project.application.template.model;

import tools.jackson.databind.JsonNode;

public record TemplatePage(
        String name, String path, int sortOrder, String seoTitle, String seoDescription, JsonNode puckTree) {}
