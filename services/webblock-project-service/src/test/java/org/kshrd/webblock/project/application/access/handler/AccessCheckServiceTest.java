package org.kshrd.webblock.project.application.access.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.groups.Tuple.tuple;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.ValidatorFactory;
import java.util.Arrays;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.kshrd.webblock.project.application.access.model.AccessAction;
import org.kshrd.webblock.project.application.access.model.AccessDecision;
import org.kshrd.webblock.project.application.access.model.AccessDenialReason;
import org.kshrd.webblock.project.application.access.request.AccessCheckRequest;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.ProjectOwnership;
import org.kshrd.webblock.project.common.domain.error.ValidationCodes;
import org.kshrd.webblock.project.domain.project.model.ProjectStatus;

class AccessCheckServiceTest {

    static final UUID OWNER = UUID.randomUUID();
    static final UUID PROJECT = UUID.randomUUID();

    private final ProjectService projects = mock(ProjectService.class);
    private final AccessCheckService service = new AccessCheckService(projects);

    private void project(ProjectStatus status) {
        when(projects.ownership(PROJECT))
                .thenReturn(Optional.of(new ProjectOwnership(OWNER, status == ProjectStatus.suspended)));
    }

    /** The check reads only the owner and the read-only state: never the project with its hostname. */
    @Test
    void theCheckReadsOnlyTheOwnership() {
        project(ProjectStatus.active);
        service.check(OWNER, PROJECT, AccessAction.GET_RELEASE);
        verify(projects).ownership(PROJECT);
        verifyNoMoreInteractions(projects);
    }

    @Test
    void anUnknownProjectIsNotFoundNeverAnError() {
        when(projects.ownership(PROJECT)).thenReturn(Optional.empty());
        assertThat(service.check(OWNER, PROJECT, AccessAction.GET_RELEASE))
                .isEqualTo(AccessDecision.denied(AccessDenialReason.NOT_FOUND));
    }

    @ParameterizedTest
    @EnumSource(AccessAction.class)
    void onlyTheOwnerIsAllowed(AccessAction action) {
        project(ProjectStatus.active);
        assertThat(service.check(OWNER, PROJECT, action)).isEqualTo(AccessDecision.ALLOWED);
        assertThat(service.check(UUID.randomUUID(), PROJECT, action))
                .isEqualTo(AccessDecision.denied(AccessDenialReason.NOT_MANAGER));
    }

    @ParameterizedTest
    @EnumSource(AccessAction.class)
    @DisplayName(
            "FR-38.5, FR-30.4: a deleted project is unknown to the check: NOT_FOUND for every action, even the owner")
    void fr38_5_aDeletedProjectIsNotFoundForEveryAction(AccessAction action) {
        when(projects.ownership(PROJECT)).thenReturn(Optional.empty());
        assertThat(service.check(OWNER, PROJECT, action))
                .isEqualTo(AccessDecision.denied(AccessDenialReason.NOT_FOUND));
    }

    @ParameterizedTest
    @EnumSource(AccessAction.class)
    @DisplayName("FR-30.5, FR-32: a suspended project refuses every write action with PROJECT_SUSPENDED, allows reads")
    void fr30_5_aSuspendedProjectAllowsReadsAndRefusesChanges(AccessAction action) {
        project(ProjectStatus.suspended);
        assertThat(service.check(OWNER, PROJECT, action))
                .isEqualTo(
                        action.isWrite()
                                ? AccessDecision.denied(AccessDenialReason.PROJECT_SUSPENDED)
                                : AccessDecision.ALLOWED);
    }

    @Test
    @DisplayName("FR-32: exactly the five changes are write actions, manage_site among them; 11 actions in all")
    void fr32_exactlyTheFiveChangesAreWriteActions() {
        assertThat(Arrays.stream(AccessAction.values())
                        .filter(AccessAction::isWrite)
                        .map(AccessAction::wireName))
                .containsExactlyInAnyOrder(
                        "deploy_release", "promote_release", "rollback_release", "build_release", "manage_site");
        assertThat(AccessAction.values()).hasSize(11);
    }

    @Test
    @DisplayName("FR-32.1: manage_site is allowed for the owner of a live project")
    void fr32_1_manageSiteIsAllowedForTheOwner() {
        project(ProjectStatus.active);
        assertThat(service.check(OWNER, PROJECT, manageSite())).isEqualTo(AccessDecision.ALLOWED);
        assertThat(service.check(UUID.randomUUID(), PROJECT, manageSite()))
                .isEqualTo(AccessDecision.denied(AccessDenialReason.NOT_MANAGER));
    }

    @Test
    @DisplayName("FR-32.2: manage_site on a suspended project is PROJECT_SUSPENDED")
    void fr32_2_manageSiteOnASuspendedProjectIsProjectSuspended() {
        project(ProjectStatus.suspended);
        assertThat(service.check(OWNER, PROJECT, manageSite()))
                .isEqualTo(AccessDecision.denied(AccessDenialReason.PROJECT_SUSPENDED));
    }

    /** The check takes an AccessAction, so drop_db cannot reach it: the request refuses it first. */
    @Test
    @DisplayName("FR-32.3: drop_db is bad input: no AccessAction, VALUE_INVALID on action in the request")
    void fr32_3_dropDbIsBadInput() {
        assertThat(AccessAction.fromWireName("drop_db")).isEmpty();
        try (ValidatorFactory validation = Validation.buildDefaultValidatorFactory()) {
            assertThat(validation
                            .getValidator()
                            .validate(new AccessCheckRequest(OWNER.toString(), PROJECT.toString(), "drop_db")))
                    .extracting(v -> v.getPropertyPath().toString(), ConstraintViolation::getMessage)
                    .containsExactly(tuple("action", ValidationCodes.VALUE_INVALID));
        }
    }

    /** Read through the wire name callers send. */
    private static AccessAction manageSite() {
        return AccessAction.fromWireName("manage_site").orElseThrow();
    }

    @ParameterizedTest
    @EnumSource(AccessAction.class)
    void aNonOwnerOfASuspendedProjectLearnsNothingAboutItsState(AccessAction action) {
        project(ProjectStatus.suspended);
        assertThat(service.check(UUID.randomUUID(), PROJECT, action))
                .isEqualTo(AccessDecision.denied(AccessDenialReason.NOT_MANAGER));
    }

    @ParameterizedTest
    @EnumSource(AccessAction.class)
    void aDraftProjectIsAllowedForItsOwnerOnEveryAction(AccessAction action) {
        project(ProjectStatus.draft);
        assertThat(service.check(OWNER, PROJECT, action)).isEqualTo(AccessDecision.ALLOWED);
    }
}
