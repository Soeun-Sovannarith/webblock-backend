package org.kshrd.webblock.project.presentation.rest.layoutfixture;

import org.kshrd.webblock.project.domain.project.model.ProjectStatus;

/** Fixture for LayoutRuleTest: a presentation class using a domain type, what presentationDoesNotUseDomain forbids. */
public final class DomainUsingController {

    private DomainUsingController() {}

    public static boolean isDraft(ProjectStatus status) {
        return status == ProjectStatus.draft;
    }
}
