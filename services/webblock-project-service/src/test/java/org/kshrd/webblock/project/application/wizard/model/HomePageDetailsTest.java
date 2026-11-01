package org.kshrd.webblock.project.application.wizard.model;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.kshrd.webblock.project.application.catalog.model.OpeningRange;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class HomePageDetailsTest {

    private static final JsonMapper JSON = JsonMapper.builder().build();

    private static final String STYLE_TREE = """
            {"root":{"props":{"templateId":"sweet-pastel","businessName":"Petal","statusText":"Open now",
              "hours":"9:00 – 18:00","hoursDetail":"x","address":"Old street","email":"old@example.test",
              "telegram":"petal","phones":[{"number":"012 345 678"}]}},"content":[]}""";

    private static final BusinessProfile PROFILE = new BusinessProfile(
            "Kiln Coffee",
            null,
            "+855 12 999 000",
            "hi@kiln.test",
            "1 Main St",
            "Asia/Phnom_Penh",
            List.of(new OpeningRange("MONDAY", "FRIDAY", "07:30", "16:00", false)));

    @Test
    @DisplayName("FR-10.11: the home page's root props carry the business details; other props stay")
    void fr10_11_theDetailsAreFilledAndTheRestStays() throws Exception {
        JsonNode template = JSON.readTree(STYLE_TREE);
        JsonNode filled = HomePageDetails.fill(template, PROFILE);
        JsonNode props = filled.path("root").path("props");
        assertThat(props.path("businessName").asString()).isEqualTo("Kiln Coffee");
        assertThat(props.path("address").asString()).isEqualTo("1 Main St");
        assertThat(props.path("email").asString()).isEqualTo("hi@kiln.test");
        assertThat(props.path("phones").toString()).isEqualTo("[{\"number\":\"+855 12 999 000\"}]");
        assertThat(props.path("hours").asString()).isEqualTo("7:30 – 16:00");
        assertThat(props.path("hoursDetail").asString()).isEqualTo("Mon – Fri 7:30 – 16:00 · Sat – Sun closed");
        assertThat(props.path("templateId").asString()).isEqualTo("sweet-pastel");
        assertThat(props.path("statusText").asString()).isEqualTo("Open now");
        assertThat(props.path("telegram").asString()).isEqualTo("petal");
        assertThat(filled.path("content").isArray()).isTrue();
        // The template's own tree, shared by every creation, is never changed.
        assertThat(template).isEqualTo(JSON.readTree(STYLE_TREE));
    }

    @Test
    @DisplayName("FR-10.11: details the client left out are emptied, never the template's sample")
    void fr10_11_missingDetailsAreEmptied() throws Exception {
        JsonNode filled = HomePageDetails.fill(
                JSON.readTree(STYLE_TREE), new BusinessProfile("Kiln Coffee", null, null, null, null, "UTC"));
        JsonNode props = filled.path("root").path("props");
        assertThat(props.path("businessName").asString()).isEqualTo("Kiln Coffee");
        assertThat(props.path("phones").toString()).isEqualTo("[]");
        assertThat(props.path("address").asString()).isEmpty();
        assertThat(props.path("email").asString()).isEmpty();
        assertThat(props.path("hours").asString()).isEmpty();
        assertThat(props.path("hoursDetail").asString()).isEmpty();
    }

    @Test
    @DisplayName("FR-10.11: a tree with empty root props (blank) is returned unchanged")
    void fr10_11_blankIsUnchanged() throws Exception {
        JsonNode blank = JSON.readTree("{\"root\":{\"props\":{}},\"content\":[]}");
        assertThat(HomePageDetails.fill(blank, PROFILE)).isSameAs(blank);
        JsonNode noRoot = JSON.readTree("{}");
        assertThat(HomePageDetails.fill(noRoot, PROFILE)).isSameAs(noRoot);
    }
}
