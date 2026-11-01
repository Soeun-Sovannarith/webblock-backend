package org.kshrd.webblock.project.presentation.rest.project;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.nullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.time.Clock;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.generation.handler.GenerationRequestService;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;

/** The list filters and the undeployed-changes flag through the whole application, on a real database. */
@ServiceIntegrationTest
class ProjectListFiltersIntegrationTest {

    static final String SHA = "0123456789abcdef0123456789abcdef01234567";

    @Autowired
    MockMvc mvc;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    Clock clock;

    @Autowired
    GenerationRequestService requests;

    private void create(UUID owner, UUID projectId, String name) throws Exception {
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"%s","subdomain":"%s","businessTypeCode":"food_shop","templateCode":"blank",
                                 "businessProfile":{"businessName":"Shop","description":null,"phone":null,
                                                    "email":"shop@example.test","address":null,
                                                    "timezone":"Asia/Phnom_Penh"}}""".formatted(name, "lf" + projectId.toString().substring(0, 8))))
                .andExpect(status().isCreated());
    }

    private long editRevision(UUID projectId) {
        return jdbc.queryForObject(
                "SELECT edit_revision FROM project.projects WHERE project_id = ?", Long.class, projectId);
    }

    /** A request captured at the project's current edit revision and then published, as a finished deploy leaves it. */
    private void deploy(UUID owner, UUID projectId) {
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID contract = rows.contract(projectId, owner);
        UUID request = rows.captured(projectId, contract, "{\"componentCatalogVersion\":\"0.1.0\",\"pages\":[]}");
        jdbc.update(
                "UPDATE project.generation_requests SET status = 'published', source_commit_sha = ? "
                        + "WHERE generation_request_id = ?",
                SHA,
                request);
    }

    @Test
    @DisplayName(
            "FR-14.8: undeployed changes are true when never deployed, false right after a deploy, true after a save")
    void fr14_8_undeployedChangesFollowDeploysAndSaves() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID id = UUID.randomUUID();
        create(owner, id, "Shop");

        mvc.perform(get("/api/v1/projects/{id}", id).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.hasUndeployedChanges").value(true))
                .andExpect(jsonPath("$.data.lastDeployedEditRevision").value(nullValue()));

        deploy(owner, id);
        long deployedAt = editRevision(id);
        mvc.perform(get("/api/v1/projects/{id}", id).with(Users.user(owner)))
                .andExpect(jsonPath("$.data.hasUndeployedChanges").value(false))
                .andExpect(jsonPath("$.data.lastDeployedEditRevision").value(deployedAt));
        mvc.perform(get("/api/v1/projects").with(Users.user(owner)))
                .andExpect(jsonPath("$.data[0].hasUndeployedChanges").value(false))
                .andExpect(jsonPath("$.data[0].lastDeployedEditRevision").value(deployedAt));

        jdbc.update("UPDATE project.projects SET edit_revision = edit_revision + 1 WHERE project_id = ?", id);
        mvc.perform(get("/api/v1/projects/{id}", id).with(Users.user(owner)))
                .andExpect(jsonPath("$.data.hasUndeployedChanges").value(true))
                .andExpect(jsonPath("$.data.lastDeployedEditRevision").value(deployedAt));
    }

    @Test
    @DisplayName("FR-14.8: a failed or in-flight request does not count as deployed")
    void fr14_8_onlyPublishedRequestsCount() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID id = UUID.randomUUID();
        create(owner, id, "Shop");
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID contract = rows.contract(id, owner);
        UUID request = rows.captured(id, contract, "{\"componentCatalogVersion\":\"0.1.0\",\"pages\":[]}");

        mvc.perform(get("/api/v1/projects/{id}", id).with(Users.user(owner)))
                .andExpect(jsonPath("$.data.hasUndeployedChanges").value(true))
                .andExpect(jsonPath("$.data.lastDeployedEditRevision").value(nullValue()));
        jdbc.update(
                "UPDATE project.generation_requests SET status = 'failed' WHERE generation_request_id = ?", request);
        mvc.perform(get("/api/v1/projects/{id}", id).with(Users.user(owner)))
                .andExpect(jsonPath("$.data.lastDeployedEditRevision").value(nullValue()));
    }

    @Test
    @DisplayName("FR-14.9: the deployed revisions of a whole page come from one grouped query (max per project)")
    void fr14_9_onePageOneGroupedQuery() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID a = UUID.randomUUID();
        UUID b = UUID.randomUUID();
        UUID never = UUID.randomUUID();
        create(owner, a, "A");
        create(owner, b, "B");
        create(owner, never, "N");
        deploy(owner, a);
        long first = editRevision(a);
        jdbc.update("UPDATE project.projects SET edit_revision = edit_revision + 2 WHERE project_id = ?", a);
        deploy(owner, a);
        deploy(owner, b);

        Map<UUID, Long> deployed = requests.lastDeployedEditRevisions(List.of(a, b, never));

        assertThat(deployed).containsOnlyKeys(a, b);
        assertThat(deployed.get(a)).isEqualTo(first + 2);
        assertThat(deployed.get(b)).isEqualTo(editRevision(b));
        assertThat(requests.lastDeployedEditRevisions(List.of())).isEmpty();
    }

    @Test
    @DisplayName("FR-14.5: the list searches by name and filters by status over HTTP; strangers' websites never appear")
    void fr14_5_listFiltersOverHttp() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID other = UUID.randomUUID();
        UUID cafe = UUID.randomUUID();
        UUID bakery = UUID.randomUUID();
        create(owner, cafe, "Kiln Cafe");
        create(owner, bakery, "Bakery");
        create(other, UUID.randomUUID(), "Kiln Stranger");
        jdbc.update("UPDATE project.projects SET status = 'active' WHERE project_id = ?", bakery);

        mvc.perform(get("/api/v1/projects").param("q", "KILN").with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(1))
                .andExpect(jsonPath("$.data[0].projectId").value(cafe.toString()));
        mvc.perform(get("/api/v1/projects").param("status", "active").with(Users.user(owner)))
                .andExpect(jsonPath("$.data.length()").value(1))
                .andExpect(jsonPath("$.data[0].projectId").value(bakery.toString()));
        mvc.perform(get("/api/v1/projects")
                        .param("q", "kiln")
                        .param("status", "active")
                        .with(Users.user(owner)))
                .andExpect(jsonPath("$.data.length()").value(0));
        mvc.perform(get("/api/v1/projects").param("status", "bogus").with(Users.user(owner)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }
}
