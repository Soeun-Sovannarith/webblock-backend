package org.kshrd.webblock.user.common.domain;

// Every error this service can return. "code" is what the frontend checks; never rename one.
public enum ErrorCode {
    VALIDATION_FAILED(400, "Some fields are not valid."),
    UNAUTHENTICATED(401, "Please sign in."),
    ACCESS_DENIED(403, "You are not allowed to do this."),
    ACCOUNT_SUSPENDED(403, "This account is suspended."),
    ACCOUNT_EMAIL_MISSING(422, "Your sign-in has no email address."),
    ACCOUNT_ALREADY_EXISTS(409, "You already have an account. Sign in or reset your password."),
    OTP_INVALID(400, "The code is not correct."),
    OTP_EXPIRED(400, "The code has expired. Please ask for a new one."),
    OTP_TOO_MANY_ATTEMPTS(429, "Too many wrong codes. Please ask for a new one."),
    OTP_RESEND_TOO_SOON(429, "Please wait a minute before asking for a new code."),
    VERIFICATION_TOKEN_INVALID(400, "Your verification has expired. Please start again."),
    PASSWORD_MISMATCH(400, "The passwords do not match."),
    PASSWORD_TOO_WEAK(422, "The password does not meet the rules."),
    CURRENT_PASSWORD_REQUIRED(400, "Enter your current password."),
    CURRENT_PASSWORD_WRONG(400, "Your current password is not correct."),
    USER_NOT_FOUND(404, "No user with this ID was found."),
    USER_STATUS_UNCHANGED(409, "The account already has this status."),
    USER_STATUS_OWN_ACCOUNT(422, "You cannot change the status of your own account."),
    USER_STATUS_REASON_REQUIRED(400, "Choose a reason for the suspension."),
    INTERNAL_ERROR(500, "Something went wrong. Please try again."),
    DEPENDENCY_UNAVAILABLE(503, "A service we need is not available. Please try again later.");

    private final int status;
    private final String title;

    ErrorCode(int status, String title) {
        this.status = status;
        this.title = title;
    }

    public int status() { return status; }
    public String title() { return title; }
}
