package org.kshrd.webblock.project.presentation.rest.template;

import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.model.BusinessType;
import org.kshrd.webblock.project.application.template.handler.TemplateCatalog;
import org.kshrd.webblock.project.application.template.model.SiteTemplate;
import org.kshrd.webblock.project.application.template.model.TemplatePage;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.json.JsonMapper;

@WebMvcTest(controllers = TemplateController.class)
@Import(WebSliceSupport.Imports.class)
class TemplateControllerTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    TemplateCatalog templates;

    @MockitoBean
    CatalogReference catalog;

    private static SiteTemplate template(String code) throws Exception {
        TemplatePage home = new TemplatePage(
                "Home", "/", 0, null, null, JsonMapper.builder().build().readTree("{}"));
        return new SiteTemplate(
                code,
                "Name " + code,
                "About " + code,
                "/mock/templates/" + code + ".png",
                "Popular",
                "style-" + code,
                "1.0.0",
                List.of("*"),
                "0.1.0",
                List.of(home));
    }

    private static BusinessType type() {
        return new BusinessType(UUID.randomUUID(), "clothing_shop", "Clothes", true);
    }

    @Test
    @DisplayName("FR-9.1: an active business type lists its templates with the display fields")
    void fr9_1_listsTheTemplatesOfAnActiveBusinessType() throws Exception {
        when(catalog.activeBusinessType("clothing_shop")).thenReturn(Optional.of(type()));
        when(templates.forBusinessType("clothing_shop")).thenReturn(List.of(template("everyday_store")));
        mvc.perform(get("/api/v1/templates")
                        .param("businessTypeCode", "clothing_shop")
                        .with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].templateCode").value("everyday_store"))
                .andExpect(jsonPath("$.data[0].description").value("About everyday_store"))
                .andExpect(jsonPath("$.data[0].thumbnailUrl").value("/mock/templates/everyday_store.png"))
                .andExpect(jsonPath("$.data[0].badge").value("Popular"))
                .andExpect(jsonPath("$.data[0].styleKey").value("style-everyday_store"))
                .andExpect(jsonPath("$.data[0].pageCount").value(1))
                .andExpect(jsonPath("$.data[0].pages").doesNotExist());
    }

    @Test
    @DisplayName("FR-9.3: without a business type every template is listed")
    void fr9_3_withoutAFilterEveryTemplateIsListed() throws Exception {
        when(templates.all()).thenReturn(List.of(template("a_one"), template("b_two")));
        mvc.perform(get("/api/v1/templates").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(2))
                .andExpect(jsonPath("$.data[1].templateCode").value("b_two"));
    }

    @Test
    @DisplayName("FR-9.2: an unknown or retired business type gives an empty list")
    void fr9_2_anUnknownOrRetiredBusinessTypeGivesAnEmptyList() throws Exception {
        when(catalog.activeBusinessType("nope_shop")).thenReturn(Optional.empty());
        mvc.perform(get("/api/v1/templates")
                        .param("businessTypeCode", "nope_shop")
                        .with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(0));
        verify(catalog).activeBusinessType("nope_shop");
    }

    @Test
    @DisplayName("FR-9: a blank or overlong business type code is refused")
    void fr9_aBlankOrOverlongBusinessTypeIsRejected() throws Exception {
        for (String value : new String[] {" ", "x".repeat(51)}) {
            mvc.perform(get("/api/v1/templates")
                            .param("businessTypeCode", value)
                            .with(Users.user(UUID.randomUUID())))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        }
    }

    @Test
    @DisplayName("FR-9.5: a template's detail has the list fields and its one page with the Puck tree")
    void fr9_5_theDetailCarriesThePage() throws Exception {
        when(templates.find("everyday_store")).thenReturn(Optional.of(template("everyday_store")));
        mvc.perform(get("/api/v1/templates/everyday_store").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.templateCode").value("everyday_store"))
                .andExpect(jsonPath("$.data.styleKey").value("style-everyday_store"))
                .andExpect(jsonPath("$.data.pageCount").value(1))
                .andExpect(jsonPath("$.data.page.name").value("Home"))
                .andExpect(jsonPath("$.data.page.path").value("/"))
                .andExpect(jsonPath("$.data.page.puckTree").isMap());
    }

    @Test
    @DisplayName("FR-9.6: an unknown template code is 404 TEMPLATE_NOT_FOUND")
    void fr9_6_anUnknownTemplateIs404() throws Exception {
        when(templates.find("nope")).thenReturn(Optional.empty());
        mvc.perform(get("/api/v1/templates/nope").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("TEMPLATE_NOT_FOUND"));
    }

    @Test
    @DisplayName("FR-9.5: the detail needs a signed-in user, as the list does")
    void fr9_5_theDetailNeedsAToken() throws Exception {
        mvc.perform(get("/api/v1/templates/everyday_store")).andExpect(status().isUnauthorized());
    }
}
