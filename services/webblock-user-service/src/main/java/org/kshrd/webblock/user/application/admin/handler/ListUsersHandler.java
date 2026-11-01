package org.kshrd.webblock.user.application.admin.handler;

import org.kshrd.webblock.user.application.account.mapper.AccountMapper;
import org.kshrd.webblock.user.application.account.response.AccountResponse;
import org.kshrd.webblock.user.application.admin.response.PageResponse;
import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.account.model.AccountStatus;
import org.kshrd.webblock.user.domain.account.model.AccountUser;
import org.kshrd.webblock.user.infrastructure.persistence.account.AccountUserRepository;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.Locale;
import java.util.Map;

// GET /admin/users: one page of accounts, newest first by default.
// search = part of the email or full name; status = ACTIVE / SUSPENDED; sort = "field,asc|desc".
@Component
public class ListUsersHandler {

    private static final int MAX_SIZE = 100;
    // Sort keys the client may use → entity fields (whitelist: never put a raw string into ORDER BY)
    private static final Map<String, String> SORT_FIELDS = Map.of(
            "createdAt", "createdAt",
            "lastLoginAt", "lastLoginAt",
            "email", "email",
            "fullName", "displayName");

    private final AccountUserRepository repository;

    public ListUsersHandler(AccountUserRepository repository) {
        this.repository = repository;
    }

    @Transactional(readOnly = true)
    public PageResponse<AccountResponse> handle(String search, String status, int page, int size, String sort) {
        Specification<AccountUser> filter = (root, query, cb) -> cb.conjunction();

        if (status != null && !status.isBlank()) {
            AccountStatus wanted = parseStatus(status);
            filter = filter.and((root, query, cb) -> cb.equal(root.get("status"), wanted));
        }
        if (search != null && !search.isBlank()) {
            String pattern = "%" + escapeLike(search.trim().toLowerCase(Locale.ROOT)) + "%";
            filter = filter.and((root, query, cb) -> cb.or(
                    cb.like(cb.lower(root.<String>get("email")), pattern, '\\'),
                    cb.like(cb.lower(root.<String>get("displayName")), pattern, '\\')));
        }

        PageRequest pageRequest = PageRequest.of(Math.max(0, page), Math.min(MAX_SIZE, Math.max(1, size)), toSort(sort));
        return PageResponse.of(repository.findAll(filter, pageRequest), AccountMapper::toResponse);
    }

    private static AccountStatus parseStatus(String value) {
        try {
            return AccountStatus.valueOf(value.trim().toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            throw new ApiException(ErrorCode.VALIDATION_FAILED);
        }
    }

    // "createdAt,desc" → Sort; anything unknown → newest first
    private static Sort toSort(String sort) {
        Sort fallback = Sort.by(Sort.Direction.DESC, "createdAt");
        if (sort == null || sort.isBlank()) {
            return fallback;
        }
        String[] parts = sort.split(",");
        String field = SORT_FIELDS.get(parts[0].trim());
        if (field == null) {
            return fallback;
        }
        boolean asc = parts.length > 1 && parts[1].trim().equalsIgnoreCase("asc");
        return Sort.by(asc ? Sort.Direction.ASC : Sort.Direction.DESC, field).and(Sort.by("userId"));
    }

    // % and _ typed by the admin are plain characters, not wildcards
    private static String escapeLike(String text) {
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }
}
