package org.kshrd.webblock.project.application.wizard.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.jayway.jsonpath.JsonPath;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

/**
 * The Payment stand-in refuses one client; no project may exist afterwards. Uses the seed's first business type.
 *
 * <p>The refusal file needs its own application context. It is kept small (its pool opens connections only as
 * needed) and closed after the class: every cached full context holds its connection pools open, and the suite's
 * PostgreSQL allows 300 connections (PostgresFixture).
 */
@ServiceIntegrationTest
@DirtiesContext
@TestPropertySource(
        properties = {
            "spring.datasource.hikari.maximum-pool-size=4",
            "spring.datasource.hikari.minimum-idle=1",
            "webblock.catalog.datasource.maximum-pool-size=2"
        })
class ProjectCreationRefusalIntegrationTest {

    static final UUID REFUSED = UUID.randomUUID();

    @DynamicPropertySource
    static void refusals(DynamicPropertyRegistry registry) throws Exception {
        Path file = Files.createTempFile("refusals", ".json");
        file.toFile().deleteOnExit();
        Files.writeString(
                file, "{\"websiteRefusals\":[{\"clientUserId\":\"" + REFUSED + "\",\"reason\":\"TRIAL_ENDED\"}]}");
        registry.add("webblock.payment.stand-in-file", file::toString);
    }

    @Autowired
    MockMvc mvc;

    @Autowired
    JdbcTemplate jdbc;

    @Test
    void aRefusedClientGets422AndNoProject() throws Exception {
        String type = JsonPath.read(
                mvc.perform(get("/api/v1/business-types").with(Users.user(REFUSED)))
                        .andExpect(status().isOk())
                        .andReturn()
                        .getResponse()
                        .getContentAsString(),
                "$.data[0].code");
        UUID projectId = UUID.randomUUID();
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(REFUSED))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name":"No","subdomain":"%s","businessTypeCode":"%s","templateCode":"blank",
                                 "businessProfile":{"businessName":"No","description":null,"phone":null,"email":null,
                                                    "address":null,"timezone":"UTC"}}""".formatted(TestSubdomains.of(projectId), type)))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("TRIAL_ENDED"));
        mvc.perform(get("/api/v1/projects/{id}", projectId).with(Users.user(REFUSED)))
                .andExpect(status().isNotFound());
        assertThat(jdbc.queryForObject(
                        "SELECT count(*) FROM project_messaging.outbox_record WHERE record_key IN (?, ?)",
                        Long.class,
                        "catalog:" + projectId,
                        "payment:" + projectId))
                .as("a refusal schedules neither the catalog registration nor a release")
                .isZero();
    }
}
