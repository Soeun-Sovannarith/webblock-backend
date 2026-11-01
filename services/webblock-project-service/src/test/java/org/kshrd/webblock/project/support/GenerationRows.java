package org.kshrd.webblock.project.support;

import java.sql.Timestamp;
import java.time.Clock;
import java.util.UUID;
import org.kshrd.webblock.project.application.generation.handler.InputHasher;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.springframework.jdbc.core.JdbcTemplate;

/**
 * Inserts generation rows with plain SQL, as the application's own login (project_app), for tests that need a
 * contract or a captured request without the capture API. Times come from the given clock, so a test that moves a
 * MutableClock controls created_at and the admission window together.
 */
public final class GenerationRows {

    public static final String API_CONTRACT_VERSION = "1.0.0";

    private static final String INSERT_CONTRACT = """
            INSERT INTO project.generation_contracts
              (generation_contract_id, project_id, version_number, api_contract_version, created_by_user_id, created_at)
            SELECT ?, ?, coalesce(max(version_number), 0) + 1, ?, ?, ?
            FROM project.generation_contracts WHERE project_id = ?""";

    private static final String INSERT_CAPTURED = """
            INSERT INTO project.generation_requests
              (generation_request_id, project_id, idempotency_key, request_hash, generation_contract_id,
               captured_edit_revision, captured_input, captured_input_hash, status, created_at, updated_at)
            SELECT ?, p.project_id, ?, ?, ?, p.edit_revision, ?::jsonb, ?, 'captured', ?, ?
            FROM project.projects p WHERE p.project_id = ?""";

    private final JdbcTemplate jdbc;
    private final Clock clock;

    /** jdbc: the application's data source (project_app); clock: the application's clock. */
    public GenerationRows(JdbcTemplate jdbc, Clock clock) {
        this.jdbc = jdbc;
        this.clock = clock;
    }

    /** The project's next contract version, with no product type; returns its generationContractId. */
    public UUID contract(UUID projectId, UUID userId) {
        UUID contractId = Uuids.v7();
        int inserted = jdbc.update(
                INSERT_CONTRACT,
                contractId,
                projectId,
                API_CONTRACT_VERSION,
                userId,
                Timestamp.from(clock.instant()),
                projectId);
        if (inserted != 1) {
            throw new IllegalStateException("The contract was not inserted");
        }
        return contractId;
    }

    /**
     * A captured request at the project's current edit revision, with captured_input_hash = InputHasher.sha256 of the
     * given JSON and the request hash of {"generationContractId":"<id>"} (plan decision P4); returns its
     * generationRequestId.
     */
    public UUID captured(UUID projectId, UUID contractId, String capturedInputJson) {
        UUID requestId = Uuids.v7();
        Timestamp now = Timestamp.from(clock.instant());
        int inserted = jdbc.update(
                INSERT_CAPTURED,
                requestId,
                "key-" + requestId,
                InputHasher.sha256("{\"generationContractId\":\"" + contractId + "\"}"),
                contractId,
                capturedInputJson,
                InputHasher.sha256(capturedInputJson),
                now,
                now,
                projectId);
        if (inserted != 1) {
            throw new IllegalStateException("The request was not inserted: no such project");
        }
        return requestId;
    }
}
