package org.kshrd.webblock.project.presentation.grpc.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.catchThrowableOfType;
import static org.assertj.core.groups.Tuple.tuple;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import com.google.protobuf.InvalidProtocolBufferException;
import com.google.rpc.BadRequest;
import com.google.rpc.ErrorInfo;
import io.grpc.Context;
import io.grpc.Deadline;
import io.grpc.ManagedChannel;
import io.grpc.Metadata;
import io.grpc.Server;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.inprocess.InProcessChannelBuilder;
import io.grpc.inprocess.InProcessServerBuilder;
import io.grpc.protobuf.StatusProto;
import io.grpc.stub.MetadataUtils;
import io.grpc.stub.StreamObserver;
import jakarta.validation.Validation;
import jakarta.validation.ValidatorFactory;
import java.io.IOException;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.kshrd.webblock.project.application.access.handler.AccessCheckService;
import org.kshrd.webblock.project.application.access.model.AccessAction;
import org.kshrd.webblock.project.application.access.request.AccessCheckRequest;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectOwnership;
import org.kshrd.webblock.project.common.presentation.grpc.GrpcCallLimitInterceptor;
import org.kshrd.webblock.project.common.presentation.grpc.GrpcServer;
import webblock.project.v1.Access.CheckAccessRequest;
import webblock.project.v1.Access.CheckAccessResponse;
import webblock.project.v1.AccessServiceGrpc;

/**
 * CheckAccess in-process, through the server's own interceptors, with the real request rules and the real
 * AccessCheckService. Only the project lookup is a stand-in.
 */
class AccessGrpcServiceTest {

    static final UUID OWNER = UUID.randomUUID();
    static final UUID OTHER = UUID.randomUUID();
    static final UUID PROJECT = UUID.randomUUID();

    private static final ValidatorFactory VALIDATION = Validation.buildDefaultValidatorFactory();

    private final ProjectService projects = mock(ProjectService.class);
    private AccessGrpcService service;
    private Server server;
    private ManagedChannel channel;

    @AfterAll
    static void closeValidation() {
        VALIDATION.close();
    }

    @BeforeEach
    void start() throws IOException {
        String name = InProcessServerBuilder.generateName();
        service = new AccessGrpcService(
                new AccessGrpcMapper(VALIDATION.getValidator()), new AccessCheckService(projects));
        server = InProcessServerBuilder.forName(name)
                .directExecutor()
                .addService(GrpcServer.intercepted(service, new GrpcCallLimitInterceptor(8)))
                .build()
                .start();
        channel = InProcessChannelBuilder.forName(name).directExecutor().build();
    }

    @AfterEach
    void stop() throws InterruptedException {
        channel.shutdownNow();
        server.shutdownNow();
        channel.awaitTermination(5, TimeUnit.SECONDS);
        server.awaitTermination(5, TimeUnit.SECONDS);
    }

    private void project(boolean suspended) {
        when(projects.ownership(PROJECT)).thenReturn(Optional.of(new ProjectOwnership(OWNER, suspended)));
    }

    private CheckAccessResponse check(UUID user, String action) {
        return check(request(user.toString(), PROJECT.toString(), action));
    }

    private CheckAccessResponse check(CheckAccessRequest request) {
        return AccessServiceGrpc.newBlockingStub(channel).checkAccess(request);
    }

    private static CheckAccessRequest request(String userId, String projectId, String action) {
        return CheckAccessRequest.newBuilder()
                .setUserId(userId)
                .setProjectId(projectId)
                .setAction(action)
                .build();
    }

    private StatusRuntimeException refused(CheckAccessRequest request) {
        return catchThrowableOfType(StatusRuntimeException.class, () -> check(request));
    }

    @Test
    @DisplayName("FR-31, TR-24: a call its caller cancelled, or whose deadline passed, does no project lookup")
    void fr31_aCancelledOrExpiredCallDoesNoLookup() throws Exception {
        project(false);
        @SuppressWarnings("unchecked") // Mockito cannot name a generic type
        StreamObserver<CheckAccessResponse> answer = mock(StreamObserver.class);
        CheckAccessRequest asked = request(OWNER.toString(), PROJECT.toString(), "deploy_release");

        try (Context.CancellableContext cancelled = Context.current().withCancellation()) {
            cancelled.cancel(null);
            cancelled.run(() -> service.checkAccess(asked, answer));
        }

        ScheduledExecutorService timer = Executors.newSingleThreadScheduledExecutor();
        try (Context.CancellableContext expired =
                Context.current().withDeadline(Deadline.after(-1, TimeUnit.SECONDS), timer)) {
            expired.run(() -> service.checkAccess(asked, answer));
        } finally {
            timer.shutdownNow();
        }

        verifyNoInteractions(projects);
        verifyNoMoreInteractions(answer);
        assertThat(check(OWNER, "deploy_release").getAllowed()).isTrue();
    }

