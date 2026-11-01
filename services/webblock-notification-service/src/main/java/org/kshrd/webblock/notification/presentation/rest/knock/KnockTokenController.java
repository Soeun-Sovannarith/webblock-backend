package org.kshrd.webblock.notification.presentation.rest.knock;

import io.swagger.v3.oas.annotations.Operation;
import org.kshrd.webblock.notification.application.notification.handler.IssueKnockTokenHandler;
import org.kshrd.webblock.notification.application.notification.response.KnockTokenResponse;
import org.kshrd.webblock.notification.infrastructure.security.CurrentUser;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/api/notification/v1/users")
public class KnockTokenController {

    private final IssueKnockTokenHandler handler;
    private final CurrentUser currentUser;

    public KnockTokenController(IssueKnockTokenHandler handler, CurrentUser currentUser) {
        this.handler = handler;
        this.currentUser = currentUser;
    }

    @Operation(summary = "Token for the Knock notification bell of the signed-in user")
    @GetMapping("/token")
    public KnockTokenResponse token() {
        KnockTokenResponse response = handler.issue(currentUser.userId());
        if (response == null) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Notification token is not available");
        }
        return response;
    }
}
