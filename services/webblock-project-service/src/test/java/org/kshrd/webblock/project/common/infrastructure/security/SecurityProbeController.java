package org.kshrd.webblock.project.common.infrastructure.security;

import java.util.Map;
import org.kshrd.webblock.project.common.presentation.security.CurrentUser;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
class SecurityProbeController {

    private final CurrentUser currentUser;

    SecurityProbeController(CurrentUser currentUser) {
        this.currentUser = currentUser;
    }

    @GetMapping("/internal/test-security")
    Map<String, String> internal() {
        return Map.of("reached", "internal");
    }

    @GetMapping("/api/test-security")
    Map<String, String> user() {
        return Map.of("userId", currentUser.userId().toString());
    }

    /**
     * Never reads {@link CurrentUser}, like the catalog handlers: whatever this answers besides 200 comes from the
     * filter chain, not from the handler.
     */
    @GetMapping("/api/test-security/open")
    Map<String, String> open() {
        return Map.of("reached", "api");
    }
}
