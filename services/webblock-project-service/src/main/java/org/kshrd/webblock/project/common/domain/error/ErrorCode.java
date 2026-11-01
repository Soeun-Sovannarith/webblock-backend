package org.kshrd.webblock.project.common.domain.error;

import java.util.Arrays;
import java.util.Map;
import java.util.Optional;
import java.util.function.Function;
import java.util.stream.Collectors;

/** Every error the service reports: a stable code (the constant's name), a category and a fixed English message. */
public enum ErrorCode {
    // Request-level codes
    VALIDATION_FAILED(ErrorCategory.VALIDATION, "The request is invalid."),
    MALFORMED_REQUEST(ErrorCategory.VALIDATION, "The request body could not be read."),
    UNAUTHENTICATED(ErrorCategory.UNAUTHENTICATED, "Authentication is required."),
    ACCESS_DENIED(ErrorCategory.FORBIDDEN, "You are not allowed to perform this action."),
    ROUTE_NOT_FOUND(ErrorCategory.NOT_FOUND, "No endpoint matches this path."),
    METHOD_NOT_ALLOWED(ErrorCategory.METHOD_NOT_ALLOWED, "This method is not allowed on this path."),
    NOT_ACCEPTABLE(ErrorCategory.NOT_ACCEPTABLE, "The requested response format is not supported."),
    UNSUPPORTED_MEDIA_TYPE(ErrorCategory.UNSUPPORTED_MEDIA_TYPE, "This content type is not supported."),
    PAYLOAD_TOO_LARGE(ErrorCategory.PAYLOAD_TOO_LARGE, "The request is too large."),
    CONCURRENT_UPDATE(ErrorCategory.CONFLICT, "The resource was changed by another request. Reload and try again."),
    DEPENDENCY_UNAVAILABLE(
            ErrorCategory.DEPENDENCY, "A service this request depends on is unavailable. Try again later."),
    INTERNAL_ERROR(ErrorCategory.INTERNAL, "An unexpected error occurred."),

    // Projects, pages and domains
    PROJECT_NOT_FOUND(ErrorCategory.NOT_FOUND, "No project with this ID was found."),
    PROJECT_ID_CONFLICT(ErrorCategory.CONFLICT, "A different project already uses this ID."),
    ADMISSION_RELEASE_PENDING(
            ErrorCategory.CONFLICT,
            "An earlier attempt to create this project is still being undone. Retry with the same ID shortly."),
    PROJECT_READ_ONLY(ErrorCategory.BUSINESS_RULE, "This project is read-only and cannot be changed."),
    STALE_EDIT_REVISION(
            ErrorCategory.CONFLICT, "The project was changed since you loaded it. Reload it and try again."),
    PAGE_NOT_FOUND(ErrorCategory.NOT_FOUND, "No page with this ID was found."),
    // No longer answered: a website has exactly one page, so no page is added. Kept for the recorded sets.
    PAGE_LIMIT_REACHED(ErrorCategory.BUSINESS_RULE, "The project has reached its page limit."),
    PAGE_PATH_TAKEN(ErrorCategory.CONFLICT, "Another page of this project already uses this path."),
    COMPONENT_CATALOG_NOT_FOUND(ErrorCategory.NOT_FOUND, "No component catalog with this version exists."),
    HOSTNAME_TAKEN(ErrorCategory.CONFLICT, "This sub-domain is already taken."),
    PROJECT_NAME_TAKEN(ErrorCategory.CONFLICT, "You already have a website with this name."),
    // For callers of the access check (FR-33): CheckAccess answers them as reasons; Project's REST keeps
    // PROJECT_READ_ONLY. Over gRPC they are FAILED_PRECONDITION (GrpcErrors).
    PROJECT_DELETED(ErrorCategory.CONFLICT, "This project is deleted."),
    PROJECT_SUSPENDED(ErrorCategory.CONFLICT, "This project is suspended."),
    PROJECT_NOT_DELETABLE(ErrorCategory.CONFLICT, "Only a website that was never published can be deleted."),

