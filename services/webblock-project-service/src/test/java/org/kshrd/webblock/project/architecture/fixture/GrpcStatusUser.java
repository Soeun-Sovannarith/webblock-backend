package org.kshrd.webblock.project.architecture.fixture;

import io.grpc.Status;

/** A class outside presentation.grpc that uses io.grpc: what onlyPresentationGrpcUsesGrpcTypes forbids. */
public class GrpcStatusUser {

    public Status refused() {
        return Status.PERMISSION_DENIED;
    }
}
