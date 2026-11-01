package org.kshrd.webblock.project.common.infrastructure.security;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.kshrd.webblock.project.common.infrastructure.config.StandIn;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.core.OAuth2AuthenticatedPrincipal;
import org.springframework.security.oauth2.server.resource.introspection.BadOpaqueTokenException;
import org.springframework.security.oauth2.server.resource.introspection.OAuth2IntrospectionAuthenticatedPrincipal;
import org.springframework.security.oauth2.server.resource.introspection.OpaqueTokenIntrospector;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

/**
 * PROTOTYPE STAND-IN for Keycloak (spec section 8), in the style of Provisioning's dev tokens: a file maps static
 * bearer tokens to a subject, a platform user ID and optional scopes. Dev tokens stand for users only (decision
 * 0002): an entry without a user ID refuses the file. Tokens are compared by SHA-256 digest in
 * constant time with no early exit. Neither the file's contents nor a presented token is ever logged or put in an
 * error.
 */
@Component
@Profile("prototype")
@ConditionalOnProperty(name = "webblock.security.identity-mode", havingValue = "dev-tokens")
public class DevTokenIntrospector implements OpaqueTokenIntrospector, StandIn {

    static final int MIN_TOKEN_LENGTH = 32;
    static final int MAX_TOKEN_LENGTH = 512;

    record FileEntry(String token, String subject, String userId, List<String> scopes) {}

    record TokenFile(List<FileEntry> tokens) {}

    private record Entry(byte[] digest, Map<String, Object> attributes, List<GrantedAuthority> authorities) {}

    private final List<Entry> entries;

    public DevTokenIntrospector(
            @Value("${webblock.security.dev-tokens-file}") String file,
            @Value("${webblock.security.user-id-claim}") String userIdClaim,
            JsonMapper jsonMapper) {
        TokenFile parsed = read(file, jsonMapper);
        if (parsed.tokens() == null || parsed.tokens().isEmpty()) {
            throw new IllegalStateException("The dev token file holds no token");
        }
        List<Entry> loaded = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < parsed.tokens().size(); i++) {
            FileEntry e = parsed.tokens().get(i);
            String where = "Dev token file entry " + i;
            if (e.token() == null
                    || e.token().length() < MIN_TOKEN_LENGTH
                    || e.token().length() > MAX_TOKEN_LENGTH) {
                throw new IllegalStateException(where + ": token must be 32 to 512 characters");
            }
            if (!seen.add(e.token())) {
                throw new IllegalStateException(where + ": token repeats an earlier entry");
            }
            if (e.subject() == null || e.subject().isBlank() || e.subject().length() > 255) {
                throw new IllegalStateException(where + ": subject must be 1 to 255 characters");
            }
            if (e.userId() == null) {
                throw new IllegalStateException(where + ": userId is required (dev tokens stand for users only)");
            }
            Map<String, Object> attributes = new HashMap<>();
            attributes.put("sub", e.subject());
            List<String> scopes = e.scopes() == null ? List.of() : List.copyOf(e.scopes());
            attributes.put("scope", scopes);
            attributes.put(userIdClaim, canonicalUuid(e.userId(), where));
            List<GrantedAuthority> authorities = new ArrayList<>();
            scopes.forEach(scope -> authorities.add(new SimpleGrantedAuthority("SCOPE_" + scope)));
            loaded.add(new Entry(sha256(e.token()), Map.copyOf(attributes), List.copyOf(authorities)));
        }
        this.entries = List.copyOf(loaded);
    }

    @Override
    public OAuth2AuthenticatedPrincipal introspect(String token) {
        byte[] presented = sha256(token);
        Entry match = null;
        for (Entry entry : entries) {
            boolean equal = MessageDigest.isEqual(entry.digest(), presented);
            if (equal && match == null) {
                match = entry;
            }
        }
        if (match == null) {
            throw new BadOpaqueTokenException("The token is not a configured dev token");
        }
        return new OAuth2IntrospectionAuthenticatedPrincipal(
                (String) match.attributes().get("sub"), match.attributes(), match.authorities());
    }

    private static TokenFile read(String file, JsonMapper jsonMapper) {
        try {
            if (file == null || file.isBlank() || !Files.isRegularFile(Path.of(file))) {
                throw new IllegalStateException("webblock.security.dev-tokens-file does not name a readable file");
            }
            return jsonMapper.readValue(Files.readAllBytes(Path.of(file)), TokenFile.class);
        } catch (IOException | JacksonException e) {
            throw new IllegalStateException("The dev token file cannot be read or is not valid JSON");
        }
    }

    private static String canonicalUuid(String text, String where) {
        try {
            if (UUID.fromString(text).toString().equals(text)) {
                return text;
            }
        } catch (IllegalArgumentException e) {
            // reported below
        }
        throw new IllegalStateException(where + ": userId must be a canonical lowercase UUID");
    }

    private static byte[] sha256(String text) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
