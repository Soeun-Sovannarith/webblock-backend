package org.kshrd.webblock.user.infrastructure.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

// webblock.mail.* in application.yml (the SMTP server itself is spring.mail.*).
@ConfigurationProperties(prefix = "webblock.mail")
public record MailProperties(String from) {
}
