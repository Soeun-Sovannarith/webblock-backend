package org.kshrd.webblock.project.presentation.rest.page;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.page.handler.PuckValidator;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = ComponentCatalogController.class)
@Import({WebSliceSupport.Imports.class, PuckValidator.class})
class ComponentCatalogControllerTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @Test
    void servesAKnownCatalog() throws Exception {
        mvc.perform(get("/api/v1/component-catalog/0.1.0").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.type").value("object"))
                .andExpect(jsonPath("$.data.$defs.component").exists());
    }

    @Test
    void anUnknownVersionIsNotFound() throws Exception {
        mvc.perform(get("/api/v1/component-catalog/9.9.9").with(Users.user(UUID.randomUUID())))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("COMPONENT_CATALOG_NOT_FOUND"));
    }
}
