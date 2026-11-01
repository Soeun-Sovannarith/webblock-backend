package org.kshrd.webblock.project.presentation.rest.project;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;

/** The status counts through the whole application, on a real database. */
@ServiceIntegrationTest
class ProjectCountsIntegrationTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    JdbcTemplate jdbc;

    private UUID create(UUID owner, String name) throws Exception {
        UUID projectId = UUID.randomUUID();
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"%s","subdomain":"%s","businessTypeCode":"food_shop","templateCode":"blank",
                                 "businessProfile":{"email":"shop@example.test"}}""".formatted(name, "pc" + projectId.toString().substring(0, 10))))
                .andExpect(status().isCreated());
        return projectId;
    }

    private void setStatus(UUID projectId, String status) {
        jdbc.update(
                "UPDATE project.projects SET status = ?::project.project_status WHERE project_id = ?",
                status,
                projectId);
    }

    @Test
    @DisplayName("FR-14.10: counts per status for the caller's live websites; deleted and other owners' are left out")
    void fr14_10_countsPerStatus() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID active = create(owner, "Active One");
        UUID suspended = create(owner, "Suspended One");
        UUID deleted = create(owner, "Deleted One");
        create(owner, "Draft One");
        create(owner, "Draft Two");
        create(UUID.randomUUID(), "Stranger");
        setStatus(active, "active");
        setStatus(suspended, "suspended");
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", deleted);

        mvc.perform(get("/api/v1/projects/counts").with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.all").value(4))
                .andExpect(jsonPath("$.data.draft").value(2))
                .andExpect(jsonPath("$.data.active").value(1))
                .andExpect(jsonPath("$.data.suspended").value(1));
    }

    @Test
    @DisplayName("FR-14.11: a caller without websites gets zeros, and /counts is never read as a project ID")
    void fr14_11_emptyIsZerosAndCountsIsNotAnId() throws Exception {
        mvc.perform(get("/api/v1/projects/counts").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.all").value(0))
                .andExpect(jsonPath("$.data.draft").value(0))
                .andExpect(jsonPath("$.data.active").value(0))
                .andExpect(jsonPath("$.data.suspended").value(0));
    }

    @Test
    @DisplayName("FR-14.11: counts without a token is 401")
    void fr14_11_withoutATokenIs401() throws Exception {
        mvc.perform(get("/api/v1/projects/counts")).andExpect(status().isUnauthorized());
    }
}
