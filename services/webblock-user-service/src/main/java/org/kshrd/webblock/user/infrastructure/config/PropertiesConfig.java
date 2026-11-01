package org.kshrd.webblock.user.infrastructure.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

// Turns the webblock.* settings in application.yml into the records above.
@Configuration
@EnableConfigurationProperties({OtpProperties.class, KeycloakAdminProperties.class, MailProperties.class})
public class PropertiesConfig {
}
