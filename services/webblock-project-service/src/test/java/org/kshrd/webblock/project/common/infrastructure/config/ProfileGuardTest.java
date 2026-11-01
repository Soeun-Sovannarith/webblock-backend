package org.kshrd.webblock.project.common.infrastructure.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Arrays;
import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.core.env.MapPropertySource;
import org.springframework.core.env.PropertySource;
import org.springframework.core.env.SystemEnvironmentPropertySource;

class ProfileGuardTest {

    static final class FakeStandIn implements StandIn {}

    interface Port {}

    static final class PortStandIn implements Port, StandIn {}

    /** The shape of a stand-in @Bean method: declared with its port type, the instance is a StandIn. */
    static class PortConfig {
        @org.springframework.context.annotation.Bean
        Port port() {
            return new PortStandIn();
        }
    }

    private final ApplicationContextRunner runner = new ApplicationContextRunner().withBean(ProfileGuard.class);

    /** What application.yml, application-production.yml and a production deployment set together. */
    private static final String[] PRODUCTION = {
        "spring.profiles.active=production",
        "spring.flyway.enabled=false",
        "webblock.catalog.migrations.enabled=false",
        "webblock.security.identity-mode=jwt",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=https://id.example.test/realms/webblock",
        "webblock.payment.base-url=https://payment.webblock.example",
        "webblock.platform.site-domain=sites.webblock.example",
        "webblock.platform.reserved-subdomains=www,auth,preview",
        "webblock.security.user-id-claim=user_id",
        "namastack.outbox.jdbc.schema-initialization.enabled=false",
        "spring.jpa.hibernate.ddl-auto=validate",
        "springdoc.api-docs.enabled=false",
        "springdoc.swagger-ui.enabled=false"
    };

    private void refused(String override, String message) {
        runner.withPropertyValues(PRODUCTION)
                .withPropertyValues(override)
                .run(context -> assertThat(context)
                        .as(override)
                        .hasFailed()
                        .getFailure()
                        .isInstanceOf(IllegalStateException.class)
                        .hasMessageContaining(message));
    }

    private void accepted(String override) {
        runner.withPropertyValues(PRODUCTION)
                .withPropertyValues(override)
                .run(context -> assertThat(context).as(override).hasNotFailed());
    }

    /**
     * The calls carry no token (mesh identity), so an in-cluster http URL is fine; what is refused is anything that is
     * not an absolute http or https URL naming a host, and user info, which would put a credential in the setting.
     */
    @Test
    void productionNeedsAnAbsoluteHttpOrHttpsPaymentUrlWithAHost() {
        accepted("webblock.payment.base-url=http://payment.payment.svc.cluster.local:8080");
        accepted("webblock.payment.base-url=HTTPS://payment.webblock.example/");
        accepted("webblock.payment.base-url= https://payment.webblock.example ");
        String setting = "webblock.payment.base-url";
        refused(setting + "=", setting);
        refused(setting + "=   ", setting);
        refused(setting + "=payment.webblock.example", setting);
        refused(setting + "=/internal", setting);
        refused(setting + "=ftp://payment.webblock.example", setting);
        refused(setting + "=file:///etc/payment", setting);
        refused(setting + "=http:payment.webblock.example", setting);
        refused(setting + "=http:///internal", setting);
        refused(setting + "=http://", setting);
        refused(setting + "=http://payment_svc:8080", setting);
        refused(setting + "=http://payment.webblock.example:80 80", setting);
        refused(setting + "=https://user@payment.webblock.example", setting);
    }

    /**
     * Plain http only to an in-cluster Service name, where the sidecar adds mTLS; any other host could be off the mesh
     * and would receive the request in clear text, unauthenticated, so it needs https.
     */
    @Test
    void productionAcceptsPlainHttpOnlyForAnInClusterServiceName() {
        accepted("webblock.payment.base-url=http://payment.payment.svc.cluster.local:8080");
        accepted("webblock.payment.base-url=http://payment.payment.svc");
        accepted("webblock.payment.base-url=HTTP://PAYMENT.PAYMENT.SVC.CLUSTER.LOCAL.:8080/");
        accepted("webblock.payment.base-url=http://payment.payment.svc.");
        accepted("webblock.payment.base-url=https://payment.webblock.example");
        String setting = "webblock.payment.base-url";
        for (String url : new String[] {
            "http://payment",
            "http://payment.webblock.example",
            "http://payment.svc.example",
            "http://payment.svc.cluster.local.evil.example",
            "http://svc",
            "http://payment.payment.svc-cluster.local"
        }) {
            refused(setting + "=" + url, "https");
        }
    }

