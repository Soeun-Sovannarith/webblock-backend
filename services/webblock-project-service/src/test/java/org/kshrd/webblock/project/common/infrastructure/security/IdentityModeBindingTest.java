package org.kshrd.webblock.project.common.infrastructure.security;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.common.infrastructure.config.StandIn;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

class IdentityModeBindingTest {

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(SecurityProperties.class)
    static class Props {}

    private final ApplicationContextRunner runner = new ApplicationContextRunner().withUserConfiguration(Props.class);

    @Test
    void absentMeansJwt() {
        runner.run(
                context -> assertThat(context.getBean(SecurityProperties.class).identityMode())
                        .isEqualTo(SecurityProperties.IdentityMode.JWT));
    }

    @Test
    void bothKnownModesBind() {
        runner.withPropertyValues("webblock.security.identity-mode=dev-tokens")
                .run(context -> assertThat(
                                context.getBean(SecurityProperties.class).identityMode())
                        .isEqualTo(SecurityProperties.IdentityMode.DEV_TOKENS));
        runner.withPropertyValues("webblock.security.identity-mode=jwt")
                .run(context -> assertThat(
                                context.getBean(SecurityProperties.class).identityMode())
                        .isEqualTo(SecurityProperties.IdentityMode.JWT));
    }

    @Test
    void anUnknownModeFailsTheStartup() {
        runner.withPropertyValues("webblock.security.identity-mode=bogus")
                .run(context -> assertThat(context).hasFailed());
    }

    /**
     * Boot binds an empty value like an absent one, so the default (jwt) applies. Safe: jwt is the strict mode, and
     * ProfileGuard reads the raw property, which for production must be exactly jwt or absent.
     */
    @Test
    void anEmptyModeBindsAsTheJwtDefault() {
        runner.withPropertyValues("webblock.security.identity-mode=")
                .run(context -> assertThat(
                                context.getBean(SecurityProperties.class).identityMode())
                        .isEqualTo(SecurityProperties.IdentityMode.JWT));
    }

    /** The dev-token stand-in exists only for dev-tokens: never when the mode is jwt or the property is missing. */
    @Test
    void theDevTokenStandInNeedsExactlyDevTokens() {
        ApplicationContextRunner devRunner = new ApplicationContextRunner()
                .withUserConfiguration(DevTokenIntrospector.class)
                .withBean(
                        tools.jackson.databind.json.JsonMapper.class,
                        () -> tools.jackson.databind.json.JsonMapper.builder().build())
                .withPropertyValues("spring.profiles.active=prototype", "webblock.security.user-id-claim=user_id");
        devRunner.run(context -> assertThat(context).doesNotHaveBean(StandIn.class));
        devRunner
                .withPropertyValues("webblock.security.identity-mode=jwt")
                .run(context -> assertThat(context).doesNotHaveBean(StandIn.class));
        devRunner
                .withPropertyValues("webblock.security.identity-mode=anything-else")
                .run(context -> assertThat(context).doesNotHaveBean(StandIn.class));
    }
}
