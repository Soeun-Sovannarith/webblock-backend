package org.kshrd.webblock.project.architecture.fixture;

import org.springframework.boot.test.context.TestComponent;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Deliberately violates the rule: a planted {@code @PreAuthorize} on an /api handler. Method security is off, so it
 * would read as protected while every user reaches it.
 */
@TestComponent // keeps it out of the application's component scan in every test context
@RestController
@RequestMapping("/api/v1/guarded")
public class GuardedApiController {

    @GetMapping
    @PreAuthorize("hasAuthority('SCOPE_admin')")
    public String guarded() {
        return "x";
    }
}
