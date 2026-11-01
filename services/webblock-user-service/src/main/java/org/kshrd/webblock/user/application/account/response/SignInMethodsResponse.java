package org.kshrd.webblock.user.application.account.response;

// Settings › Password and Settings › Google: how this user can sign in.
// googleEmail = the linked Google account, or null when Google is not connected.
public record SignInMethodsResponse(boolean hasPassword, String googleEmail) {
}
