package org.kshrd.webblock.project.infrastructure.config.generation;

import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;

/** Turns on {@code @Scheduled}; the stuck-capture sweeper (FR-28) is the application's own scheduled job. */
@Configuration(proxyBeanMethods = false)
@EnableScheduling
class SchedulingConfig {}
