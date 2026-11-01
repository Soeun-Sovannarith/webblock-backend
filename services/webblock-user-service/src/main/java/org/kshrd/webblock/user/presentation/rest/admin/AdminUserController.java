package org.kshrd.webblock.user.presentation.rest.admin;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.kshrd.webblock.user.application.account.response.AccountResponse;
import org.kshrd.webblock.user.application.admin.handler.ChangeUserStatusHandler;
import org.kshrd.webblock.user.application.admin.handler.ListUsersHandler;
import org.kshrd.webblock.user.application.admin.request.ChangeUserStatusRequest;
import org.kshrd.webblock.user.application.admin.response.PageResponse;
import org.kshrd.webblock.user.infrastructure.security.CurrentUser;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

// Admin Portal › Users. SecurityConfig: /api/user/v1/admin/** needs the ADMIN role (else 403).
@RestController
@RequestMapping("/api/user/v1/admin/users")
@Tag(name = "Admin: users")
public class AdminUserController {

    private final ListUsersHandler listUsers;
    private final ChangeUserStatusHandler changeStatus;

    public AdminUserController(ListUsersHandler listUsers, ChangeUserStatusHandler changeStatus) {
        this.listUsers = listUsers;
        this.changeStatus = changeStatus;
    }

    @GetMapping
    @Operation(summary = "List accounts (search by email or name, filter by status, page from 0, size up to 100)")
    public PageResponse<AccountResponse> list(@RequestParam(required = false) String search,
                                              @RequestParam(required = false) String status,
                                              @RequestParam(defaultValue = "0") int page,
                                              @RequestParam(defaultValue = "20") int size,
                                              @RequestParam(defaultValue = "createdAt,desc") String sort) {
        return listUsers.handle(search, status, page, size, sort);
    }

    @PutMapping("/{userId}/status")
    @Operation(summary = "Suspend (reason required) or activate an account")
    public AccountResponse changeStatus(@AuthenticationPrincipal Jwt jwt,
                                        @PathVariable UUID userId,
                                        @Valid @RequestBody ChangeUserStatusRequest request) {
        return changeStatus.handle(CurrentUser.from(jwt), userId, request);
    }
}
