package org.kshrd.webblock.notification.infrastructure.client;

/**
 * Asks user-service for the contact details of a user (contract: account/v1/users.proto, GetUserContact).
 * The email is personal data: use it to send, never log it.
 * Only a stub exists so far (profile "local-stub"); the real gRPC client is not written yet.
 */
public interface UserContactClient {

    UserContact getUserContact(String userId);

    record UserContact(String email, String fullName, String status) {
        @Override
        public String toString() {
            return "UserContact[status=" + status + "]";
        }
    }
}