    /** A loopback Payment URL in production reaches the pod itself, never Payment. */
    @Test
    void productionRefusesALoopbackPaymentUrl() {
        for (String url : new String[] {
            "http://localhost:8080",
            "http://127.0.0.1",
            "https://localhost",
            "https://LOCALHOST.:8443",
            "https://payment.localhost",
            "https://127.0.0.1:8443",
            "https://127.8.9.10",
            "https://[::1]:8443",
            "https://[0:0:0:0:0:0:0:1]",
            "https://[::ffff:127.0.0.1]",
            "https://0.0.0.0",
            "https://[::]"
        }) {
            refused("webblock.payment.base-url=" + url, "loopback");
        }
        accepted("webblock.payment.base-url=https://128.0.0.1");
        accepted("webblock.payment.base-url=https://localhost-payment.webblock.example");
    }

    /**
     * User info is refused even when empty: {@code http://@host} has an empty, not an absent, user info, so a check for
     * text instead of presence would let it pass.
     */
    @Test
    void productionRefusesEmptyOrPasswordOnlyUserInfo() {
        accepted("webblock.payment.base-url=http://payment.payment.svc:8080");
        for (String url : new String[] {
            "http://@payment.payment.svc:8080",
            "http://:hunter2@payment.payment.svc:8080",
            "http://:@payment.payment.svc:8080",
            "https://@payment.webblock.example"
        }) {
            refused("webblock.payment.base-url=" + url, "without user info");
        }
    }

    /**
     * An IPv6 literal is a host like any other: accepted with https, refused as plain http (not an in-cluster Service
     * name). {@code http://[::1]:8080} is refused as loopback, the pod itself.
     */
    @Test
    void productionTreatsAnIpv6HostLikeAnyOtherHost() {
        accepted("webblock.payment.base-url=https://[2001:db8::1]:8443");
        refused("webblock.payment.base-url=http://[2001:db8::1]:8080", "https");
        refused("webblock.payment.base-url=http://[::1]:8080", "loopback");
    }

    /**
     * java.net.URI accepts an all-digit host, which the JDK resolves as a number (2130706433 is 127.0.0.1, 0 is
     * 0.0.0.0), and an IPv6 zone ID, which InetAddress.ofLiteral cannot parse: neither can be checked for loopback.
     */
    @Test
    void productionRefusesNumericHostsOtherThanADottedQuadAndIpv6Zones() {
        for (String url : new String[] {
            "https://2130706433",
            "https://0",
            "https://[::1%25lo]",
            "https://4294967295:8443",
            "https://017700000001",
            "https://[fe80::1%25eth0]:8443"
        }) {
            refused("webblock.payment.base-url=" + url, "canonical IPv4");
        }
        accepted("webblock.payment.base-url=https://10.0.0.7:8443");
        accepted("webblock.payment.base-url=https://payment1.webblock.example");
    }

