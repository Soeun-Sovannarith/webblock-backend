package org.kshrd.webblock.project.common.infrastructure.config;

import java.io.DataInput;
import java.io.InputStream;
import java.io.Reader;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.jackson.autoconfigure.JsonFactoryBuilderCustomizer;
import org.springframework.boot.jackson.autoconfigure.JsonMapperBuilderCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import tools.jackson.core.JsonParser;
import tools.jackson.core.JsonToken;
import tools.jackson.core.ObjectReadContext;
import tools.jackson.core.SerializableString;
import tools.jackson.core.exc.StreamReadException;
import tools.jackson.core.io.IOContext;
import tools.jackson.core.json.JsonFactory;
import tools.jackson.core.json.JsonFactoryBuilder;
import tools.jackson.core.sym.PropertyNameMatcher;
import tools.jackson.core.util.JsonParserDelegate;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.cfg.CoercionAction;
import tools.jackson.databind.cfg.CoercionInputShape;
import tools.jackson.databind.cfg.EnumFeature;
import tools.jackson.databind.type.LogicalType;

/**
 * Strict JSON input for Spring Boot's own {@code JsonMapper} (Spring MVC and every bean that injects it): unknown
 * properties, scalar coercion, numbers for enums and null primitives are refused. Messaging builds its own mappers.
 *
 * <p>The JSON factory also refuses U+0000 and unpaired UTF-16 surrogates in any string value or object key, at any depth
 * and in tree-model reads too: PostgreSQL rejects U+0000 in text and jsonb, and pgjdbc would silently store a lone
 * surrogate as '?'. The refusal is a parse error, so the request answers 400.
 */
@Configuration(proxyBeanMethods = false)
public class JacksonConfig {

    @Bean
    JsonMapperBuilderCustomizer strictJsonInput() {
        return builder -> builder.enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .enable(DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES)
                .enable(EnumFeature.FAIL_ON_NUMBERS_FOR_ENUMS)
                .withCoercionConfig(LogicalType.Textual, config -> {
                    config.setCoercion(CoercionInputShape.Integer, CoercionAction.Fail);
                    config.setCoercion(CoercionInputShape.Float, CoercionAction.Fail);
                    config.setCoercion(CoercionInputShape.Boolean, CoercionAction.Fail);
                })
                .withCoercionConfig(LogicalType.Integer, config -> {
                    config.setCoercion(CoercionInputShape.String, CoercionAction.Fail);
                    config.setCoercion(CoercionInputShape.Float, CoercionAction.Fail);
                })
                .withCoercionConfig(
                        LogicalType.Float, config -> config.setCoercion(CoercionInputShape.String, CoercionAction.Fail))
                .withCoercionConfig(LogicalType.Boolean, config -> {
                    config.setCoercion(CoercionInputShape.String, CoercionAction.Fail);
                    config.setCoercion(CoercionInputShape.Integer, CoercionAction.Fail);
                });
    }

    /**
     * Replaces Spring Boot's JSON factory (keeping its customizers) with one whose parsers refuse text PostgreSQL cannot
     * store, in names and values.
     */
    @Bean
    JsonFactory storableTextJsonFactory(ObjectProvider<JsonFactoryBuilderCustomizer> customizers) {
        JsonFactoryBuilder builder = JsonFactory.builder();
        customizers.orderedStream().forEach(customizer -> customizer.customize(builder));
        return new StorableTextJsonFactory(builder);
    }

    static final class StorableTextJsonFactory extends JsonFactory {

        StorableTextJsonFactory(JsonFactoryBuilder builder) {
            super(builder);
        }

        private StorableTextJsonFactory(StorableTextJsonFactory source) {
            super(source);
        }

        @Override
        public JsonFactory copy() {
            return new StorableTextJsonFactory(this);
        }

        @Override
        protected JsonParser _createParser(ObjectReadContext readCtxt, IOContext ioCtxt, InputStream in) {
            return new StorableTextParser(super._createParser(readCtxt, ioCtxt, in));
        }

        @Override
        protected JsonParser _createParser(ObjectReadContext readCtxt, IOContext ioCtxt, Reader r) {
            return new StorableTextParser(super._createParser(readCtxt, ioCtxt, r));
        }

        @Override
        protected JsonParser _createParser(
                ObjectReadContext readCtxt, IOContext ioCtxt, char[] data, int offset, int len, boolean recyclable) {
            return new StorableTextParser(super._createParser(readCtxt, ioCtxt, data, offset, len, recyclable));
        }

        @Override
        protected JsonParser _createParser(
                ObjectReadContext readCtxt, IOContext ioCtxt, byte[] data, int offset, int len) {
            return new StorableTextParser(super._createParser(readCtxt, ioCtxt, data, offset, len));
        }

        @Override
        protected JsonParser _createParser(ObjectReadContext readCtxt, IOContext ioCtxt, DataInput input) {
            return new StorableTextParser(super._createParser(readCtxt, ioCtxt, input));
        }
    }

    /** Checks every property name and string value as the parser reaches it; the message never echoes the input. */
    private static final class StorableTextParser extends JsonParserDelegate {

        StorableTextParser(JsonParser delegate) {
            super(delegate);
        }

        @Override
        public JsonToken nextToken() {
            return checked(super.nextToken());
        }

        @Override
        public JsonToken nextValue() {
            JsonToken token = nextToken();
            return token == JsonToken.PROPERTY_NAME ? nextToken() : token;
        }

        @Override
        public String nextName() {
            String name = super.nextName();
            refuseUnstorable(name);
            return name;
        }

        @Override
        public boolean nextName(SerializableString str) {
            boolean matched = super.nextName(str);
            checked(currentToken());
            return matched;
        }

        @Override
        public int nextNameMatch(PropertyNameMatcher matcher) {
            int match = super.nextNameMatch(matcher);
            checked(currentToken());
            return match;
        }

        private JsonToken checked(JsonToken token) {
            if (token == JsonToken.PROPERTY_NAME || token == JsonToken.VALUE_STRING) {
                refuseUnstorable(getString());
            }
            return token;
        }

        private void refuseUnstorable(String text) {
            if (text == null) {
                return;
            }
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                if (c == '\0') {
                    throw new StreamReadException(this, "A string contains the NUL character (U+0000)");
                }
                if (Character.isHighSurrogate(c)
                        && i + 1 < text.length()
                        && Character.isLowSurrogate(text.charAt(i + 1))) {
                    i++; // a pair: one supplementary character
                } else if (Character.isSurrogate(c)) {
                    throw new StreamReadException(this, "A string contains an unpaired UTF-16 surrogate");
                }
            }
        }
    }
}
