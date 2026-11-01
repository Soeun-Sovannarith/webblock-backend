package org.kshrd.webblock.project.application.wizard.request;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.kshrd.webblock.project.application.wizard.model.CreateProjectCommand;

/** The sub-domain's rule is checked by ProjectService.requireValidSubdomain, its one place (FR-13). */
public record CreateProjectRequest(
        @NotBlank @Size(max = 255) String name,
        @NotNull String subdomain,
        @NotBlank @Size(max = 50) String businessTypeCode,
        @NotBlank @Size(max = 100) String templateCode,
        @NotNull @Valid BusinessProfileRequest businessProfile) {

    public CreateProjectCommand toCommand() {
        return new CreateProjectCommand(
                name, subdomain, businessTypeCode, templateCode, businessProfile.toProfile(name));
    }
}
