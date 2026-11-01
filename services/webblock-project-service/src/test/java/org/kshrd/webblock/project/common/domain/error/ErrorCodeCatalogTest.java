package org.kshrd.webblock.project.common.domain.error;

import static org.assertj.core.api.Assertions.assertThat;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

class ErrorCodeCatalogTest {

    @Test
    void everyValidationCodesConstantNamesAFieldErrorCode() throws IllegalAccessException {
        for (Field field : validationCodeConstants()) {
            String value = (String) field.get(null);
            assertThat(value).as(field.getName()).isEqualTo(field.getName());
            assertThat(ErrorCode.fromCode(value))
                    .as(field.getName())
                    .hasValueSatisfying(code -> assertThat(code.isFieldCode()).isTrue());
        }
    }

    @Test
    void everyFieldErrorCodeHasAValidationCodesConstant() {
        Set<String> constants =
                validationCodeConstants().stream().map(Field::getName).collect(Collectors.toSet());
        List<String> fieldCodes = Arrays.stream(ErrorCode.values())
                .filter(ErrorCode::isFieldCode)
                .map(ErrorCode::code)
                .toList();
        assertThat(constants).containsAll(fieldCodes);
    }

    @Test
    void requestLevelValidationCodesAreNotFieldCodes() {
        assertThat(ErrorCode.VALIDATION_FAILED.isFieldCode()).isFalse();
        assertThat(ErrorCode.MALFORMED_REQUEST.isFieldCode()).isFalse();
        assertThat(ErrorCode.CONCURRENT_UPDATE.isFieldCode()).isFalse();
    }

    @Test
    void everyMessageIsASentence() {
        for (ErrorCode code : ErrorCode.values()) {
            assertThat(code.message()).as(code.code()).isNotBlank().endsWith(".");
        }
    }

    @Test
    void fromCodeFindsKnownCodesOnly() {
        assertThat(ErrorCode.fromCode("PAGE_PATH_INVALID")).contains(ErrorCode.PAGE_PATH_INVALID);
        assertThat(ErrorCode.fromCode("page_path_invalid")).isEmpty();
        assertThat(ErrorCode.fromCode("must not be blank")).isEmpty();
        assertThat(ErrorCode.fromCode(null)).isEmpty();
    }

    @Test
    void categoriesCarryTheirHttpStatus() {
        assertThat(ErrorCode.VALIDATION_FAILED.category().httpStatus()).isEqualTo(400);
        assertThat(ErrorCode.UNAUTHENTICATED.category().httpStatus()).isEqualTo(401);
        assertThat(ErrorCode.ACCESS_DENIED.category().httpStatus()).isEqualTo(403);
        assertThat(ErrorCode.ROUTE_NOT_FOUND.category().httpStatus()).isEqualTo(404);
        assertThat(ErrorCode.METHOD_NOT_ALLOWED.category().httpStatus()).isEqualTo(405);
        assertThat(ErrorCode.NOT_ACCEPTABLE.category().httpStatus()).isEqualTo(406);
        assertThat(ErrorCode.CONCURRENT_UPDATE.category().httpStatus()).isEqualTo(409);
        assertThat(ErrorCode.PAYLOAD_TOO_LARGE.category().httpStatus()).isEqualTo(413);
        assertThat(ErrorCode.UNSUPPORTED_MEDIA_TYPE.category().httpStatus()).isEqualTo(415);
        assertThat(ErrorCategory.BUSINESS_RULE.httpStatus()).isEqualTo(422);
        assertThat(ErrorCode.INTERNAL_ERROR.category().httpStatus()).isEqualTo(500);
        assertThat(ErrorCode.DEPENDENCY_UNAVAILABLE.category().httpStatus()).isEqualTo(503);
    }

    private static List<Field> validationCodeConstants() {
        return Arrays.stream(ValidationCodes.class.getDeclaredFields())
                .filter(f -> Modifier.isStatic(f.getModifiers()) && Modifier.isFinal(f.getModifiers()))
                .filter(f -> f.getType() == String.class)
                .toList();
    }
}
