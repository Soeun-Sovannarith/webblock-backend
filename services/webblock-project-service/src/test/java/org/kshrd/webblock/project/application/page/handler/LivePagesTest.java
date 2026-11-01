package org.kshrd.webblock.project.application.page.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.page.model.NewPage;
import org.kshrd.webblock.project.application.page.model.PageRecord;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
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
import org.springframework.transaction.IllegalTransactionStateException;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

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
class LivePagesTest {

    @Autowired
    PageService pages;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JsonMapper json;

    @Autowired
    JdbcTemplate jdbc;

    private NewPage page(String path, String tree) {
        return new NewPage("Page " + path, path, json.readTree(tree), null, null, 0);
    }

    private void insert(UUID projectId, NewPage page) {
        tx.executeWithoutResult(s -> pages.insertInitialPages(projectId, "test-1", List.of(page)));
    }

    @Test
    @DisplayName("FR-18: livePages returns the website's one live page and leaves out a soft-deleted one")
    void returnsTheOneLivePage() {
        UUID id = Uuids.v7();
        UUID owner = UUID.randomUUID();
        tx.execute(s ->
                projects.create(new NewProject(id, owner, "P", TestSubdomains.of(id), UUID.randomUUID(), "test-1")));
        UUID other = Uuids.v7();
        tx.execute(s -> projects.create(
                new NewProject(other, owner, "P", TestSubdomains.of(other), UUID.randomUUID(), "test-1")));
        insert(id, page("/", "{\"root\":{\"props\":{}},\"content\":[]}"));
        insert(other, page("/", "{\"root\":{\"props\":{}},\"content\":[]}"));
        jdbc.update("UPDATE project.pages SET deleted_at = now() WHERE project_id = ?", other);

        List<PageRecord> live = tx.execute(s -> pages.livePages(id));
        assertThat(live).extracting(PageRecord::path).containsExactly("/");
        List<PageRecord> none = tx.execute(s -> pages.livePages(other));
        assertThat(none).isEmpty();
    }

    @Test
    @DisplayName("FR-14.2: one query gives the style key of each live home page; no key or no home page is absent")
    void fr14_2_homeTemplateKeysForManyProjects() {
        UUID withKey = Uuids.v7();
        UUID withoutKey = Uuids.v7();
        UUID noHome = Uuids.v7();
        UUID owner = UUID.randomUUID();
        for (UUID id : List.of(withKey, withoutKey, noHome)) {
            tx.execute(s -> projects.create(
                    new NewProject(id, owner, "P", TestSubdomains.of(id), UUID.randomUUID(), "test-1")));
        }
        String plain = "{\"root\":{\"props\":{}},\"content\":[]}";
        insert(withKey, page("/", "{\"root\":{\"props\":{\"templateId\":\"sweet-pastel\"}},\"content\":[]}"));
        insert(withoutKey, page("/", plain));
        insert(noHome, page("/about", plain));

        assertThat(pages.homeTemplateKeys(List.of(withKey, withoutKey, noHome)))
                .containsExactly(Map.entry(withKey, "sweet-pastel"));
        assertThat(pages.homeTemplateKeys(List.of())).isEmpty();
    }

    @Test
    void needsATransaction() {
        assertThatThrownBy(() -> pages.livePages(Uuids.v7())).isInstanceOf(IllegalTransactionStateException.class);
    }
}
