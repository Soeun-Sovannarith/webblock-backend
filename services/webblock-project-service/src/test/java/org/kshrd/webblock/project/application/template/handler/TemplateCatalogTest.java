package org.kshrd.webblock.project.application.template.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.kshrd.webblock.project.application.page.config.PageProperties;
import org.kshrd.webblock.project.application.page.handler.PageRules;
import org.kshrd.webblock.project.application.page.handler.PuckValidator;
import org.kshrd.webblock.project.application.template.model.SiteTemplate;
import org.kshrd.webblock.project.common.infrastructure.config.JacksonConfig;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.jackson.autoconfigure.JacksonAutoConfiguration;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.core.io.ClassPathResource;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

class TemplateCatalogTest {

    static final JsonMapper JSON = JsonMapper.builder().build();
    static final int MAX_TREE_BYTES = 1_048_576;

    private static TemplateCatalog load(String file) throws Exception {
        return load(JSON, file, MAX_TREE_BYTES);
    }

    private static TemplateCatalog load(JsonMapper json, String file, int maxTreeBytes) throws Exception {
        PuckValidator validator = new PuckValidator(json);
        PageProperties properties = new PageProperties(maxTreeBytes);
        return new TemplateCatalog(
                json, validator, new PageRules(validator, properties, json), new ClassPathResource(file));
    }

    @Test
    @DisplayName("FR-18: a template with more than one page stops the start-up (a website has one page)")
    void fr18_aTwoPageTemplateIsRefused() {
        assertThatThrownBy(() -> load("site-templates-test/five-pages.json"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("needs exactly 1 page");
    }

    @Test
    void theBundledTemplatesAreValidAndBlankAppliesToEveryBusinessType() throws Exception {
        TemplateCatalog bundled = load(TemplateCatalog.LOCATION);
        assertThat(bundled.find("blank", "anything_shop")).isPresent();
        assertThat(bundled.forBusinessType("anything_shop")).isNotEmpty();
    }

    @Test
    @DisplayName("FR-14.3: a style key gives its template's thumbnail; an unknown key or null gives none")
    void fr14_3_aStyleKeyMapsToItsThumbnail() throws Exception {
        TemplateCatalog bundled = load(TemplateCatalog.LOCATION);
        assertThat(bundled.thumbnailForStyleKey("sweet-pastel")).contains("/mock/templates/sweet-pastel.png");
        assertThat(bundled.thumbnailForStyleKey("clean-mono")).contains("/mock/templates/clean-mono.png");
        assertThat(bundled.thumbnailForStyleKey("no-such-style")).isEmpty();
        assertThat(bundled.thumbnailForStyleKey(null)).isEmpty();
    }

    @Test
    @DisplayName("FR-9: the four style templates load, apply to every business type and carry their display fields")
    void fr9_theFourStyleTemplatesLoadForEveryBusinessType() throws Exception {
        TemplateCatalog bundled = load(TemplateCatalog.LOCATION);
        assertThat(bundled.all())
                .extracting(SiteTemplate::templateCode)
                .containsExactlyInAnyOrder("everyday_store", "sweet_pastel", "warm_bold", "clean_mono", "blank");
        assertThat(bundled.forBusinessType("food_shop")).hasSize(5);
        SiteTemplate sweet = bundled.find("sweet_pastel", "tech_shop").orElseThrow();
        assertThat(sweet.styleKey()).isEqualTo("sweet-pastel");
        assertThat(sweet.thumbnailUrl()).isEqualTo("/mock/templates/sweet-pastel.png");
        assertThat(sweet.badge()).isNull();
        assertThat(bundled.find("everyday_store", "food_shop").orElseThrow().badge())
                .isEqualTo("Popular");
        assertThat(sweet.pages()).singleElement().satisfies(page -> {
            assertThat(page.path()).isEqualTo("/");
            assertThat(page.puckTree().at("/root/props/templateId").asString()).isEqualTo("sweet-pastel");
            assertThat(page.puckTree().get("content")).hasSize(6);
        });
    }

    @Test
    void filtersByBusinessType() throws Exception {
        TemplateCatalog catalog = load("site-templates-test/valid.json");
        assertThat(catalog.forBusinessType("clothing_shop"))
                .extracting(SiteTemplate::templateCode)
                .containsExactlyInAnyOrder("shop_basic", "any_blank");
        assertThat(catalog.forBusinessType("food_shop"))
                .extracting(SiteTemplate::templateCode)
                .containsExactly("any_blank");
        assertThat(catalog.find("shop_basic", "food_shop")).isEmpty();
        assertThat(catalog.find("missing", "clothing_shop")).isEmpty();
    }

    /**
     * ARCH-L-4: a template breaks start-up for anything the page rules would refuse when the wizard inserts its pages,
     * after Payment has admitted the website.
     */
    @ParameterizedTest(name = "{0}")
    @CsvSource(
            delimiter = '|',
            value = {
                "five-pages.json|needs exactly 1 page",
                "invalid-tree.json|VALIDATION_FAILED at puckTree/content/0/props/text",
                "no-home.json|no home page",
                "invalid-path.json|VALIDATION_FAILED at path PAGE_PATH_INVALID",
                "duplicate-code.json|repeated",
                "unknown-catalog.json|unknown component catalog",
                "no-name.json|VALIDATION_FAILED at name FIELD_REQUIRED",
                "long-name.json|VALIDATION_FAILED at name FIELD_LENGTH_INVALID",
                "long-seo-title.json|VALIDATION_FAILED at seoTitle FIELD_LENGTH_INVALID",
                "long-seo-description.json|VALIDATION_FAILED at seoDescription FIELD_LENGTH_INVALID",
                "sort-order.json|VALIDATION_FAILED at sortOrder FIELD_OUT_OF_RANGE",
                "negative-sort-order.json|VALIDATION_FAILED at sortOrder FIELD_OUT_OF_RANGE",
                "deep-tree.json|VALIDATION_FAILED at puckTree PUCK_TREE_INVALID",
                "long-number.json|VALIDATION_FAILED at puckTree PUCK_TREE_INVALID"
            })
    void refusesAnInvalidFileWithAnExplainingMessage(String file, String message) {
        assertThatThrownBy(() -> load("site-templates-test/" + file))
                .as(file)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining(message);
    }

    @Test
    void aTreeLargerThanAStoredPageMayBeIsRefused() {
        assertThatThrownBy(() -> load(JSON, "site-templates-test/valid.json", 64))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("page 0 breaks the page rules: PAYLOAD_TOO_LARGE");
    }

    /** The application's JsonMapper, which reads the bundled file at start-up, refuses U+0000 before any check. */
    @Test
    void aNulCharacterStopsTheApplicationsReader() {
        new ApplicationContextRunner()
                .withConfiguration(AutoConfigurations.of(JacksonAutoConfiguration.class))
                .withUserConfiguration(JacksonConfig.class)
                .run(context -> assertThatThrownBy(() -> load(
                                context.getBean(JsonMapper.class), "site-templates-test/null.json", MAX_TREE_BYTES))
                        .isInstanceOf(JacksonException.class)
                        .hasMessageContaining("NUL"));
    }
}
