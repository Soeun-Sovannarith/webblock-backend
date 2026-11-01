package org.kshrd.webblock.project.architecture.fixture;

import org.springframework.boot.test.context.TestComponent;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Deliberately violates the rule: a planted {@code @PreAuthorize} on a handler (no leading slash, a placeholder). */
@TestComponent // keeps it out of the application's component scan in every test context
@RestController
@RequestMapping("internal/v1/guarded")
public class GuardedInternalController {

    @PostMapping("{id}")
    @PreAuthorize("hasAuthority('SCOPE_fixture:use')")
    public String guarded() {
        return "x";
    }
}
