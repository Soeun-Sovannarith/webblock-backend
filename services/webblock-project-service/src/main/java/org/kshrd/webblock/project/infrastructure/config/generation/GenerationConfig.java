package org.kshrd.webblock.project.infrastructure.config.generation;

import java.util.Map;
import org.kshrd.webblock.project.application.generation.config.GenerationProperties;
import org.kshrd.webblock.project.common.domain.error.ConstraintErrorRegistration;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * The generation feature's settings and the database constraints its errors come from (read by GlobalExceptionHandler).
 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(GenerationProperties.class)
class GenerationConfig {

    /** Decision P6: the partial unique index that allows one request in flight per project. */
    @Bean
    ConstraintErrorRegistration generationConstraints() {
        return () -> Map.of("generation_requests_one_in_flight", ErrorCode.GENERATION_IN_PROGRESS);
    }
}
