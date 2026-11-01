package org.kshrd.webblock.project.presentation.rest.catalog;

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
import org.kshrd.webblock.project.application.catalog.model.ActiveProductType;
import org.kshrd.webblock.project.application.catalog.model.BusinessType;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.json.JsonMapper;

@WebMvcTest(controllers = BusinessTypeController.class)
@Import(WebSliceSupport.Imports.class)
class BusinessTypeControllerTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    CatalogReference catalog;

    @Test
    void listsCodesAndNamesOnly() throws Exception {
        when(catalog.activeBusinessTypes())
                .thenReturn(List.of(new BusinessType(UUID.randomUUID(), "clothing_shop", "Clothing shop", true)));
        mvc.perform(get("/api/v1/business-types").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].code").value("clothing_shop"))
                .andExpect(jsonPath("$.data[0].name").value("Clothing shop"))
                .andExpect(jsonPath("$.data[0].businessTypeId").doesNotExist());
    }

    @Test
    void aCatalogOutageIs503() throws Exception {
        when(catalog.activeBusinessTypes()).thenThrow(new WebBlockException(ErrorCode.CATALOG_UNAVAILABLE));
        mvc.perform(get("/api/v1/business-types").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value("CATALOG_UNAVAILABLE"));
    }

    @Test
    @DisplayName("FR-8.3: a known business type answers its product types with their field schemas")
    void fr8_3_theDetailHasProductTypesAndFieldSchemas() throws Exception {
        UUID id = UUID.randomUUID();
        when(catalog.activeBusinessType("food_shop"))
                .thenReturn(Optional.of(new BusinessType(id, "food_shop", "Foods", true)));
        when(catalog.activeProductTypes(id))
                .thenReturn(List.of(new ActiveProductType(
                        "menu_item", "Menu item", JsonMapper.builder().build().readTree("{\"type\":\"object\"}"))));
        mvc.perform(get("/api/v1/business-types/food_shop").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.code").value("food_shop"))
                .andExpect(jsonPath("$.data.name").value("Foods"))
                .andExpect(jsonPath("$.data.productTypes[0].code").value("menu_item"))
                .andExpect(jsonPath("$.data.productTypes[0].name").value("Menu item"))
                .andExpect(jsonPath("$.data.productTypes[0].fieldSchema.type").value("object"))
                .andExpect(jsonPath("$.data.businessTypeId").doesNotExist());
    }

    @Test
    @DisplayName("FR-8.4: an unknown or retired business type is 404 BUSINESS_TYPE_NOT_FOUND")
    void fr8_4_anUnknownOrRetiredTypeIs404() throws Exception {
        when(catalog.activeBusinessType("nope_shop")).thenReturn(Optional.empty());
        mvc.perform(get("/api/v1/business-types/nope_shop").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("BUSINESS_TYPE_NOT_FOUND"));
    }
}
