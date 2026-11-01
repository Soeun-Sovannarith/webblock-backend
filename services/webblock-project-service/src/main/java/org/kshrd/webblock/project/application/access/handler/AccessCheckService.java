package org.kshrd.webblock.project.application.access.handler;

import java.util.Optional;
import java.util.UUID;
import org.kshrd.webblock.project.application.access.model.AccessAction;
import org.kshrd.webblock.project.application.access.model.AccessDecision;
import org.kshrd.webblock.project.application.access.model.AccessDenialReason;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectOwnership;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * May this user act on this project? Owner only (communication note section 3). A suspended project keeps read-only
 * access for its owner (decision 0007); a deleted one is unknown (NOT_FOUND). Operators are authorised by Provisioning
 * itself.
 */
@Service
public class AccessCheckService {

    private static final Logger log = LoggerFactory.getLogger(AccessCheckService.class);

    private final ProjectService projects;

    public AccessCheckService(ProjectService projects) {
        this.projects = projects;
    }

    public AccessDecision check(UUID userId, UUID projectId, AccessAction action) {
        AccessDecision decision = decide(userId, projects.ownership(projectId), action);
        log.debug(
                "Access check project={} action={} allowed={} reason={}",
                projectId,
                action,
                decision.allowed(),
                decision.reason());
        return decision;
    }

    private static AccessDecision decide(UUID userId, Optional<ProjectOwnership> found, AccessAction action) {
        if (found.isEmpty()) {
            return AccessDecision.denied(AccessDenialReason.NOT_FOUND);
        }
        ProjectOwnership project = found.get();
        if (!project.ownerUserId().equals(userId)) {
            return AccessDecision.denied(AccessDenialReason.NOT_MANAGER);
        }
        if (project.suspended() && action.isWrite()) {
            return AccessDecision.denied(AccessDenialReason.PROJECT_SUSPENDED);
        }
        return AccessDecision.ALLOWED;
    }
}
