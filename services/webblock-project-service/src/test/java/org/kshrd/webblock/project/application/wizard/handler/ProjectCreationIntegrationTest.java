package org.kshrd.webblock.project.application.wizard.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doCallRealMethod;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.catalog.job.CatalogRegistrationScheduler;
import org.kshrd.webblock.project.application.payment.handler.AdmissionReleaseFence;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.db.PostgresFixture;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.env.Environment;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.springframework.test.util.AopTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * The wizard end to end on real PostgreSQL 18 and RabbitMQ.
 *
 * <p>The spies need their own application context. It is kept small (its pool opens connections only as needed) and
 * closed after the class: every cached full context holds its connection pools open, and the suite's PostgreSQL
 * allows 300 connections (PostgresFixture).
 */
@ServiceIntegrationTest
@DirtiesContext
@TestPropertySource(
        properties = {
            "spring.datasource.hikari.maximum-pool-size=6",
            "spring.datasource.hikari.minimum-idle=1",
            "webblock.catalog.datasource.maximum-pool-size=2"
        })
class ProjectCreationIntegrationTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    Environment environment;

    @Autowired
    JdbcTemplate jdbc;

    /** The real bean; one test makes it fail after it has scheduled, to prove the wizard's transaction rolls back. */
    @MockitoSpyBean
    CatalogRegistrationScheduler registrations;

    /** The prototype's Payment stand-in; the release race tests hold its answers back. */
    @MockitoSpyBean
    PaymentAdmissions payment;

    /** The real fence; one race test holds a request just before its release decision. */
    @MockitoSpyBean
    AdmissionReleaseFence releases;

    String typeCode;

    private Connection catalogAsOwner() throws Exception {
        Connection c = DriverManager.getConnection(
                environment.getProperty("webblock.catalog.datasource.url"),
                PostgresFixture.CATALOG_MIGRATOR_LOGIN,
                PostgresFixture.PASSWORD);
        c.createStatement().execute("SET ROLE catalog_owner");
        return c;
    }

    @BeforeEach
    void ownBusinessType() throws Exception {
        typeCode = "wiz_" + UUID.randomUUID().toString().substring(0, 8) + "_shop";
        try (Connection owner = catalogAsOwner();
                PreparedStatement ps = owner.prepareStatement(
                        "INSERT INTO catalog.business_types VALUES (gen_random_uuid(), ?, 'Wizard', true, now())")) {
            ps.setString(1, typeCode);
            ps.executeUpdate();
        }
    }

    /** The test's own sub-domain for a name: the same name gives the same sub-domain, so a repeat is identical. */
    private String subdomain(String name) {
        return "w" + typeCode.substring(4, 12) + "-"
                + name.toLowerCase(java.util.Locale.ROOT).replaceAll("[^a-z0-9]", "-");
    }

    private String body(String name) {
        return body(name, subdomain(name));
    }

    private String body(String name, String subdomain) {
        return """
                {"name":"%s","subdomain":"%s","businessTypeCode":"%s","templateCode":"blank",
                 "businessProfile":{"businessName":"Shop %s","description":null,"phone":null,
                                    "email":"shop@example.test","address":null,"timezone":"Asia/Phnom_Penh"}}""".formatted(name, subdomain, typeCode, name);
    }

    private MvcResult send(UUID owner, UUID projectId, String body) throws Exception {
        return mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andReturn();
    }

    private int create(UUID owner, UUID projectId, String name) throws Exception {
        return mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(name)))
                .andReturn()
                .getResponse()
                .getStatus();
    }

    private long count(String sql, Object... args) {
        return jdbc.queryForObject(sql, Long.class, args);
    }

    @Test
    void createsTheProjectItsPagesAndItsCatalogWebsite() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("Mine")))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.status").value("draft"))
                .andExpect(jsonPath("$.data.editRevision").value(0));
        mvc.perform(get("/api/v1/projects/{id}/pages", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.pages[0].path").value("/"));
        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> {
            try (Connection owner2 = catalogAsOwner();
                    PreparedStatement ps =
                            owner2.prepareStatement("SELECT status::text FROM catalog.websites WHERE website_id = ?")) {
                ps.setObject(1, projectId);
                try (ResultSet rs = ps.executeQuery()) {
                    assertThat(rs.next()).isTrue();
                    assertThat(rs.getString(1)).isEqualTo("active");
                }
            }
        });
        // The registrar deletes the pending profile only after the catalog transaction that wrote the website and its
        // business profile committed (catalog_owner cannot read business_profiles: FORCE ROW LEVEL SECURITY).
        await().atMost(Duration.ofSeconds(10))
                .untilAsserted(() -> assertThat(count(
                                "SELECT count(*) FROM project_messaging.pending_catalog_registrations"
                                        + " WHERE project_id = ?",
                                projectId))
                        .isZero());
    }

    private String bodyWithTemplate(String name, String template, String openingHours) {
        return """
                {"name":"%s","subdomain":"%s","businessTypeCode":"%s","templateCode":"%s",
                 "businessProfile":{"businessName":"Shop %s","phone":"+855 12 345 678",
                                    "email":"shop@example.test","address":"1 Main St","timezone":"Asia/Phnom_Penh"%s}}""".formatted(
                        name,
                        subdomain(name),
                        typeCode,
                        template,
                        name,
                        openingHours == null ? "" : ",\"openingHours\":" + openingHours);
    }

    private static final String WEEK = "["
            + String.join(
                    ",",
                    List.of("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY").stream()
                            .map(d -> "{\"day\":\"" + d
                                    + "\",\"open\":true,\"opensAt\":\"08:00\",\"closesAt\":\"17:00\"}")
                            .toList())
            + ",{\"day\":\"SATURDAY\",\"open\":false,\"opensAt\":\"09:00\",\"closesAt\":\"17:00\"}"
            + ",{\"day\":\"SUNDAY\",\"open\":false,\"opensAt\":\"09:00\",\"closesAt\":\"17:00\"}]";

    private String catalogOpeningHours(UUID projectId) throws Exception {
        try (Connection c = DriverManager.getConnection(
                        environment.getProperty("webblock.catalog.datasource.url"),
                        PostgresFixture.superuser(),
                        PostgresFixture.superuserPassword());
                PreparedStatement ps = c.prepareStatement(
                        "SELECT opening_hours::text FROM catalog.business_profiles WHERE website_id = ?")) {
            ps.setObject(1, projectId);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getString(1) : null;
            }
        }
    }

    @Test
    @DisplayName("FR-10.10: opening hours reach the catalog profile and the home page shows the business details")
    void fr10_10_openingHoursReachTheCatalogAndTheHomePage() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        assertThat(send(owner, projectId, bodyWithTemplate("Hours", "sweet_pastel", WEEK))
                        .getResponse()
                        .getStatus())
                .isEqualTo(201);
        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(() -> assertThat(catalogOpeningHours(projectId)).isNotNull());
        assertThat(catalogOpeningHours(projectId))
                .contains("\"from_day\": \"MONDAY\"", "\"to_day\": \"FRIDAY\"", "\"opens_at\": \"08:00\"")
                .doesNotContain("SATURDAY");
        String tree = jdbc.queryForObject(
                "SELECT puck_tree::text FROM project.pages WHERE project_id = ? AND path = '/'",
                String.class,
                projectId);
        assertThat(tree)
                .contains("\"businessName\": \"Shop Hours\"", "\"address\": \"1 Main St\"")
                .contains(
                        "\"hours\": \"8:00 – 17:00\"", "\"hoursDetail\": \"Mon – Fri 8:00 – 17:00 · Sat – Sun closed\"")
                .contains("+855 12 345 678", "\"templateId\"");
    }

    @Test
    @DisplayName("FR-10.11: a style template's home page without a phone or hours keeps no sample details")
    void fr10_11_noSampleDetailsRemain() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        String body = bodyWithTemplate("Bare", "sweet_pastel", null)
                .replace("\"phone\":\"+855 12 345 678\",", "")
                .replace("\"address\":\"1 Main St\",", "");
        assertThat(send(owner, projectId, body).getResponse().getStatus()).isEqualTo(201);
        String props = jdbc.queryForObject(
                "SELECT puck_tree->'root'->'props' FROM project.pages WHERE project_id = ? AND path = '/'",
                String.class,
                projectId);
        assertThat(props)
                .contains("\"phones\": []", "\"address\": \"\"", "\"hours\": \"\"", "\"hoursDetail\": \"\"")
                .contains("\"businessName\": \"Shop Bare\"")
                .doesNotContain("Garden Lane", "012 345 678");
    }

    @Test
    @DisplayName("FR-10.10: without opening hours the catalog profile has [] and the blank home page is unchanged")
    void fr10_10_withoutOpeningHoursTheCatalogHasNoneAndBlankIsUnchanged() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        assertThat(send(owner, projectId, bodyWithTemplate("Plain", "blank", null))
                        .getResponse()
                        .getStatus())
                .isEqualTo(201);
        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(() -> assertThat(catalogOpeningHours(projectId)).isEqualTo("[]"));
        assertThat(jdbc.queryForObject(
                        "SELECT puck_tree->'root'->'props' = '{}'::jsonb FROM project.pages"
                                + " WHERE project_id = ? AND path = '/'",
                        Boolean.class,
                        projectId))
                .isTrue();
    }

    @Test
    @DisplayName("FR-13.1: a free sub-domain gives 201 with subdomain and platformHostname <subdomain>.<site domain>")
    void fr13_1_aFreeSubdomainIsStoredAndAnswered() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        String kiln = subdomain("kilncoffee");
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("Kiln Coffee", kiln)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.subdomain").value(kiln))
                .andExpect(jsonPath("$.data.platformHostname").value(kiln + ".sites.webblock.test"));
        mvc.perform(get("/api/v1/projects/{id}", projectId).with(Users.user(owner)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.subdomain").value(kiln))
                .andExpect(jsonPath("$.data.platformHostname").value(kiln + ".sites.webblock.test"));
        assertThat(jdbc.queryForObject(
                        "SELECT subdomain FROM project.projects WHERE project_id = ?", String.class, projectId))
                .isEqualTo(kiln);
    }

    @Test
    @DisplayName("FR-13.2: Payment not asked when a deleted project holds the sub-domain; 409 HOSTNAME_TAKEN")
    void fr13_2_aDeletedProjectsSubdomainIs409AndPaymentIsNotAsked() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID gone = UUID.randomUUID();
        String kiln = subdomain("kilncoffee");
        assertThat(send(owner, gone, body("Old", kiln)).getResponse().getStatus())
                .isEqualTo(201);
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", gone);

        UUID projectId = UUID.randomUUID();
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("New", kiln)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("HOSTNAME_TAKEN"));
        verify(paymentSpy(), never()).admitWebsite(any(), eq(projectId));
        assertThat(count("SELECT count(*) FROM project.projects WHERE project_id = ?", projectId))
                .isZero();
        assertThat(count(
                        "SELECT count(*) FROM project_messaging.outbox_record WHERE record_key IN (?, ?)",
                        "catalog:" + projectId,
                        "payment:" + projectId))
                .isZero();
    }

    @Test
    @DisplayName("FR-13.3: auth, -shop and ab are 400 VALIDATION_FAILED, field subdomain VALUE_INVALID")
    void fr13_3_aReservedOrMalformedSubdomainIs400() throws Exception {
        UUID owner = UUID.randomUUID();
        for (String bad : List.of("auth", "-shop", "ab")) {
            UUID projectId = UUID.randomUUID();
            mvc.perform(put("/api/v1/projects/{id}", projectId)
                            .with(Users.user(owner))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(body("Bad", bad)))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                    .andExpect(jsonPath("$.errors[0].field").value("subdomain"))
                    .andExpect(jsonPath("$.errors[0].code").value("VALUE_INVALID"));
            verify(paymentSpy(), never()).admitWebsite(any(), eq(projectId));
            assertThat(count("SELECT count(*) FROM project.projects WHERE project_id = ?", projectId))
                    .isZero();
        }
    }

    @Test
    @DisplayName("FR-13.4: the website exists with kilncoffee; the same PUT with kiln2 is 409 PROJECT_ID_CONFLICT")
    void fr13_4_aRepeatWithAnotherSubdomainIs409() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        assertThat(send(owner, projectId, body("Kiln", subdomain("kilncoffee")))
                        .getResponse()
                        .getStatus())
                .isEqualTo(201);
        MvcResult other = send(owner, projectId, body("Kiln", subdomain("kiln2")));
        assertThat(other.getResponse().getStatus()).isEqualTo(409);
        assertThat(other.getResponse().getContentAsString()).contains("PROJECT_ID_CONFLICT");
        assertThat(jdbc.queryForObject(
                        "SELECT subdomain FROM project.projects WHERE project_id = ?", String.class, projectId))
                .isEqualTo(subdomain("kilncoffee"));
    }

    /**
     * Two creates with different IDs and one sub-domain both find it free and are both admitted; the unique key lets
     * one win. The loser answers 409 HOSTNAME_TAKEN, writes nothing and schedules the release of its admission.
     */
    @Test
    @DisplayName("FR-13.2: two creates racing for one sub-domain make one project; the loser is HOSTNAME_TAKEN")
    void fr13_2_twoCreatesRacingForOneSubdomain() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID first = UUID.randomUUID();
        UUID second = UUID.randomUUID();
        String shared = subdomain("race");
        java.util.concurrent.CyclicBarrier bothLooked = new java.util.concurrent.CyclicBarrier(2);
        java.util.Set<String> held = java.util.concurrent.ConcurrentHashMap.newKeySet();
        doAnswer(invocation -> {
                    // The first look, before Payment: both requests have found the sub-domain free by now. The fence
                    // reads the state again under its lock; that call goes through.
                    String thread = Thread.currentThread().getName();
                    if ((onThread("race-a") || onThread("race-b")) && held.add(thread)) {
                        bothLooked.await(30, TimeUnit.SECONDS);
                    }
                    return invocation.callRealMethod();
                })
                .when((AdmissionReleaseFence) AopTestUtils.getUltimateTargetObject(releases))
                .state(argThat(id -> id.equals(first) || id.equals(second)));

        CompletableFuture<MvcResult> a = sendOnThread("race-a", owner, first, body("Race A", shared));
        CompletableFuture<MvcResult> b = sendOnThread("race-b", owner, second, body("Race B", shared));
        List<MvcResult> results = List.of(a.get(60, TimeUnit.SECONDS), b.get(60, TimeUnit.SECONDS));

        assertThat(results).extracting(r -> r.getResponse().getStatus()).containsExactlyInAnyOrder(201, 409);
        boolean firstLost = results.get(0).getResponse().getStatus() == 409;
        MvcResult lost = results.get(firstLost ? 0 : 1);
        assertThat(lost.getResponse().getContentAsString()).contains("HOSTNAME_TAKEN");
        UUID loser = firstLost ? first : second;
        assertThat(count("SELECT count(*) FROM project.projects WHERE subdomain = ?", shared))
                .isEqualTo(1);
        assertThat(count("SELECT count(*) FROM project.projects WHERE project_id = ?", loser))
                .isZero();
        assertThat(count("SELECT count(*) FROM project.pages WHERE project_id = ?", loser))
                .isZero();
        awaitReleases(loser, 1);
    }

    @Test
    void aRepeatIs200AndADifferentBodyOrOwnerIs409() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        assertThat(create(owner, projectId, "Same")).isEqualTo(201);
        assertThat(create(owner, projectId, "Same")).isEqualTo(200);
        assertThat(create(owner, projectId, "Different")).isEqualTo(409);
        assertThat(create(UUID.randomUUID(), projectId, "Same")).isEqualTo(409);
        mvc.perform(get("/api/v1/projects/{id}", projectId).with(Users.user(UUID.randomUUID())))
                .andExpect(status().isNotFound());
        assertThat(count("SELECT count(*) FROM project.pages WHERE project_id = ?", projectId))
                .isEqualTo(1);
        assertThat(count(
                        "SELECT count(*) FROM project_messaging.outbox_record WHERE record_key = ?",
                        "catalog:" + projectId))
                .isEqualTo(1);
    }

    @Test
    void twoIdenticalCreatesAtOnceMakeOneProject() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        Callable<Integer> call = () -> create(owner, projectId, "Race");
        ExecutorService pool = Executors.newFixedThreadPool(2);
        List<Integer> statuses;
        try {
            statuses = pool.invokeAll(List.of(call, call)).stream()
                    .map(f -> {
                        try {
                            return f.get();
                        } catch (Exception e) {
                            throw new IllegalStateException(e);
                        }
                    })
                    .toList();
        } finally {
            pool.shutdown();
        }
        assertThat(statuses).containsExactlyInAnyOrder(200, 201);
        assertThat(count("SELECT count(*) FROM project.projects WHERE project_id = ?", projectId))
                .isEqualTo(1);
        assertThat(count("SELECT count(*) FROM project.pages WHERE project_id = ?", projectId))
                .isEqualTo(1);
    }

    /** A website the catalog holds under the ID, registered outside this service: refused before Payment admits it. */
    @Test
    void anIdTheCatalogAlreadyHoldsIs409WithoutAskingPayment() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        try (Connection catalogOwner = catalogAsOwner();
                PreparedStatement ps = catalogOwner.prepareStatement(
                        "INSERT INTO catalog.websites SELECT ?, business_type_id, ?, 'active', 1, 1, 0, now(), now()"
                                + " FROM catalog.business_types WHERE code = ?")) {
            ps.setObject(1, projectId);
            ps.setString(2, "site_" + projectId.toString().replace("-", ""));
            ps.setString(3, typeCode);
            assertThat(ps.executeUpdate()).isEqualTo(1);
        }

        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("Taken")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("PROJECT_ID_CONFLICT"));

        verify(paymentSpy(), never()).admitWebsite(any(), eq(projectId));
        assertThat(count("SELECT count(*) FROM project.projects WHERE project_id = ?", projectId))
                .isZero();
        assertThat(count(
                        "SELECT count(*) FROM project_messaging.outbox_record WHERE record_key IN (?, ?)",
                        "catalog:" + projectId,
                        "payment:" + projectId))
                .isZero();
    }

    @Test
    void anUnknownBusinessTypeOrTemplateIs422() throws Exception {
        UUID owner = UUID.randomUUID();
        mvc.perform(put("/api/v1/projects/{id}", UUID.randomUUID())
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("x").replace(typeCode, "missing_shop")))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("BUSINESS_TYPE_NOT_AVAILABLE"));
        mvc.perform(put("/api/v1/projects/{id}", UUID.randomUUID())
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("x").replace("\"blank\"", "\"no_such_template\"")))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("TEMPLATE_NOT_AVAILABLE"));
    }

    /**
     * Task 6's review: the pending profile row and the outbox job are written in the wizard's transaction, so a failure
     * after scheduling leaves neither (nor the project or its pages); the admission release is
     * scheduled.
     */
    @Test
    void aFailureAfterSchedulingRollsBackThePendingRowAndTheOutboxJob() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        doAnswer(invocation -> {
                    invocation.callRealMethod();
                    assertThat(count(
                                    "SELECT count(*) FROM project_messaging.pending_catalog_registrations"
                                            + " WHERE project_id = ?",
                                    projectId))
                            .as("the real schedule wrote the pending row in the wizard's transaction")
                            .isEqualTo(1);
                    throw new WebBlockException(ErrorCode.INTERNAL_ERROR);
                })
                // Stubbed on the spy itself: through the bean's proxy the MANDATORY transaction check would refuse the
                // call.
                .when((CatalogRegistrationScheduler) AopTestUtils.getUltimateTargetObject(registrations))
                .schedule(eq(projectId), any());

        assertThat(create(owner, projectId, "Rolled back")).isEqualTo(500);

        assertThat(count("SELECT count(*) FROM project.projects WHERE project_id = ?", projectId))
                .isZero();
        assertThat(count("SELECT count(*) FROM project.pages WHERE project_id = ?", projectId))
                .isZero();
        assertThat(count(
                        "SELECT count(*) FROM project_messaging.pending_catalog_registrations WHERE project_id = ?",
                        projectId))
                .isZero();
        assertThat(count(
                        "SELECT count(*) FROM project_messaging.outbox_record WHERE record_key = ?",
                        "catalog:" + projectId))
                .isZero();
        assertThat(count(
                        "SELECT count(*) FROM project_messaging.outbox_record WHERE record_key = ?",
                        "payment:" + projectId))
                .isEqualTo(1);
    }

    // Lead ruling J1-R19: a release of the admission must never free it under a project that exists. Each request is
    // told apart by its name: body("R1") carries the business name "Shop R1".

    private MvcResult request(UUID owner, UUID projectId, String name) throws Exception {
        return send(owner, projectId, body(name));
    }

    /** Runs a request on a thread of that name, which the spies' answers recognise. */
    private CompletableFuture<MvcResult> onThread(String thread, UUID owner, UUID projectId, String name) {
        return sendOnThread(thread, owner, projectId, body(name));
    }

    private CompletableFuture<MvcResult> sendOnThread(String thread, UUID owner, UUID projectId, String body) {
        CompletableFuture<MvcResult> result = new CompletableFuture<>();
        new Thread(
                        () -> {
                            try {
                                result.complete(send(owner, projectId, body));
                            } catch (Throwable e) {
                                result.completeExceptionally(e);
                            }
                        },
                        thread)
                .start();
        return result;
    }

    private static boolean onThread(String name) {
        return Thread.currentThread().getName().equals(name);
    }

    /** The request named name fails permanently inside its transaction, after it has written the project. */
    private void failPermanently(UUID projectId, String name) {
        doThrow(new WebBlockException(ErrorCode.INTERNAL_ERROR))
                .when((CatalogRegistrationScheduler) AopTestUtils.getUltimateTargetObject(registrations))
                .schedule(eq(projectId), argThat(p -> p != null && ("Shop " + name).equals(p.businessName())));
    }

    private PaymentAdmissions paymentSpy() {
        return AopTestUtils.getUltimateTargetObject(payment);
    }

    /** The admission behind the project is still held: Payment refuses the ID to another client. */
    private void assertAdmissionHeld(UUID projectId) {
        assertThatThrownBy(() -> payment.admitWebsite(UUID.randomUUID(), projectId))
                .isInstanceOfSatisfying(
                        WebBlockException.class,
                        e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PROJECT_ID_CONFLICT));
    }

    private void awaitReleases(UUID projectId, long scheduled) {
        await().atMost(Duration.ofSeconds(60))
                .untilAsserted(() ->
                        assertThat(releases.state(projectId)).isEqualTo(new AdmissionReleaseFence.State(scheduled, 0)));
    }

    /**
     * The release fence is correct only under READ COMMITTED: after waiting for the lock it must see what the lock
     * holder committed. Both of the wizard's fenced transactions pin it, whatever the database's default.
     */
    @Test
    void theInsertAndTheReleaseDecisionRunReadCommitted() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        failPermanently(projectId, "R1");
        List<String> seen = new CopyOnWriteArrayList<>();
        doAnswer(invocation -> {
                    seen.add(TransactionSynchronizationManager.getCurrentTransactionIsolationLevel() + " "
                            + jdbc.queryForObject("SHOW transaction_isolation", String.class));
                    return invocation.callRealMethod();
                })
                .when((AdmissionReleaseFence) AopTestUtils.getUltimateTargetObject(releases))
                .lockAndRead(projectId);

        assertThat(request(owner, projectId, "R1").getResponse().getStatus()).isEqualTo(500);

        String readCommitted = TransactionDefinition.ISOLATION_READ_COMMITTED + " read committed";
        assertThat(seen).as("the insert, then the release decision").containsExactly(readCommitted, readCommitted);
    }

    /** Payment is never called inside a database transaction: neither by the wizard nor by the release job. */
    @Test
    void paymentIsCalledWithNoTransactionOpen() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        failPermanently(projectId, "R1");
        List<String> calls = new CopyOnWriteArrayList<>();
        doAnswer(invocation -> {
                    calls.add("admit, transaction " + TransactionSynchronizationManager.isActualTransactionActive());
                    return invocation.callRealMethod();
                })
                .when(paymentSpy())
                .admitWebsite(owner, projectId);
        doAnswer(invocation -> {
                    calls.add("release, transaction " + TransactionSynchronizationManager.isActualTransactionActive());
                    return invocation.callRealMethod();
                })
                .when(paymentSpy())
                .releaseWebsiteAdmission(projectId);

        assertThat(request(owner, projectId, "R1").getResponse().getStatus()).isEqualTo(500);
        awaitReleases(projectId, 1); // run by Namastack's AdmissionReleaseHandler

        assertThat(calls).containsExactly("admit, transaction false", "release, transaction false");
    }

    /** The reviewer's probe B: the retry comes while the failed request's release cannot reach Payment yet. */
    @Test
    void aRetryWhileTheReleaseIsPendingIs409WithRetryAfterAndSucceedsOnceItCompleted() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        failPermanently(projectId, "R1");
        doThrow(new WebBlockException(ErrorCode.PAYMENT_UNAVAILABLE))
                .when(paymentSpy())
                .releaseWebsiteAdmission(projectId);

        assertThat(request(owner, projectId, "R1").getResponse().getStatus()).isEqualTo(500);
        mvc.perform(put("/api/v1/projects/{id}", projectId)
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body("R2")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("ADMISSION_RELEASE_PENDING"))
                .andExpect(header().string(HttpHeaders.RETRY_AFTER, "5"));
        assertThat(count("SELECT count(*) FROM project.projects WHERE project_id = ?", projectId))
                .isZero();

        doCallRealMethod().when(paymentSpy()).releaseWebsiteAdmission(projectId);
        awaitReleases(projectId, 1);
        assertThat(request(owner, projectId, "R3").getResponse().getStatus()).isEqualTo(201);
        assertAdmissionHeld(projectId);
        assertThat(releases.state(projectId).scheduled())
                .as("no release was scheduled once the project existed")
                .isEqualTo(1);
    }

    /**
     * A retry handed the admission before the failed request released it, and whose insert comes after that release
     * completed: a fence that only looked for a pending release would let it create a project with no admission.
     */
    @Test
    void aRetryAdmittedBeforeTheReleaseIsRefusedEvenWhenTheReleaseHasCompleted() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        failPermanently(projectId, "R1");
        CountDownLatch r2Admitted = new CountDownLatch(1);
        CountDownLatch r2Go = new CountDownLatch(1);
        // Held after Payment admitted R2 and before its insert's fence (not inside the stand-in, whose methods are
        // synchronized: a spy's answer would hold its monitor and stall the release job).
        doAnswer(invocation -> {
                    if (onThread("r2")) {
                        r2Admitted.countDown();
                        assertThat(r2Go.await(30, TimeUnit.SECONDS)).isTrue();
                    }
                    return invocation.callRealMethod();
                })
                .when((AdmissionReleaseFence) AopTestUtils.getUltimateTargetObject(releases))
                .lockAndRead(projectId);

        CompletableFuture<MvcResult> r2 = onThread("r2", owner, projectId, "R2");
        assertThat(r2Admitted.await(30, TimeUnit.SECONDS)).isTrue();
        assertThat(request(owner, projectId, "R1").getResponse().getStatus()).isEqualTo(500);
        awaitReleases(projectId, 1); // the admission R2 holds is now released
        r2Go.countDown();

        MvcResult refused = r2.get(30, TimeUnit.SECONDS);
        assertThat(refused.getResponse().getStatus()).isEqualTo(409);
        assertThat(refused.getResponse().getContentAsString()).contains("ADMISSION_RELEASE_PENDING");
        assertThat(count("SELECT count(*) FROM project.projects WHERE project_id = ?", projectId))
                .isZero();

        awaitReleases(projectId, 2); // R2 released its admission as well
        assertThat(request(owner, projectId, "R3").getResponse().getStatus()).isEqualTo(201);
        assertAdmissionHeld(projectId);
        assertThat(releases.state(projectId).scheduled()).isEqualTo(2);
    }

    /**
     * The reviewer's FIX-B: the failed request decides on its release while the retry holds the fence with its
     * project written but not committed. It waits for the lock, sees the project and schedules no release.
     */
    @Test
    void aFailedRequestSchedulesNoReleaseForTheProjectItsRetryCreated() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        failPermanently(projectId, "R1");
        CountDownLatch r1Deciding = new CountDownLatch(1);
        CountDownLatch r2HoldsFence = new CountDownLatch(1);
        CountDownLatch r2Go = new CountDownLatch(1);
        AtomicInteger r1Fences = new AtomicInteger();
        doAnswer(invocation -> {
                    if (onThread("r1") && r1Fences.incrementAndGet() == 2) { // its release decision
                        r1Deciding.countDown();
                        assertThat(r2HoldsFence.await(30, TimeUnit.SECONDS)).isTrue();
                    }
                    return invocation.callRealMethod();
                })
                .when((AdmissionReleaseFence) AopTestUtils.getUltimateTargetObject(releases))
                .lockAndRead(projectId);
        doAnswer(invocation -> {
                    invocation.callRealMethod();
                    r2HoldsFence.countDown();
                    assertThat(r2Go.await(30, TimeUnit.SECONDS)).isTrue();
                    return null;
                })
                .when((CatalogRegistrationScheduler) AopTestUtils.getUltimateTargetObject(registrations))
                .schedule(eq(projectId), argThat(p -> p != null && "Shop R2".equals(p.businessName())));

        CompletableFuture<MvcResult> r1 = onThread("r1", owner, projectId, "R1");
        assertThat(r1Deciding.await(30, TimeUnit.SECONDS)).isTrue();
        CompletableFuture<MvcResult> r2 = onThread("r2", owner, projectId, "R2");
        assertThat(r2HoldsFence.await(30, TimeUnit.SECONDS)).isTrue();
        Thread.sleep(500);
        assertThat(r1).as("R1's release decision waits for R2's fence").isNotDone();
        r2Go.countDown();

        assertThat(r2.get(30, TimeUnit.SECONDS).getResponse().getStatus()).isEqualTo(201);
        assertThat(r1.get(30, TimeUnit.SECONDS).getResponse().getStatus()).isEqualTo(500);
        assertThat(releases.state(projectId).scheduled())
                .as("no release for a project that exists")
                .isZero();
        assertAdmissionHeld(projectId);
    }

    private String catalogProfile(UUID projectId, String column) throws Exception {
        try (Connection c = DriverManager.getConnection(
                        environment.getProperty("webblock.catalog.datasource.url"),
                        PostgresFixture.superuser(),
                        PostgresFixture.superuserPassword());
                PreparedStatement ps = c.prepareStatement(
                        "SELECT " + column + " FROM catalog.business_profiles WHERE website_id = ?")) {
            ps.setObject(1, projectId);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getString(1) : null;
            }
        }
    }

    private String bodyWithoutProfileDefaults(String name, String subdomain) {
        return """
                {"name":"%s","subdomain":"%s","businessTypeCode":"%s","templateCode":"blank",
                 "businessProfile":{"email":"shop@example.test"}}""".formatted(name, subdomain, typeCode);
    }

    @Test
    @DisplayName("FR-10.13: without timezone and businessName the catalog profile has Asia/Phnom_Penh and the name")
    void fr10_13_theProfileDefaultsAreStored() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        assertThat(send(owner, projectId, bodyWithoutProfileDefaults("Plain Name", subdomain("plain-name")))
                        .getResponse()
                        .getStatus())
                .isEqualTo(201);
        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(
                        () -> assertThat(catalogProfile(projectId, "timezone")).isNotNull());
        assertThat(catalogProfile(projectId, "timezone")).isEqualTo("Asia/Phnom_Penh");
        assertThat(catalogProfile(projectId, "business_name")).isEqualTo("Plain Name");
    }

    @Test
    @DisplayName("FR-10.13: a time zone that is sent is still checked, and one that is sent is kept")
    void fr10_13_aSentTimezoneIsCheckedAndKept() throws Exception {
        UUID owner = UUID.randomUUID();
        String bad = bodyWithoutProfileDefaults("Zoned", subdomain("zoned"))
                .replace("\"email\"", "\"timezone\":\"Mars/Base\",\"email\"");
        mvc.perform(put("/api/v1/projects/{id}", UUID.randomUUID())
                        .with(Users.user(owner))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(bad))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        UUID projectId = UUID.randomUUID();
        String good = bodyWithoutProfileDefaults("Zoned", subdomain("zoned"))
                .replace("\"email\"", "\"timezone\":\"UTC\",\"businessName\":\"Given\",\"email\"");
        assertThat(send(owner, projectId, good).getResponse().getStatus()).isEqualTo(201);
        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(
                        () -> assertThat(catalogProfile(projectId, "timezone")).isNotNull());
        assertThat(catalogProfile(projectId, "timezone")).isEqualTo("UTC");
        assertThat(catalogProfile(projectId, "business_name")).isEqualTo("Given");
    }

    @Test
    @DisplayName("FR-10.12: a live website of the owner with the same name, any case, outer spaces ignored, is 409")
    void fr10_12_aDuplicateNameIsRefused() throws Exception {
        UUID owner = UUID.randomUUID();
        assertThat(send(owner, UUID.randomUUID(), body("Kiln Cafe", subdomain("kiln-a")))
                        .getResponse()
                        .getStatus())
                .isEqualTo(201);
        for (String name : new String[] {"Kiln Cafe", "kiln cafe", "  KILN CAFE  "}) {
            UUID again = UUID.randomUUID();
            mvc.perform(put("/api/v1/projects/{id}", again)
                            .with(Users.user(owner))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content(body(name, subdomain("kiln-" + name.strip().length() + name.hashCode()))))
                    .andExpect(status().isConflict())
                    .andExpect(jsonPath("$.code").value("PROJECT_NAME_TAKEN"));
            assertThat(count("SELECT count(*) FROM project.projects WHERE project_id = ?", again))
                    .isZero();
        }
    }

    @Test
    @DisplayName("FR-10.12: a deleted website, or another owner's, with the same name does not block")
    void fr10_12_aDeletedOrForeignNameDoesNotBlock() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID gone = UUID.randomUUID();
        assertThat(send(owner, gone, body("Old Name", subdomain("old-a")))
                        .getResponse()
                        .getStatus())
                .isEqualTo(201);
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", gone);
        assertThat(send(owner, UUID.randomUUID(), body("Old Name", subdomain("old-b")))
                        .getResponse()
                        .getStatus())
                .isEqualTo(201);
        assertThat(send(UUID.randomUUID(), UUID.randomUUID(), body("Old Name", subdomain("old-c")))
                        .getResponse()
                        .getStatus())
                .isEqualTo(201);
    }

    @Test
    @DisplayName("FR-10.12: an identical repeat of a create still answers 200, not PROJECT_NAME_TAKEN")
    void fr10_12_anIdenticalRepeatIsStill200() throws Exception {
        UUID owner = UUID.randomUUID();
        UUID projectId = UUID.randomUUID();
        assertThat(send(owner, projectId, body("Repeat Me")).getResponse().getStatus())
                .isEqualTo(201);
        assertThat(send(owner, projectId, body("Repeat Me")).getResponse().getStatus())
                .isEqualTo(200);
    }
}
