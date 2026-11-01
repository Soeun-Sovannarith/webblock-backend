package org.kshrd.webblock.project.common.infrastructure.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.oauth2.core.OAuth2AuthenticatedPrincipal;
import org.springframework.security.oauth2.server.resource.introspection.BadOpaqueTokenException;
import tools.jackson.databind.json.JsonMapper;

class DevTokenIntrospectorTest {

    static final String USER_TOKEN = "user-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    static final String USER_ID = "0199e0a2-0000-7000-8000-000000000001";
    static final String OTHER_TOKEN = "other-token-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

    @TempDir
    Path dir;

    private DevTokenIntrospector load(String json) throws Exception {
        Path file = Files.writeString(dir.resolve("tokens.json"), json);
        return new DevTokenIntrospector(
                file.toString(), "user_id", JsonMapper.builder().build());
    }

    private DevTokenIntrospector standard() throws Exception {
        return load("""
                {"tokens":[{"token":"%s","subject":"alice","userId":"%s","scopes":["project:test"]}]}""".formatted(USER_TOKEN, USER_ID));
    }

    @Test
    void aUserTokenCarriesTheUserIdClaimAndItsScopes() throws Exception {
        OAuth2AuthenticatedPrincipal principal = standard().introspect(USER_TOKEN);
        assertThat(principal.<String>getAttribute("user_id")).isEqualTo(USER_ID);
        assertThat(principal.getAttributes()).containsOnlyKeys("sub", "user_id", "scope");
        assertThat(principal.getAuthorities())
                .extracting(GrantedAuthority::getAuthority)
                .containsExactly("SCOPE_project:test");
    }

    @Test
    void anUnknownTokenIsRefused() throws Exception {
        DevTokenIntrospector introspector = standard();
        assertThatThrownBy(() -> introspector.introspect("unknown-token-cccccccccccccccccccccccccccc"))
                .isInstanceOf(BadOpaqueTokenException.class);
    }

    /** Dev tokens stand for users only (decision 0002): no entry without a user. */
    @Test
    void anEntryWithoutAUserIdRefusesTheFileWithoutQuotingTheToken() {
        String nullUser = """
                {"tokens":[{"token":"%s","subject":"alice","userId":"%s","scopes":[]},
                           {"token":"%s","subject":"provisioning","userId":null,"scopes":[]}]}""".formatted(USER_TOKEN, USER_ID, OTHER_TOKEN);
        assertThatThrownBy(() -> load(nullUser))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("entry 1")
                .hasMessageContaining("userId")
                .hasMessageNotContaining(OTHER_TOKEN);
        String absentUser = """
                {"tokens":[{"token":"%s","subject":"provisioning"}]}""".formatted(OTHER_TOKEN);
        assertThatThrownBy(() -> load(absentUser))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("entry 0")
                .hasMessageContaining("userId")
                .hasMessageNotContaining(OTHER_TOKEN);
    }

    @Test
    void scopesAreOptional() throws Exception {
        DevTokenIntrospector introspector = load("""
                {"tokens":[{"token":"%s","subject":"alice","userId":"%s"}]}""".formatted(USER_TOKEN, USER_ID));
        assertThat(introspector.introspect(USER_TOKEN).getAuthorities()).isEmpty();
    }

    @Test
    void aShortTokenOrABadUserIdOrADuplicateRefusesTheFileWithoutQuotingIt() {
        String shortToken =
                "{\"tokens\":[{\"token\":\"short\",\"subject\":\"a\",\"userId\":\"" + USER_ID + "\",\"scopes\":[]}]}";
        assertThatThrownBy(() -> load(shortToken))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("entry 0")
                .hasMessageNotContaining("short");
        String badUser = """
                {"tokens":[{"token":"%s","subject":"a","userId":"not-a-uuid","scopes":[]}]}""".formatted(USER_TOKEN);
        assertThatThrownBy(() -> load(badUser)).isInstanceOf(IllegalStateException.class);
        String duplicate = """
                {"tokens":[{"token":"%1$s","subject":"a","userId":"%2$s","scopes":[]},
                           {"token":"%1$s","subject":"b","userId":"%2$s","scopes":[]}]}""".formatted(USER_TOKEN, USER_ID);
        assertThatThrownBy(() -> load(duplicate))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("repeats");
    }

    @Test
    void aMissingFileRefusesToStart() {
        assertThatThrownBy(() -> new DevTokenIntrospector(
                        dir.resolve("absent.json").toString(),
                        "user_id",
                        JsonMapper.builder().build()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("dev-tokens-file");
    }
}
