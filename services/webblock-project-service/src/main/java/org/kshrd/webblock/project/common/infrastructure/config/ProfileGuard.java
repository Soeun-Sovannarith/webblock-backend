package org.kshrd.webblock.project.common.infrastructure.config;

import java.net.InetAddress;
import java.net.URI;
import java.net.URISyntaxException;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.beans.factory.SmartInitializingSingleton;
import org.springframework.beans.factory.config.BeanFactoryPostProcessor;
import org.springframework.beans.factory.config.ConfigurableListableBeanFactory;
import org.springframework.boot.context.properties.bind.Bindable;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.context.EnvironmentAware;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

/**
 * Exactly one of the profiles prototype and production. Production refuses to start when it would change a database
 * schema in-process (Flyway, the catalog runner, Namastack's schema initialisation, Hibernate's ddl-auto), holds a
 * migrator password (migrations run as a separate job, decision 0005), uses any identity mode other than jwt,
 * a user ID claim other than user_id, an issuer that is not https, a Payment URL that is not an absolute http or https
 * URL with a host, a port of at most 65535 and without user info, a plain http Payment URL to anything but an
 * in-cluster Service name (.svc, .svc.cluster.local), a loopback Payment host, a numeric Payment host other than a
 * canonical dotted IPv4 address or an IPv6 one with a zone ID, a non-ASCII Payment host, or one of application.yml's
 * placeholder hosts (invalid, test, .invalid, .test), when it would serve the OpenAPI description or Swagger UI
 * (springdoc, TR-40), when the reserved sub-domain list is empty (FR-13.9), or when any StandIn bean is defined or instantiated.
 * Environment variables can override application-production.yml, so every setting is checked here.
 *
 * <p>In every profile, a database URL may not set a driver property the pools rely on (JdbcUrls).
 *
 * <p>A BeanFactoryPostProcessor, so it refuses before any bean exists: Flyway and the catalog runner migrate while their
 * beans initialise, and a refusal after that would come too late.
 */
@Component
public class ProfileGuard implements BeanFactoryPostProcessor, EnvironmentAware, SmartInitializingSingleton {

    /** The project pool's URL, and the catalog's, which the catalog pool and the catalog migrations both read. */
    private static final List<String> DATABASE_URLS =
            List.of("spring.datasource.url", "webblock.catalog.datasource.url");

    /** What a resolver may read as an IPv4 address (inet_aton: decimal, octal or hex parts), trailing dot included. */
    private static final Pattern NUMERIC = Pattern.compile("(0x[0-9a-f]*|[0-9]+)(\\.(0x[0-9a-f]*|[0-9]+))*\\.?");

    private static final String OCTET = "(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])";

    /** A canonical dotted quad: four decimal parts of 0 to 255 without leading zeros. */
    private static final Pattern IPV4_LITERAL = Pattern.compile(OCTET + "(\\." + OCTET + "){3}");

    /** The usual /etc/hosts names of the loopback address that are not under localhost. */
    private static final Set<String> LOOPBACK_NAMES = Set.of("ip6-localhost", "ip6-loopback", "localhost.localdomain");

    private Environment environment;
    private ConfigurableListableBeanFactory beanFactory;

    @Override
    public void setEnvironment(Environment environment) {
        this.environment = environment;
    }

    @Override
    public void postProcessBeanFactory(ConfigurableListableBeanFactory beans) {
        this.beanFactory = beans;
        boolean prototype = environment.matchesProfiles("prototype");
        boolean production = environment.matchesProfiles("production");
        if (prototype == production) {
            throw new IllegalStateException("Exactly one of the profiles prototype and production must be active");
        }
        refuseUrlsOverridingPoolSettings();
        if (production) {
            refuseInProcessMigrations();
            refuseInProcessSchemaChanges();
            refuseWeakIdentity();
            refuseUnusableOrPlaceholderEndpoints();
            refuseApiDocs();
            refuseEmptyReservedSubdomains();
            refuseStandIns(beans.getBeanNamesForType(StandIn.class, true, false));
        }
    }

    /**
     * The definitions' declared types miss a stand-in that a @Bean method returns as its port type, so the instances are
     * checked too once they exist. Stand-ins do not migrate, so the late refusal is safe.
     */
    @Override
    public void afterSingletonsInstantiated() {
        if (environment.matchesProfiles("production")) {
            refuseStandIns(beanFactory.getBeanNamesForType(StandIn.class));
        }
    }

    private static void refuseStandIns(String[] standIns) {
        if (standIns.length > 0) {
            throw new IllegalStateException(
                    "The production profile refuses stand-ins: " + String.join(", ", Arrays.asList(standIns)));
        }
    }

