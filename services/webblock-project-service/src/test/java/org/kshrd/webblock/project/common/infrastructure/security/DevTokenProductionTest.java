package org.kshrd.webblock.project.common.infrastructure.security;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.kshrd.webblock.project.common.infrastructure.config.ProfileGuard;
import org.kshrd.webblock.project.common.infrastructure.config.StandIn;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import tools.jackson.databind.json.JsonMapper;

/** The dev-token stand-in never exists under the production profile, whatever the identity mode says. */
class DevTokenProductionTest {

    @TempDir
    Path dir;

    private ApplicationContextRunner production() throws Exception {
        Path file = Files.writeString(
                dir.resolve("tokens.json"),
                "{\"tokens\":[{\"token\":\"%s\",\"subject\":\"alice\",\"userId\":\"%s\",\"scopes\":[]}]}"
                        .formatted(DevTokenIntrospectorTest.USER_TOKEN, DevTokenIntrospectorTest.USER_ID));
        return new ApplicationContextRunner()
                .withBean(ProfileGuard.class)
                .withUserConfiguration(DevTokenIntrospector.class)
                .withBean(JsonMapper.class, () -> JsonMapper.builder().build())
                .withPropertyValues(
                        "spring.profiles.active=production",
                        "spring.flyway.enabled=false",
                        "webblock.catalog.migrations.enabled=false",
                        "spring.security.oauth2.resourceserver.jwt.issuer-uri=https://id.example.test/realms/webblock",
                        "webblock.security.user-id-claim=user_id",
                        "webblock.payment.base-url=https://payment.webblock.example",
                        "webblock.platform.site-domain=sites.webblock.example",
                        "webblock.platform.reserved-subdomains=www,auth",
                        "namastack.outbox.jdbc.schema-initialization.enabled=false",
                        "springdoc.api-docs.enabled=false",
                        "springdoc.swagger-ui.enabled=false",
                        "webblock.security.dev-tokens-file=" + file);
    }

    @Test
    void devTokensModeInProductionFailsStartupAndCreatesNoStandIn() throws Exception {
        java.util.List<String> standIns = new java.util.ArrayList<>();
        production()
                .withInitializer(ctx -> ctx.addBeanFactoryPostProcessor(factory -> standIns.addAll(
                        java.util.Arrays.asList(factory.getBeanNamesForType(StandIn.class, true, false)))))
                .withPropertyValues("webblock.security.identity-mode=dev-tokens")
                .run(context -> {
                    assertThat(context).hasFailed();
                    assertThat(context.getStartupFailure()).hasMessageContaining("identity-mode=jwt");
                });
        assertThat(standIns).isEmpty();
    }

    @Test
    void defaultModeInProductionHasNoDevTokenIntrospector() throws Exception {
        production().run(context -> {
            assertThat(context).hasNotFailed();
            assertThat(context).doesNotHaveBean(DevTokenIntrospector.class);
            assertThat(context.getBeanNamesForType(StandIn.class)).isEmpty();
        });
        production()
                .withPropertyValues("webblock.security.identity-mode=jwt")
                .run(context -> assertThat(context).doesNotHaveBean(DevTokenIntrospector.class));
    }
}
