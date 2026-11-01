package org.kshrd.webblock.notification.infrastructure.client.stub;

import org.kshrd.webblock.notification.infrastructure.client.UserContactClient;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

/** LOCAL TESTING ONLY. Every user has the contact in "webblock.notification.stub". Never enable in uat/prod. */
@Component
@Profile("local-stub")
public class StubUserContactClient implements UserContactClient {

    private static final Logger log = LoggerFactory.getLogger(StubUserContactClient.class);

    private final NotificationProperties.Stub stub;

    public StubUserContactClient(NotificationProperties properties) {
        this.stub = properties.stub();
        log.warn("Using STUB user contact lookup (profile local-stub). Not for uat/prod.");
    }

    @Override
    public UserContact getUserContact(String userId) {
        return new UserContact(stub.contactEmail(), stub.contactName(), "ACTIVE");
    }
}
