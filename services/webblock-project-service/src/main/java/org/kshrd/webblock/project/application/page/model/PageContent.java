package org.kshrd.webblock.project.application.page.model;

import tools.jackson.databind.JsonNode;

/** What an editor save sends for one page. */
public record PageContent(String name, String path, JsonNode puckTree, String seoTitle, String seoDescription) {}
