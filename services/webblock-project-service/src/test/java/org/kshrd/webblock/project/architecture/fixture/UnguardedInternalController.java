package org.kshrd.webblock.project.architecture.fixture;

import org.springframework.boot.test.context.TestComponent;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** The permanent "plain internal handler passes" fixture: an /internal handler without method security. */
@TestComponent // keeps it out of the application's component scan in every test context
@RestController
@RequestMapping("/internal/v1/fixture")
public class UnguardedInternalController {

    @GetMapping
    public String open() {
        return "x";
    }
}
