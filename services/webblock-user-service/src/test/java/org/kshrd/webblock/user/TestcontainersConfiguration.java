package org.kshrd.webblock.user;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

// Real PostgreSQL in Docker for tests; @ServiceConnection sets the datasource URL/user/password
@TestConfiguration(proxyBeanMethods = false)
public class TestcontainersConfiguration {

    @Bean
    @ServiceConnection
    PostgreSQLContainer postgresContainer() {
        // test-init.sql creates role "account" (needed by SET ROLE in spring.flyway.init-sqls)
        return new PostgreSQLContainer(DockerImageName.parse("postgres:17-alpine"))
                .withInitScript("db/test-init.sql");
    }

}