    /**
     * Aligned with Provisioning's production URL check: a resolver may read decimal, octal or hex parts as an IPv4
     * address (0x7f000001 and 0177.0.0.1 reach 127.0.0.1), so a numeric host must be a canonical dotted quad.
     */
    @Test
    void productionRefusesANumericHostThatIsNotACanonicalDottedQuad() {
        for (String url : new String[] {
            "https://0x7f000001",
            "https://0X7F000001:8443",
            "https://0177.0.0.1",
            "https://127.000.000.001",
            "https://010.0.0.5",
            "https://0x",
            "https://0x7f.0.0.1",
            "https://10.0.0.5.",
            "https://256.1.1.1"
        }) {
            refused("webblock.payment.base-url=" + url, "webblock.payment.base-url");
        }
        refused("webblock.payment.base-url=https://0x7f000001", "canonical IPv4");
        refused("webblock.payment.base-url=https://0177.0.0.1", "canonical IPv4");
        refused("webblock.payment.base-url=https://127.000.000.001", "canonical IPv4");
        accepted("webblock.payment.base-url=https://10.0.0.5");
        accepted("webblock.payment.base-url=https://[fd00::5]:8443");
        accepted("webblock.payment.base-url=https://0xdead.example.com");
        accepted("webblock.payment.base-url=https://1password.example.com");
        accepted("webblock.payment.base-url=https://deadbeef");
        accepted("webblock.payment.base-url=http://123.project.svc");
    }

    /** The usual /etc/hosts names of the loopback address that are not under localhost. */
    @Test
    void productionRefusesTheOtherLoopbackNames() {
        for (String url : new String[] {
            "https://ip6-localhost",
            "https://IP6-LOCALHOST.:8443",
            "https://ip6-loopback",
            "https://Ip6-Loopback.",
            "https://localhost.localdomain",
            "https://LOCALHOST.LOCALDOMAIN."
        }) {
            refused("webblock.payment.base-url=" + url, "loopback");
        }
        accepted("webblock.payment.base-url=https://ip6-localhost.webblock.example");
    }

    /**
     * A host with a non-ASCII character is refused: the HTTP client could map the Unicode full stops to "." before it
     * connects. java.net.URI already gives such a URL no host, so the shape check refuses it.
     */
    @Test
    void productionRefusesANonAsciiHost() {
        for (String url : new String[] {
            "https://127\u30020\u30020\u30021",
            "https://127\uFF0E0\uFF0E0\uFF0E1",
            "https://127\uFF610\uFF610\uFF611",
            "https://payment\u3002payment\u3002svc",
            "https://b\u00FCcher.example",
            "http://payment.payment.svc\u3002"
        }) {
            refused("webblock.payment.base-url=" + url, "webblock.payment.base-url");
        }
        accepted("webblock.payment.base-url=https://xn--bcher-kva.example");
    }

    /** java.net.URI parses any digits as a port; one above 65535 would fail only at the first call. */
    @Test
    void productionRefusesAPortAbove65535() {
        accepted("webblock.payment.base-url=https://payment.webblock.example:65535");
        refused("webblock.payment.base-url=https://payment.webblock.example:65536", "webblock.payment.base-url");
        refused("webblock.payment.base-url=https://payment.webblock.example:99999", "webblock.payment.base-url");
    }

    /** A credential in the URL must not reach the startup log: the message names the setting, never its value. */
    @Test
    void theRefusalNeverEchoesThePaymentUrl() {
        for (String url : new String[] {
            "https://project:hunter2@payment.webblock.example",
            "http://hunter2 x",
            "http://hunter2.webblock.example",
            "https://hunter2.localhost",
            "https://2130706433",
            "https://[::1%25hunter2]",
            "https://0x7f000001",
            "https://ip6-localhost"
        }) {
            runner.withPropertyValues(PRODUCTION)
                    .withPropertyValues("webblock.payment.base-url=" + url)
                    .run(context -> assertThat(context)
                            .as(url)
                            .hasFailed()
                            .getFailure()
                            .isInstanceOf(IllegalStateException.class)
                            .hasMessageContaining("webblock.payment.base-url")
                            .hasMessageNotContaining("hunter2")
                            .hasNoCause());
        }
    }

    /** application.yml's defaults name hosts that cannot exist (RFC 2606) and must be replaced in production. */
    @Test
    void productionRefusesThePlaceholderDefaults() {
        refused("webblock.payment.base-url=http://payment.invalid", "placeholder");
        refused("webblock.payment.base-url=https://payment.invalid", "placeholder");
        refused("webblock.payment.base-url=http://PAYMENT.INVALID.:8080", "placeholder");
        refused("webblock.platform.site-domain=sites.webblock.test", "placeholder");
        refused("webblock.platform.site-domain=SITES.WEBBLOCK.TEST.", "placeholder");
        refused("webblock.payment.base-url=https://payment.test:8443/api", "placeholder");
        refused("webblock.platform.site-domain=", "webblock.platform.site-domain");
    }

