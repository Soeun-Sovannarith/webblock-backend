package org.kshrd.webblock.project.infrastructure.config.payment;

import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.infrastructure.client.payment.HttpPaymentAdmissions;
import org.kshrd.webblock.project.infrastructure.standin.payment.PaymentStandIn;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;
import tools.jackson.databind.json.JsonMapper;

@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(PaymentProperties.class)
class PaymentConfig {

    @Bean
    @ConditionalOnProperty(name = "webblock.payment.mode", havingValue = "http", matchIfMissing = true)
    PaymentAdmissions httpPaymentAdmissions(PaymentProperties properties) {
        return new HttpPaymentAdmissions(properties);
    }

    /** Prototype only; the production profile never creates it, and ProfileGuard refuses it if it were present. */
    @Bean
    @Profile("prototype")
    @ConditionalOnProperty(name = "webblock.payment.mode", havingValue = "stand-in")
    PaymentAdmissions paymentStandIn(PaymentProperties properties, JsonMapper jsonMapper) {
        return new PaymentStandIn(properties.standInFile(), jsonMapper);
    }
}
