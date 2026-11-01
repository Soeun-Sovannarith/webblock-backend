package org.kshrd.webblock.project.application.generation.handler;

import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import org.erdtman.jcs.JsonCanonicalizer;

/**
 * SHA-256 of the RFC 8785 (JCS) form of a JSON document. Produces the hashes behind generation
 * capture, idempotency and codegen input.
 */
public final class InputHasher {

    private InputHasher() {}

    /**
     * The UTF-8 bytes of the JCS form of {@code json}.
     *
     * @throws IllegalArgumentException when {@code json} is not valid JSON
     */
    public static byte[] canonicalize(String json) {
        try {
            return new JsonCanonicalizer(json).getEncodedUTF8();
        } catch (IOException | RuntimeException | StackOverflowError e) {
            // fixed message and no cause: the library's message can echo fragments of the captured input, and a logged
            // cause would print it (or a huge StackOverflowError trace)
            throw new IllegalArgumentException("not valid JSON");
        }
    }

    /** Lowercase hex SHA-256 (64 characters) of {@link #canonicalize(String)}. */
    public static String sha256(String json) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonicalize(json)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required by the Java platform", e);
        }
    }
}
