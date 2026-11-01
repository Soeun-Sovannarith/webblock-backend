package org.kshrd.webblock.project.support;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.PlainJWT;
import com.nimbusds.jwt.SignedJWT;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import java.util.List;

/** A local OpenID issuer for tests: OIDC discovery and a JWKS over HTTP, and RS256 tokens it signs. */
public final class LocalIssuer implements AutoCloseable {

    private final HttpServer server;
    private final RSAKey key;
    private final RSAKey foreignKey;

    public LocalIssuer() {
        try {
            key = new RSAKeyGenerator(2048).keyID("k1").generate();
            foreignKey = new RSAKeyGenerator(2048).keyID("k1").generate();
            server = HttpServer.create(new InetSocketAddress(java.net.InetAddress.getLoopbackAddress(), 0), 0);
        } catch (JOSEException | IOException e) {
            throw new IllegalStateException(e);
        }
        server.createContext(
                "/.well-known/openid-configuration",
                exchange -> respond(exchange.getResponseBody(), exchange, """
                {"issuer":"%s","jwks_uri":"%s/jwks","id_token_signing_alg_values_supported":["RS256"],
                 "subject_types_supported":["public"],"response_types_supported":["code"]}""".formatted(issuer(), issuer())));
        server.createContext(
                "/jwks",
                exchange -> respond(exchange.getResponseBody(), exchange, new JWKSet(key.toPublicJWK()).toString()));
        server.start();
    }

    private static void respond(OutputStream body, com.sun.net.httpserver.HttpExchange exchange, String json)
            throws IOException {
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(200, bytes.length);
        try (body) {
            body.write(bytes);
        }
    }

    public String issuer() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    /**
     * Claims of a Keycloak access token ({@code typ} Bearer) valid for audience {@code project} for five minutes; tests
     * add or override claims.
     */
    public JWTClaimsSet.Builder claims() {
        Instant now = Instant.now();
        return new JWTClaimsSet.Builder()
                .claim("typ", "Bearer")
                .issuer(issuer())
                .audience(List.of("project"))
                .subject("subject-1")
                .issueTime(Date.from(now))
                .expirationTime(Date.from(now.plusSeconds(300)));
    }

    public String token(JWTClaimsSet claims) {
        return sign(claims, key);
    }

    /** Signed with a key the JWKS does not publish, under the same key ID. */
    public String foreignToken(JWTClaimsSet claims) {
        return sign(claims, foreignKey);
    }

    /** The classic algorithm-confusion token: HS256, with the RSA public key's encoded bytes as the HMAC secret. */
    public String hmacTokenWithPublicKeyAsSecret(JWTClaimsSet claims) {
        try {
            SignedJWT jwt = new SignedJWT(
                    new JWSHeader.Builder(JWSAlgorithm.HS256)
                            .keyID(key.getKeyID())
                            .build(),
                    claims);
            byte[] secret = key.toRSAPublicKey().getEncoded();
            jwt.sign(new com.nimbusds.jose.crypto.MACSigner(
                    java.util.Arrays.copyOf(secret, Math.max(secret.length, 32))));
            return jwt.serialize();
        } catch (JOSEException e) {
            throw new IllegalStateException(e);
        }
    }

    public String unsignedToken(JWTClaimsSet claims) {
        return new PlainJWT(claims).serialize();
    }

    private static String sign(JWTClaimsSet claims, RSAKey signingKey) {
        try {
            SignedJWT jwt = new SignedJWT(
                    new JWSHeader.Builder(JWSAlgorithm.RS256)
                            .keyID(signingKey.getKeyID())
                            .build(),
                    claims);
            jwt.sign(new RSASSASigner(signingKey));
            return jwt.serialize();
        } catch (JOSEException e) {
            throw new IllegalStateException(e);
        }
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
