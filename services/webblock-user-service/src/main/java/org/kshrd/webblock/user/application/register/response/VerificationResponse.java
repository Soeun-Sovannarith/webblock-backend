package org.kshrd.webblock.user.application.register.response;

// After a correct code: send this token with the password in the next step
public record VerificationResponse(String verificationToken, long expiresInSeconds) {
}