    @Test
    @DisplayName("FR-31.1: the owner of a live project may deploy_release: OK, allowed, reason empty")
    void fr31_1_theOwnerIsAllowed() {
        project(false);

        CheckAccessResponse answer = check(OWNER, "deploy_release");

        assertThat(answer.getAllowed()).isTrue();
        assertThat(answer.getReason()).isEmpty();
    }

    @Test
    @DisplayName("FR-31.2: another user is not allowed: NOT_MANAGER")
    void fr31_2_anotherUserIsNotTheManager() {
        project(false);

        CheckAccessResponse answer = check(OTHER, "deploy_release");

        assertThat(answer.getAllowed()).isFalse();
        assertThat(answer.getReason()).isEqualTo("NOT_MANAGER");
    }

    @Test
    @DisplayName("FR-31.3: an unknown project is an OK answer with NOT_FOUND, never the gRPC NOT_FOUND status")
    void fr31_3_anUnknownProjectIsAnAnswer() {
        when(projects.ownership(PROJECT)).thenReturn(Optional.empty());

        CheckAccessResponse answer = check(OWNER, "get_release");

        assertThat(answer.getAllowed()).isFalse();
        assertThat(answer.getReason()).isEqualTo("NOT_FOUND");
    }

    @Test
    @DisplayName("FR-38.5, FR-31.4: a deleted project is unknown: NOT_FOUND for rollback_release and list_releases")
    void fr38_5_aDeletedProjectIsNotFound() {
        when(projects.ownership(PROJECT)).thenReturn(Optional.empty());

        CheckAccessResponse change = check(OWNER, "rollback_release");
        CheckAccessResponse read = check(OWNER, "list_releases");

        assertThat(change.getAllowed()).isFalse();
        assertThat(change.getReason()).isEqualTo("NOT_FOUND");
        assertThat(read.getAllowed()).isFalse();
        assertThat(read.getReason()).isEqualTo("NOT_FOUND");
    }

    @Test
    @DisplayName("FR-31.4: a suspended project refuses rollback_release (PROJECT_SUSPENDED), allows list_releases")
    void fr31_4_aSuspendedProjectRefusesChangesAndAllowsReads() {
        project(true);

        CheckAccessResponse change = check(OWNER, "rollback_release");
        CheckAccessResponse read = check(OWNER, "list_releases");

        assertThat(change.getAllowed()).isFalse();
        assertThat(change.getReason()).isEqualTo("PROJECT_SUSPENDED");
        assertThat(read.getAllowed()).isTrue();
    }

    @Test
    @DisplayName("FR-31.5: project_id abc is INVALID_ARGUMENT, ErrorInfo VALIDATION_FAILED, field project_id")
    void fr31_5_aBadProjectIdIsInvalidArgument() throws InvalidProtocolBufferException {
        StatusRuntimeException error = refused(request(OWNER.toString(), "abc", "deploy_release"));

        assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        ErrorInfo info = errorInfo(error);
        assertThat(info.getReason()).isEqualTo("VALIDATION_FAILED");
        assertThat(info.getDomain()).isEqualTo("project.webblock");
        assertThat(info.getMetadataMap()).containsKey("request_id");
        assertThat(violations(error))
                .extracting(BadRequest.FieldViolation::getField, BadRequest.FieldViolation::getReason)
                .containsExactly(tuple("project_id", "UUID_INVALID"));
        verifyNoInteractions(projects);
    }

    @Test
    @DisplayName("FR-31.5: empty fields read as missing: FIELD_REQUIRED for user_id, project_id and action")
    void fr31_5_emptyFieldsAreMissing() throws InvalidProtocolBufferException {
        StatusRuntimeException error = refused(CheckAccessRequest.getDefaultInstance());

        assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        assertThat(violations(error))
                .extracting(BadRequest.FieldViolation::getField, BadRequest.FieldViolation::getReason)
                .containsExactly(
                        tuple("user_id", "FIELD_REQUIRED"),
                        tuple("project_id", "FIELD_REQUIRED"),
                        tuple("action", "FIELD_REQUIRED"));
        verifyNoInteractions(projects);
    }

    @ParameterizedTest
    @ValueSource(strings = {"AZngogAAcACAAAAAAAAAqg==", "0190a0b8c1d27e3f8a4b5c6d7e8f9a0b", "1-1-1-1-1"})
    @DisplayName("FR-31.5: a user_id that is not a canonical UUID is UUID_INVALID, as over REST")
    void fr31_5_nonCanonicalIdsAreRefused(String id) throws InvalidProtocolBufferException {
        StatusRuntimeException error = refused(request(id, PROJECT.toString(), "get_release"));

        assertThat(violations(error))
                .extracting(BadRequest.FieldViolation::getField, BadRequest.FieldViolation::getReason)
                .containsExactly(tuple("user_id", "UUID_INVALID"));
        verifyNoInteractions(projects);
    }

