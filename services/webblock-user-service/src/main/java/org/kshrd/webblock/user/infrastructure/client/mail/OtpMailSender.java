package org.kshrd.webblock.user.infrastructure.client.mail;

import org.kshrd.webblock.user.common.domain.ApiException;
import org.kshrd.webblock.user.common.domain.ErrorCode;
import org.kshrd.webblock.user.domain.otp.model.OtpPurpose;
import org.kshrd.webblock.user.infrastructure.config.MailProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.mail.MailException;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.stereotype.Component;

import java.time.Duration;

// Sends the 6-digit code by email (SMTP from spring.mail.*; Mailpit on localhost:1025 in dev).
// Never logs the code or the email address.
@Component
public class OtpMailSender {

    private static final Logger log = LoggerFactory.getLogger(OtpMailSender.class);

    private final JavaMailSender mailSender;
    private final MailProperties mail;

    public OtpMailSender(JavaMailSender mailSender, MailProperties mail) {
        this.mailSender = mailSender;
        this.mail = mail;
    }

    public void send(String toEmail, String code, OtpPurpose purpose, Duration validFor) {
        SimpleMailMessage message = new SimpleMailMessage();
        message.setFrom(mail.from());
        message.setTo(toEmail);
        if (purpose == OtpPurpose.REGISTER) {
            message.setSubject("Your WebBlock verification code");
            message.setText("Your WebBlock verification code is: " + code + "\n\n"
                    + "It works for " + validFor.toMinutes() + " minutes.\n"
                    + "If you did not try to create a WebBlock account, you can ignore this email.");
        } else {
            message.setSubject("Your WebBlock password reset code");
            message.setText("Your WebBlock password reset code is: " + code + "\n\n"
                    + "It works for " + validFor.toMinutes() + " minutes.\n"
                    + "If you did not ask to reset your password, you can ignore this email.");
        }
        try {
            mailSender.send(message);
            log.info("OTP email sent, purpose={}", purpose);
        } catch (MailException e) {
            log.warn("OTP email could not be sent, purpose={}, reason={}", purpose, e.getClass().getSimpleName());
            throw new ApiException(ErrorCode.DEPENDENCY_UNAVAILABLE);
        }
    }
}
