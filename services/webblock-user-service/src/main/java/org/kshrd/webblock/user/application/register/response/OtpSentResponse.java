package org.kshrd.webblock.user.application.register.response;

// "We sent a code": the page can show a countdown for expiry and for the Resend button
public record OtpSentResponse(long expiresInSeconds, long resendAfterSeconds) {
}
