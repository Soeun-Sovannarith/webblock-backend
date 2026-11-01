package org.kshrd.webblock.notification.infrastructure.client.stub;

import org.kshrd.webblock.notification.infrastructure.client.ProjectOwnerClient;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.util.UUID;

/** LOCAL TESTING ONLY. Every project belongs to the owner in "webblock.notification.stub". Never enable in uat/prod. */
@Component
@Profile("local-stub")
public class StubProjectOwnerClient implements ProjectOwnerClient {

    private static final Logger log = LoggerFactory.getLogger(StubProjectOwnerClient.class);

    private final NotificationProperties.Stub stub;

    public StubProjectOwnerClient(NotificationProperties properties) {
        this.stub = properties.stub();
        log.warn("Using STUB project owner lookup (profile local-stub). Not for uat/prod.");
    }

    @Override
    public ProjectOwner getProjectOwner(UUID projectId) {
        return new ProjectOwner(stub.ownerUserId(), stub.projectName());
    }
}
