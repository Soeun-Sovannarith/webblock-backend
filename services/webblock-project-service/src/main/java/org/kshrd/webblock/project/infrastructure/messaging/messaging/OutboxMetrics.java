package org.kshrd.webblock.project.infrastructure.messaging.messaging;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.binder.MeterBinder;
import javax.sql.DataSource;
import org.kshrd.webblock.project.application.messaging.handler.OutboxRecords;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * Makes unsent outbox work visible. Broker sends retry without limit, so a stuck record shows only here:
 *
 * <ul>
 *   <li>{@code webblock_outbox_oldest_unsent_age_seconds}: age of the oldest record not yet handled (status {@code
 *       NEW}), 0 when there is none;
 *   <li>{@code webblock_outbox_max_failures}: the highest failure count among those records;
 *   <li>{@code webblock_outbox_failed_records}: records a handler gave up on ({@code FAILED}; each blocks its key).
 * </ul>
 *
 * Each value is one indexed query, run when the registry is scraped, bounded by a {@value #QUERY_TIMEOUT_SECONDS}
 * second statement timeout so a slow database cannot hold a scrape thread; a failed query reports NaN.
 */
class OutboxMetrics implements MeterBinder {

    private static final Logger log = LoggerFactory.getLogger(OutboxMetrics.class);

    static final int QUERY_TIMEOUT_SECONDS = 2;

    private final JdbcClient jdbc;

    /** Every statement of this client times out after {@value #QUERY_TIMEOUT_SECONDS} seconds. */
    OutboxMetrics(DataSource dataSource) {
        JdbcTemplate template = new JdbcTemplate(dataSource);
        template.setQueryTimeout(QUERY_TIMEOUT_SECONDS);
        this.jdbc = JdbcClient.create(template);
    }

    @Override
    public void bindTo(MeterRegistry registry) {
        Gauge.builder("webblock.outbox.oldest.unsent.age", this, OutboxMetrics::oldestUnsentAgeSeconds)
                .description("Age of the oldest outbox record not yet handled")
                .baseUnit("seconds")
                .register(registry);
        Gauge.builder("webblock.outbox.max.failures", this, OutboxMetrics::maxFailures)
                .description("Highest failure count among outbox records not yet handled")
                .register(registry);
        Gauge.builder("webblock.outbox.failed.records", this, OutboxMetrics::failedRecords)
                .description("Outbox records a handler gave up on; each blocks its key")
                .register(registry);
    }

    double oldestUnsentAgeSeconds() {
        return query(
                "SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(created_at)), 0)::float8 FROM " + OutboxRecords.TABLE
                        + " WHERE status = ?",
                OutboxRecords.NEW);
    }

    double maxFailures() {
        return query(
                "SELECT COALESCE(max(failure_count), 0)::float8 FROM " + OutboxRecords.TABLE + " WHERE status = ?",
                OutboxRecords.NEW);
    }

    double failedRecords() {
        return query("SELECT count(*)::float8 FROM " + OutboxRecords.TABLE + " WHERE status = ?", OutboxRecords.FAILED);
    }

    private double query(String sql, String status) {
        try {
            Double value = jdbc.sql(sql).param(status).query(Double.class).single();
            return value == null ? Double.NaN : value;
        } catch (DataAccessException e) {
            log.warn("Outbox gauge query failed: {}", e.getMessage());
            return Double.NaN;
        }
    }
}
