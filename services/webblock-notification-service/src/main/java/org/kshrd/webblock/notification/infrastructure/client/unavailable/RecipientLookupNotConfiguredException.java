package org.kshrd.webblock.notification.infrastructure.client.unavailable;

import org.kshrd.webblock.notification.common.domain.PermanentFailure;

/**
 * The owner of a project or a user's contact cannot be looked up because the real client is not integrated yet.
 * Retrying cannot help, so the message goes straight to the dead-letter queue, where it is kept until the real client exists.
 */
public class RecipientLookupNotConfiguredException extends IllegalStateException implements PermanentFailure {

    public RecipientLookupNotConfiguredException(String message) {
        super(message);
    }
}
