package org.kshrd.webblock.user.infrastructure.client.keycloak;

import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.infrastructure.config.KeycloakAdminProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.client.UnknownContentTypeException;

import java.net.URI;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Supplier;

// Talks to Keycloak for user-service:
// - the Admin REST API as the service account "webblock-auth-admin" (realm-management: manage-users, view-users)
// - the token endpoint with "webblock-password-check", only to check a user's current password
// Plain Spring RestClient: easy to read, no extra library.
@Component
public class KeycloakAdminClient {

    private static final Logger log = LoggerFactory.getLogger(KeycloakAdminClient.class);
    private static final ParameterizedTypeReference<List<Map<String, Object>>> LIST_OF_MAPS =
            new ParameterizedTypeReference<>() { };
    private static final ParameterizedTypeReference<Map<String, Object>> MAP =
            new ParameterizedTypeReference<>() { };

    private final KeycloakAdminProperties props;
    private final Clock clock;
    private final RestClient http;

    // Service-account token, reused until shortly before it expires
    private String cachedToken;
    private Instant cachedTokenExpiresAt = Instant.EPOCH;

    public KeycloakAdminClient(KeycloakAdminProperties props, Clock clock) {
        this.props = props;
        this.clock = clock;
        SimpleClientHttpRequestFactory timeouts = new SimpleClientHttpRequestFactory();
        timeouts.setConnectTimeout(Duration.ofSeconds(3));
        timeouts.setReadTimeout(Duration.ofSeconds(10));
        this.http = RestClient.builder().requestFactory(timeouts).build();
    }

    // ---- users ----

    public Optional<KeycloakUser> findUserByEmail(String email) {
        List<Map<String, Object>> users = call(() -> http.get()
                .uri(adminBase() + "/users?exact=true&email={email}", email)
                .headers(this::adminHeaders)
                .retrieve()
                .body(LIST_OF_MAPS));
        if (users == null) {
            return Optional.empty();
        }
        return users.stream()
                .filter(u -> email.equalsIgnoreCase(String.valueOf(u.get("email"))))
                .findFirst()
                .map(KeycloakAdminClient::toUser);
    }

    // The user by Keycloak id; empty when Keycloak does not know this id
    public Optional<KeycloakUser> findUserById(String keycloakUserId) {
        return Optional.ofNullable(getUserRepresentation(keycloakUserId)).map(KeycloakAdminClient::toUser);
    }

    // Creates an enabled user with a verified email and a password. Default role USER comes from the realm.
    // Returns the new Keycloak user id.
    public String createUser(String email, String fullName, String password) {
        Map<String, Object> body = Map.of(
                "username", email,
                "email", email,
                "firstName", fullName,
                "enabled", true,
                "emailVerified", true,
                "credentials", List.of(Map.of("type", "password", "value", password, "temporary", false)));
        ResponseEntity<Void> response = call(() -> http.post()
                .uri(adminBase() + "/users")
                .headers(this::adminHeaders)
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .toBodilessEntity());
        URI location = response.getHeaders().getLocation();
        if (location == null) {
            throw new ApiException(ErrorCode.INTERNAL_ERROR);
        }
        String path = location.getPath();
        return path.substring(path.lastIndexOf('/') + 1);
    }

    // Full name lives in firstName (lastName stays empty), so the token's "name" claim is the full name
    public void updateFullName(String keycloakUserId, String fullName) {
        updateUser(keycloakUserId, Map.of("firstName", fullName, "lastName", ""));
    }

    // enabled = false: the user cannot sign in or refresh a token any more (admin suspend)
    public void setEnabled(String keycloakUserId, boolean enabled) {
        updateUser(keycloakUserId, Map.of("enabled", enabled));
    }

    // ---- password and sign-in methods ----

    // True when the user already has a password (false = only signs in with Google)
    public boolean hasPassword(String keycloakUserId) {
        List<Map<String, Object>> credentials = call(() -> http.get()
                .uri(adminBase() + "/users/{id}/credentials", keycloakUserId)
                .headers(this::adminHeaders)
                .retrieve()
                .body(LIST_OF_MAPS));
        return credentials != null && credentials.stream().anyMatch(c -> "password".equals(c.get("type")));
    }

    public void setPassword(String keycloakUserId, String password) {
        call(() -> http.put()
                .uri(adminBase() + "/users/{id}/reset-password", keycloakUserId)
                .headers(this::adminHeaders)
                .contentType(MediaType.APPLICATION_JSON)
                .body(Map.of("type", "password", "value", password, "temporary", false))
                .retrieve()
                .toBodilessEntity());
    }

