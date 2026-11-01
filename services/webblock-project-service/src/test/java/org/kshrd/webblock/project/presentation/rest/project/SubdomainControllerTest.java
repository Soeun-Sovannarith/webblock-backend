package org.kshrd.webblock.project.presentation.rest.project;

import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.SubdomainAvailability;
import org.kshrd.webblock.project.support.Users;
import org.kshrd.webblock.project.support.WebSliceSupport;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(controllers = SubdomainController.class)
@Import(WebSliceSupport.Imports.class)
class SubdomainControllerTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtDecoder jwtDecoder;

    @MockitoBean
    ProjectService projects;

    @Test
    @DisplayName("FR-13.13: a free sub-domain answers 200 with available true and reason null, in the envelope")
    void fr13_13_aFreeSubdomainIsAvailable() throws Exception {
        when(projects.checkSubdomain("kilncoffee")).thenReturn(SubdomainAvailability.AVAILABLE);
        mvc.perform(get("/api/v1/subdomains/{subdomain}", "kilncoffee").with(Users.anyUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data.subdomain").value("kilncoffee"))
                .andExpect(jsonPath("$.data.available").value(true))
                .andExpect(jsonPath("$.data.reason").isEmpty())
                .andExpect(jsonPath("$.meta.status").value(200));
    }

    @Test
    @DisplayName("FR-13.14: a held sub-domain answers 200 with available false and reason TAKEN, nothing else")
    void fr13_14_aHeldSubdomainIsTaken() throws Exception {
        when(projects.checkSubdomain("kilncoffee")).thenReturn(SubdomainAvailability.TAKEN);
        mvc.perform(get("/api/v1/subdomains/{subdomain}", "kilncoffee").with(Users.anyUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.subdomain").value("kilncoffee"))
                .andExpect(jsonPath("$.data.available").value(false))
                .andExpect(jsonPath("$.data.reason").value("TAKEN"))
                .andExpect(jsonPath("$.data.length()").value(3));
    }

    @Test
    @DisplayName("FR-13.15: a label the rule refuses answers 200 with available false and reason INVALID, as given")
    void fr13_15_aRefusedLabelIsInvalidAndEchoedAsGiven() throws Exception {
        when(projects.checkSubdomain("Kiln")).thenReturn(SubdomainAvailability.INVALID);
        mvc.perform(get("/api/v1/subdomains/{subdomain}", "Kiln").with(Users.anyUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.subdomain").value("Kiln"))
                .andExpect(jsonPath("$.data.available").value(false))
                .andExpect(jsonPath("$.data.reason").value("INVALID"));
        verify(projects).checkSubdomain("Kiln");
    }

    @Test
    @DisplayName("FR-13.16: no token is 401 and the check never runs")
    void fr13_16_noTokenIs401() throws Exception {
        mvc.perform(get("/api/v1/subdomains/{subdomain}", "kilncoffee"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.status").value(401));
        verifyNoInteractions(projects);
    }
}
