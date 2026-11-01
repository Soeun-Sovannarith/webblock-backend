package org.kshrd.webblock.project.application.page.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

class PagePropertiesTest {

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(PageProperties.class)
    static class Config {}

    private final ApplicationContextRunner runner = new ApplicationContextRunner().withUserConfiguration(Config.class);

    @Test
    @DisplayName("FR-18: webblock.pages.max-puck-tree-bytes binds; the page count is no setting any more")
    void fr18_theTreeLimitBinds() {
        runner.withPropertyValues("webblock.pages.max-puck-tree-bytes=100")
                .run(context -> assertThat(context.getBean(PageProperties.class).maxPuckTreeBytes())
                        .isEqualTo(100));
    }
}