    private void refuseUrlsOverridingPoolSettings() {
        for (String name : DATABASE_URLS) {
            JdbcUrls.refuseDriverPropertyOverrides(name, environment.getProperty(name));
        }
    }

    private void refuseInProcessMigrations() {
        if (environment.getProperty("spring.flyway.enabled", Boolean.class, true)
                || environment.getProperty("webblock.catalog.migrations.enabled", Boolean.class, true)) {
            throw new IllegalStateException("The production profile runs no migrations in-process: set "
                    + "spring.flyway.enabled and webblock.catalog.migrations.enabled to false (migrations run as a "
                    + "separate job)");
        }
        if (StringUtils.hasText(environment.getProperty("spring.flyway.password"))
                || StringUtils.hasText(environment.getProperty("webblock.catalog.migrations.password"))) {
            throw new IllegalStateException("The production service must not hold migrator credentials");
        }
    }

    private void refuseInProcessSchemaChanges() {
        // Bound as Namastack binds it (relaxed names, its default true), so no spelling of the variable slips past.
        if (Binder.get(environment)
                .bind("namastack.outbox.jdbc.schema-initialization.enabled", Boolean.class)
                .orElse(true)) {
            throw new IllegalStateException(
                    "The production profile refuses "
                            + "namastack.outbox.jdbc.schema-initialization.enabled: the outbox tables come from the migration job");
        }
        String ddl =
                environment.getProperty("spring.jpa.hibernate.ddl-auto", "none").strip();
        if (!"validate".equalsIgnoreCase(ddl) && !"none".equalsIgnoreCase(ddl)) {
            throw new IllegalStateException(
                    "The production profile accepts only spring.jpa.hibernate.ddl-auto=validate or none");
        }
    }

    /** FR-13.9: with no reserved names a client could take preview, www or auth; the list must name at least one. */
    private void refuseEmptyReservedSubdomains() {
        String name = "webblock.platform.reserved-subdomains";
        List<String> names = Binder.get(environment)
                .bind(name, Bindable.listOf(String.class))
                .orElse(List.of());
        if (names.stream().noneMatch(StringUtils::hasText)) {
            throw new IllegalStateException(
                    "The production profile needs a non-empty " + name + " (application.yml's default list)");
        }
    }

    /**
     * TR-40: the OpenAPI description and Swagger UI are a prototype aid. springdoc's switches are
     * {@code @ConditionalOnProperty} without {@code havingValue} and with {@code matchIfMissing}: on unless the value is
     * exactly {@code false}, ignoring case and untrimmed. So each is read as raw text under every relaxed spelling and
     * only that value passes; off, no, 0, a padded false or an absent setting are refused. application.yml sets both.
     */
    private void refuseApiDocs() {
        Binder binder = Binder.get(environment);
        for (String name : List.of("springdoc.api-docs.enabled", "springdoc.swagger-ui.enabled")) {
            if (!"false".equalsIgnoreCase(binder.bind(name, String.class).orElse(null))) {
                throw new IllegalStateException("The production profile serves no OpenAPI description or Swagger UI:"
                        + " set springdoc.api-docs.enabled and springdoc.swagger-ui.enabled to false");
            }
        }
    }

    private void refuseWeakIdentity() {
        String mode = environment.getProperty("webblock.security.identity-mode", "jwt");
        if (!"jwt".equalsIgnoreCase(mode.strip())) {
            throw new IllegalStateException("The production profile accepts only webblock.security.identity-mode=jwt");
        }
        String issuer = environment.getProperty("spring.security.oauth2.resourceserver.jwt.issuer-uri", "");
        if (!isHttps(issuer)) {
            throw new IllegalStateException(
                    "The production profile needs an https issuer: spring.security.oauth2.resourceserver.jwt.issuer-uri");
        }
        // Any other claim would make every service-account token a user identified by that claim.
        if (!"user_id".equals(environment.getProperty("webblock.security.user-id-claim", "user_id"))) {
            throw new IllegalStateException(
                    "The production profile accepts only webblock.security.user-id-claim=user_id");
        }
    }

