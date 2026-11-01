package org.kshrd.webblock.project.infrastructure.config.payment;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.infrastructure.client.payment.HttpPaymentAdmissions;
import org.kshrd.webblock.project.infrastructure.standin.payment.PaymentStandIn;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import tools.jackson.databind.json.JsonMapper;

class PaymentConfigTest {

    private final ApplicationContextRunner runner = new ApplicationContextRunner()
            .withUserConfiguration(PaymentConfig.class)
            .withBean(JsonMapper.class, () -> JsonMapper.builder().build())
            .withPropertyValues("webblock.payment.base-url=http://payment.invalid", "webblock.payment.timeout=2s");

    @Test
    void theDefaultIsTheHttpClientWithTwoSecondTimeout() {
        runner.run(context -> {
            assertThat(context).getBean(PaymentAdmissions.class).isInstanceOf(HttpPaymentAdmissions.class);
            assertThat(context.getBean(PaymentProperties.class).timeout()).hasSeconds(2);
        });
    }

    @Test
    void theStandInNeedsThePrototypeProfile() {
        runner.withPropertyValues("spring.profiles.active=prototype", "webblock.payment.mode=stand-in")
                .run(context ->
                        assertThat(context).getBean(PaymentAdmissions.class).isInstanceOf(PaymentStandIn.class));
        runner.withPropertyValues("spring.profiles.active=production", "webblock.payment.mode=stand-in")
                .run(context -> assertThat(context).doesNotHaveBean(PaymentAdmissions.class));
    }
}
