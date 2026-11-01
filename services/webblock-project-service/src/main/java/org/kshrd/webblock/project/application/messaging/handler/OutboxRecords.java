package org.kshrd.webblock.project.application.messaging.handler;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

/**
 * The one place that knows Namastack Outbox's own table, {@value #TABLE} (created to the library's 1.7.3 schema by
 * V4__outbox.sql), so a library upgrade is checked here only. A record is {@value #NEW} until it is handled, then
 * {@value #COMPLETED}, or {@value #FAILED} once its handler's retries are spent. The status enum is in
 * namastack-outbox-core, which is only on the runtime classpath, so its names are repeated here; OutboxRecordsTest
 * compares them with the library's.
 */
@Service
public class OutboxRecords {

    public static final String TABLE = "project_messaging.outbox_record";
    public static final String NEW = "NEW";
    public static final String COMPLETED = "COMPLETED";
    public static final String FAILED = "FAILED";

    private static final String COUNT =
            "SELECT count(*), count(*) FILTER (WHERE status <> ?) FROM " + TABLE + " WHERE record_key = ?";

    /** The records ever stored under a key, and how many of them have not completed (FAILED ones included). */
    public record Count(long records, long unfinished) {}

    private final JdbcClient projectDb;

    public OutboxRecords(JdbcClient projectDb) {
        this.projectDb = projectDb;
    }

    /** Runs in the caller's transaction when there is one. */
    public Count count(String recordKey) {
        return projectDb
                .sql(COUNT)
                .params(COMPLETED, recordKey)
                .query((rs, n) -> new Count(rs.getLong(1), rs.getLong(2)))
                .single();
    }
}
