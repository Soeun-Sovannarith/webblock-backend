package org.kshrd.webblock.project.presentation.grpc.access;

import io.grpc.Context;
import io.grpc.Deadline;
import io.grpc.stub.StreamObserver;
import org.kshrd.webblock.project.application.access.handler.AccessCheckService;
import org.kshrd.webblock.project.application.access.model.AccessDecision;
import org.kshrd.webblock.project.application.access.request.AccessCheckRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import webblock.project.v1.Access.CheckAccessRequest;
import webblock.project.v1.Access.CheckAccessResponse;
import webblock.project.v1.AccessServiceGrpc;

/**
 * {@code webblock.project.v1.AccessService/CheckAccess} (FR-31, FR-33): the same decision as the REST check, asked of
 * the same {@link AccessCheckService}. A denial, an unknown project included, is an OK answer, never gRPC NOT_FOUND.
 * The subject is only the {@code user_id} field: no metadata (x-user-id, roles, a token) is read, so an admin gets
 * the answer any other user gets. A call cancelled or past its deadline stops before the lookup. Failures become gRPC
 * errors in the server's error interceptor.
 */
@Component
class AccessGrpcService extends AccessServiceGrpc.AccessServiceImplBase {

    private static final Logger log = LoggerFactory.getLogger(AccessGrpcService.class);

    private final AccessGrpcMapper mapper;
    private final AccessCheckService service;

    AccessGrpcService(AccessGrpcMapper mapper, AccessCheckService service) {
        this.mapper = mapper;
        this.service = service;
    }

    @Override
    public void checkAccess(CheckAccessRequest request, StreamObserver<CheckAccessResponse> answer) {
        AccessCheckRequest checked = mapper.toRequest(request);
        if (gone(Context.current())) {
            log.debug("CheckAccess cancelled or past its deadline before the lookup");
            return;
        }
        AccessDecision decision = service.check(checked.user(), checked.project(), checked.accessAction());
        answer.onNext(mapper.toResponse(decision));
        answer.onCompleted();
    }

    /**
     * True when the caller cancelled or the deadline passed: nobody waits for the answer, so the call does no lookup
     * and takes no pooled connection. grpc-java has already closed such a call.
     */
    private static boolean gone(Context call) {
        Deadline deadline = call.getDeadline();
        return call.isCancelled() || (deadline != null && deadline.isExpired());
    }
}
