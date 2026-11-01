package org.kshrd.webblock.project.presentation.rest.project;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/** The list and the detail with a real catalog and real pages: the business type and the template thumbnail. */
@ServiceIntegrationTest
class ProjectListFieldsIntegrationTest {

    @Autowired
    MockMvc mvc;

    private void create(UUID owner, UUID projectId, String businessType, String templateCode) throws Exception {
        String subdomain = "lf" + projectId.toString().substring(0, 8);
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"Shop %1$s","subdomain":"%1$s","businessTypeCode":"%2$s","templateCode":"%3$s",
                                 "businessProfile":{"businessName":"Shop","description":null,"phone":null,
                                                    "email":"shop@example.test","address":null,
                                                    "timezone":"Asia/Phnom_Penh"}}""".formatted(subdomain, businessType, templateCode)))
                .andExpect(status().isCreated());
    }

    @Test
    @DisplayName("FR-14.2: the list shows each website's business type and its template thumbnail; blank has none")
    void fr14_2_theListShowsTypeAndThumbnail() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID styled = UUID.randomUUID();
        UUID blank = UUID.randomUUID();
        create(owner, blank, "tech_shop", "blank");
        create(owner, styled, "food_shop", "sweet_pastel");

        mvc.perform(get("/api/v1/projects").with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(2))
                .andExpect(jsonPath("$.data[?(@.projectId=='%s')].businessTypeCode".formatted(styled))
                        .value("food_shop"))
                .andExpect(jsonPath("$.data[?(@.projectId=='%s')].businessTypeName".formatted(styled))
                        .value("Foods"))
                .andExpect(jsonPath("$.data[?(@.projectId=='%s')].thumbnailUrl".formatted(styled))
                        .value("/mock/templates/sweet-pastel.png"))
                .andExpect(jsonPath("$.data[?(@.projectId=='%s')].businessTypeCode".formatted(blank))
                        .value("tech_shop"))
                .andExpect(jsonPath("$.data[?(@.projectId=='%s')].businessTypeName".formatted(blank))
                        .value("Technology"))
                .andExpect(jsonPath("$.data[?(@.projectId=='%s')].thumbnailUrl".formatted(blank))
                        .value(org.hamcrest.Matchers.contains((Object) null)));
    }

    @Test
    @DisplayName("FR-15.2: the detail shows the business type and the template thumbnail; blank has a null thumbnail")
    void fr15_2_theDetailShowsTypeAndThumbnail() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID styled = UUID.randomUUID();
        UUID blank = UUID.randomUUID();
        create(owner, styled, "beauty_shop", "clean_mono");
        create(owner, blank, "beauty_shop", "blank");

        mvc.perform(get("/api/v1/projects/{id}", styled).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.businessTypeCode").value("beauty_shop"))
                .andExpect(jsonPath("$.data.businessTypeName").value("Beauty"))
                .andExpect(jsonPath("$.data.thumbnailUrl").value("/mock/templates/clean-mono.png"));
        mvc.perform(get("/api/v1/projects/{id}", blank).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.businessTypeCode").value("beauty_shop"))
                .andExpect(jsonPath("$.data.thumbnailUrl").value(org.hamcrest.Matchers.nullValue()));
    }

    @Test
    @DisplayName("FR-14.2: the create response and the repeat PUT carry the business type name and the thumbnail")
    void fr14_2_theCreateResponseCarriesTypeAndThumbnail() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID id = UUID.randomUUID();
        String body = """
                {"name":"Shop","subdomain":"%s","businessTypeCode":"food_shop","templateCode":"sweet_pastel",
                 "businessProfile":{"businessName":"Shop","description":null,"phone":null,
                                    "email":"shop@example.test","address":null,
                                    "timezone":"Asia/Phnom_Penh"}}""".formatted("cr" + id.toString().substring(0, 8));
        for (var expected :
                new org.springframework.test.web.servlet.ResultMatcher[] {status().isCreated(), status().isOk()}) {
            mvc.perform(put("/api/v1/projects/{id}", id)
                            .with(Users.user(owner))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(body))
                    .andExpect(expected)
                    .andExpect(jsonPath("$.data.businessTypeCode").value("food_shop"))
                    .andExpect(jsonPath("$.data.businessTypeName").value("Foods"))
                    .andExpect(jsonPath("$.data.thumbnailUrl").value("/mock/templates/sweet-pastel.png"));
        }
    }
}