    /**
     * The calls to Payment carry no token (Payment's sidecar admits this service by its mesh identity), so plain http is
     * accepted, but only to an in-cluster Service name ({@code .svc}, {@code .svc.cluster.local}), where the sidecar
     * adds mTLS; any other host could be off the mesh and needs https. A loopback host reaches the pod itself. The URL
     * must name a host: a relative or opaque URL, another scheme or user info (a credential in a setting) is refused.
     * Every message names the setting, never the URL.
     */
    private void refuseUnusableOrPlaceholderEndpoints() {
        String name = "webblock.payment.base-url";
        URI paymentUrl = absoluteHttpUrl(name, environment.getProperty(name, ""));
        refusePlaceholder(name, paymentUrl.getHost());
        if (isUncheckableLiteral(paymentUrl.getHost())) {
            throw new IllegalStateException(
                    "The production profile needs " + name + " to name its host by a DNS name, a"
                            + " canonical IPv4 address or an IPv6 address without a zone ID");
        }
        if (isLoopback(paymentUrl.getHost())) {
            throw new IllegalStateException("The production profile refuses a loopback host in " + name);
        }
        if ("http".equalsIgnoreCase(paymentUrl.getScheme()) && !isInClusterServiceName(paymentUrl.getHost())) {
            throw new IllegalStateException("The production profile accepts a plain http " + name
                    + " only for an in-cluster Service name (.svc or .svc.cluster.local); any other host needs https");
        }
        String siteDomain = environment.getProperty("webblock.platform.site-domain", "");
        if (siteDomain.isBlank()) {
            throw new IllegalStateException("The production profile needs webblock.platform.site-domain");
        }
        refusePlaceholder("webblock.platform.site-domain", siteDomain);
    }

    /**
     * RFC 2606's invalid and test, as names or as suffixes (application.yml's defaults use them), never name a
     * production host.
     */
    private static void refusePlaceholder(String name, String host) {
        String domain = normalisedHost(host);
        if (domain.isEmpty()
                || domain.equals("invalid")
                || domain.equals("test")
                || domain.endsWith(".invalid")
                || domain.endsWith(".test")) {
            throw new IllegalStateException(
                    "The production profile refuses a placeholder host in " + name + " (application.yml's default)");
        }
    }

    private static boolean isInClusterServiceName(String host) {
        String domain = normalisedHost(host);
        return domain.endsWith(".svc") || domain.endsWith(".svc.cluster.local");
    }

    /**
     * A host isLoopback cannot judge (the same rule as Provisioning's production URL check): decimal, octal or hex
     * parts that are not a canonical dotted quad, trailing dot included (2130706433, 0x7f000001 and 0177.0.0.1 may all
     * reach 127.0.0.1, 0 reaches 0.0.0.0), or an IPv6 literal with a zone ID, which InetAddress.ofLiteral does not
     * parse. A non-ASCII host never gets here: java.net.URI gives it no host, so absoluteHttpUrl refuses it.
     */
    private static boolean isUncheckableLiteral(String host) {
        String raw = host == null ? "" : host.strip().toLowerCase(Locale.ROOT);
        return NUMERIC.matcher(raw).matches() && !IPV4_LITERAL.matcher(raw).matches()
                || raw.startsWith("[") && raw.contains("%");
    }

    /**
     * localhost and its subdomains (RFC 6761), ip6-localhost, ip6-loopback, localhost.localdomain, and an IP literal in 127.0.0.0/8, ::1 (IPv4-mapped forms included) or
     * the unspecified address, which also reaches the local host. Only literals are parsed: no name is ever resolved.
     */
    private static boolean isLoopback(String host) {
        String domain = normalisedHost(host);
        if (domain.equals("localhost") || domain.endsWith(".localhost") || LOOPBACK_NAMES.contains(domain)) {
            return true;
        }
        boolean ipv6 = domain.startsWith("[") && domain.endsWith("]");
        if (!ipv6 && !IPV4_LITERAL.matcher(domain).matches()) {
            return false;
        }
        try {
            InetAddress address = InetAddress.ofLiteral(ipv6 ? domain.substring(1, domain.length() - 1) : domain);
            return address.isLoopbackAddress() || address.isAnyLocalAddress();
        } catch (IllegalArgumentException e) {
            return false; // java.net.URI accepted it as a host, but it is no address literal
        }
    }

    /** Lower case, without surrounding blanks and without the trailing dot of a fully qualified name. */
    private static String normalisedHost(String host) {
        String domain = host == null ? "" : host.strip().toLowerCase(Locale.ROOT);
        return domain.endsWith(".") ? domain.substring(0, domain.length() - 1) : domain;
    }

    private static URI absoluteHttpUrl(String name, String value) {
        URI url;
        try {
            url = new URI(value.strip());
        } catch (URISyntaxException e) {
            url = null; // the value is not named: it may hold a credential
        }
        if (url == null
                || !("http".equalsIgnoreCase(url.getScheme()) || "https".equalsIgnoreCase(url.getScheme()))
                || url.isOpaque()
                || url.getHost() == null
                || url.getPort() > 65535
                || url.getRawUserInfo() != null) {
            throw new IllegalStateException("The production profile needs " + name
                    + " to be an absolute http or https URL with a host, a valid port and without user info");
        }
        return url;
    }

    private static boolean isHttps(String url) {
        return url.strip().regionMatches(true, 0, "https://", 0, "https://".length());
    }
}
