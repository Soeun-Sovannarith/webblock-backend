package org.kshrd.webblock.project.common.infrastructure.config;

import java.util.Properties;
import org.springframework.boot.flyway.autoconfigure.FlywayConfigurationCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jdbc.datasource.AbstractDriverBasedDataSource;

/**
 * The prototype's in-process project migration (production runs none) connects with logServerErrorDetail=false, like
 * the pool and the migration job, so PostgreSQL's DETAIL (which can quote row values) never reaches a log. Spring Boot
 * builds that connection from spring.flyway's URL and credentials only, so the pool's data-source-properties do not
 * reach it.
 */
@Configuration(proxyBeanMethods = false)
class FlywayConnectionConfig {

    @Bean
    FlywayConfigurationCustomizer flywayWithoutServerErrorDetail() {
        return configuration -> {
            if (configuration.getDataSource() instanceof AbstractDriverBasedDataSource migration) {
                Properties properties = new Properties();
                properties.setProperty("logServerErrorDetail", "false");
                migration.setConnectionProperties(properties);
            }
        };
    }
}
