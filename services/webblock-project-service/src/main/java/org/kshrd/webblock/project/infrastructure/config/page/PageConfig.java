package org.kshrd.webblock.project.infrastructure.config.page;

import java.util.Map;
import org.kshrd.webblock.project.application.page.config.PageProperties;
import org.kshrd.webblock.project.common.domain.error.ConstraintErrorRegistration;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** The page feature's settings and the database constraints its errors come from (read by GlobalExceptionHandler). */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(PageProperties.class)
class PageConfig {

    @Bean
    ConstraintErrorRegistration pageConstraints() {
        return () -> Map.of(
                "pages_project_path_key", ErrorCode.PAGE_PATH_TAKEN,
                "pages_path_check", ErrorCode.PAGE_PATH_INVALID);
    }
}
