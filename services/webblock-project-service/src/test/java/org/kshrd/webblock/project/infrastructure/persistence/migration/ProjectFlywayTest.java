package org.kshrd.webblock.project.infrastructure.persistence.migration;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.handler.CatalogFlyway;
import org.springframework.beans.factory.config.YamlPropertiesFactoryBean;
import org.springframework.core.io.ClassPathResource;

/** The job's project-database configuration must stay the in-process one of application.yml. */
class ProjectFlywayTest {

    @Test
    void matchesSpringsFlywaySettings() {
        YamlPropertiesFactoryBean yaml = new YamlPropertiesFactoryBean();
        yaml.setResources(new ClassPathResource("application.yml"));
        Properties p = yaml.getObject();
        assertThat(p.getProperty("spring.flyway.schemas")).isEqualTo(String.join(",", ProjectFlyway.SCHEMAS));
        assertThat(p.getProperty("spring.flyway.default-schema")).isEqualTo(ProjectFlyway.DEFAULT_SCHEMA);
        assertThat(p.getProperty("spring.flyway.init-sqls")).isEqualTo(ProjectFlyway.INIT_SQL);
        assertThat(p.getProperty("spring.flyway.locations")).isEqualTo(ProjectFlyway.LOCATION);
    }

    @Test
    void theJobReliesOnExactlyTheKeysOfApplicationYml() {
        YamlPropertiesFactoryBean yaml = new YamlPropertiesFactoryBean();
        yaml.setResources(new ClassPathResource("application.yml"));
        Set<String> keys = yaml.getObject().stringPropertyNames().stream()
                .filter(k -> k.startsWith("spring.flyway."))
                .collect(Collectors.toSet());
        // a new key here must be mirrored in ProjectFlyway (and added to this list)
        assertThat(keys)
                .containsExactlyInAnyOrder(
                        "spring.flyway.user",
                        "spring.flyway.password",
                        "spring.flyway.init-sqls",
                        "spring.flyway.schemas",
                        "spring.flyway.default-schema",
                        "spring.flyway.locations");
    }

    @Test
    void theProjectJobOwnsItsDriverSettings() {
        assertThat(ProjectFlyway.JDBC_PROPERTIES).containsExactly(Map.entry("logServerErrorDetail", "false"));
    }

    @Test
    void bothJobConfigurationsKeepServerErrorDetailOutOfMessages() {
        assertThat(ProjectFlyway.configure("jdbc:postgresql://localhost/x", "u", "p")
                        .getConfiguration()
                        .getJdbcProperties())
                .containsEntry("logServerErrorDetail", "false");
        assertThat(CatalogFlyway.configure("jdbc:postgresql://localhost/x", "u", "p")
                        .getConfiguration()
                        .getJdbcProperties())
                .containsEntry("logServerErrorDetail", "false");
    }
}
