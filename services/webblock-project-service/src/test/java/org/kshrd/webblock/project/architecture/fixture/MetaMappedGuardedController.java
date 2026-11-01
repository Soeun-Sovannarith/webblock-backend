package org.kshrd.webblock.project.architecture.fixture;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import org.springframework.boot.test.context.TestComponent;
import org.springframework.core.annotation.AliasFor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

/**
 * Deliberately violates the rule: a class-level {@code @PreAuthorize} over a custom (meta-annotated) mapping whose path
 * has no leading slash, so the rule must find the handler through the meta-annotation to catch it.
 */
@TestComponent // keeps it out of the application's component scan in every test context
@RestController
@PreAuthorize("hasAuthority('SCOPE_fixture:use')")
public class MetaMappedGuardedController {

    @Retention(RetentionPolicy.RUNTIME)
    @RequestMapping(method = RequestMethod.GET)
    @interface InternalGet {
        @AliasFor(annotation = RequestMapping.class, attribute = "path")
        String[] value();
    }

    @InternalGet("internal/v1/meta/{id}")
    public String guarded() {
        return "x";
    }
}
