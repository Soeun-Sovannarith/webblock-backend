package org.kshrd.webblock.project.support;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;

@TestConfiguration(proxyBeanMethods = false)
public class TestClockConfig {

    @Bean
    MutableClock clock() {
        return new MutableClock();
    }
}
