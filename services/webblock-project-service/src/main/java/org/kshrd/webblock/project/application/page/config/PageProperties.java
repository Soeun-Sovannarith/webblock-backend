package org.kshrd.webblock.project.application.page.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * webblock.pages.max-puck-tree-bytes: the largest stored Puck tree, in UTF-8 bytes (spec 9: 1 MB, configurable).
 */
@ConfigurationProperties("webblock.pages")
public record PageProperties(int maxPuckTreeBytes) {}
