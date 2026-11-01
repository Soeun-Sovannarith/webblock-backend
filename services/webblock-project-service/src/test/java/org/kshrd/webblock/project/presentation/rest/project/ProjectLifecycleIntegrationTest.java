package org.kshrd.webblock.project.presentation.rest.project;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.Clock;
import java.time.Duration;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.messaging.handler.OutboxRecords;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.env.Environment;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/**
 * Deleting, suspending and reactivating a website through the real application: the REST answers, the rows, the
 * outbox and the catalog (the registration and archive jobs run live against the catalog database).
 */
@ServiceIntegrationTest
class ProjectLifecycleIntegrationTest {

    private static final String COMMIT = "0123456789abcdef0123456789abcdef01234567";

    @Autowired
    MockMvc mvc;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    Clock clock;

    @Autowired
    OutboxRecords outbox;

    @Autowired
    Environment environment;

    private UUID create(UUID owner) throws Exception {
        UUID projectId = UUID.randomUUID();
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"Shop %1$s","subdomain":"lc%1$s","businessTypeCode":"tech_shop","templateCode":"blank",
                                 "businessProfile":{"businessName":"Shop","description":null,"phone":null,
                                                    "email":"shop@example.test","address":null,
                                                    "timezone":"Asia/Phnom_Penh"}}""".formatted(projectId.toString().substring(0, 8))))
                .andExpect(status().isCreated());
        return projectId;
    }

    private ResultActions deleteAs(UUID user, UUID projectId) throws Exception {
        return mvc.perform(delete("/api/v1/projects/{id}", projectId).with(Users.user(user)));
    }

    private String projectStatus(UUID projectId) {
        return jdbc.queryForObject(
                "SELECT status::text FROM project.projects WHERE project_id = ?", String.class, projectId);
    }

    private boolean deletedAtSet(UUID projectId) {
        return Boolean.TRUE.equals(jdbc.queryForObject(
                "SELECT deleted_at IS NOT NULL FROM project.projects WHERE project_id = ?", Boolean.class, projectId));
    }

    private String catalogStatus(UUID projectId) throws Exception {
        try (Connection c = DriverManager.getConnection(
                environment.getProperty("webblock.catalog.datasource.url"),
                PostgresFixture.CATALOG_MIGRATOR_LOGIN,
                PostgresFixture.PASSWORD)) {
            c.createStatement().execute("SET ROLE catalog_owner");
            try (PreparedStatement ps =
                    c.prepareStatement("SELECT status::text FROM catalog.websites WHERE website_id = ?")) {
                ps.setObject(1, projectId);
                try (ResultSet rs = ps.executeQuery()) {
                    return rs.next() ? rs.getString(1) : null;
                }
            }
        }
    }

    @Test
    @DisplayName("FR-38.1: the owner deletes a draft website: 204, deleted_at set, event and archive job stored")
    void fr38_1_theOwnerDeletesAWebsite() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);

        deleteAs(owner, projectId)
                .andExpect(status().isNoContent())
                .andExpect(content().string(""));

        assertThat(deletedAtSet(projectId)).isTrue();
        // The event's outbox key is the project ID; the archive job's is catalog-archive:<id>.
        assertThat(outbox.count(projectId.toString()).records()).isEqualTo(1);
        assertThat(outbox.count("catalog-archive:" + projectId).records()).isEqualTo(1);
    }

    @Test
    @DisplayName("FR-38.2: a stranger's delete is 404 and changes nothing; a repeat delete is 404")
    void fr38_2_aStrangerAndARepeatAreNotFound() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);

        deleteAs(UUID.randomUUID(), projectId)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        assertThat(deletedAtSet(projectId)).isFalse();
        assertThat(outbox.count("catalog-archive:" + projectId).records()).isZero();

        deleteAs(owner, projectId).andExpect(status().isNoContent());
        deleteAs(owner, projectId)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        deleteAs(owner, UUID.randomUUID()).andExpect(status().isNotFound());
        assertThat(outbox.count("catalog-archive:" + projectId).records())
                .as("one archive job, however often the delete is repeated")
                .isEqualTo(1);
    }

    @Test
    @DisplayName("FR-38.3: a deleted website is 404 on get, list, pages and generation, and not in the list")
    void fr38_3_aDeletedWebsiteIsNotFoundEverywhere() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        UUID kept = create(owner);
        UUID contractId = new GenerationRows(jdbc, clock).contract(projectId, owner);
        deleteAs(owner, projectId).andExpect(status().isNoContent());

        mvc.perform(get("/api/v1/projects/{id}", projectId).with(Users.user(owner)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        mvc.perform(get("/api/v1/projects").with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(1))
                .andExpect(jsonPath("$.data[0].projectId").value(kept.toString()));
        mvc.perform(get("/api/v1/projects/{id}/pages", projectId).with(Users.user(owner)))
                .andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/projects/{id}/generation-contracts", projectId).with(Users.user(owner)))
                .andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/projects/{id}/generation-requests", projectId).with(Users.user(owner)))
                .andExpect(status().isNotFound());
        mvc.perform(post("/api/v1/projects/{id}/generation-requests", projectId)
                        .with(Users.user(owner))
                        .header("Idempotency-Key", "key-deleted-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"generationContractId\":\"" + contractId + "\"}"))
                .andExpect(status().isNotFound());
        mvc.perform(post("/api/v1/projects/{id}/suspend", projectId).with(Users.user(owner)))
                .andExpect(status().isNotFound());
        mvc.perform(post("/api/v1/projects/{id}/reactivate", projectId).with(Users.user(owner)))
                .andExpect(status().isNotFound());
        assertThat(jdbc.queryForObject(
                        "SELECT name FROM project.projects WHERE project_id = ?", String.class, projectId))
                .isEqualTo("Shop " + projectId.toString().substring(0, 8));
    }

    @Test
    @DisplayName("FR-38.4: deleting a website with a request in flight is 409 GENERATION_IN_PROGRESS; nothing changes")
    void fr38_4_deleteWhileARequestIsInFlightIsRefused() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID requestId = rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");

        deleteAs(owner, projectId)
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("GENERATION_IN_PROGRESS"));

        assertThat(deletedAtSet(projectId)).isFalse();
        assertThat(outbox.count("catalog-archive:" + projectId).records()).isZero();
        assertThat(jdbc.queryForObject(
                        "SELECT status::text FROM project.generation_requests WHERE generation_request_id = ?",
                        String.class,
                        requestId))
                .isEqualTo("captured");
    }

    @Test
    @DisplayName("FR-38.8: a draft website whose requests all ended can be deleted")
    void fr38_8_aFailedRequestDoesNotStopTheDelete() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID requestId = rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");
        jdbc.update(
                "UPDATE project.generation_requests SET status = 'failed', failure_code = 'admission_error'"
                        + " WHERE generation_request_id = ?",
                requestId);

        deleteAs(owner, projectId).andExpect(status().isNoContent());
    }

    @Test
    @DisplayName("FR-38.9: an active or a suspended website is 409 PROJECT_NOT_DELETABLE; a stranger gets 404")
    void fr38_9_onlyADraftCanBeDeleted() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID active = create(owner);
        UUID suspended = create(owner);
        jdbc.update("UPDATE project.projects SET status = 'active' WHERE project_id = ?", active);
        mvc.perform(post("/api/v1/projects/{id}/suspend", suspended).with(Users.user(owner)))
                .andExpect(status().isOk());

        for (UUID projectId : new UUID[] {active, suspended}) {
            deleteAs(UUID.randomUUID(), projectId)
                    .andExpect(status().isNotFound())
                    .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
            deleteAs(owner, projectId)
                    .andExpect(status().isConflict())
                    .andExpect(jsonPath("$.code").value("PROJECT_NOT_DELETABLE"));
            assertThat(deletedAtSet(projectId)).isFalse();
            assertThat(outbox.count("catalog-archive:" + projectId).records()).isZero();
        }
    }

    @Test
    @DisplayName("FR-38.10: the owner's repeat create of a deleted website's ID is 409 PROJECT_ID_CONFLICT")
    void fr38_10_aRepeatCreateOfADeletedWebsiteIsAConflict() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        String body = """
                {"name":"Shop","subdomain":"rp%s","businessTypeCode":"tech_shop","templateCode":"blank",
                 "businessProfile":{"businessName":"Shop","description":null,"phone":null,
                                    "email":"shop@example.test","address":null,
                                    "timezone":"Asia/Phnom_Penh"}}""".formatted(projectId.toString().substring(0, 8));
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated());
        deleteAs(owner, projectId).andExpect(status().isNoContent());

        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("PROJECT_ID_CONFLICT"));
    }

    @Test
    @DisplayName("FR-38.5: the internal access check answers NOT_FOUND for a deleted project, any action")
    void fr38_5_theAccessCheckAnswersNotFoundForADeletedProject() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        String body = "{\"userId\":\"%s\",\"projectId\":\"%s\",\"action\":\"%s\"}";
        mvc.perform(post("/internal/v1/access-checks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body.formatted(owner, projectId, "deploy_release")))
                .andExpect(jsonPath("$.data.allowed").value(true));

        deleteAs(owner, projectId).andExpect(status().isNoContent());

        for (String action : new String[] {"deploy_release", "list_releases"}) {
            mvc.perform(post("/internal/v1/access-checks")
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(body.formatted(owner, projectId, action)))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.data.allowed").value(false))
                    .andExpect(jsonPath("$.data.reason").value("NOT_FOUND"));
        }
    }

    @Test
    @DisplayName("FR-40.1: the registered catalog website is archived after the delete; the row stays")
    void fr40_1_theCatalogWebsiteIsArchived() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(() -> assertThat(catalogStatus(projectId)).isEqualTo("active"));

        deleteAs(owner, projectId).andExpect(status().isNoContent());

        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(() -> assertThat(catalogStatus(projectId)).isEqualTo("archived"));
        assertThat(outbox.count("catalog-archive:" + projectId).unfinished()).isZero();
    }

    @Test
    @DisplayName("FR-40.2: deleting right after create, before the registration ran, still ends archived")
    void fr40_2_deleteBeforeTheRegistrationRan() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);

        deleteAs(owner, projectId).andExpect(status().isNoContent());

        await().atMost(Duration.ofSeconds(60))
                .untilAsserted(() -> assertThat(catalogStatus(projectId)).isEqualTo("archived"));
    }

    @Test
    @DisplayName("FR-39.1: suspend makes a draft website suspended and read-only; a repeat changes nothing")
    void fr39_1_suspendMakesTheWebsiteReadOnly() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);

        mvc.perform(post("/api/v1/projects/{id}/suspend", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("suspended"))
                .andExpect(jsonPath("$.data.readOnly").value(true))
                .andExpect(jsonPath("$.data.ownerUserId").doesNotExist());
        assertThat(projectStatus(projectId)).isEqualTo("suspended");
        mvc.perform(post("/api/v1/projects/{id}/suspend", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("suspended"));
    }

    @Test
    @DisplayName("FR-39.2: suspend makes an active website suspended; reactivate returns it to active")
    void fr39_2_reactivateReturnsAnActiveWebsiteToActive() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        jdbc.update("UPDATE project.projects SET status = 'active' WHERE project_id = ?", projectId);
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID requestId = rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");
        jdbc.update(
                "UPDATE project.generation_requests SET status = 'published', source_commit_sha = ?"
                        + " WHERE generation_request_id = ?",
                COMMIT,
                requestId);

        mvc.perform(post("/api/v1/projects/{id}/suspend", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("suspended"));
        mvc.perform(post("/api/v1/projects/{id}/reactivate", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("active"))
                .andExpect(jsonPath("$.data.readOnly").value(false));
    }

    @Test
    @DisplayName("FR-39.3: reactivate returns a website never published to draft; a not-suspended one is unchanged")
    void fr39_3_reactivateReturnsANeverPublishedWebsiteToDraft() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        GenerationRows rows = new GenerationRows(jdbc, clock);
        rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");

        mvc.perform(post("/api/v1/projects/{id}/reactivate", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("draft"));
        mvc.perform(post("/api/v1/projects/{id}/suspend", projectId).with(Users.user(owner)))
                .andExpect(status().isOk());
        mvc.perform(post("/api/v1/projects/{id}/reactivate", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("draft"));
        assertThat(projectStatus(projectId)).isEqualTo("draft");
    }

    @Test
    @DisplayName("FR-39.4: a suspended website refuses a page save and a generation request with 422")
    void fr39_4_aSuspendedWebsiteIsReadOnly() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        UUID contractId = new GenerationRows(jdbc, clock).contract(projectId, owner);
        mvc.perform(post("/api/v1/projects/{id}/suspend", projectId).with(Users.user(owner)))
                .andExpect(status().isOk());

        UUID pageId =
                jdbc.queryForObject("SELECT page_id FROM project.pages WHERE project_id = ?", UUID.class, projectId);
        mvc.perform(put("/api/v1/projects/{id}/pages/{pageId}", projectId, pageId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"expectedEditRevision":0,"sortOrder":0,"name":"Home","path":"/",
                                 "puckTree":{"content":[],"root":{}}}"""))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("PROJECT_READ_ONLY"));
        mvc.perform(post("/api/v1/projects/{id}/generation-requests", projectId)
                        .with(Users.user(owner))
                        .header("Idempotency-Key", "key-suspended-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"generationContractId\":\"" + contractId + "\"}"))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("PROJECT_READ_ONLY"));
        mvc.perform(get("/api/v1/projects/{id}", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.name")
                        .value("Shop " + projectId.toString().substring(0, 8)));
    }

    @Test
    @DisplayName("FR-39.5: a stranger's suspend and reactivate are 404 and change nothing")
    void fr39_5_aStrangerCannotSuspendOrReactivate() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID stranger = UUID.randomUUID();
        UUID projectId = create(owner);

        mvc.perform(post("/api/v1/projects/{id}/suspend", projectId).with(Users.user(stranger)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        assertThat(projectStatus(projectId)).isEqualTo("draft");
        mvc.perform(post("/api/v1/projects/{id}/suspend", projectId).with(Users.user(owner)))
                .andExpect(status().isOk());
        mvc.perform(post("/api/v1/projects/{id}/reactivate", projectId).with(Users.user(stranger)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        assertThat(projectStatus(projectId)).isEqualTo("suspended");
        mvc.perform(post("/api/v1/projects/{id}/suspend", UUID.randomUUID()).with(Users.user(owner)))
                .andExpect(status().isNotFound());
    }
}
