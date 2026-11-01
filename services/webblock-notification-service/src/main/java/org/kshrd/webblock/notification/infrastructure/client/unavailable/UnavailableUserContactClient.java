package org.kshrd.webblock.notification.infrastructure.client.unavailable;

import org.kshrd.webblock.notification.infrastructure.client.UserContactClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

/**
 * Stands in for the real user-service client (not written yet) in every profile except "local-stub" (uat/prod).
 * It returns no contact and invents none: it fails closed, so no email goes to a guessed or fallback address.
 * Delete this class when the real gRPC client is added.
 */
@Component
@Profile("!local-stub")
public class UnavailableUserContactClient implements UserContactClient {

    private static final Logger log = LoggerFactory.getLogger(UnavailableUserContactClient.class);

    public UnavailableUserContactClient() {
        log.warn("No user-contact client is integrated yet: notifications for a user cannot be delivered "
                + "and are dead-lettered until the real client exists. Admin-only notifications are not affected.");
    }

    @Override
    public UserContact getUserContact(String userId) {
        throw new RecipientLookupNotConfiguredException(
                "User contact lookup is not integrated yet; owner notification cannot be delivered");
    }
}
