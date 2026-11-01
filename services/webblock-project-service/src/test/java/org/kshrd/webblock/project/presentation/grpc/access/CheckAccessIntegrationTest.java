package org.kshrd.webblock.project.presentation.grpc.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.catchThrowableOfType;

import com.google.rpc.ErrorInfo;
import io.grpc.Grpc;
import io.grpc.InsecureChannelCredentials;
import io.grpc.ManagedChannel;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.protobuf.StatusProto;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.presentation.grpc.GrpcServer;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import webblock.project.v1.Access.CheckAccessRequest;
import webblock.project.v1.Access.CheckAccessResponse;
import webblock.project.v1.AccessServiceGrpc;

/** CheckAccess over Netty on a free port, against the real project database, in the whole application. */
@ServiceIntegrationTest
class CheckAccessIntegrationTest {

    @Autowired
    GrpcServer grpc;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    Environment environment;

    private ManagedChannel channel;

    @BeforeEach
    void connect() {
        channel = Grpc.newChannelBuilderForAddress("localhost", grpc.port(), InsecureChannelCredentials.create())
                .build();
    }

    @AfterEach
    void disconnect() throws InterruptedException {
        channel.shutdownNow();
        channel.awaitTermination(5, TimeUnit.SECONDS);
    }

    private AccessServiceGrpc.AccessServiceBlockingStub stub() {
        return AccessServiceGrpc.newBlockingStub(channel).withDeadlineAfter(30, TimeUnit.SECONDS);
    }

    private static CheckAccessRequest request(UUID user, UUID project, String action) {
        return CheckAccessRequest.newBuilder()
                .setUserId(user.toString())
                .setProjectId(project.toString())
                .setAction(action)
                .build();
    }

    private UUID project(UUID owner) {
        UUID projectId = UUID.randomUUID();
        jdbc.update(
                "INSERT INTO project.projects (project_id, owner_user_id, name, subdomain, business_type_id, "
                        + "component_catalog_version, status, created_at, updated_at) "
                        + "VALUES (?, ?, 'Checked shop', ?, ?, '0.1.0', 'draft', now(), now())",
                projectId,
                owner,
                TestSubdomains.of(projectId),
                UUID.randomUUID());
        return projectId;
    }

    @Test
    @DisplayName("FR-31.1: the running application answers CheckAccess on its gRPC port from the project database")
    void fr31_1_theApplicationServesCheckAccess() {
        UUID owner = UUID.randomUUID();
        UUID projectId = project(owner);

        CheckAccessResponse allowed = stub().checkAccess(request(owner, projectId, "deploy_release"));
        CheckAccessResponse stranger = stub().checkAccess(request(UUID.randomUUID(), projectId, "deploy_release"));
        CheckAccessResponse unknown = stub().checkAccess(request(owner, UUID.randomUUID(), "deploy_release"));

        assertThat(allowed.getAllowed()).isTrue();
        assertThat(allowed.getReason()).isEmpty();
        assertThat(stranger.getReason()).isEqualTo("NOT_MANAGER");
        assertThat(unknown.getReason()).isEqualTo("NOT_FOUND");
    }

    @Test
    @DisplayName("TR-24: the running application serves exactly one gRPC service, AccessService")
    void tr24_theApplicationServesOnlyAccessService() {
        assertThat(grpc.serviceNames()).containsExactly("webblock.project.v1.AccessService");
    }

    @Test
    @DisplayName("FR-31.6: projects locked past the lock timeout answers UNAVAILABLE, reason DEPENDENCY_UNAVAILABLE")
    void fr31_6_aTableLockAnswersUnavailable() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = project(owner);
        try (Connection holder = DriverManager.getConnection(
                        environment.getProperty("spring.datasource.url"),
                        PostgresFixture.MIGRATOR_LOGIN,
                        PostgresFixture.PASSWORD);
                Statement lock = holder.createStatement()) {
            holder.setAutoCommit(false);
            // Only the table's owner may take this lock; the application's login may not.
            lock.execute("SET ROLE project_migrator");
            lock.execute("LOCK TABLE project.projects IN ACCESS EXCLUSIVE MODE");

            long started = System.nanoTime();
            StatusRuntimeException busy = catchThrowableOfType(
                    StatusRuntimeException.class, () -> stub().checkAccess(request(owner, projectId, "get_release")));
            Duration waited = Duration.ofNanos(System.nanoTime() - started);
            holder.rollback();

            assertThat(busy.getStatus().getCode()).isEqualTo(Status.Code.UNAVAILABLE);
            com.google.rpc.Status details = StatusProto.fromThrowable(busy);
            assertThat(details).isNotNull();
            assertThat(details.getDetails(0).unpack(ErrorInfo.class).getReason())
                    .isEqualTo("DEPENDENCY_UNAVAILABLE");
            assertThat(waited).isBetween(Duration.ofSeconds(4), Duration.ofSeconds(12));
            assertThat(busy.toString()).doesNotContain("lock").doesNotContain("project.projects");
        }

        assertThat(stub().checkAccess(request(owner, projectId, "get_release")).getAllowed())
                .isTrue();
    }
}
