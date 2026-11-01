package org.kshrd.webblock.project.presentation.rest.page;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.sql.Timestamp;
import java.time.Clock;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Deploy versions (FR-41) through the whole application, on a real database. */
@ServiceIntegrationTest
class PageVersionIntegrationTest {

    static final String SHA = "0123456789abcdef0123456789abcdef01234567";
    static final String RESTORE = "/api/v1/projects/{id}/versions/{v}/restore";
    static final String VERSIONS = "/api/v1/projects/{id}/versions";

    @Autowired
    MockMvc mvc;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    Clock clock;

    @Autowired
    JsonMapper json;

    private UUID create(UUID owner) throws Exception {
        UUID projectId = UUID.randomUUID();
        String id = projectId.toString().substring(0, 8);
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"Site %1$s","subdomain":"pv%1$s","businessTypeCode":"food_shop",
                                 "templateCode":"blank",
                                 "businessProfile":{"businessName":"Shop","description":null,"phone":null,
                                                    "email":"shop@example.test","address":null,
                                                    "timezone":"Asia/Phnom_Penh"}}""".formatted(id)))
                .andExpect(status().isCreated());
        return projectId;
    }

    private JsonNode pages(UUID owner, UUID projectId) throws Exception {
        String body = mvc.perform(get("/api/v1/projects/{id}/pages", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andReturn()
                .getResponse()
                .getContentAsString();
        return json.readTree(body).get("data");
    }

    /** Saves the home page with a new SEO title (the tree stays) and returns the new edit revision. */
    private long saveSeo(UUID owner, UUID projectId, String seoTitle) throws Exception {
        JsonNode data = pages(owner, projectId);
        JsonNode home = data.get("pages").get(0);
        ObjectNode body = json.createObjectNode();
        body.put("expectedEditRevision", data.get("editRevision").asLong());
        body.put("name", home.get("name").asString());
        body.put("path", "/");
        body.set("puckTree", home.get("puckTree"));
        body.put("seoTitle", seoTitle);
        body.putNull("seoDescription");
        body.put("sortOrder", 0);
        String result = mvc.perform(put(
                                "/api/v1/projects/{id}/pages/{pageId}",
                                projectId,
                                home.get("pageId").asString())
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(body)))
                .andExpect(status().isOk())
                .andReturn()
                .getResponse()
                .getContentAsString();
        return json.readTree(result).get("data").get("editRevision").asLong();
    }

    /** A published request that froze the current home page with the given SEO title, published at the given time. */
    private UUID deploy(UUID owner, UUID projectId, String seoTitle, Instant publishedAt) throws Exception {
        JsonNode home = pages(owner, projectId).get("pages").get(0);
        ObjectNode page = json.createObjectNode();
        page.put("pageId", home.get("pageId").asString());
        page.put("path", "/");
        page.put("name", home.get("name").asString());
        page.put("sortOrder", 0);
        page.put("seoTitle", seoTitle);
        page.putNull("seoDescription");
        page.set("puckTree", home.get("puckTree"));
        ObjectNode input = json.createObjectNode();
        input.put("componentCatalogVersion", "0.1.0");
        input.putArray("pages").add(page);
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID request = rows.captured(projectId, rows.contract(projectId, owner), json.writeValueAsString(input));
        publish(request, publishedAt);
        return request;
    }

    private void publish(UUID requestId, Instant at) {
        jdbc.update(
                "UPDATE project.generation_requests SET status = 'published', source_commit_sha = ?, updated_at = ?"
                        + " WHERE generation_request_id = ?",
                SHA,
                Timestamp.from(at),
                requestId);
    }

    private static String restoreBody(long revision) {
        return "{\"expectedEditRevision\":" + revision + "}";
    }

    private org.springframework.test.web.servlet.ResultActions restore(
            UUID user, UUID projectId, UUID versionId, long revision) throws Exception {
        return mvc.perform(post(RESTORE, projectId, versionId)
                .with(Users.user(user))
                .contentType(MediaType.APPLICATION_JSON)
                .content(restoreBody(revision)));
    }

    @Test
    @DisplayName("FR-41.1: deploys are listed newest first by publish time; only the newest is current")
    void fr41_1_listNewestFirst() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        Instant base = clock.instant();
        UUID older = deploy(owner, projectId, "Older", base.minusSeconds(60));
        UUID newer = deploy(owner, projectId, "Newer", base.minusSeconds(10));

        mvc.perform(get(VERSIONS, projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(2))
                .andExpect(jsonPath("$.data[0].versionId").value(newer.toString()))
                .andExpect(jsonPath("$.data[0].current").value(true))
                .andExpect(jsonPath("$.data[0].editRevision").value(0))
                .andExpect(jsonPath("$.data[0].publishedAt").exists())
                .andExpect(jsonPath("$.data[1].versionId").value(older.toString()))
                .andExpect(jsonPath("$.data[1].current").value(false));
    }

    @Test
    @DisplayName("FR-41.2: captured and failed requests are not versions")
    void fr41_2_onlyPublishedRequestsAreListed() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID failed = rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");
        jdbc.update(
                "UPDATE project.generation_requests SET status = 'failed', failure_code = 'admission_error'"
                        + " WHERE generation_request_id = ?",
                failed);
        UUID published = deploy(owner, projectId, "Live", clock.instant());
        rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");

        mvc.perform(get(VERSIONS, projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(1))
                .andExpect(jsonPath("$.data[0].versionId").value(published.toString()));
    }

    @Test
    @DisplayName("FR-41.3: a stranger, an unknown and a deleted project are 404 PROJECT_NOT_FOUND on list and restore")
    void fr41_3_notTheOwnerIs404() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        UUID version = deploy(owner, projectId, "Live", clock.instant());
        UUID stranger = UUID.randomUUID();

        mvc.perform(get(VERSIONS, projectId).with(Users.user(stranger)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        mvc.perform(get(VERSIONS, UUID.randomUUID()).with(Users.user(owner)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        restore(stranger, projectId, version, 0)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        restore(stranger, projectId, UUID.randomUUID(), 0)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));

        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", projectId);
        mvc.perform(get(VERSIONS, projectId).with(Users.user(owner)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
        restore(owner, projectId, version, 0)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("PROJECT_NOT_FOUND"));
    }

    @Test
    @DisplayName("FR-41.4: the list pages by cursor, newest first, and refuses a bad cursor")
    void fr41_4_paging() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        Instant base = clock.instant();
        UUID v1 = deploy(owner, projectId, "One", base.minusSeconds(30));
        UUID v2 = deploy(owner, projectId, "Two", base.minusSeconds(20));
        UUID v3 = deploy(owner, projectId, "Three", base.minusSeconds(10));

        String first = mvc.perform(get(VERSIONS, projectId).param("limit", "2").with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(2))
                .andExpect(jsonPath("$.data[0].versionId").value(v3.toString()))
                .andExpect(jsonPath("$.data[1].versionId").value(v2.toString()))
                .andReturn()
                .getResponse()
                .getContentAsString();
        String cursor = json.readTree(first).findValue("nextCursor").asString();
        assertThat(cursor).isNotBlank();

        mvc.perform(get(VERSIONS, projectId)
                        .param("limit", "2")
                        .param("cursor", cursor)
                        .with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(1))
                .andExpect(jsonPath("$.data[0].versionId").value(v1.toString()))
                .andExpect(jsonPath("$.data[0].current").value(false));
        mvc.perform(get(VERSIONS, projectId).param("cursor", "not-a-cursor").with(Users.user(owner)))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("FR-41.5: a restore writes the frozen home page into the live one, moves the revision by one and"
            + " leaves the website with undeployed changes")
    void fr41_5_restore() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        UUID version = deploy(owner, projectId, "Deployed title", clock.instant());
        long revision = saveSeo(owner, projectId, "Edited later");
        JsonNode frozenTree = pages(owner, projectId).get("pages").get(0).get("puckTree");

        String body = restore(owner, projectId, version, revision)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.editRevision").value(revision + 1))
                .andExpect(jsonPath("$.data.page.path").value("/"))
                .andExpect(jsonPath("$.data.page.seoTitle").value("Deployed title"))
                .andReturn()
                .getResponse()
                .getContentAsString();
        assertThat(json.readTree(body).at("/data/page/puckTree")).isEqualTo(frozenTree);

        JsonNode after = pages(owner, projectId);
        assertThat(after.get("editRevision").asLong()).isEqualTo(revision + 1);
        assertThat(after.get("pages").size()).isEqualTo(1);
        assertThat(after.at("/pages/0/seoTitle").asString()).isEqualTo("Deployed title");
        mvc.perform(get("/api/v1/projects/{id}", projectId).with(Users.user(owner)))
                .andExpect(jsonPath("$.data.hasUndeployedChanges").value(true));
        mvc.perform(get(VERSIONS, projectId).with(Users.user(owner)))
                .andExpect(jsonPath("$.data.length()").value(1));
    }

    @Test
    @DisplayName("FR-41.6: a restore with a stale revision is 409 STALE_EDIT_REVISION and changes nothing")
    void fr41_6_staleRevision() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        UUID version = deploy(owner, projectId, "Deployed title", clock.instant());
        long revision = saveSeo(owner, projectId, "Edited later");

        restore(owner, projectId, version, revision - 1)
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("STALE_EDIT_REVISION"));

        JsonNode after = pages(owner, projectId);
        assertThat(after.get("editRevision").asLong()).isEqualTo(revision);
        assertThat(after.at("/pages/0/seoTitle").asString()).isEqualTo("Edited later");
    }

    @Test
    @DisplayName("FR-41.7: a suspended website refuses a restore with 422 PROJECT_READ_ONLY; the list still works")
    void fr41_7_suspendedIsReadOnly() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        UUID version = deploy(owner, projectId, "Deployed title", clock.instant());
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", projectId);

        restore(owner, projectId, version, 0)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("PROJECT_READ_ONLY"));
        mvc.perform(get(VERSIONS, projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(1));
    }

    @Test
    @DisplayName("FR-41.8: an unknown, an unpublished and another website's version are 404 VERSION_NOT_FOUND")
    void fr41_8_versionNotFound() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        UUID other = create(owner);
        UUID otherVersion = deploy(owner, other, "Other", clock.instant());
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID captured = rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");

        for (UUID version : new UUID[] {UUID.randomUUID(), captured, otherVersion}) {
            restore(owner, projectId, version, 0)
                    .andExpect(status().isNotFound())
                    .andExpect(jsonPath("$.code").value("VERSION_NOT_FOUND"));
        }
        assertThat(pages(owner, projectId).get("editRevision").asLong()).isZero();
    }

    @Test
    @DisplayName("FR-41.9: a deploy without a home page, or a tree the catalog refuses, restores nothing")
    void fr41_9_unusableFrozenInput() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = create(owner);
        GenerationRows rows = new GenerationRows(jdbc, clock);
        UUID noHome = rows.captured(projectId, rows.contract(projectId, owner), "{\"pages\":[]}");
        publish(noHome, clock.instant().minusSeconds(60));
        UUID badTree = rows.captured(
                projectId,
                rows.contract(projectId, owner),
                "{\"pages\":[{\"path\":\"/\",\"name\":\"Home\",\"sortOrder\":0,"
                        + "\"puckTree\":{\"content\":[{\"type\":\"NoSuchBlock\",\"props\":{}}],\"root\":{}}}]}");
        publish(badTree, clock.instant());

        restore(owner, projectId, noHome, 0)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("VERSION_NOT_FOUND"));
        restore(owner, projectId, badTree, 0).andExpect(status().isBadRequest());
        assertThat(pages(owner, projectId).get("editRevision").asLong()).isZero();
    }
}
