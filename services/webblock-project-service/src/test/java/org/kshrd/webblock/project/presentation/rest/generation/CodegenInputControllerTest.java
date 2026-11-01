package org.kshrd.webblock.project.presentation.rest.generation;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.generation.handler.CodegenInputService;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = CodegenInputController.class)
@Import(WebSliceSupport.Imports.class)
class CodegenInputControllerTest {

    static final UUID REQUEST = UUID.randomUUID();
    static final String URL = "/internal/v1/generation-requests/" + REQUEST + "/codegen-input";

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    CodegenInputService service;

    @Test
    void aCatalogOutageIs503CatalogUnavailable() throws Exception {
        when(service.codegenInput(REQUEST)).thenThrow(new WebBlockException(ErrorCode.CATALOG_UNAVAILABLE));
        mvc.perform(get(URL))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value("CATALOG_UNAVAILABLE"))
                .andExpect(header().exists("Retry-After"));
    }

    @Test
    void aMalformedIdIs400() throws Exception {
        mvc.perform(get("/internal/v1/generation-requests/not-a-uuid/codegen-input"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }
}
