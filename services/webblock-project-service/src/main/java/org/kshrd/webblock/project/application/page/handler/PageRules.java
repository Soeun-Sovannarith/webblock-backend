package org.kshrd.webblock.project.application.page.handler;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.kshrd.webblock.project.application.page.config.PageProperties;
import org.kshrd.webblock.project.application.page.model.NewPage;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.ErrorDetail;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.PagePaths;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * What a stored page must be, without the database: the editor's saves and the wizard's template pages obey it at
 * request time, and the site templates are checked against it at start-up. A refusal is a WebBlockException with the
 * code the API answers.
 */
@Component
public class PageRules {

    /** The lengths of the columns name, seo_title and seo_description of project.pages. */
    public static final int MAX_NAME_LENGTH = 255;

    public static final int MAX_SEO_TITLE_LENGTH = 255;
    public static final int MAX_SEO_DESCRIPTION_LENGTH = 500;

    /** The largest sort order a page may have; the next page's sort order stops there. */
    public static final int MAX_SORT_ORDER = 1000;

    /**
     * The deepest Puck tree accepted, the tree object itself counting as 1. Jackson reads and writes at most 500
     * levels, and every response embeds the tree a few levels deeper (envelope, data, pages, page), so a stored tree
     * can always be served. Puck trees are shallow; this only stops hostile input before the validator walks it.
     */
    public static final int MAX_TREE_DEPTH = 100;

    /**
     * The longest number accepted, in characters of its plain decimal form. PostgreSQL keeps a jsonb number as numeric
     * and prints it without an exponent, so 1e308 (7 bytes as Jackson writes it) would be stored as 309 digits. With
     * this bound the stored text of a tree is at most about 5 times webblock.pages.max-puck-tree-bytes (1e31: "1.0E31,"
     * becomes "10000000000000000000000000000000, "), which PageStoredSizeTest asserts on PostgreSQL.
     */
    private static final int MAX_NUMBER_CHARS = 32;

    private final PuckValidator puck;
    private final PageProperties properties;
    private final JsonMapper json;

    public PageRules(PuckValidator puck, PageProperties properties, JsonMapper json) {
        this.puck = puck;
        this.properties = properties;
        this.json = json;
    }

    /**
     * A whole page as the wizard inserts it from a template: name, path, SEO lengths, sort order and tree. Answers the
     * tree as the JSON text to store.
     */
    public String checkedPage(String componentCatalogVersion, NewPage page) {
        List<ErrorDetail> problems = new ArrayList<>();
        if (page.name() == null || page.name().isBlank()) {
            problems.add(ErrorDetail.of("name", ErrorCode.FIELD_REQUIRED));
        } else if (page.name().length() > MAX_NAME_LENGTH) {
            problems.add(ErrorDetail.of("name", ErrorCode.FIELD_LENGTH_INVALID));
        }
        if (!PagePaths.isValid(page.path())) {
            problems.add(ErrorDetail.of("path", ErrorCode.PAGE_PATH_INVALID));
        }
        if (page.seoTitle() != null && page.seoTitle().length() > MAX_SEO_TITLE_LENGTH) {
            problems.add(ErrorDetail.of("seoTitle", ErrorCode.FIELD_LENGTH_INVALID));
        }
        if (page.seoDescription() != null && page.seoDescription().length() > MAX_SEO_DESCRIPTION_LENGTH) {
            problems.add(ErrorDetail.of("seoDescription", ErrorCode.FIELD_LENGTH_INVALID));
        }
        if (!problems.isEmpty()) {
            throw new WebBlockException(ErrorCode.VALIDATION_FAILED, problems);
        }
        checkSortOrder(page.sortOrder());
        return checkedTree(componentCatalogVersion, page.puckTree());
    }

    public void checkSortOrder(int sortOrder) {
        if (sortOrder < 0 || sortOrder > MAX_SORT_ORDER) {
            throw new WebBlockException(
                    ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("sortOrder", ErrorCode.FIELD_OUT_OF_RANGE)));
        }
    }

    /**
     * The tree as the JSON text to store, or a refusal: missing (PuckValidator never sees null), not storable as jsonb
     * as it is (see {@link #unstorable}), larger than webblock.pages.max-puck-tree-bytes, or not matching the component
     * catalog. U+0000, which jsonb cannot hold either, never gets here: the application's JSON reader refuses it.
     */
    public String checkedTree(String componentCatalogVersion, JsonNode tree) {
        if (tree == null || tree.isNull() || tree.isMissingNode()) {
            throw invalid(ErrorCode.FIELD_REQUIRED);
        }
        if (unstorable(tree)) {
            throw invalid(ErrorCode.PUCK_TREE_INVALID);
        }
        String text = json.writeValueAsString(tree);
        if (text.getBytes(StandardCharsets.UTF_8).length > properties.maxPuckTreeBytes()) {
            throw new WebBlockException(ErrorCode.PAYLOAD_TOO_LARGE);
        }
        List<ErrorDetail> problems = puck.validate(componentCatalogVersion, tree);
        if (!problems.isEmpty()) {
            throw new WebBlockException(ErrorCode.VALIDATION_FAILED, problems);
        }
        return text;
    }

    private static WebBlockException invalid(ErrorCode fieldCode) {
        return new WebBlockException(ErrorCode.VALIDATION_FAILED, List.of(ErrorDetail.of("puckTree", fieldCode)));
    }

    /**
     * Whether the tree cannot be stored and served back as it is: nested deeper than MAX_TREE_DEPTH, or a number that
     * is not finite (jsonb would store the string "Infinity") or whose plain form is longer than MAX_NUMBER_CHARS (jsonb
     * would store every digit). Walks one level at a time, without recursion, and stops at the first problem.
     */
    private static boolean unstorable(JsonNode tree) {
        List<JsonNode> level = List.of(tree);
        for (int depth = 1; !level.isEmpty(); depth++) {
            List<JsonNode> next = new ArrayList<>();
            for (JsonNode node : level) {
                if (!node.isContainer()) {
                    if (!storableNumber(node)) {
                        return true;
                    }
                } else if (depth > MAX_TREE_DEPTH) {
                    return true;
                } else {
                    next.addAll(node.values());
                }
            }
            level = next;
        }
        return false;
    }

    private static boolean storableNumber(JsonNode value) {
        if (!value.isFloatingPointNumber()) {
            return true; // integers keep their digits, at most 1000 (Jackson's number length limit)
        }
        if (!Double.isFinite(value.doubleValue())) {
            return false;
        }
        BigDecimal number = value.decimalValue();
        // The length of number.toPlainString(), computed without building it.
        long digits = number.scale() <= 0
                ? (long) number.precision() - number.scale()
                : Math.max(number.precision(), number.scale() + 1L);
        long plain = digits + (number.signum() < 0 ? 1 : 0) + (number.scale() > 0 ? 1 : 0);
        return plain <= MAX_NUMBER_CHARS;
    }
}
