package org.kshrd.webblock.project.infrastructure.config.catalog;

import static org.assertj.core.api.Assertions.assertThat;

import com.zaxxer.hikari.HikariDataSource;
import java.time.Duration;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/** The catalog pool as CatalogDataSourceConfig builds it; no connection is opened, so no database is needed. */
class CatalogPoolResilienceTest {

    @Test
    @DisplayName("TR-35: the catalog pool keeps idle connections alive and recycles them, within its 2 s connect bound")
    void tr35_theCatalogPoolKeepsConnectionsAliveAndRecyclesThem() {
        try (HikariDataSource pool = new CatalogDataSourceConfig().catalogDataSource(properties(5))) {
            assertThat(pool.getKeepaliveTime()).isEqualTo(Duration.ofSeconds(60).toMillis());
            assertThat(pool.getMaxLifetime()).isEqualTo(Duration.ofMinutes(10).toMillis());
            assertThat(pool.getIdleTimeout()).isEqualTo(Duration.ofMinutes(5).toMillis());
            assertThat(pool.getMinimumIdle()).isEqualTo(2);
            assertThat(pool.getConnectionTimeout())
                    .isEqualTo(Duration.ofSeconds(2).toMillis());
            assertThat(pool.getValidationTimeout())
                    .isEqualTo(Duration.ofSeconds(1).toMillis())
                    .isLessThan(pool.getConnectionTimeout());
        }
    }

    @Test
    @DisplayName("TR-35: the catalog pool never keeps more idle connections than its maximum size")
    void tr35_theCatalogMinimumIdleNeverExceedsTheMaximumPoolSize() {
        try (HikariDataSource pool = new CatalogDataSourceConfig().catalogDataSource(properties(1))) {
            assertThat(pool.getMaximumPoolSize()).isEqualTo(1);
            assertThat(pool.getMinimumIdle()).isEqualTo(1);
        }
    }

    private static CatalogProperties properties(int maximumPoolSize) {
        return new CatalogProperties(
                new CatalogProperties.Datasource(
                        "jdbc:postgresql://localhost:5432/catalog", "catalog_platform_login", "", maximumPoolSize),
                null,
                1,
                1);
    }
}
