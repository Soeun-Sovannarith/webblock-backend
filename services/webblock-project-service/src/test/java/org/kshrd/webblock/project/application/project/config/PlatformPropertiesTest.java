package org.kshrd.webblock.project.application.project.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.domain.project.model.Subdomain;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.StandardEnvironment;
import org.springframework.core.io.ClassPathResource;

class PlatformPropertiesTest {

    private static final List<String> RESERVED = List.of("www", "auth");

    @Test
    void acceptsALowercaseMultiLabelDomain() {
        assertThat(new PlatformProperties("sites.webblock.test", RESERVED).siteDomain())
                .isEqualTo("sites.webblock.test");
    }

    @Test
    void refusesAnythingTheHostnameConstraintWouldRefuse() {
        for (String bad : new String[] {"", "localhost", "Sites.Example.com", "*.example.com", "a..b.com", "x.c0m"}) {
            assertThatThrownBy(() -> new PlatformProperties(bad, RESERVED))
                    .as(bad)
                    .isInstanceOf(IllegalArgumentException.class);
        }
    }

    /** 253 (DNS name limit) minus a 63-character sub-domain minus the dot between them. */
    @Test
    @DisplayName("FR-13.12: the site domain is at most 189 characters, so the longest preview host is 253")
    void fr13_theSiteDomainLeavesRoomForTheLongestSubdomain() {
        String longest = "a".repeat(63) + "." + "b".repeat(63) + "." + "c".repeat(61);
        assertThat(longest).hasSize(189);
        PlatformProperties properties = new PlatformProperties(longest, RESERVED);
        assertThat("x".repeat(Subdomain.MAX_LABEL_LENGTH) + "-preview." + properties.siteDomain())
                .hasSize(253);

        String oneOver = longest + "c";
        assertThatThrownBy(() -> new PlatformProperties(oneOver, RESERVED))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("189");
    }

    @Test
    @DisplayName("FR-13.3: each reserved sub-domain passes the label shape, or the settings are refused")
    void fr13_3_eachReservedEntryPassesTheLabelShape() {
        for (String bad : new String[] {"WWW", "ab", "-api", "mail.", ""}) {
            assertThatThrownBy(() -> new PlatformProperties("sites.webblock.test", List.of("www", bad)))
                    .as(bad)
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("webblock.platform.reserved-subdomains");
        }
        assertThatThrownBy(() -> new PlatformProperties("sites.webblock.test", java.util.Arrays.asList("www", null)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("FR-13.3: no reserved list means none is reserved; the list is copied")
    void fr13_3_noListIsEmptyAndTheListIsCopied() {
        assertThat(new PlatformProperties("sites.webblock.test", null).reservedSubdomains())
                .isEmpty();
        List<String> given = new java.util.ArrayList<>(RESERVED);
        PlatformProperties properties = new PlatformProperties("sites.webblock.test", given);
        given.add("shop");
        assertThat(properties.reservedSubdomains()).containsExactly("www", "auth");
    }

    /** The FRD's default list, as application.yml sets it; every entry passes the label shape. */
    @Test
    @DisplayName("FR-13.6: application.yml reserves the FRD list, preview, webblock, billing and support included")
    void fr13_theDefaultReservedListIsTheFrdOne() throws Exception {
        StandardEnvironment environment = new StandardEnvironment();
        new YamlPropertySourceLoader()
                .load("application", new ClassPathResource("application.yml"))
                .forEach(source -> environment.getPropertySources().addLast(source));
        PlatformProperties properties = Binder.get(environment)
                .bind("webblock.platform", PlatformProperties.class)
                .get();
        assertThat(properties.reservedSubdomains())
                .containsExactly(
                        "www",
                        "app",
                        "api",
                        "admin",
                        "git",
                        "mail",
                        "status",
                        "docs",
                        "auth",
                        "preview",
                        "webblock",
                        "billing",
                        "support")
                .allMatch(Subdomain::isLabel);
    }
}
