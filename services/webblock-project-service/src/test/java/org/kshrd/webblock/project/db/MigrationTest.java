package org.kshrd.webblock.project.db;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;
import org.flywaydb.core.api.FlywayException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

@Tag("integration")
class MigrationTest {

    @Test
    void migratesAnEmptyDatabaseToTheRevision19Tables() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement()
                        .executeQuery("SELECT table_schema || '.' || table_name FROM information_schema.tables "
                                + "WHERE table_schema IN ('project', 'project_messaging', 'project_ops') "
                                + "AND table_name <> 'flyway_schema_history' "
                                + "ORDER BY 1")) {
            List<String> tables = new ArrayList<>();
            while (rs.next()) {
                tables.add(rs.getString(1));
            }
            assertThat(tables)
                    .containsExactly(
                            "project.generation_contract_schemas",
                            "project.generation_contracts",
                            "project.generation_requests",
                            "project.pages",
                            "project.projects",
                            "project_messaging.outbox_instance",
                            "project_messaging.outbox_partition",
                            "project_messaging.outbox_record",
                            "project_messaging.pending_catalog_registrations",
                            "project_messaging.processed_messages");
        }
    }

    private static List<String> rows(Connection c, String sql) throws Exception {
        List<String> out = new ArrayList<>();
        try (ResultSet rs = c.createStatement().executeQuery(sql)) {
            while (rs.next()) {
                out.add(rs.getString(1));
            }
        }
        return out;
    }

    /** Notion "2 · Project", projects: every column, its type and NULL rule, in the order the page lists them. */
    @Test
    @DisplayName("G1: projects columns in S1 order")
    void g1_projectsColumnsInS1Order() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        try (Connection c = db.asSuperuser()) {
            assertThat(rows(
                            c,
                            "SELECT a.attname || ' ' || format_type(a.atttypid, a.atttypmod) || "
                                    + "CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END "
                                    + "FROM pg_attribute a WHERE a.attrelid = 'project.projects'::regclass "
                                    + "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"))
                    .containsExactly(
                            "project_id uuid NOT NULL",
                            "owner_user_id uuid NOT NULL",
                            "name character varying(255) NOT NULL",
                            "subdomain character varying(63) NOT NULL",
                            "business_type_id uuid NOT NULL",
                            "edit_revision bigint NOT NULL",
                            "component_catalog_version character varying(100) NOT NULL",
                            "repository_id bigint",
                            "repository_full_name character varying(140)",
                            "status project.project_status NOT NULL",
                            "created_at timestamp with time zone NOT NULL",
                            "updated_at timestamp with time zone NOT NULL",
                            "deleted_at timestamp with time zone");
            assertThat(rows(
                            c,
                            "SELECT conname || ' ' || pg_get_constraintdef(oid) FROM pg_constraint "
                                    + "WHERE conrelid = 'project.projects'::regclass ORDER BY conname"))
                    .containsExactly(
                            "projects_business_type_id_not_null NOT NULL business_type_id",
                            "projects_component_catalog_version_not_null NOT NULL component_catalog_version",
                            "projects_created_at_not_null NOT NULL created_at",
                            "projects_edit_revision_check CHECK ((edit_revision >= 0))",
                            "projects_edit_revision_not_null NOT NULL edit_revision",
                            "projects_name_not_null NOT NULL name",
                            "projects_owner_user_id_not_null NOT NULL owner_user_id",
                            "projects_pkey PRIMARY KEY (project_id)",
                            "projects_project_id_not_null NOT NULL project_id",
                            "projects_repository_id_check CHECK ((repository_id > 0))",
                            "projects_repository_id_key UNIQUE (repository_id)",
                            "projects_repository_pair CHECK (((repository_id IS NULL) = (repository_full_name IS NULL)))",
                            "projects_status_not_null NOT NULL status",
                            "projects_subdomain_key UNIQUE (subdomain)",
                            "projects_subdomain_not_null NOT NULL subdomain",
                            "projects_updated_at_not_null NOT NULL updated_at");
            assertThat(rows(
                            c,
                            "SELECT indexname FROM pg_indexes WHERE schemaname = 'project' "
                                    + "AND tablename = 'projects' ORDER BY 1"))
                    .containsExactly(
                            "projects_owner_idx",
                            "projects_pkey",
                            "projects_repository_id_key",
                            "projects_subdomain_key");
            assertThat(rows(
                            c,
                            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'project.projects'::regclass "
                                    + "AND NOT tgisinternal"))
                    .containsExactly("projects_guard");
            assertThat(rows(
                            c,
                            "SELECT column_default FROM information_schema.columns WHERE table_schema = 'project' "
                                    + "AND table_name = 'projects' AND column_default IS NOT NULL"))
                    .containsExactly("0");
        }
    }

    @Test
    @DisplayName("FR-36.4: repository columns, no github_repository_*")
    void fr36_4_repositoryColumnsReplaceTheGithubOnes() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        try (Connection c = db.asSuperuser()) {
            assertThat(rows(
                            c,
                            "SELECT table_name || '.' || column_name FROM information_schema.columns "
                                    + "WHERE table_schema LIKE 'project%' AND column_name LIKE '%repository%' "
                                    + "ORDER BY 1"))
                    .containsExactly("projects.repository_full_name", "projects.repository_id");
        }
    }

    @Test
    @DisplayName("FR-36.5: no domains table or enums")
    void fr36_5_noDomainsTableOrEnums() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        try (Connection c = db.asSuperuser()) {
            assertThat(rows(
                            c,
                            "SELECT to_regclass('project.domains')::text UNION ALL "
                                    + "SELECT to_regtype('project.domain_kind')::text UNION ALL "
                                    + "SELECT to_regtype('project.domain_status')::text UNION ALL "
                                    + "SELECT to_regproc('project.domains_guard')::text"))
                    .containsOnlyNulls();
            assertThat(rows(
                            c,
                            "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                                    + "WHERE n.nspname = 'project' AND t.typtype = 'e' ORDER BY 1"))
                    .containsExactly("generation_request_status", "project_status");
            assertThat(rows(
                            c,
                            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                                    + "WHERE n.nspname = 'project' ORDER BY 1"))
                    .containsExactly("generation_requests_guard", "projects_guard");
        }
    }

    @Test
    void everyObjectIsOwnedByTheMigrator() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement()
                        .executeQuery("SELECT count(*) FROM pg_tables "
                                + "WHERE schemaname IN ('project', 'project_messaging', 'project_ops') "
                                + "AND tableowner <> 'project_migrator'")) {
            rs.next();
            assertThat(rs.getInt(1)).isZero();
        }
    }

    @Test
    void theThreeSchemasAreOwnedByTheMigratorAndClosedToPublic() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement()
                        .executeQuery("SELECT nspname, pg_get_userbyid(nspowner), "
                                + "has_schema_privilege('public', oid, 'USAGE') FROM pg_namespace "
                                + "WHERE nspname LIKE 'project%' ORDER BY 1")) {
            List<String> schemas = new ArrayList<>();
            while (rs.next()) {
                schemas.add(rs.getString(1) + " " + rs.getString(2) + " public-usage=" + rs.getBoolean(3));
            }
            assertThat(schemas)
                    .containsExactly(
                            "project project_migrator public-usage=false",
                            "project_messaging project_migrator public-usage=false",
                            "project_ops project_migrator public-usage=false");
        }
    }

    /** project_ops holds Flyway's history and nothing else: no table, view, sequence, type or function. */
    @Test
    void projectOpsHoldsOnlyTheFlywayHistory() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement()
                        .executeQuery("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                + "WHERE n.nspname = 'project_ops' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
                                + "UNION ALL SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                                + "WHERE n.nspname = 'project_ops' AND t.typtype IN ('e', 'd', 'c') AND t.typrelid = 0 "
                                + "UNION ALL SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                                + "WHERE n.nspname = 'project_ops'")) {
            List<String> objects = new ArrayList<>();
            while (rs.next()) {
                objects.add(rs.getString(1));
            }
            assertThat(objects).containsExactly("flyway_schema_history");
        }
    }

    @Test
    void refusesToMigrateAsAnotherRole() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase(false);
        assertThatThrownBy(() -> PostgresFixture.flyway(db.url(), PostgresFixture.superuser(), false)
                        .migrate())
                .isInstanceOf(FlywayException.class)
                .hasMessageContaining("project_migrator");
    }

    @Test
    void secondRunChangesNothing() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();
        assertThat(PostgresFixture.flyway(db.url(), PostgresFixture.MIGRATOR_LOGIN, true)
                        .migrate()
                        .migrationsExecuted)
                .isZero();
    }

    private static org.flywaydb.core.Flyway withLaterMigration(String url, boolean setRole) {
        var config = org.flywaydb.core.Flyway.configure()
                .dataSource(url, PostgresFixture.MIGRATOR_LOGIN, PostgresFixture.PASSWORD)
                .schemas("project_ops", "project", "project_messaging")
                .defaultSchema("project_ops")
                .locations("classpath:db/migration/project", "classpath:db/later-migration");
        if (setRole) {
            config.initSql("SET ROLE project_migrator");
        }
        return config.load();
    }

    @Test
    void aLaterMigrationRefusesToRunAsAnyRoleButTheMigrator() throws Exception {
        PostgresFixture.Database db = PostgresFixture.freshDatabase();

        // The login role itself, without SET ROLE: V1 is long applied, so only the callback can refuse this.
        assertThatThrownBy(() -> withLaterMigration(db.url(), false).migrate())
                .isInstanceOf(FlywayException.class)
                .hasMessageContaining("Migrations must run as project_migrator");
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement().executeQuery("SELECT to_regclass('project_ops.later_probe')")) {
            rs.next();
            assertThat(rs.getString(1)).isNull();
        }

        // As the migrator it runs.
        withLaterMigration(db.url(), true).migrate();
        try (Connection c = db.asSuperuser();
                ResultSet rs = c.createStatement().executeQuery("SELECT to_regclass('project_ops.later_probe')")) {
            rs.next();
            assertThat(rs.getString(1)).isEqualTo("project_ops.later_probe");
        }
    }
}
