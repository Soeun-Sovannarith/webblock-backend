package org.kshrd.webblock.user.presentation.rest.register;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.security.SecurityRequirements;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.kshrd.webblock.user.application.register.handler.CompleteRegisterHandler;
import org.kshrd.webblock.user.application.register.handler.ResendRegisterOtpHandler;
import org.kshrd.webblock.user.application.register.handler.StartRegisterHandler;
import org.kshrd.webblock.user.application.register.handler.VerifyRegisterOtpHandler;
import org.kshrd.webblock.user.application.register.request.CompleteRegisterRequest;
import org.kshrd.webblock.user.application.register.request.ResendOtpRequest;
import org.kshrd.webblock.user.application.register.request.StartRegisterRequest;
import org.kshrd.webblock.user.application.register.request.VerifyOtpRequest;
import org.kshrd.webblock.user.application.register.response.OtpSentResponse;
import org.kshrd.webblock.user.application.register.response.RegisteredResponse;
import org.kshrd.webblock.user.application.register.response.VerificationResponse;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

// Sign up with email + 6-digit code. Public: no token (the person has no account yet).
@RestController
@RequestMapping("/api/user/v1/register")
@Tag(name = "Register")
@SecurityRequirements // no lock in Swagger: these endpoints need no token
public class RegisterController {

    private final StartRegisterHandler start;
    private final ResendRegisterOtpHandler resend;
    private final VerifyRegisterOtpHandler verify;
    private final CompleteRegisterHandler complete;

    public RegisterController(StartRegisterHandler start, ResendRegisterOtpHandler resend,
                              VerifyRegisterOtpHandler verify, CompleteRegisterHandler complete) {
        this.start = start;
        this.resend = resend;
        this.verify = verify;
        this.complete = complete;
    }

    @PostMapping("/start")
    @ResponseStatus(HttpStatus.ACCEPTED)
    @Operation(summary = "Step 1: full name + email, sends a 6-digit code")
    public OtpSentResponse start(@Valid @RequestBody StartRegisterRequest request) {
        return start.handle(request);
    }

    @PostMapping("/resend-otp")
    @ResponseStatus(HttpStatus.ACCEPTED)
    @Operation(summary = "Send a new code (after 60 seconds)")
    public OtpSentResponse resend(@Valid @RequestBody ResendOtpRequest request) {
        return resend.handle(request);
    }

    @PostMapping("/verify-otp")
    @Operation(summary = "Step 2: check the code, returns a verification token")
    public VerificationResponse verify(@Valid @RequestBody VerifyOtpRequest request) {
        return verify.handle(request);
    }

    @PostMapping("/complete")
    @ResponseStatus(HttpStatus.CREATED)
    @Operation(summary = "Step 3: create the password and the account")
    public RegisteredResponse complete(@Valid @RequestBody CompleteRegisterRequest request) {
        return complete.handle(request);
    }
}
