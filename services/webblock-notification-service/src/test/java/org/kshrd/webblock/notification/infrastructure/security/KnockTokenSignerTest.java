package org.kshrd.webblock.notification.infrastructure.security;

import com.nimbusds.jose.crypto.RSASSAVerifier;
import com.nimbusds.jwt.SignedJWT;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.notification.TestProperties;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPublicKey;
import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class KnockTokenSignerTest {

    private static KeyPair keyPair() throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048);
        return generator.generateKeyPair();
    }

    private static String pem(KeyPair pair) {
        String body = Base64.getMimeEncoder(64, "\n".getBytes()).encodeToString(pair.getPrivate().getEncoded());
        return "-----BEGIN PRIVATE KEY-----\n" + body + "\n-----END PRIVATE KEY-----\n";
    }

    private static KnockTokenSigner signer(String signingKey) {
        var base = TestProperties.PROPERTIES;
        return new KnockTokenSigner(new NotificationProperties(base.workflows(), base.admins(), base.stub(),
                new NotificationProperties.Knock(signingKey, Duration.ofHours(1)), base.alertmanager()));
    }

    @Test
    @DisplayName("signsATokenForTheUserThatTheKnockPublicKeyCanVerify")
    void signsAVerifiableToken() throws Exception {
        KeyPair pair = keyPair();
        Instant now = Instant.now().truncatedTo(ChronoUnit.SECONDS);

        String token = signer(pem(pair)).sign("user-1", now, now.plusSeconds(3600));

        SignedJWT jwt = SignedJWT.parse(token);
        assertThat(jwt.verify(new RSASSAVerifier((RSAPublicKey) pair.getPublic()))).isTrue();
        assertThat(jwt.getJWTClaimsSet().getSubject()).isEqualTo("user-1");
        assertThat(jwt.getJWTClaimsSet().getExpirationTime().toInstant()).isEqualTo(now.plusSeconds(3600));
    }

    @Test
    @DisplayName("acceptsTheKeyAsBase64OfThePemText")
    void acceptsBase64OfPem() throws Exception {
        KeyPair pair = keyPair();
        String base64 = Base64.getEncoder().encodeToString(pem(pair).getBytes());
        Instant now = Instant.now();

        String token = signer(base64).sign("user-1", now, now.plusSeconds(60));

        assertThat(SignedJWT.parse(token).verify(new RSASSAVerifier((RSAPublicKey) pair.getPublic()))).isTrue();
    }

    @Test
    @DisplayName("rejectsAPkcs1KeyWithAHintToConvertIt")
    void rejectsPkcs1() {
        String pkcs1 = "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----";
        Instant now = Instant.now();

        assertThatThrownBy(() -> signer(pkcs1).sign("user-1", now, now.plusSeconds(60)))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("PKCS#8");
    }

    @Test
    @DisplayName("reportsNotConfiguredWhenTheSigningKeyIsEmpty")
    void notConfiguredWithoutKey() {
        assertThat(signer(null).isConfigured()).isFalse();
        assertThat(signer(" ").isConfigured()).isFalse();
    }
}
