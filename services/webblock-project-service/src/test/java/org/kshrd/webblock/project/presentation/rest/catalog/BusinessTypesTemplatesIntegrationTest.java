package org.kshrd.webblock.project.presentation.rest.catalog;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.env.Environment;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/** The five seeded business types, their product fields and the style templates, on a migrated catalog database. */
@ServiceIntegrationTest
class BusinessTypesTemplatesIntegrationTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    Environment environment;

    private void insertRetiredType(String code) throws Exception {
        try (Connection c = DriverManager.getConnection(
                environment.getProperty("webblock.catalog.datasource.url"),
                PostgresFixture.CATALOG_MIGRATOR_LOGIN,
                PostgresFixture.PASSWORD)) {
            c.createStatement().execute("SET ROLE catalog_owner");
            try (PreparedStatement ps = c.prepareStatement(
                    "INSERT INTO catalog.business_types VALUES (gen_random_uuid(), ?, 'Retired', false, now())")) {
                ps.setString(1, code);
                ps.executeUpdate();
            }
        }
    }

    @Test
    @DisplayName("FR-8: the list holds the five seeded business types")
    void fr8_theListHoldsTheFiveSeededTypes() throws Exception {
        mvc.perform(get("/api/v1/business-types").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[?(@.code=='clothing_shop')].name").value("Clothes"))
                .andExpect(jsonPath("$.data[?(@.code=='food_shop')].name").value("Foods"))
                .andExpect(jsonPath("$.data[?(@.code=='beauty_shop')].name").value("Beauty"))
                .andExpect(jsonPath("$.data[?(@.code=='furniture_shop')].name").value("Furniture"))
                .andExpect(jsonPath("$.data[?(@.code=='tech_shop')].name").value("Technology"));
    }

    @Test
    @DisplayName("FR-8.3: the detail of food_shop has its product type and field schema")
    void fr8_3_theDetailOfAKnownType() throws Exception {
        mvc.perform(get("/api/v1/business-types/food_shop").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.code").value("food_shop"))
                .andExpect(jsonPath("$.data.name").value("Foods"))
                .andExpect(jsonPath("$.data.productTypes.length()").value(1))
                .andExpect(jsonPath("$.data.productTypes[0].code").value("menu_item"))
                .andExpect(jsonPath("$.data.productTypes[0].name").value("Menu item"))
                .andExpect(jsonPath("$.data.productTypes[0].fieldSchema.type").value("object"))
                .andExpect(jsonPath("$.data.productTypes[0].fieldSchema.additionalProperties")
                        .value(false))
                .andExpect(jsonPath("$.data.productTypes[0].fieldSchema.properties.spice_level.enum.length()")
                        .value(4));
        mvc.perform(get("/api/v1/business-types/clothing_shop").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.productTypes.length()").value(4));
    }

    @Test
    @DisplayName("FR-8.4: an unknown or retired business type is 404 BUSINESS_TYPE_NOT_FOUND")
    void fr8_4_anUnknownOrRetiredTypeIs404() throws Exception {
        String retired = "gone_" + UUID.randomUUID().toString().substring(0, 8) + "_shop";
        insertRetiredType(retired);
        for (String code : new String[] {"nope_shop", retired}) {
            mvc.perform(get("/api/v1/business-types/{code}", code).with(Users.user(UUID.randomUUID())))
                    .andExpect(status().isNotFound())
                    .andExpect(jsonPath("$.code").value("BUSINESS_TYPE_NOT_FOUND"));
        }
    }

    @Test
    @DisplayName("FR-9.3: without a business type the five templates are listed")
    void fr9_3_noFilterListsFiveTemplates() throws Exception {
        mvc.perform(get("/api/v1/templates").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(5))
                .andExpect(jsonPath("$.data[?(@.templateCode=='sweet_pastel')].styleKey")
                        .value("sweet-pastel"))
                .andExpect(jsonPath("$.data[?(@.templateCode=='everyday_store')].badge")
                        .value("Popular"));
    }

    @Test
    @DisplayName("FR-9.1: an active business type lists the five templates")
    void fr9_1_anActiveTypeListsFiveTemplates() throws Exception {
        mvc.perform(get("/api/v1/templates")
                        .param("businessTypeCode", "beauty_shop")
                        .with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(5));
    }

    @Test
    @DisplayName("FR-9.2: an unknown or retired business type gives an empty list")
    void fr9_2_anUnknownTypeGivesAnEmptyList() throws Exception {
        String retired = "old_" + UUID.randomUUID().toString().substring(0, 8) + "_shop";
        insertRetiredType(retired);
        for (String code : new String[] {"nope_shop", retired}) {
            mvc.perform(get("/api/v1/templates").param("businessTypeCode", code).with(Users.user(UUID.randomUUID())))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.data.length()").value(0));
        }
    }

    @Test
    @DisplayName("FR-9.4: creating a project with sweet_pastel and food_shop copies the template's home page")
    void fr9_4_creatingWithAStyleTemplateCopiesItsHomePage() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        String subdomain = "sp" + projectId.toString().substring(0, 8);
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"Sugar","subdomain":"%s","businessTypeCode":"food_shop",
                                 "templateCode":"sweet_pastel",
                                 "businessProfile":{"businessName":"Sugar","description":null,"phone":null,
                                                    "email":"shop@example.test","address":null,
                                                    "timezone":"Asia/Phnom_Penh"}}""".formatted(subdomain)))
                .andExpect(status().isCreated());
        mvc.perform(get("/api/v1/projects/{id}/pages", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.pages.length()").value(1))
                .andExpect(jsonPath("$.data.pages[0].name").value("Home"))
                .andExpect(jsonPath("$.data.pages[0].path").value("/"));
    }
}
