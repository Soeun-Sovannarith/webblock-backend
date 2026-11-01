package org.kshrd.webblock.project.common.infrastructure.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.startsWith;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.jayway.jsonpath.JsonPath;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.ServedPaths;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationContext;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/** TR-40: the prototype profile serves the OpenAPI description of /api/v1 and Swagger UI, without a token. */
@ServiceIntegrationTest
class OpenApiPrototypeTest {

    private static final String GARBAGE = "Bearer not-a-token-not-a-token-not-a-token";

    @Autowired
    MockMvc mvc;

    @Autowired
    ApplicationContext context;

    @Test
    @DisplayName("TR-40: prototype serves the plain OpenAPI description of /api/v1 without a token, never /internal")
    void tr40_prototypeServesThePlainDescriptionOfTheUserApi() throws Exception {
        String body = mvc.perform(get("/v3/api-docs"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.openapi").value(startsWith("3.")))
                .andExpect(jsonPath("$.paths").isMap())
                .andExpect(jsonPath("$.success").doesNotExist())
                .andExpect(jsonPath("$.data").doesNotExist())
                .andExpect(jsonPath("$.meta").doesNotExist())
                .andExpect(jsonPath("$.paths['/api/v1/projects']").exists())
                .andExpect(jsonPath("$.paths['/api/v1/projects/{projectId}']").exists())
                .andReturn()
                .getResponse()
                .getContentAsString();
        assertThat(body).doesNotContain("/internal/");
        Map<String, Object> paths = JsonPath.read(body, "$.paths");
        assertThat(paths.keySet())
                .isNotEmpty()
                .allSatisfy(path -> assertThat(path).startsWith("/api/v1/"));
        assertThat(ServedPaths.of(context)).contains("/v3/api-docs");
    }

    @Test
    @DisplayName("TR-40: the description offers a Bearer scheme on every operation and names Notion as the authority")
    void tr40_theDescriptionOffersABearerSchemeAndNamesTheAuthority() throws Exception {
        mvc.perform(get("/v3/api-docs"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.components.securitySchemes.bearer.type").value("http"))
                .andExpect(
                        jsonPath("$.components.securitySchemes.bearer.scheme").value("bearer"))
                .andExpect(jsonPath("$.security[0].bearer").isArray())
                .andExpect(jsonPath("$.info.description").value(containsString("Web Block architecture")));
    }

    /**
     * Without a declared server springdoc would write the request's host into the shared description, so a forged Host
     * could point Swagger UI, and the token pasted into Authorize, at another host.
     */
    @Test
    @DisplayName("TR-40: the description names only the relative server /, whatever Host the request carries")
    void tr40_theDescriptionNamesOnlyTheRelativeServer() throws Exception {
        for (String host : new String[] {"collect.attacker.example", "localhost"}) {
            String body = mvc.perform(get("http://" + host + "/v3/api-docs")
                            .header("Host", host)
                            .header("X-Forwarded-Host", "forwarded.attacker.example"))
                    .andExpect(status().isOk())
                    .andReturn()
                    .getResponse()
                    .getContentAsString();
            List<Map<String, Object>> servers = JsonPath.read(body, "$.servers");
            assertThat(servers).as(host).containsExactly(Map.of("url", "/"));
            assertThat(body).doesNotContain("attacker.example");
        }
    }

    @Test
    @DisplayName("TR-40: prototype serves Swagger UI and the doc paths without a token, and ignores a stray one")
    void tr40_prototypeServesSwaggerUiWithoutAToken() throws Exception {
        mvc.perform(get("/swagger-ui/index.html"))
                .andExpect(status().isOk())
                .andExpect(content().string(containsString("swagger-ui")));
        mvc.perform(get("/swagger-ui.html")).andExpect(status().is3xxRedirection());
        mvc.perform(get("/v3/api-docs/swagger-config")).andExpect(status().isOk());
        mvc.perform(get("/v3/api-docs").header("Authorization", GARBAGE)).andExpect(status().isOk());
    }

    @Test
    @DisplayName("TR-40, FR-8: the user API still needs a token while the doc paths are open (TR-30)")
    void tr40_theUserApiStillNeedsAToken() throws Exception {
        mvc.perform(get("/api/v1/projects")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/v1/projects/{projectId}", "0199e0a2-0000-7000-8000-000000000001"))
                .andExpect(status().isUnauthorized());
    }
}
