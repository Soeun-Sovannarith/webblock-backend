package org.kshrd.webblock.project.infrastructure.persistence.migration;

import static org.assertj.core.api.Assertions.assertThat;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.handler.CatalogFlyway;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.slf4j.LoggerFactory;

@Tag("integration")
class MigrationsTest {

    static Map<String, String> settings(String projectUrl, String catalogUrl) {
        Map<String, String> env = new HashMap<>();
        env.put("PROJECT_DB_URL", projectUrl);
        env.put("PROJECT_DB_MIGRATOR_USER", PostgresFixture.MIGRATOR_LOGIN);
        env.put("PROJECT_DB_MIGRATOR_PASSWORD", PostgresFixture.PASSWORD);
        env.put("CATALOG_DB_URL", catalogUrl);
        env.put("CATALOG_DB_MIGRATOR_USER", PostgresFixture.CATALOG_MIGRATOR_LOGIN);
        env.put("CATALOG_DB_MIGRATOR_PASSWORD", PostgresFixture.PASSWORD);
        return env;
    }

    @Test
    void aMissingSettingExits2() {
        assertThat(Migrations.run(Map.of())).isEqualTo(2);
    }

    /**
     * A URL parameter would replace the migrations' logServerErrorDetail=false (and the service's pool settings), so
     * the job refuses it before it connects: both databases stay unmigrated. The log names the parameter, not the URL.
     */
    @Test
    void aUrlSettingAGuardedDriverPropertyExits2BeforeConnecting() throws Exception {
        PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        Logger logger = (Logger) LoggerFactory.getLogger(Migrations.class);
        ListAppender<ILoggingEvent> logs = new ListAppender<>();
        logs.start();
        logger.addAppender(logs);
        try {
            for (String setting : new String[] {"PROJECT_DB_URL", "CATALOG_DB_URL"}) {
                for (String[] parameter : new String[][] {
                    {"logServerErrorDetail=true", "logServerErrorDetail"},
                    {"socketTimeout=0", "socketTimeout"},
                    {"OPTIONS=-c%20statement_timeout%3D0", "options"}
                }) {
                    Map<String, String> env = settings(project.url(), catalog.url());
                    String url = env.get(setting);
                    env.put(setting, url + (url.contains("?") ? "&" : "?") + "password=url-secret&" + parameter[0]);
                    logs.list.clear();

                    assertThat(Migrations.run(env))
                            .as(setting + " " + parameter[0])
                            .isEqualTo(2);
                    assertThat(logs.list)
                            .extracting(ILoggingEvent::getFormattedMessage)
                            .containsExactly("Migration job: " + setting + " must not set the driver property "
                                    + parameter[1] + ": it would replace the setting the service relies on");
                }
            }
        } finally {
            logger.detachAppender(logs);
        }
        assertThat(PostgresFixture.flyway(project.url(), PostgresFixture.MIGRATOR_LOGIN, true)
                        .info()
                        .applied())
                .isEmpty();
        assertThat(CatalogFlyway.configure(
                                catalog.url(), PostgresFixture.CATALOG_MIGRATOR_LOGIN, PostgresFixture.PASSWORD)
                        .info()
                        .applied())
                .isEmpty();
    }

    @Test
    void migratesBothDatabasesAndIsSafeToRunAgain() throws Exception {
        PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        assertThat(Migrations.run(settings(project.url(), catalog.url()))).isZero();
        assertThat(PostgresFixture.flyway(project.url(), PostgresFixture.MIGRATOR_LOGIN, true)
                        .info()
                        .pending())
                .isEmpty();
        assertThat(CatalogFlyway.configure(
                                catalog.url(), PostgresFixture.CATALOG_MIGRATOR_LOGIN, PostgresFixture.PASSWORD)
                        .info()
                        .pending())
                .isEmpty();
        assertThat(Migrations.run(settings(project.url(), catalog.url()))).isZero();
    }

    @Test
    void aFailedMigrationExits1() throws Exception {
        PostgresFixture.Database project = PostgresFixture.freshDatabase(false);
        PostgresFixture.CatalogDatabase catalog = PostgresFixture.freshCatalogDatabase(false);
        Map<String, String> env = settings(project.url(), catalog.url());
        env.put("CATALOG_DB_MIGRATOR_PASSWORD", "wrong");
        assertThat(Migrations.run(env)).isEqualTo(1);
    }
}
