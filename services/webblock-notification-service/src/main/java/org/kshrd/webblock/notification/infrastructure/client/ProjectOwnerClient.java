package org.kshrd.webblock.notification.infrastructure.client;

import java.util.UUID;

/**
 * Asks project-service who owns a project (contract: project/v1/owner.proto, GetProjectOwner).
 * Only a stub exists so far (profile "local-stub"); the real gRPC client is not written yet.
 */
public interface ProjectOwnerClient {

    ProjectOwner getProjectOwner(UUID projectId);

    record ProjectOwner(String ownerUserId, String projectName) {
    }
}