    // Wizard choices and the catalog
    BUSINESS_TYPE_NOT_FOUND(ErrorCategory.NOT_FOUND, "No active business type with this code was found."),
    BUSINESS_TYPE_NOT_AVAILABLE(ErrorCategory.BUSINESS_RULE, "This business type is not available."),
    TEMPLATE_NOT_FOUND(ErrorCategory.NOT_FOUND, "No template with this code was found."),
    VERSION_NOT_FOUND(ErrorCategory.NOT_FOUND, "No deployed version with this ID was found."),
    TEMPLATE_NOT_AVAILABLE(ErrorCategory.BUSINESS_RULE, "This template is not available for the business type."),
    CATALOG_UNAVAILABLE(ErrorCategory.DEPENDENCY, "The catalog is unavailable. Try again later."),

    // Generation contracts and requests (J2)
    GENERATION_CONTRACT_NOT_FOUND(
            ErrorCategory.NOT_FOUND, "No generation contract with this ID was found for the project."),
    GENERATION_REQUEST_NOT_FOUND(ErrorCategory.NOT_FOUND, "No generation request with this ID was found."),
    PRODUCT_TYPE_NOT_ALLOWED(
            ErrorCategory.BUSINESS_RULE, "This product type is not available for the project's business type."),
    IDEMPOTENCY_KEY_REUSED(ErrorCategory.CONFLICT, "This idempotency key was already used with a different request."),
    GENERATION_IN_PROGRESS(ErrorCategory.CONFLICT, "A generation request for this project is already in progress."),
    REQUEST_NOT_GENERATING(ErrorCategory.CONFLICT, "This generation request is not generating."),

    // Payment admission (Payment contract section 2.1)
    WEBSITE_LIMIT_REACHED(ErrorCategory.BUSINESS_RULE, "Your plan's website limit is reached."),
    TRIAL_ENDED(ErrorCategory.BUSINESS_RULE, "Your trial has ended."),
    NO_ACTIVE_PLAN(ErrorCategory.BUSINESS_RULE, "You have no active plan."),
    CLIENT_SUSPENDED(ErrorCategory.BUSINESS_RULE, "Your account is suspended."),
    WEBSITE_NOT_ADMITTED(ErrorCategory.BUSINESS_RULE, "Your plan does not allow a new website now."),
    PAYMENT_UNAVAILABLE(ErrorCategory.DEPENDENCY, "The payment service is unavailable. Try again later."),

    // Field-level codes: each has a constant in ValidationCodes
    FIELD_REQUIRED(ErrorCategory.VALIDATION, "This field is required."),
    FIELD_LENGTH_INVALID(ErrorCategory.VALIDATION, "This field's length is outside the allowed range."),
    FIELD_OUT_OF_RANGE(ErrorCategory.VALIDATION, "This value is outside the allowed range."),
    VALUE_INVALID(ErrorCategory.VALIDATION, "This value is invalid."),
    UUID_INVALID(ErrorCategory.VALIDATION, "This value must be a UUID."),
    PAGE_PATH_INVALID(
            ErrorCategory.VALIDATION, "A page path must be lowercase letters, digits and hyphens, separated by '/'."),
    IDEMPOTENCY_KEY_INVALID(
            ErrorCategory.VALIDATION,
            "The idempotency key must be 1 to 255 bytes with no control characters or surrounding spaces."),
    PUCK_TREE_INVALID(ErrorCategory.VALIDATION, "The page content does not match the component catalog.");

    private static final Map<String, ErrorCode> BY_CODE =
            Arrays.stream(values()).collect(Collectors.toUnmodifiableMap(ErrorCode::code, Function.identity()));

    private final ErrorCategory category;
    private final String message;

    ErrorCode(ErrorCategory category, String message) {
        this.category = category;
        this.message = message;
    }

    public String code() {
        return name();
    }

    public ErrorCategory category() {
        return category;
    }

    public String message() {
        return message;
    }

    /** True for codes that describe one field rather than the whole request. */
    public boolean isFieldCode() {
        return category == ErrorCategory.VALIDATION && this != VALIDATION_FAILED && this != MALFORMED_REQUEST;
    }

    public static Optional<ErrorCode> fromCode(String code) {
        return code == null ? Optional.empty() : Optional.ofNullable(BY_CODE.get(code));
    }
}
