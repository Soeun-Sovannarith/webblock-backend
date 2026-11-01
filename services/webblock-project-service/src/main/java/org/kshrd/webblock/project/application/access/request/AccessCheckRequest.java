package org.kshrd.webblock.project.application.access.request;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.util.UUID;
import org.kshrd.webblock.project.application.access.model.AccessAction;
import org.kshrd.webblock.project.application.access.validation.AccessActionName;
import org.kshrd.webblock.project.common.domain.error.ValidationCodes;

/**
 * The ids are text in the canonical 8-4-4-4-12 form; UUID.fromString alone would also take short forms. A refusal
 * reports UUID_INVALID, as a path UUID does. The action is text too, read exactly as sent: JSON's enum reading would
 * trim it and refuse an unknown name as an unreadable body, where FR-30.6 wants VALUE_INVALID on {@code action}.
 */
public record AccessCheckRequest(
        @NotNull @Pattern(regexp = ID, message = ValidationCodes.UUID_INVALID)
        String userId,

        @NotNull @Pattern(regexp = ID, message = ValidationCodes.UUID_INVALID)
        String projectId,

        @NotNull @AccessActionName String action) {

    private static final String ID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

    public UUID user() {
        return UUID.fromString(userId);
    }

    public UUID project() {
        return UUID.fromString(projectId);
    }

    /** The checked action; call only after validation passed. */
    public AccessAction accessAction() {
        return AccessAction.fromWireName(action).orElseThrow();
    }
}
