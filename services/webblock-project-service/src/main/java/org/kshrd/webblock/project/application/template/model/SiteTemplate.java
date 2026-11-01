package org.kshrd.webblock.project.application.template.model;

import java.util.List;

/** A site template: its starting pages and the component catalog they use. "*" applies to every business type. */
public record SiteTemplate(
        String templateCode,
        String name,
        String description,
        String thumbnailUrl,
        String badge,
        String styleKey,
        String version,
        List<String> businessTypeCodes,
        String componentCatalogVersion,
        List<TemplatePage> pages) {

    public boolean appliesTo(String businessTypeCode) {
        return businessTypeCodes.contains("*") || businessTypeCodes.contains(businessTypeCode);
    }
}