    @Test
    @DisplayName("FR-13.9: production refuses an empty or missing reserved sub-domain list, naming the setting")
    void fr13_9_productionRefusesAnEmptyReservedList() {
        String setting = "webblock.platform.reserved-subdomains";
        refused(setting + "=", setting);
        refused(setting + "= , ", setting);
        runner.withPropertyValues(productionWithout(setting))
                .run(context -> assertThat(context).hasFailed().getFailure().hasMessageContaining(setting));
    }

    @Test
    @DisplayName("FR-13.10: production starts with application.yml's default reserved list")
    void fr13_10_productionStartsWithTheDefaultReservedList() {
        runner.withPropertyValues(productionWithout("webblock.platform.reserved-subdomains"))
                .withInitializer(context -> addApplicationYmlList(context.getEnvironment()))
                .run(context -> assertThat(context).hasNotFailed());
    }

    private static String[] productionWithout(String setting) {
        return Arrays.stream(PRODUCTION)
                .filter(property -> !property.startsWith(setting))
                .toArray(String[]::new);
    }

    private static void addApplicationYmlList(org.springframework.core.env.ConfigurableEnvironment environment) {
        try {
            new org.springframework.boot.env.YamlPropertySourceLoader()
                    .load("application", new org.springframework.core.io.ClassPathResource("application.yml")).stream()
                            .filter(source -> source.containsProperty("webblock.platform.reserved-subdomains"))
                            .forEach(source -> environment.getPropertySources().addLast(source));
        } catch (java.io.IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
    }

    /** RFC 2606 reserves the names themselves too: the single-label hosts invalid and test. */
    @Test
    void productionRefusesTheBareReservedNames() {
        refused("webblock.payment.base-url=https://invalid", "placeholder");
        refused("webblock.payment.base-url=https://test:8443", "placeholder");
        refused("webblock.payment.base-url=https://INVALID.", "placeholder");
        refused("webblock.payment.base-url=https://test./api", "placeholder");
        refused("webblock.platform.site-domain=invalid", "placeholder");
        refused("webblock.platform.site-domain=TEST.", "placeholder");
        accepted("webblock.platform.site-domain=test-sites.webblock.example");
        accepted("webblock.payment.base-url=https://invalidation.webblock.example");
    }

    /** Another claim would let every service-account token pass as a user identified by that claim. */
    @Test
    void productionRefusesAUserIdClaimOtherThanUserId() {
        refused("webblock.security.user-id-claim=sub", "user-id-claim");
        refused("webblock.security.user-id-claim=userId", "user-id-claim");
    }

    /** The service never changes its own schema in production: Namastack's tables come from the migration job too. */
    @Test
    void productionRefusesInProcessSchemaChanges() {
        refused("namastack.outbox.jdbc.schema-initialization.enabled=true", "schema-initialization");
        runner.withPropertyValues(PRODUCTION)
                .withPropertyValues("spring.profiles.active=production")
                .withInitializer(ctx -> ctx.getEnvironment()
                        .getPropertySources()
                        .addFirst(new org.springframework.core.env.SystemEnvironmentPropertySource(
                                "systemEnvironment",
                                java.util.Map.of("NAMASTACK_OUTBOX_JDBC_SCHEMAINITIALIZATION_ENABLED", "true"))))
                .run(context ->
                        assertThat(context).hasFailed().getFailure().hasMessageContaining("schema-initialization"));
        refused("spring.jpa.hibernate.ddl-auto=update", "ddl-auto");
        refused("spring.jpa.hibernate.ddl-auto=create-drop", "ddl-auto");
        accepted("spring.jpa.hibernate.ddl-auto=none");
        accepted("spring.jpa.hibernate.ddl-auto=VALIDATE");
    }

    @Test
    @DisplayName("TR-40: production refuses the OpenAPI description or Swagger UI switched on, in any spelling")
    void tr40_productionRefusesTheOpenApiDescriptionAndSwaggerUi() {
        refused("springdoc.api-docs.enabled=true", "springdoc.api-docs.enabled");
        refused("springdoc.swagger-ui.enabled=true", "springdoc.swagger-ui.enabled");
        for (String variable : new String[] {"SPRINGDOC_APIDOCS_ENABLED", "SPRINGDOC_SWAGGERUI_ENABLED"}) {
            runner.withPropertyValues(PRODUCTION)
                    .withInitializer(ctx -> ctx.getEnvironment()
                            .getPropertySources()
                            .addFirst(
                                    new SystemEnvironmentPropertySource("systemEnvironment", Map.of(variable, "true"))))
                    .run(context -> assertThat(context)
                            .as(variable)
                            .hasFailed()
                            .getFailure()
                            .hasMessageContaining("springdoc.api-docs.enabled"));
        }
    }

    private static final String[] SPRINGDOC_SWITCHES = {"springdoc.api-docs.enabled", "springdoc.swagger-ui.enabled"};

    /** Production with one raw value put first, untrimmed (withPropertyValues trims its values). */
    private ApplicationContextRunner productionWith(PropertySource<?> source) {
        return runner.withPropertyValues(PRODUCTION)
                .withInitializer(
                        ctx -> ctx.getEnvironment().getPropertySources().addFirst(source));
    }

    /**
     * springdoc 3.1.1's switches are @ConditionalOnProperty without havingValue: Spring Boot 4.1.1 turns them on unless
     * the value is exactly false, ignoring case and without trimming. So off, no, 0 or a padded false serve the docs,
     * and the guard accepts only what springdoc reads as off.
     */
    @Test
    @DisplayName("TR-40: production accepts only the value springdoc reads as off: false, ignoring case, untrimmed")
    void tr40_productionAcceptsOnlyTheValueSpringdocReadsAsOff() {
        for (String name : SPRINGDOC_SWITCHES) {
            for (String on : new String[] {"true", "off", "no", "0", " false", "false ", "", "yes"}) {
                productionWith(new MapPropertySource("raw", Map.of(name, on)))
                        .run(context -> assertThat(context)
                                .as(name + "=[" + on + "]")
                                .hasFailed()
                                .getFailure()
                                .isInstanceOf(IllegalStateException.class)
                                .hasMessageContaining(name));
            }
            for (String off : new String[] {"false", "FALSE", "False"}) {
                productionWith(new MapPropertySource("raw", Map.of(name, off)))
                        .run(context ->
                                assertThat(context).as(name + "=[" + off + "]").hasNotFailed());
            }
        }
        for (String variable : new String[] {
            "SPRINGDOC_APIDOCS_ENABLED",
            "SPRINGDOC_API_DOCS_ENABLED",
            "SPRINGDOC_SWAGGERUI_ENABLED",
            "SPRINGDOC_SWAGGER_UI_ENABLED"
        }) {
            productionWith(new SystemEnvironmentPropertySource("systemEnvironment", Map.of(variable, "off")))
                    .run(context -> assertThat(context)
                            .as(variable + "=off")
                            .hasFailed()
                            .getFailure()
                            .hasMessageContaining("springdoc.api-docs.enabled"));
            productionWith(new SystemEnvironmentPropertySource("systemEnvironment", Map.of(variable, "FALSE")))
                    .run(context -> assertThat(context).as(variable + "=FALSE").hasNotFailed());
        }
    }

    /** springdoc serves both when the settings are absent, so production needs them set to false (application.yml). */
    @Test
    @DisplayName("TR-40: production refuses springdoc's own default, which serves the description and Swagger UI")
    void tr40_productionRefusesSpringdocLeftAtItsDefault() {
        for (String left : new String[] {"springdoc.api-docs.enabled", "springdoc.swagger-ui.enabled"}) {
            String[] without = Arrays.stream(PRODUCTION)
                    .filter(setting -> !setting.startsWith(left + "="))
                    .toArray(String[]::new);
            runner.withPropertyValues(without)
                    .run(context -> assertThat(context)
                            .as(left)
                            .hasFailed()
                            .getFailure()
                            .isInstanceOf(IllegalStateException.class)
                            .hasMessageContaining(left));
        }
    }

    @Test
    @DisplayName("TR-40: prototype may serve the OpenAPI description and Swagger UI")
    void tr40_prototypeMayServeTheOpenApiDescriptionAndSwaggerUi() {
        runner.withPropertyValues(
                        "spring.profiles.active=prototype",
                        "springdoc.api-docs.enabled=true",
                        "springdoc.swagger-ui.enabled=true")
                .run(context -> assertThat(context).hasNotFailed());
    }

    @Test
    void prototypeMayUseThePlaceholderDefaults() {
        runner.withPropertyValues(
                        "spring.profiles.active=prototype",
                        "webblock.payment.base-url=http://payment.invalid",
                        "webblock.platform.site-domain=sites.webblock.test",
                        "namastack.outbox.jdbc.schema-initialization.enabled=true")
                .run(context -> assertThat(context).hasNotFailed());
    }

    @Test
    void prototypeWithAStandInStarts() {
        runner.withPropertyValues("spring.profiles.active=prototype")
                .withBean(FakeStandIn.class)
                .run(context -> assertThat(context).hasNotFailed());
    }

    @Test
    void productionWithoutStandInsStarts() {
        runner.withPropertyValues(PRODUCTION).run(context -> assertThat(context).hasNotFailed());
    }

    @Test
    void productionRefusesInProcessMigrations() {
        runner.withPropertyValues("spring.profiles.active=production")
                .run(context -> assertThat(context).hasFailed().getFailure().hasMessageContaining("separate job"));
        runner.withPropertyValues("spring.profiles.active=production", "spring.flyway.enabled=false")
                .run(context -> assertThat(context).hasFailed());
    }

    @Test
    void productionRefusesMigratorCredentials() {
        runner.withPropertyValues(PRODUCTION)
                .withPropertyValues("spring.flyway.password=secret")
                .run(context -> assertThat(context)
                        .hasFailed()
                        .getFailure()
                        .hasMessage("The production service must not hold migrator credentials"));
        runner.withPropertyValues(PRODUCTION)
                .withPropertyValues("webblock.catalog.migrations.password=secret")
                .run(context -> assertThat(context).hasFailed());
    }

    @Test
    void productionRefusesEnvironmentStyleSpellings() {
        runner.withPropertyValues(PRODUCTION)
                .withInitializer(ctx -> ctx.getEnvironment()
                        .getPropertySources()
                        .addFirst(new org.springframework.core.env.SystemEnvironmentPropertySource(
                                "systemEnvironment", java.util.Map.of("SPRING_FLYWAY_PASSWORD", "secret"))))
                .run(context -> assertThat(context)
                        .hasFailed()
                        .getFailure()
                        .hasMessage("The production service must not hold migrator credentials"));
        runner.withPropertyValues(PRODUCTION)
                .withInitializer(ctx -> ctx.getEnvironment()
                        .getPropertySources()
                        .addFirst(new org.springframework.core.env.SystemEnvironmentPropertySource(
                                "systemEnvironment", java.util.Map.of("SPRING_FLYWAY_ENABLED", "true"))))
                .run(context ->
                        assertThat(context).hasFailed().getFailure().hasMessageContaining("no migrations in-process"));
    }

    @Test
    void prototypeMayMigrateInProcess() {
        runner.withPropertyValues("spring.profiles.active=prototype", "spring.flyway.password=secret")
                .run(context -> assertThat(context).hasNotFailed());
    }

    @Test
    void productionRefusesAStandIn() {
        runner.withPropertyValues(PRODUCTION)
                .withBean(FakeStandIn.class)
                .run(context -> assertThat(context)
                        .hasFailed()
                        .getFailure()
                        .hasMessage("The production profile refuses stand-ins: profileGuardTest.FakeStandIn"));
    }

    @Test
    void productionRefusesAStandInReturnedAsItsPortType() {
        runner.withPropertyValues(PRODUCTION)
                .withUserConfiguration(PortConfig.class)
                .run(context -> assertThat(context)
                        .hasFailed()
                        .getFailure()
                        .hasMessageContaining("The production profile refuses stand-ins: port"));
        runner.withPropertyValues("spring.profiles.active=prototype")
                .withUserConfiguration(PortConfig.class)
                .run(context -> assertThat(context).hasNotFailed());
    }

    @Test
    void productionRefusesAnyIdentityModeButJwt() {
        runner.withPropertyValues(PRODUCTION)
                .withPropertyValues("webblock.security.identity-mode=dev-tokens")
                .run(context -> assertThat(context).hasFailed().getFailure().hasMessageContaining("identity-mode=jwt"));
        runner.withPropertyValues(PRODUCTION)
                .withPropertyValues("webblock.security.identity-mode=bogus")
                .run(context -> assertThat(context).hasFailed());
    }

    @Test
    void productionRefusesAnIssuerThatIsNotHttps() {
        runner.withPropertyValues(PRODUCTION)
                .withPropertyValues(
                        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://id.example.test/realms/x")
                .run(context -> assertThat(context).hasFailed().getFailure().hasMessageContaining("https"));
        runner.withPropertyValues(PRODUCTION)
                .withPropertyValues("spring.security.oauth2.resourceserver.jwt.issuer-uri=")
                .run(context -> assertThat(context).hasFailed());
    }

    @Test
    void prototypeMayUseAnHttpIssuerAndDevTokens() {
        runner.withPropertyValues(
                        "spring.profiles.active=prototype",
                        "webblock.security.identity-mode=dev-tokens",
                        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost:8180/realms/webblock")
                .run(context -> assertThat(context).hasNotFailed());
    }

    /**
     * A URL parameter replaces the pool's driver property of the same name, in every profile. The message names the
     * setting and the parameter, never the URL, which may hold a password.
     */
    @Test
    void aDatabaseUrlMustNotOverrideThePoolsDriverSettings() {
        for (String profile : new String[] {"prototype", "production"}) {
            for (String setting : new String[] {"spring.datasource.url", "webblock.catalog.datasource.url"}) {
                for (String[] parameter : new String[][] {
                    {"logServerErrorDetail=true", "logServerErrorDetail"},
                    {"socketTimeout=0", "socketTimeout"},
                    {"options=-c%20statement_timeout%3D0", "options"},
                    {"SOCKETTIMEOUT=0", "socketTimeout"},
                    {"LogServerErrorDetail", "logServerErrorDetail"}
                }) {
                    String url =
                            "jdbc:postgresql://db.example:5432/app?password=hunter2&sslmode=require&" + parameter[0];
                    runner.withPropertyValues(PRODUCTION)
                            .withPropertyValues("spring.profiles.active=" + profile, setting + "=" + url)
                            .run(context -> assertThat(context)
                                    .as(profile + " " + setting + " " + parameter[0])
                                    .hasFailed()
                                    .getFailure()
                                    .isInstanceOf(IllegalStateException.class)
                                    .hasMessage(setting + " must not set the driver property " + parameter[1]
                                            + ": it would replace the setting the service relies on"));
                }
            }
        }
    }

    @Test
    void aDatabaseUrlWithOtherParametersStarts() {
        for (String profile : new String[] {"prototype", "production"}) {
            runner.withPropertyValues(PRODUCTION)
                    .withPropertyValues(
                            "spring.profiles.active=" + profile,
                            "spring.datasource.url=jdbc:postgresql://db.example/app?sslmode=require&loginTimeout=5",
                            "webblock.catalog.datasource.url=jdbc:postgresql://db.example:5432/catalog")
                    .run(context -> assertThat(context).as(profile).hasNotFailed());
        }
    }

    @Test
    void noProfileOrBothProfilesRefuseToStart() {
        runner.run(context -> assertThat(context).hasFailed());
        runner.withPropertyValues("spring.profiles.active=prototype,production")
                .run(context -> assertThat(context).hasFailed());
    }
}
