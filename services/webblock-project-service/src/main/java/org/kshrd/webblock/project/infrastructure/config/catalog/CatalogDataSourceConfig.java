package org.kshrd.webblock.project.infrastructure.config.catalog;

import com.zaxxer.hikari.HikariDataSource;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.health.contributor.HealthIndicator;
import org.springframework.boot.jdbc.DataSourceBuilder;
import org.springframework.boot.jdbc.health.DataSourceHealthIndicator;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.support.JdbcTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * The catalog database. Every bean here is defaultCandidate = false: Spring Boot's auto-configuration ignores such
 * beans (OnBeanCondition), so the auto-configured project DataSource, JPA, Flyway, Namastack and TransactionTemplate
 * stay on the project database, and only an injection point qualified "catalog" reaches these.
 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(CatalogProperties.class)
class CatalogDataSourceConfig {

    @Bean(defaultCandidate = false)
    @Qualifier("catalog")
    HikariDataSource catalogDataSource(CatalogProperties properties) {
        CatalogProperties.Datasource config = properties.datasource();
        HikariDataSource dataSource = DataSourceBuilder.create()
                .type(HikariDataSource.class)
                .url(config.url())
                .username(config.username())
                .password(config.password())
                .build();
        dataSource.setPoolName("catalog");
        dataSource.setMaximumPoolSize(config.maximumPoolSize());
        dataSource.setConnectionTimeout(2_000); // a catalog outage answers 503 quickly
        // A connection closed under the pool (a laptop sleep, a dropped socket, a database restart) is found by the
        // keepalive and replaced, and no connection outlives the maximum lifetime. As the project pool does.
        dataSource.setKeepaliveTime(60_000);
        dataSource.setMaxLifetime(600_000);
        dataSource.setIdleTimeout(300_000);
        dataSource.setMinimumIdle(Math.min(2, config.maximumPoolSize()));
        dataSource.setValidationTimeout(1_000); // Hikari needs it below the 2 s connection timeout
        // pgjdbc otherwise appends the server's Detail ("Failing row contains (...)", customer content) to
        // SQLException messages, which logs and the outbox's failure_reason keep.
        dataSource.addDataSourceProperty("logServerErrorDetail", "false");
        // Every catalog statement is bounded: the server cancels one after 5 s, lock waits included, and the driver
        // gives up on a silent socket after 10 s (a half-open connection the server cannot cancel). The statement
        // timeout is set on each new connection, not through the driver property options.
        // A URL parameter replaces a driver property of the same name, so ProfileGuard refuses a CATALOG_DB_URL that
        // sets logServerErrorDetail, socketTimeout or options.
        dataSource.setConnectionInitSql("SET statement_timeout = '5s'");
        dataSource.addDataSourceProperty("socketTimeout", "10");
        return dataSource;
    }

    @Bean(defaultCandidate = false)
    @Qualifier("catalog")
    JdbcClient catalogJdbcClient(@Qualifier("catalog") DataSource dataSource) {
        return JdbcClient.create(dataSource);
    }

    @Bean(defaultCandidate = false)
    @Qualifier("catalog")
    TransactionTemplate catalogTransactionTemplate(@Qualifier("catalog") DataSource dataSource) {
        return new TransactionTemplate(new JdbcTransactionManager(dataSource));
    }

    @Bean
    HealthIndicator catalogDbHealthIndicator(@Qualifier("catalog") DataSource dataSource) {
        return new DataSourceHealthIndicator(dataSource, "SELECT 1");
    }
}
