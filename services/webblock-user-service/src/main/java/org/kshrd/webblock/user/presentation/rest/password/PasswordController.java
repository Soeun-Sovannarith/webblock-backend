package org.kshrd.webblock.user.presentation.rest.password;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.security.SecurityRequirements;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.kshrd.webblock.user.application.password.handler.ForgotPasswordHandler;
import org.kshrd.webblock.user.application.password.handler.ResetPasswordHandler;
import org.kshrd.webblock.user.application.password.handler.VerifyResetOtpHandler;
import org.kshrd.webblock.user.application.password.request.ForgotPasswordRequest;
import org.kshrd.webblock.user.application.password.request.ResetPasswordRequest;
import org.kshrd.webblock.user.application.password.request.VerifyResetOtpRequest;
import org.kshrd.webblock.user.application.register.response.OtpSentResponse;
import org.kshrd.webblock.user.application.register.response.VerificationResponse;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

// Forgot password with a 6-digit email code. Public: the person cannot sign in.
@RestController
@RequestMapping("/api/user/v1/password")
@Tag(name = "Forgot password")
@SecurityRequirements // no lock in Swagger: these endpoints need no token
public class PasswordController {

    private final ForgotPasswordHandler forgot;
    private final VerifyResetOtpHandler verify;
    private final ResetPasswordHandler reset;

    public PasswordController(ForgotPasswordHandler forgot, VerifyResetOtpHandler verify, ResetPasswordHandler reset) {
        this.forgot = forgot;
        this.verify = verify;
        this.reset = reset;
    }

    @PostMapping("/forgot")
    @ResponseStatus(HttpStatus.ACCEPTED)
    @Operation(summary = "Step 1 (and Resend): sends a code if the email has an account. Same answer either way.")
    public OtpSentResponse forgot(@Valid @RequestBody ForgotPasswordRequest request) {
        return forgot.handle(request);
    }

    @PostMapping("/verify-otp")
    @Operation(summary = "Step 2: check the code, returns a verification token")
    public VerificationResponse verify(@Valid @RequestBody VerifyResetOtpRequest request) {
        return verify.handle(request);
    }

    @PostMapping("/reset")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    @Operation(summary = "Step 3: set the new password and sign out everywhere")
    public void reset(@Valid @RequestBody ResetPasswordRequest request) {
        reset.handle(request);
    }
}
