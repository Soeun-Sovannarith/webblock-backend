package org.kshrd.webblock.notification.infrastructure.config;

import app.knock.api.client.KnockClient;
import app.knock.api.client.okhttp.KnockOkHttpClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.StringUtils;

/**
 * Knock Client configuration bean.
 * Reads the secret API key from application.properties or environment variable: ${knock.api-key}
 */
@Configuration
public class KnockConfig {

    private static final Logger log = LoggerFactory.getLogger(KnockConfig.class);

    @Value("${knock.api-key}")
    private String apiKey;

    @Bean
    public KnockClient knockClient() {
        if (!StringUtils.hasText(apiKey) || apiKey.startsWith("sk_test_placeholder")) {
            log.warn("Knock API Key is missing or using placeholder ('knock.api-key'). Outgoing notifications will fail until configured.");
        }

        log.info("Initializing KnockClient with configured API key.");
        return KnockOkHttpClient.builder()
                .apiKey(apiKey)
                .build();
    }
}
