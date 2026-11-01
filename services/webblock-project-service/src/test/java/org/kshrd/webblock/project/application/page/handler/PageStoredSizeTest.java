package org.kshrd.webblock.project.application.page.handler;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.page.model.NewPage;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.infrastructure.config.page.PageConfigImport;
import org.kshrd.webblock.project.infrastructure.config.project.ProjectConfigImport;
import org.kshrd.webblock.project.support.JpaSliceTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.ImportAutoConfiguration;
import org.springframework.boot.jackson.autoconfigure.JacksonAutoConfiguration;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * H-1: the tree limit (1 MiB, the production value) bounds what PostgreSQL stores, not only what Jackson wrote. The
 * worst tree still accepted packs the number whose jsonb text grows the most, 1e31: Jackson writes "1.0E31," (7
 * bytes) and jsonb "10000000000000000000000000000000, " (34 bytes), under 5 times. Longer plain forms are refused.
 */
@JpaSliceTest
@Import({
    PageService.class,
    PageRules.class,
    PageConfigImport.class,
    ProjectService.class,
    ProjectConfigImport.class,
    PuckValidator.class
})
@ImportAutoConfiguration(JacksonAutoConfiguration.class)
@TestPropertySource(properties = "webblock.pages.max-puck-tree-bytes=" + PageStoredSizeTest.LIMIT)
class PageStoredSizeTest {

    static final int LIMIT = 1_048_576;

    /** The bound this test asserts: the stored jsonb text is at most 5 times the limit. */
    static final long STORED_BOUND = 5L * LIMIT;

    @Autowired
    PageService pages;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    JsonMapper json;

    @Test
    void theWorstTreeJustUnderTheLimitIsStoredWithinFiveTimesTheLimit() {
        UUID projectId = Uuids.v7();
        ProjectRecord p = tx.execute(s -> projects.create(new NewProject(
                projectId, UUID.randomUUID(), "P", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        String head = "{\"root\":{\"props\":{\"n\":[1e31";
        String tail = "]}},\"content\":[]}";
        int count = (LIMIT - head.length() - tail.length()) / ",1.0E31".length();
        JsonNode tree = json.readTree(head + ",1e31".repeat(count) + tail);
        int checked = json.writeValueAsString(tree).getBytes(StandardCharsets.UTF_8).length;
        assertThat(checked).as("just under the limit").isBetween(LIMIT - 16, LIMIT);

        tx.executeWithoutResult(s ->
                pages.insertInitialPages(projectId, "0.1.0", List.of(new NewPage("Big", "/", tree, null, null, 0))));
        UUID pageId = pages.list(projectId, p.ownerUserId()).pages().getFirst().pageId();

        Long stored = jdbc.queryForObject(
                "SELECT octet_length(puck_tree::text) FROM project.pages WHERE page_id = ?", Long.class, pageId);
        assertThat(stored).isGreaterThan((long) LIMIT).isLessThanOrEqualTo(STORED_BOUND);
    }
}
