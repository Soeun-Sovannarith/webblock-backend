package org.kshrd.webblock.project.infrastructure.config.project;

import java.util.Map;
import org.kshrd.webblock.project.application.project.config.PlatformProperties;
import org.kshrd.webblock.project.common.domain.error.ConstraintErrorRegistration;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** The project feature's settings and the database constraints its errors come from (read by GlobalExceptionHandler). */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(PlatformProperties.class)
class ProjectConfig {

    @Bean
    ConstraintErrorRegistration projectConstraints() {
        return () -> Map.of(
                "projects_pkey", ErrorCode.PROJECT_ID_CONFLICT,
                "projects_subdomain_key", ErrorCode.HOSTNAME_TAKEN);
    }
}
