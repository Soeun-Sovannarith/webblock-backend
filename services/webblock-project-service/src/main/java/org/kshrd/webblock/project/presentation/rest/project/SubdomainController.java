package org.kshrd.webblock.project.presentation.rest.project;

import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.response.SubdomainAvailabilityView;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

/** The create wizard asks, while the user types, whether a sub-domain can be used (FR-13); the same rule as create. */
@RestController
class SubdomainController {

    private final ProjectService projects;

    SubdomainController(ProjectService projects) {
        this.projects = projects;
    }

    @GetMapping("/api/v1/subdomains/{subdomain}")
    SubdomainAvailabilityView check(@PathVariable String subdomain) {
        return SubdomainAvailabilityView.of(subdomain, projects.checkSubdomain(subdomain));
    }
}
