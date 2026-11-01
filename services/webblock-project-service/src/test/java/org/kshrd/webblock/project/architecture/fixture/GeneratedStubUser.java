package org.kshrd.webblock.project.architecture.fixture;

import webblock.project.v1.Access.CheckAccessRequest;

/** Uses a generated proto type outside presentation.grpc: what onlyPresentationGrpcUsesGrpcTypes forbids. */
public class GeneratedStubUser {

    public String userOf(CheckAccessRequest request) {
        return request.getUserId();
    }
}
