package org.kshrd.webblock.project.infrastructure.config.project;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.domain.error.ConstraintErrorRegistration;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.domain.project.model.ProjectEntity;
import org.kshrd.webblock.project.infrastructure.persistence.project.ProjectRepository;
import org.kshrd.webblock.project.support.DatabaseErrors;
import org.kshrd.webblock.project.support.JpaSliceTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

@JpaSliceTest
@Import(ProjectConfig.class)
class ProjectConfigTest {

    @Autowired
    ConstraintErrorRegistration projectConstraints;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    ProjectRepository projects;

    @Autowired
    TransactionTemplate tx;

    @Test
    void everyRegisteredConstraintExistsInTheSchema() {
        assertThat(projectConstraints.constraints()).isNotEmpty();
        for (String name : projectConstraints.constraints().keySet()) {
            Integer found = jdbc.queryForObject(
                    "SELECT (SELECT count(*) FROM pg_constraint WHERE conname = ?)"
                            + " + (SELECT count(*) FROM pg_indexes WHERE schemaname = 'project' AND indexname = ?)"
                            + " + (SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid"
                            + "    WHERE p.prosrc LIKE '%' || ? || '%')",
                    Integer.class, name, name, name);
            assertThat(found).as(name).isPositive();
        }
    }

    @Test
    @DisplayName("FR-13.2: a taken sub-domain maps to HOSTNAME_TAKEN, a taken ID to PROJECT_ID_CONFLICT; nothing else")
    void fr13_2_theRegisteredConstraintsAreTheTwoKeysOfProjects() {
        assertThat(projectConstraints.constraints())
                .containsOnly(
                        Map.entry("projects_pkey", ErrorCode.PROJECT_ID_CONFLICT),
                        Map.entry("projects_subdomain_key", ErrorCode.HOSTNAME_TAKEN));
    }

    @Test
    @DisplayName("FR-13.2: a second project with a held sub-domain violates projects_subdomain_key")
    void fr13_2_aHeldSubdomainViolationMapsToHostnameTaken() {
        String subdomain = "taken-" + UUID.randomUUID().toString().substring(0, 8);
        tx.execute(s -> projects.saveAndFlush(draft(UUID.randomUUID(), subdomain)));
        assertThatThrownBy(() -> tx.execute(s -> projects.saveAndFlush(draft(UUID.randomUUID(), subdomain))))
                .isInstanceOf(DataIntegrityViolationException.class)
                .satisfies(e -> {
                    String constraint = DatabaseErrors.constraintName(e).orElseThrow();
                    assertThat(constraint).isEqualTo("projects_subdomain_key");
                    assertThat(projectConstraints.constraints()).containsEntry(constraint, ErrorCode.HOSTNAME_TAKEN);
                });
    }

    private static ProjectEntity draft(UUID projectId, String subdomain) {
        return ProjectEntity.draft(
                projectId, UUID.randomUUID(), "p", subdomain, UUID.randomUUID(), "0.1.0", Instant.now());
    }
}
