package org.kshrd.webblock.project.presentation.rest.project;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** The sub-domain check in the whole application, against the real project database (FR-13). */
@ServiceIntegrationTest
class SubdomainCheckIntegrationTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    JsonMapper json;

    private String project(boolean deleted) {
        UUID projectId = UUID.randomUUID();
        String subdomain = TestSubdomains.of(projectId);
        jdbc.update(
                "INSERT INTO project.projects (project_id, owner_user_id, name, subdomain, business_type_id, "
                        + "component_catalog_version, status, created_at, updated_at, deleted_at) "
                        + "VALUES (?, ?, 'Held shop', ?, ?, '0.1.0', 'draft', now(), now(), "
                        + (deleted ? "now()" : "NULL") + ")",
                projectId,
                UUID.randomUUID(),
                subdomain,
                UUID.randomUUID());
        return subdomain;
    }

    /** The answer's data with the label as given taken out, so two answers for two labels can be compared. */
    private JsonNode data(String subdomain) throws Exception {
        String body = mvc.perform(
                        get("/api/v1/subdomains/{subdomain}", subdomain).with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.subdomain").value(subdomain))
                .andReturn()
                .getResponse()
                .getContentAsString();
        JsonNode data = json.readTree(body).get("data");
        return ((ObjectNode) data).without("subdomain");
    }

    @Test
    @DisplayName("FR-13.13: a free sub-domain is available")
    void fr13_13_aFreeSubdomainIsAvailable() throws Exception {
        String free = TestSubdomains.of(UUID.randomUUID());
        mvc.perform(get("/api/v1/subdomains/{subdomain}", free).with(Users.anyUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.subdomain").value(free))
                .andExpect(jsonPath("$.data.available").value(true))
                .andExpect(jsonPath("$.data.reason").isEmpty());
    }

    @Test
    @DisplayName("FR-13.14: a live and a deleted holder give the same answer, TAKEN, and nothing about the holder")
    void fr13_14_liveAndDeletedHoldersAnswerTheSame() throws Exception {
        String live = project(false);
        String gone = project(true);
        JsonNode liveAnswer = data(live);
        JsonNode goneAnswer = data(gone);
        assertThat(liveAnswer).isEqualTo(goneAnswer);
        assertThat(liveAnswer.get("available").asBoolean()).isFalse();
        assertThat(liveAnswer.get("reason").asString()).isEqualTo("TAKEN");
        assertThat(liveAnswer.propertyNames()).containsExactlyInAnyOrder("available", "reason");
    }

    @Test
    @DisplayName("FR-13.15: a label create refuses is INVALID: ab, -shop, a 64-character label, auth, upper case")
    void fr13_15_aLabelCreateRefusesIsInvalid() throws Exception {
        for (String bad : new String[] {"ab", "-shop", "a".repeat(64), "auth", "Kilncoffee"}) {
            mvc.perform(get("/api/v1/subdomains/{subdomain}", bad).with(Users.anyUser()))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.data.subdomain").value(bad))
                    .andExpect(jsonPath("$.data.available").value(false))
                    .andExpect(jsonPath("$.data.reason").value("INVALID"));
        }
    }

    @Test
    @DisplayName("FR-13.16: no token is 401")
    void fr13_16_noTokenIs401() throws Exception {
        mvc.perform(get("/api/v1/subdomains/{subdomain}", "kilncoffee")).andExpect(status().isUnauthorized());
    }
}
