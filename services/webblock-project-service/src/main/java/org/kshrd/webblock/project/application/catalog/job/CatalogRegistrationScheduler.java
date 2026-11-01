package org.kshrd.webblock.project.application.catalog.job;

import io.namastack.outbox.Outbox;
import java.sql.Timestamp;
import java.time.Clock;
import java.util.UUID;
import org.kshrd.webblock.project.application.catalog.model.BusinessProfile;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;

/** Schedules a website's catalog registration in the caller's project-database transaction (spec 5.1 step 4). */
@Service
public class CatalogRegistrationScheduler {

    private static final String INSERT_PENDING = """
            INSERT INTO project_messaging.pending_catalog_registrations (project_id, business_profile, created_at)
            VALUES (?, ?::jsonb, ?)""";

    private final Outbox outbox;
    private final JdbcClient projectDb;
    private final JsonMapper json;
    private final Clock clock;

    public CatalogRegistrationScheduler(Outbox outbox, JdbcClient projectDb, JsonMapper json, Clock clock) {
        this.outbox = outbox;
        this.projectDb = projectDb;
        this.json = json;
        this.clock = clock;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void schedule(UUID projectId, BusinessProfile profile) {
        projectDb
                .sql(INSERT_PENDING)
                .params(projectId, json.writeValueAsString(profile), Timestamp.from(clock.instant()))
                .update();
        // Key catalog:<projectId>: a project's catalog jobs keep their order without holding back its events.
        outbox.schedule(new CatalogRegistrationJob(projectId), "catalog:" + projectId);
    }
}
