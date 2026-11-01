package org.kshrd.webblock.project.architecture.fixture;

import io.grpc.BindableService;
import io.grpc.ServerServiceDefinition;

/** A gRPC service outside presentation.grpc: what bindableServicesSitInPresentationGrpc forbids. */
public class StrayGrpcService implements BindableService {

    @Override
    public ServerServiceDefinition bindService() {
        return ServerServiceDefinition.builder("fixture.Stray").build();
    }
}