    // The Google account linked to this user (its email), or empty when Google is not connected
    public Optional<String> googleEmail(String keycloakUserId) {
        List<Map<String, Object>> links = call(() -> http.get()
                .uri(adminBase() + "/users/{id}/federated-identity", keycloakUserId)
                .headers(this::adminHeaders)
                .retrieve()
                .body(LIST_OF_MAPS));
        if (links == null) {
            return Optional.empty();
        }
        return links.stream()
                .filter(l -> "google".equals(l.get("identityProvider")))
                .map(l -> String.valueOf(l.get("userName")))
                .findFirst();
    }

    // Checks the password like a sign-in would (Keycloak counts wrong tries for brute-force protection).
    // true = right password, false = wrong password (or account locked/disabled).
    public boolean checkPassword(String email, String password) {
        if (isBlank(props.passwordCheckClientId()) || isBlank(props.passwordCheckClientSecret())) {
            log.error("KEYCLOAK_PASSWORD_CHECK_CLIENT_ID / _SECRET is not set");
            throw new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        }
        MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
        form.add("grant_type", "password");
        form.add("client_id", props.passwordCheckClientId());
        form.add("client_secret", props.passwordCheckClientSecret());
        form.add("username", email);
        form.add("password", password);
        Map<String, Object> token;
        try {
            token = http.post()
                    .uri(tokenUrl())
                    .contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form)
                    .retrieve()
                    .body(MAP);
        } catch (RestClientResponseException e) {
            // Keycloak answers "invalid_grant" for a wrong password, a locked or a disabled account
            if (e.getResponseBodyAsString().contains("invalid_grant")) {
                return false;
            }
            // Anything else is a setup problem (e.g. Direct access grants OFF on the client)
            log.error("Password check refused by Keycloak, status={}", e.getStatusCode().value());
            throw new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        } catch (RestClientException e) {
            log.error("Password check failed: {}", e.getClass().getSimpleName());
            throw new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        }
        endPasswordCheckSession(token);
        return true;
    }

    // ---- sessions ----

    // Signs the user out everywhere (all browsers and devices). Used after a password reset and on suspend.
    public void logoutAllSessions(String keycloakUserId) {
        call(() -> http.post()
                .uri(adminBase() + "/users/{id}/logout", keycloakUserId)
                .headers(this::adminHeaders)
                .retrieve()
                .toBodilessEntity());
    }

    // Signs the user out on every OTHER browser/device, keeps the session that made the request
    // (keepSessionId = the "sid" claim of the caller's token). No sid → signs out everywhere.
    public void logoutOtherSessions(String keycloakUserId, String keepSessionId) {
        if (isBlank(keepSessionId)) {
            logoutAllSessions(keycloakUserId);
            return;
        }
        List<Map<String, Object>> sessions = call(() -> http.get()
                .uri(adminBase() + "/users/{id}/sessions", keycloakUserId)
                .headers(this::adminHeaders)
                .retrieve()
                .body(LIST_OF_MAPS));
        if (sessions == null) {
            return;
        }
        for (Map<String, Object> session : sessions) {
            String sessionId = String.valueOf(session.get("id"));
            if (!sessionId.equals(keepSessionId)) {
                call(() -> http.delete()
                        .uri(adminBase() + "/sessions/{sessionId}", sessionId)
                        .headers(this::adminHeaders)
                        .retrieve()
                        .toBodilessEntity());
            }
        }
    }

    // ---- plumbing ----

    private String adminBase() {
        return props.adminUrl() + "/admin/realms/" + props.realm();
    }

    private String tokenUrl() {
        return props.serverUrl() + "/realms/" + props.realm() + "/protocol/openid-connect/token";
    }

    // GET /users/{id}: the full user, or null when Keycloak does not know this id
    private Map<String, Object> getUserRepresentation(String keycloakUserId) {
        try {
            return http.get()
                    .uri(adminBase() + "/users/{id}", keycloakUserId)
                    .headers(this::adminHeaders)
                    .retrieve()
                    .body(MAP);
        } catch (RestClientResponseException e) {
            if (e.getStatusCode().value() == HttpStatus.NOT_FOUND.value()) {
                return null;
            }
            throw translate(e);
        } catch (RestClientException e) {
            throw translate(e);
        }
    }

    // Keycloak's PUT /users/{id} wants the whole user: read it, change some fields, send it back
    private void updateUser(String keycloakUserId, Map<String, Object> changes) {
        Map<String, Object> user = getUserRepresentation(keycloakUserId);
        if (user == null) {
            throw new ApiException(ErrorCode.USER_NOT_FOUND);
        }
        Map<String, Object> updated = new HashMap<>(user);
        updated.putAll(changes);
        call(() -> http.put()
                .uri(adminBase() + "/users/{id}", keycloakUserId)
                .headers(this::adminHeaders)
                .contentType(MediaType.APPLICATION_JSON)
                .body(updated)
                .retrieve()
                .toBodilessEntity());
    }

    // The password check opened a short Keycloak session; close it right away
    private void endPasswordCheckSession(Map<String, Object> token) {
        Object refreshToken = token == null ? null : token.get("refresh_token");
        if (!(refreshToken instanceof String value)) {
            return;
        }
        MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
        form.add("client_id", props.passwordCheckClientId());
        form.add("client_secret", props.passwordCheckClientSecret());
        form.add("refresh_token", value);
        try {
            http.post()
                    .uri(props.serverUrl() + "/realms/" + props.realm() + "/protocol/openid-connect/logout")
                    .contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form)
                    .retrieve()
                    .toBodilessEntity();
        } catch (RestClientException e) {
            log.warn("Could not close the password-check session: {}", e.getClass().getSimpleName());
        }
    }

    // Bearer token of the service account (+ Cloudflare Access service token when configured)
    private void adminHeaders(HttpHeaders headers) {
        headers.setBearerAuth(serviceToken());
        if (props.usesCloudflareAccess()) {
            headers.set("CF-Access-Client-Id", props.cfAccessClientId());
            headers.set("CF-Access-Client-Secret", props.cfAccessClientSecret());
        }
    }

    private synchronized String serviceToken() {
        Instant now = clock.instant();
        if (cachedToken != null && now.isBefore(cachedTokenExpiresAt)) {
            return cachedToken;
        }
        if (isBlank(props.clientSecret())) {
            log.error("KEYCLOAK_ADMIN_CLIENT_SECRET is not set");
            throw new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        }
        MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
        form.add("grant_type", "client_credentials");
        form.add("client_id", props.clientId());
        form.add("client_secret", props.clientSecret());
        Map<String, Object> token;
        try {
            token = http.post()
                    .uri(tokenUrl())
                    .contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form)
                    .retrieve()
                    .body(MAP);
        } catch (RestClientResponseException e) {
            log.error("Keycloak service-account login failed, status={}", e.getStatusCode().value());
            throw new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        } catch (ResourceAccessException e) {
            log.error("Keycloak not reachable for service-account login");
            throw new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        } catch (UnknownContentTypeException e) {
            log.error("Keycloak token endpoint answered {} instead of JSON (a login or proxy page?)", e.getContentType());
            throw new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        }
        if (token == null || token.get("access_token") == null) {
            throw new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        }
        long expiresIn = token.get("expires_in") instanceof Number n ? n.longValue() : 60;
        cachedToken = (String) token.get("access_token");
        cachedTokenExpiresAt = now.plusSeconds(Math.max(5, expiresIn - 30));
        return cachedToken;
    }

    // Turns Keycloak HTTP errors into our error codes (never shows Keycloak's raw answer to the caller)
    private <T> T call(Supplier<T> request) {
        try {
            return request.get();
        } catch (RestClientException e) {
            throw translate(e);
        }
    }

    private ApiException translate(RestClientException e) {
        if (e instanceof RestClientResponseException response) {
            int status = response.getStatusCode().value();
            if (status == HttpStatus.CONFLICT.value()) {
                return new ApiException(ErrorCode.ACCOUNT_ALREADY_EXISTS);
            }
            if (status == HttpStatus.BAD_REQUEST.value()) {
                // Keycloak says 400 when the password breaks the realm password policy
                log.info("Keycloak refused the request with 400 (usually the password policy)");
                return new ApiException(ErrorCode.PASSWORD_TOO_WEAK);
            }
            log.error("Keycloak Admin API error, status={}", status);
            return new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        }
        if (e instanceof ResourceAccessException) {
            log.error("Keycloak Admin API not reachable");
        } else if (e instanceof UnknownContentTypeException unknown) {
            // Seen in dev: kc-admin is behind Cloudflare Access and answers its "Sign in" HTML page
            log.error("Keycloak Admin API answered {} instead of JSON (Cloudflare Access login page?) - check KEYCLOAK_ADMIN_URL / CF Access token",
                    unknown.getContentType());
        } else {
            log.error("Keycloak Admin API call failed: {}", e.getClass().getSimpleName());
        }
        return new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
    }

    private static KeycloakUser toUser(Map<String, Object> u) {
        return new KeycloakUser(
                String.valueOf(u.get("id")),
                String.valueOf(u.get("email")),
                Boolean.TRUE.equals(u.get("enabled")));
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    // enabled = false when an admin suspended the account
    public record KeycloakUser(String id, String email, boolean enabled) {
    }
}
