package org.kshrd.webblock.user.presentation.rest.account;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.kshrd.webblock.user.application.account.handler.ChangePasswordHandler;
import org.kshrd.webblock.user.application.account.handler.GetMyAccountHandler;
import org.kshrd.webblock.user.application.account.handler.GetSignInMethodsHandler;
import org.kshrd.webblock.user.application.account.handler.UpdateMyProfileHandler;
import org.kshrd.webblock.user.application.account.request.ChangePasswordRequest;
import org.kshrd.webblock.user.application.account.request.UpdateProfileRequest;
import org.kshrd.webblock.user.application.account.response.AccountResponse;
import org.kshrd.webblock.user.application.account.response.SignInMethodsResponse;
import org.kshrd.webblock.user.infrastructure.security.CurrentUser;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

// The signed-in user's own account. Who "me" is comes ONLY from the verified token.
@RestController
@RequestMapping("/api/user/v1/me")
@Tag(name = "My account")
public class AccountController {

    private final GetMyAccountHandler getMyAccount;
    private final UpdateMyProfileHandler updateMyProfile;
    private final GetSignInMethodsHandler getSignInMethods;
    private final ChangePasswordHandler changePassword;

    public AccountController(GetMyAccountHandler getMyAccount, UpdateMyProfileHandler updateMyProfile,
                             GetSignInMethodsHandler getSignInMethods, ChangePasswordHandler changePassword) {
        this.getMyAccount = getMyAccount;
        this.updateMyProfile = updateMyProfile;
        this.getSignInMethods = getSignInMethods;
        this.changePassword = changePassword;
    }

    @GetMapping
    @Operation(summary = "Get my account (creates it on the first call)")
    public AccountResponse getMe(@AuthenticationPrincipal Jwt jwt) {
        return getMyAccount.handle(CurrentUser.from(jwt));
    }

    @PutMapping
    @Operation(summary = "Change my full name (also in Keycloak)")
    public AccountResponse updateMe(@AuthenticationPrincipal Jwt jwt,
                                    @Valid @RequestBody UpdateProfileRequest request) {
        return updateMyProfile.handle(CurrentUser.from(jwt), request);
    }

    @GetMapping("/sign-in-methods")
    @Operation(summary = "Do I have a password? Is Google connected?")
    public SignInMethodsResponse signInMethods(@AuthenticationPrincipal Jwt jwt) {
        return getSignInMethods.handle(CurrentUser.from(jwt));
    }

    @PutMapping("/password")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    @Operation(summary = "Change my password (or set a first one for a Google-only account); signs out my other devices")
    public void changePassword(@AuthenticationPrincipal Jwt jwt,
                               @Valid @RequestBody ChangePasswordRequest request) {
        changePassword.handle(CurrentUser.from(jwt), request);
    }
}
