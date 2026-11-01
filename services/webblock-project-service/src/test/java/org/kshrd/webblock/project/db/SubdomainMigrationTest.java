package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.FlywayException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/** V6 on a V5 database with rows: the sub-domain backfill, its guards, and the pre-check that stops V6 (FR-36). */
@Tag("integration")
class SubdomainMigrationTest {

    private static Flyway flyway(String url, String target) {
        var config = Flyway.configure()
                .dataSource(url, PostgresFixture.MIGRATOR_LOGIN, PostgresFixture.PASSWORD)
                .schemas("project_ops", "project", "project_messaging")
                .defaultSchema("project_ops")
                .locations("classpath:db/migration/project")
                .initSql("SET ROLE project_migrator");
        if (target != null) {
            config.target(target);
        }
        return config.load();
    }

    /** A V5 database: project.projects still has github_repository_* and project.domains exists. */
    private static PostgresFixture.Database atVersion5() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase(false);
        flyway(db.url(), "5").migrate();
        return db;
    }

    private static void v5Project(Connection c, UUID id, Long repositoryId, boolean deleted) throws SQLException {
        Rows.exec(
                c,
                "INSERT INTO project.projects (project_id, owner_user_id, name, business_type_id, edit_revision, "
                        + "component_catalog_version, github_repository_id, github_repository_full_name, status, "
                        + "created_at, updated_at, deleted_at) VALUES (?, ?, 'Old shop', ?, 3, '0.1.0', ?, ?, "
                        + "'draft', now(), now(), " + (deleted ? "now()" : "NULL") + ")",
                id,
                UUID.randomUUID(),
                UUID.randomUUID(),
                repositoryId,
                repositoryId == null ? null : "webblock-sites/r" + repositoryId);
    }

    private static UUID withPrefix(String prefix12) {
        String random = UUID.randomUUID().toString();
        return UUID.fromString(prefix12 + random.substring(12));
    }

    private static String one(Connection c, String sql, Object... params) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            for (int i = 0; i < params.length; i++) {
                ps.setObject(i + 1, params[i]);
            }
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getString(1) : null;
            }
        }
    }

    private static Connection asMigrator(PostgresFixture.Database db) throws SQLException {
        Connection c = java.sql.DriverManager.getConnection(
                db.url(), PostgresFixture.MIGRATOR_LOGIN, PostgresFixture.PASSWORD);
        try (Statement st = c.createStatement()) {
            st.execute("SET ROLE project_migrator");
        }
        return c;
    }

    @Test
    @DisplayName("FR-36.1: old rows get p- and 12 characters; every other value and every link is kept")
    void fr36_1_oldRowsGetTheBackfilledSubdomain() throws Exception {
        PostgresFixture.Database db = atVersion5();
        UUID live = UUID.randomUUID();
        UUID deleted = UUID.randomUUID();
        UUID page;
        try (Connection su = db.asSuperuser()) {
            v5Project(su, live, 77L, false);
            v5Project(su, deleted, null, true);
            page = Rows.page(su, live, "/");
            Rows.request(su, live, Rows.contract(su, live, 1), "k", "captured");
            Rows.exec(
                    su,
                    "INSERT INTO project.domains (domain_id, project_id, hostname, kind, status, is_primary, "
                            + "created_at, updated_at) VALUES (?, ?, ?, 'platform_subdomain', 'active', true, now(), "
                            + "now())",
                    UUID.randomUUID(),
                    live,
                    live + ".sites.webblock.test");
        }

        assertThat(flyway(db.url(), "6").migrate().migrationsExecuted).isEqualTo(1);

        try (Connection su = db.asSuperuser()) {
            assertThat(one(su, "SELECT subdomain FROM project.projects WHERE project_id = ?", live))
                    .isEqualTo("p-" + live.toString().substring(0, 12));
            assertThat(one(su, "SELECT subdomain FROM project.projects WHERE project_id = ?", deleted))
                    .isEqualTo("p-" + deleted.toString().substring(0, 12));
            assertThat(one(
                            su,
                            "SELECT name || ' ' || edit_revision || ' ' || repository_id || ' ' || repository_full_name"
                                    + " || ' ' || status FROM project.projects WHERE project_id = ?",
                            live))
                    .isEqualTo("Old shop 3 77 webblock-sites/r77 draft");
            assertThat(one(su, "SELECT deleted_at IS NOT NULL FROM project.projects WHERE project_id = ?", deleted))
                    .isEqualTo("t");
            assertThat(one(
                            su,
                            "SELECT is_nullable FROM information_schema.columns WHERE table_schema = 'project' "
                                    + "AND table_name = 'projects' AND column_name = 'subdomain'"))
                    .isEqualTo("NO");
            assertThat(one(su, "SELECT project_id FROM project.pages WHERE page_id = ?", page))
                    .isEqualTo(live.toString());
            // The three foreign keys point at the new table again.
            Rows.assertViolates("pages_project_fk", () -> Rows.page(su, UUID.randomUUID(), "/"));
            Rows.assertViolates("generation_contracts_project_fk", () -> Rows.contract(su, UUID.randomUUID(), 1));
            Rows.assertViolates(
                    "projects_subdomain_immutable",
                    () -> Rows.exec(su, "UPDATE project.projects SET subdomain = 'x-new' WHERE project_id = ?", live));
            assertThat(one(
                            su,
                            "SELECT string_agg(conname, ',' ORDER BY conname) FROM pg_constraint "
                                    + "WHERE confrelid = 'project.projects'::regclass"))
                    .isEqualTo("generation_contracts_project_fk,generation_requests_project_fk,pages_project_fk");
        }
    }

    @Test
    @DisplayName("FR-36.2: subdomain update refused as project_migrator; the value is unchanged")
    void fr36_2_subdomainUpdateRefusedAsTheMigrator() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        UUID project;
        try (Connection c = db.asApp()) {
            project = Rows.project(c);
        }
        String before;
        try (Connection migrator = asMigrator(db)) {
            before = one(migrator, "SELECT subdomain FROM project.projects WHERE project_id = ?", project);
            Rows.assertViolates(
                    "projects_subdomain_immutable",
                    () -> Rows.exec(
                            migrator,
                            "UPDATE project.projects SET subdomain = 'renamed' WHERE project_id = ?",
                            project));
            assertThat(one(migrator, "SELECT subdomain FROM project.projects WHERE project_id = ?", project))
                    .isEqualTo(before);
            // The other columns still change: the guard refuses only the frozen ones.
            Rows.exec(migrator, "UPDATE project.projects SET name = 'Renamed' WHERE project_id = ?", project);
        }
    }

    @Test
    @DisplayName("FR-36.3: a deleted project's sub-domain refused for a new row")
    void fr36_3_aDeletedProjectsSubdomainIsNeverReused() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        try (Connection c = db.asApp()) {
            UUID gone = Rows.project(c, "kiln");
            Rows.exec(c, "UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", gone);
            Rows.assertViolates("projects_subdomain_key", () -> Rows.project(c, "kiln"));
        }
    }

    @Test
    @DisplayName("FR-36.8: owner index has no deleted_at condition")
    void fr36_8_theOwnerIndexCoversEveryProject() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        try (Connection su = db.asSuperuser()) {
            assertThat(one(
                            su,
                            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'project' "
                                    + "AND indexname = 'projects_owner_idx'"))
                    .isEqualTo("CREATE INDEX projects_owner_idx ON project.projects USING btree (owner_user_id)");
        }
    }

    @Test
    @DisplayName("G2: shared 12-character prefix stops V6, nothing changed")
    void g2_aSharedPrefixStopsV6AndChangesNothing() throws Exception {
        PostgresFixture.Database db = atVersion5();
        String prefix = UUID.randomUUID().toString().substring(0, 12);
        try (Connection su = db.asSuperuser()) {
            v5Project(su, withPrefix(prefix), null, false);
            v5Project(su, withPrefix(prefix), null, true);
            v5Project(su, UUID.randomUUID(), null, false);
        }

        assertThatThrownBy(() -> flyway(db.url(), null).migrate())
                .isInstanceOf(FlywayException.class)
                .hasMessageContaining("23505")
                .hasMessageContaining("1 sub-domain prefix");

        try (Connection su = db.asSuperuser()) {
            List<String> columns = new ArrayList<>();
            try (ResultSet rs = su.createStatement()
                    .executeQuery("SELECT column_name FROM information_schema.columns WHERE table_schema = 'project' "
                            + "AND table_name = 'projects' AND column_name LIKE '%repository%' ORDER BY 1")) {
                while (rs.next()) {
                    columns.add(rs.getString(1));
                }
            }
            assertThat(columns).containsExactly("github_repository_full_name", "github_repository_id");
            assertThat(one(su, "SELECT to_regclass('project.domains')")).isEqualTo("project.domains");
            assertThat(one(su, "SELECT to_regclass('project.projects_v5')")).isNull();
            assertThat(one(su, "SELECT count(*) FROM project.projects")).isEqualTo("3");
            assertThat(one(
                            su,
                            "SELECT max(version::int) FROM project_ops.flyway_schema_history WHERE success "
                                    + "AND version IS NOT NULL"))
                    .isEqualTo("5");
        }
    }
}
