package org.kshrd.webblock.notification.infrastructure.security;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import org.kshrd.webblock.notification.infrastructure.config.NotificationProperties;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.interfaces.RSAPrivateKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.time.Instant;
import java.util.Base64;
import java.util.Date;

/**
 * Signs the short-lived user token the browser gives to the Knock notification bell.
 * The signing key comes from the env var behind "webblock.notification.knock.signing-key": a PKCS#8 private key,
 * either the PEM text or the base64 of that PEM text. A "BEGIN RSA PRIVATE KEY" (PKCS#1) key must be converted first:
 * {@code openssl pkcs8 -topk8 -nocrypt -in key.pem -out key-pkcs8.pem}.
 */
@Component
public class KnockTokenSigner {

    private final NotificationProperties.Knock knock;
    private volatile RSAPrivateKey privateKey;

    public KnockTokenSigner(NotificationProperties properties) {
        this.knock = properties.knock();
    }

    public boolean isConfigured() {
        return knock.signingKey() != null && !knock.signingKey().isBlank();
    }

    public String sign(String userId, Instant issuedAt, Instant expiresAt) {
        if (!isConfigured()) {
            throw new IllegalStateException("Knock signing key is not configured");
        }
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .subject(userId)
                .issueTime(Date.from(issuedAt))
                .expirationTime(Date.from(expiresAt))
                .build();
        SignedJWT jwt = new SignedJWT(new JWSHeader(JWSAlgorithm.RS256), claims);
        try {
            jwt.sign(new RSASSASigner(key()));
        } catch (JOSEException e) {
            throw new IllegalStateException("Could not sign the Knock user token", e);
        }
        return jwt.serialize();
    }

    private RSAPrivateKey key() {
        RSAPrivateKey key = privateKey;
        if (key == null) {
            key = parse(knock.signingKey().trim());
            privateKey = key;
        }
        return key;
    }

    static RSAPrivateKey parse(String configured) {
        String pem = configured.contains("-----BEGIN")
                ? configured
                : new String(Base64.getMimeDecoder().decode(configured), StandardCharsets.UTF_8);
        if (pem.contains("BEGIN RSA PRIVATE KEY")) {
            throw new IllegalStateException("Knock signing key is PKCS#1; convert it to PKCS#8 (openssl pkcs8 -topk8 -nocrypt)");
        }
        String body = pem.replaceAll("-----(BEGIN|END) [A-Z ]+-----", "").replaceAll("\\s", "");
        try {
            byte[] der = Base64.getDecoder().decode(body);
            return (RSAPrivateKey) KeyFactory.getInstance("RSA").generatePrivate(new PKCS8EncodedKeySpec(der));
        } catch (GeneralSecurityException | IllegalArgumentException e) {
            throw new IllegalStateException("Knock signing key is not a valid PKCS#8 RSA private key", e);
        }
    }
}
