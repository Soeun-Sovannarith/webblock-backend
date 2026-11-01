package org.kshrd.webblock.notification.infrastructure.client.unavailable;

import org.kshrd.webblock.notification.infrastructure.client.ProjectOwnerClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.util.UUID;

/**
 * Stands in for the real project-service client (not written yet) in every profile except "local-stub" (uat/prod).
 * It returns no owner and invents none: it fails closed, so owner notifications are never sent to a guessed recipient.
 * Delete this class when the real gRPC client is added.
 */
@Component
@Profile("!local-stub")
public class UnavailableProjectOwnerClient implements ProjectOwnerClient {

    private static final Logger log = LoggerFactory.getLogger(UnavailableProjectOwnerClient.class);

    public UnavailableProjectOwnerClient() {
        log.warn("No project-owner client is integrated yet: notifications for a project's owner cannot be delivered "
                + "and are dead-lettered until the real client exists. Admin-only notifications are not affected.");
    }

    @Override
    public ProjectOwner getProjectOwner(UUID projectId) {
        throw new RecipientLookupNotConfiguredException(
                "Project owner lookup is not integrated yet; owner notification cannot be delivered");
    }
}
