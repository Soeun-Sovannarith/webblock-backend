package org.kshrd.webblock.project.application.wizard.model;

import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.kshrd.webblock.project.application.catalog.model.OpeningHours;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.JsonNodeFactory;
import tools.jackson.databind.node.ObjectNode;

/**
 * Fills the business details of the wizard into the root props of a template's home page, as the front end's mock
 * backend does. A template whose root props are empty (blank) is left as it is, and so is every prop that is not a
 * business detail (status text, style, social links, template ID). A detail the client left out is emptied, so the
 * template's sample business never shows on the new site.
 */
public final class HomePageDetails {

    private HomePageDetails() {}

    /** A filled copy; the template's own tree, which is shared, is never changed. */
    public static JsonNode fill(JsonNode puckTree, BusinessProfile profile) {
        JsonNode props = puckTree.path("root").path("props");
        if (!props.isObject() || props.isEmpty()) {
            return puckTree;
        }
        JsonNode copy = puckTree.deepCopy();
        ObjectNode target = (ObjectNode) copy.path("root").path("props");
        target.put("businessName", profile.businessName());
        target.put("address", orEmpty(profile.address()));
        target.put("email", orEmpty(profile.email()));
        ArrayNode phones = JsonNodeFactory.instance.arrayNode();
        if (profile.phone() != null) {
            phones.addObject().put("number", profile.phone());
        }
        target.set("phones", phones);
        boolean hasHours = !profile.openingHours().isEmpty();
        target.put("hours", hasHours ? OpeningHours.hoursText(profile.openingHours()) : "");
        target.put("hoursDetail", hasHours ? OpeningHours.detailText(profile.openingHours()) : "");
        return copy;
    }

    private static String orEmpty(String value) {
        return value == null ? "" : value;
    }
}
