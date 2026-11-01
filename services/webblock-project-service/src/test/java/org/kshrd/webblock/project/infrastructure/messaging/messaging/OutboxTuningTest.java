package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.nio.file.Path;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.util.Map;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.config.YamlPropertiesFactoryBean;
import org.springframework.core.io.FileSystemResource;

/** Review fix M-7: gauge queries are bounded in time, and outbox retries are jittered. */
class OutboxTuningTest {

    @Test
    void gaugeQueriesCarryAShortStatementTimeout() throws Exception {
        DataSource dataSource = mock(DataSource.class);
        Connection connection = mock(Connection.class);
        PreparedStatement statement = mock(PreparedStatement.class);
        when(dataSource.getConnection()).thenReturn(connection);
        when(connection.prepareStatement(anyString())).thenReturn(statement);
        when(statement.executeQuery()).thenReturn(mock(java.sql.ResultSet.class));

        double value = new OutboxMetrics(dataSource).failedRecords();

        assertThat(value).isNaN(); // the mocked statement returns no rows
        verify(statement).setQueryTimeout(2);
    }

    @Test
    void theShippedConfigurationJittersRetriesAndHidesRowValuesInDriverErrors() {
        var yaml = new YamlPropertiesFactoryBean();
        yaml.setResources(new FileSystemResource(Path.of("src/main/resources/application.yml")));
        Map<Object, Object> properties = yaml.getObject();

        assertThat(properties.get("namastack.outbox.retry.jitter")).isEqualTo("500ms");
        assertThat(properties.get("spring.datasource.hikari.data-source-properties.logServerErrorDetail"))
                .isEqualTo("false");
        assertThat(properties.get("management.endpoint.health.group.readiness.include"))
                .isEqualTo("readinessState,db,catalogDb,rabbit");
    }
}