    @Test
    @DisplayName("FR-32.1: manage_site is allowed for the owner of a live project: OK, allowed, reason empty")
    void fr32_1_manageSiteIsAllowedForTheOwner() {
        project(false);

        CheckAccessResponse owner = check(OWNER, "manage_site");
        CheckAccessResponse other = check(OTHER, "manage_site");

        assertThat(owner.getAllowed()).isTrue();
        assertThat(owner.getReason()).isEmpty();
        assertThat(other.getAllowed()).isFalse();
        assertThat(other.getReason()).isEqualTo("NOT_MANAGER");
    }

    @Test
    @DisplayName("FR-32.2: manage_site on a suspended project is not allowed: PROJECT_SUSPENDED")
    void fr32_2_manageSiteOnASuspendedProjectIsProjectSuspended() {
        project(true);

        CheckAccessResponse answer = check(OWNER, "manage_site");

        assertThat(answer.getAllowed()).isFalse();
        assertThat(answer.getReason()).isEqualTo("PROJECT_SUSPENDED");
    }

    @ParameterizedTest
    @ValueSource(strings = {"drop_db", "DEPLOY_RELEASE", "deploy_release ", " get_release", "0"})
    @DisplayName("FR-32.3: an action outside the list, drop_db included, is INVALID_ARGUMENT, field action")
    void fr32_3_anUnknownActionIsInvalidArgument(String action) throws InvalidProtocolBufferException {
        StatusRuntimeException error = refused(request(OWNER.toString(), PROJECT.toString(), action));

        assertThat(error.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        assertThat(errorInfo(error).getReason()).isEqualTo("VALIDATION_FAILED");
        assertThat(violations(error))
                .extracting(BadRequest.FieldViolation::getField, BadRequest.FieldViolation::getReason)
                .containsExactly(tuple("action", "VALUE_INVALID"));
        verifyNoInteractions(projects);
    }

    @Test
    @DisplayName("FR-33.1: admin D who does not own P gets NOT_MANAGER; x-user-id and role metadata are not read")
    void fr33_1_anAdminGetsNoExtraPower() {
        project(false);
        UUID admin = UUID.randomUUID();
        Metadata claims = new Metadata();
        claims.put(Metadata.Key.of("x-user-id", Metadata.ASCII_STRING_MARSHALLER), OWNER.toString());
        claims.put(Metadata.Key.of("x-user-roles", Metadata.ASCII_STRING_MARSHALLER), "admin");
        claims.put(Metadata.Key.of("authorization", Metadata.ASCII_STRING_MARSHALLER), "Bearer admin-token");

        CheckAccessResponse answer = AccessServiceGrpc.newBlockingStub(channel)
                .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(claims))
                .checkAccess(request(admin.toString(), PROJECT.toString(), "rollback_release"));

        assertThat(answer.getAllowed()).isFalse();
        assertThat(answer.getReason()).isEqualTo("NOT_MANAGER");
    }

    @Test
    @DisplayName("FR-33.1: the owner named in user_id is allowed whatever x-user-id says")
    void fr33_1_onlyTheUserIdFieldIsTheSubject() {
        project(false);
        Metadata claims = new Metadata();
        claims.put(Metadata.Key.of("x-user-id", Metadata.ASCII_STRING_MARSHALLER), OTHER.toString());

        CheckAccessResponse answer = AccessServiceGrpc.newBlockingStub(channel)
                .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(claims))
                .checkAccess(request(OWNER.toString(), PROJECT.toString(), "rollback_release"));

        assertThat(answer.getAllowed()).isTrue();
    }

    @ParameterizedTest
    @EnumSource(AccessAction.class)
    @DisplayName("TR-26: every action the REST request accepts is accepted by CheckAccess under the same name")
    void tr26_everyRestActionNameIsACheckAccessAction(AccessAction action) {
        project(false);
        String restName = action.name().toLowerCase(Locale.ROOT);
        AccessCheckRequest rest = new AccessCheckRequest(OWNER.toString(), PROJECT.toString(), restName);

        assertThat(VALIDATION.getValidator().validate(rest)).isEmpty();
        assertThat(rest.accessAction()).isEqualTo(action);
        assertThat(check(OWNER, restName).getAllowed()).isTrue();
    }

    private static ErrorInfo errorInfo(StatusRuntimeException error) throws InvalidProtocolBufferException {
        com.google.rpc.Status details = StatusProto.fromThrowable(error);
        assertThat(details).isNotNull();
        return details.getDetails(0).unpack(ErrorInfo.class);
    }

    private static List<BadRequest.FieldViolation> violations(StatusRuntimeException error)
            throws InvalidProtocolBufferException {
        com.google.rpc.Status details = StatusProto.fromThrowable(error);
        assertThat(details).isNotNull();
        assertThat(details.getDetailsCount()).isEqualTo(2);
        return details.getDetails(1).unpack(BadRequest.class).getFieldViolationsList();
    }
}
