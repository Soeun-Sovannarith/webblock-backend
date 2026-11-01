package org.kshrd.webblock.project.common.infrastructure.security;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = SecurityProbeController.class)
@Import({WebSliceSupport.Imports.class, DevTokenIntrospector.class})
@ActiveProfiles("prototype")
class DevTokenSecurityWebTest {

    @DynamicPropertySource
    static void tokens(DynamicPropertyRegistry registry) throws Exception {
        Path file = Files.createTempFile("dev-tokens", ".json");
        file.toFile().deleteOnExit();
        Files.writeString(file, """
                {"tokens":[{"token":"%s","subject":"alice","userId":"%s","scopes":[]}]}""".formatted(DevTokenIntrospectorTest.USER_TOKEN, DevTokenIntrospectorTest.USER_ID));
        registry.add("webblock.security.identity-mode", () -> "dev-tokens");
        registry.add("webblock.security.dev-tokens-file", file::toString);
    }

    @Autowired
    MockMvc mvc;

    @Test
    void internalNeedsNoDevTokenAndIgnoresAnUnknownOne() throws Exception {
        mvc.perform(get("/internal/test-security")).andExpect(status().isOk());
        mvc.perform(get("/internal/test-security").header("Authorization", "Bearer nope-nope-nope-nope-nope-nope-nope"))
                .andExpect(status().isOk());
    }

    @Test
    void theUserDevTokenIsKnownOnTheApi() throws Exception {
        mvc.perform(get("/api/test-security").header("Authorization", "Bearer " + DevTokenIntrospectorTest.USER_TOKEN))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.userId").value(DevTokenIntrospectorTest.USER_ID));
    }

    @Test
    void anUnknownDevTokenIsUnauthenticated() throws Exception {
        mvc.perform(get("/api/test-security").header("Authorization", "Bearer nope-nope-nope-nope-nope-nope-nope-nope"))
                .andExpect(status().isUnauthorized())
                .andExpect(header().string("WWW-Authenticate", "Bearer error=\"invalid_token\""));
    }
}
