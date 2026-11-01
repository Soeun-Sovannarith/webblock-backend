package org.kshrd.webblock.project.infrastructure.config.catalog;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class CatalogPropertiesTest {

    @Test
    void toStringNeverShowsEitherPassword() {
        CatalogProperties properties = new CatalogProperties(
                new CatalogProperties.Datasource("jdbc:postgresql://db/catalog", "platform", "runtime-secret", 5),
                new CatalogProperties.Migrations(true, "migrator", "migrator-secret"),
                104_857_600L,
                2_097_152L);

        assertThat(properties.toString())
                .doesNotContain("runtime-secret", "migrator-secret")
                .contains("jdbc:postgresql://db/catalog", "platform", "migrator", "password=***");
        assertThat(properties.datasource().toString()).doesNotContain("runtime-secret");
        assertThat(properties.migrations().toString()).doesNotContain("migrator-secret");
    }

    /** A JDBC URL can carry password= in its query: only the scheme, host, port and database are printed. */
    @Test
    void toStringPrintsTheUrlWithoutItsQuery() {
        assertThat(datasource("jdbc:postgresql://db.example:5432/catalog?user=x&password=url-secret")
                        .toString())
                .contains("url=jdbc:postgresql://db.example:5432/catalog,")
                .doesNotContain("url-secret", "?", "user=x");
        assertThat(datasource("jdbc:postgresql://db.example/catalog").toString())
                .contains("url=jdbc:postgresql://db.example/catalog,");
        // Not a single host the URI parser reads: masked whole.
        for (String url : new String[] {
            "jdbc:postgresql://db1:5432,db2:5432/catalog?password=url-secret",
            "jdbc:postgresql://user:url-secret@db/catalog",
            "postgresql://db/catalog?password=url-secret",
            "jdbc:postgresql://db/cat alog?password=url-secret"
        }) {
            assertThat(datasource(url).toString()).as(url).contains("url=***,").doesNotContain("url-secret");
        }
        assertThat(datasource(null).toString()).contains("url=***,");
    }

    private static CatalogProperties.Datasource datasource(String url) {
        return new CatalogProperties.Datasource(url, "platform", "runtime-secret", 5);
    }
}
