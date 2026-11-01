package org.kshrd.webblock.project.application.generation.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatIllegalArgumentException;

import java.time.Duration;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.assertj.AssertableApplicationContext;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

class GenerationPropertiesTest {

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(GenerationProperties.class)
    static class Import {}

    @Test
    void bindsTheDefaults() {
        // the values application.yml carries
        new ApplicationContextRunner()
                .withUserConfiguration(Import.class)
                .withPropertyValues(
                        "webblock.generation.api-contract-version=1.0.0", "webblock.generation.admission-window=15m")
                .run((AssertableApplicationContext context) -> {
                    GenerationProperties properties = context.getBean(GenerationProperties.class);
                    assertThat(properties.apiContractVersion()).isEqualTo("1.0.0");
                    assertThat(properties.admissionWindow()).isEqualTo(Duration.ofMinutes(15));
                });
    }

    @Test
    void refusesABlankOrOverlongApiContractVersion() {
        Duration window = Duration.ofMinutes(15);
        assertThatIllegalArgumentException().isThrownBy(() -> new GenerationProperties("", window, null, null));
        assertThatIllegalArgumentException().isThrownBy(() -> new GenerationProperties("  ", window, null, null));
        assertThatIllegalArgumentException().isThrownBy(() -> new GenerationProperties(null, window, null, null));
        assertThatIllegalArgumentException()
                .isThrownBy(() -> new GenerationProperties("x".repeat(101), window, null, null));
        assertThat(new GenerationProperties("x".repeat(100), window, null, null).apiContractVersion())
                .hasSize(100);
    }

    @Test
    void refusesANonPositiveWindow() {
        assertThatIllegalArgumentException()
                .isThrownBy(() -> new GenerationProperties("1.0.0", Duration.ZERO, null, null));
        assertThatIllegalArgumentException()
                .isThrownBy(() -> new GenerationProperties("1.0.0", Duration.ofSeconds(-1), null, null));
        assertThatIllegalArgumentException().isThrownBy(() -> new GenerationProperties("1.0.0", null, null, null));
    }

    @Test
    void fr28_sweeperSettingsDefaultToFiveMinutesAndOneMinuteAndRefuseBadValues() {
        Duration window = Duration.ofMinutes(15);
        GenerationProperties defaults = new GenerationProperties("1.0.0", window, null, null);
        assertThat(defaults.stuckCaptureMargin()).isEqualTo(Duration.ofMinutes(5));
        assertThat(defaults.stuckSweepInterval()).isEqualTo(Duration.ofMinutes(1));
        assertThatIllegalArgumentException()
                .isThrownBy(() -> new GenerationProperties("1.0.0", window, Duration.ofSeconds(-1), null));
        assertThatIllegalArgumentException()
                .isThrownBy(() -> new GenerationProperties("1.0.0", window, null, Duration.ZERO));
    }
}
