package org.kshrd.webblock.project.application.generation.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import java.time.Clock;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.generation.model.CaptureOutcome;
import org.kshrd.webblock.project.application.messaging.handler.OutboxRecords;
import org.kshrd.webblock.project.application.page.handler.PageService;
import org.kshrd.webblock.project.application.page.model.NewPage;
import org.kshrd.webblock.project.application.page.model.PageContent;
import org.kshrd.webblock.project.application.page.model.PageRecord;
import org.kshrd.webblock.project.application.page.model.PageWrite;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.domain.generation.model.FailureCodes;
import org.kshrd.webblock.project.domain.generation.model.GenerationRequestStatus;
import org.kshrd.webblock.project.support.GenerationRows;
import org.kshrd.webblock.project.support.ServiceIntegrationTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.kshrd.webblock.project.support.Users;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Capture and the request API end to end on real PostgreSQL 18 and RabbitMQ, through MockMvc or the service.
 *
 * <p>The live admission job runs during these tests (lead ruling C3): Payment is a mock that refuses, so a request
 * fails soon after its capture; a test that needs a request to stay in flight holds Payment's answer back until the
 * test ends. Every test waits for its admission jobs to complete, so no job outlives the test's mock. Its own
 * application context, kept small and closed after the class (see ProjectCreationRefusalIntegrationTest).
 */
@ServiceIntegrationTest
@DirtiesContext
@TestPropertySource(
        properties = {
            "spring.datasource.hikari.maximum-pool-size=8",
            "spring.datasource.hikari.minimum-idle=1",
            "webblock.catalog.datasource.maximum-pool-size=2"
        })
class CaptureIntegrationTest {

    static final String PATH = "/api/v1/projects/{projectId}/generation-requests";
    static final GenerationAdmission REFUSED = GenerationAdmission.refused("NO_ENTITLEMENT");
    static final List<String> RECORD_KEYS =
            List.of("generationRequestId", "status", "failureCode", "createdAt", "updatedAt");

    @MockitoBean
    PaymentAdmissions payment;

    @Autowired
    MockMvc mvc;

    @Autowired
    GenerationRequestService service;

    @Autowired
    GenerationTransitions transitions;

    @Autowired
    ProjectService projects;

    @Autowired
    PageService pages;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    JdbcClient db;

    @Autowired
    JsonMapper json;

    @Autowired
    Clock clock;

    GenerationRows rows;

    /** What the Payment mock answers; read by the admission job's thread. */
    volatile Supplier<GenerationAdmission> paymentAnswer;

    /** Opened when the test ends: a held Payment answer then refuses. */
    final CountDownLatch release = new CountDownLatch(1);

    final List<UUID> testProjects = new CopyOnWriteArrayList<>();

    record Owned(UUID owner, UUID projectId) {}

    record Answer(int status, JsonNode body) {}

    @BeforeEach
    void setUp() {
        rows = new GenerationRows(jdbc, clock);
        paymentAnswer = () -> REFUSED;
        doAnswer(call -> paymentAnswer.get()).when(payment).admitGeneration(any(), any(), any());
    }

    /** Releases a held Payment answer, then waits until every admission job of the test's projects completed. */
    @AfterEach
    void finishJobs() {
        release.countDown();
        await().atMost(Duration.ofSeconds(60))
                .until(() -> db.sql("SELECT count(*) FROM " + OutboxRecords.TABLE
                                        + " o JOIN project.generation_requests r"
                                        + " ON o.record_key = 'generation:' || r.generation_request_id"
                                        + " WHERE r.project_id = ANY(?) AND o.status <> ?")
                                .params(testProjects.toArray(UUID[]::new), OutboxRecords.COMPLETED)
                                .query(Long.class)
                                .single()
                        == 0);
    }

    /** Payment's answer waits until the test ends, so the request stays captured (in flight). */
    private void holdPayment() {
        paymentAnswer = () -> {
            try {
                release.await(120, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return REFUSED;
        };
    }

    private Owned project() {
        UUID owner = Uuids.v7();
        UUID projectId = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(
                new NewProject(projectId, owner, "Capture", TestSubdomains.of(projectId), UUID.randomUUID(), "0.1.0")));
        testProjects.add(projectId);
        return new Owned(owner, projectId);
    }

    private UUID contract(Owned p) {
        return rows.contract(p.projectId(), p.owner());
    }

    private long editRevision(Owned p) {
        return projects.find(p.projectId()).orElseThrow().editRevision();
    }

    /** The website's one page, put in by the wizard's own path; the project's revision stays 0. */
    private PageRecord seedHome(Owned p, NewPage page) {
        tx.executeWithoutResult(s -> pages.insertInitialPages(p.projectId(), "0.1.0", List.of(page)));
        return tx.execute(s -> pages.livePages(p.projectId())).getFirst();
    }

    private JsonNode heading(String text) {
        return json.readTree("{\"root\":{\"props\":{}},\"content\":[{\"type\":\"Heading\",\"props\":{\"id\":\"h\","
                + "\"text\":\"" + text + "\"}}]}");
    }

    private Answer capture(UUID caller, UUID projectId, String key, UUID contractId) throws Exception {
        MvcResult result = mvc.perform(post(PATH, projectId)
                        .with(Users.user(caller))
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"generationContractId\":\"" + contractId + "\"}"))
                .andReturn();
        return answer(result);
    }

    private Answer capture(Owned p, String key, UUID contractId) throws Exception {
        return capture(p.owner(), p.projectId(), key, contractId);
    }

    private Answer read(UUID caller, String uri, Object... variables) throws Exception {
        return answer(mvc.perform(get(uri, variables).with(Users.user(caller))).andReturn());
    }

    private Answer answer(MvcResult result) throws Exception {
        return new Answer(
                result.getResponse().getStatus(),
                json.readTree(result.getResponse().getContentAsString()));
    }

    private static UUID requestId(Answer answer) {
        return UUID.fromString(
                answer.body().get("data").get("generationRequestId").asString());
    }

    private String status(UUID requestId) {
        return jdbc.queryForObject(
                "SELECT status::text FROM project.generation_requests WHERE generation_request_id = ?",
                String.class,
                requestId);
    }

    private void awaitStatus(UUID requestId, String expected) {
        await().atMost(Duration.ofSeconds(30)).until(() -> expected.equals(status(requestId)));
    }

    /** Frees the project's in-flight slot without waiting for the job (it may already have failed the request). */
    private void fail(UUID requestId) {
        tx.executeWithoutResult(s -> transitions.move(
                requestId,
                GenerationRequestStatus.captured,
                GenerationRequestStatus.failed,
                FailureCodes.ADMISSION_ERROR));
        assertThat(status(requestId)).isEqualTo("failed");
    }

    private long requestRows(UUID projectId) {
        return jdbc.queryForObject(
                "SELECT count(*) FROM project.generation_requests WHERE project_id = ?", Long.class, projectId);
    }

    private long jobRecords(UUID requestId) {
        return db.sql("SELECT count(*) FROM " + OutboxRecords.TABLE + " WHERE record_key = ?")
                .param("generation:" + requestId)
                .query(Long.class)
                .single();
    }

    private static <T> List<T> concurrently(int threads, Callable<T> call) throws Exception {
        CountDownLatch start = new CountDownLatch(1);
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        try {
            List<Future<T>> futures = new ArrayList<>();
            for (int i = 0; i < threads; i++) {
                futures.add(pool.submit(() -> {
                    start.await();
                    return call.call();
                }));
            }
            start.countDown();
            List<T> results = new ArrayList<>();
            for (Future<T> future : futures) {
                results.add(future.get(60, TimeUnit.SECONDS));
            }
            return results;
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void aCaptureAnswers202WithLocationAndFreezesTheInput() throws Exception {
        Owned p = project();
        UUID contractId = contract(p);
        seedHome(p, new NewPage("Home", "/", heading("Welcome"), "Home title", "Home description", 0));
        long revision = editRevision(p);
        List<PageRecord> live = tx.execute(s -> pages.livePages(p.projectId()));

        MvcResult result = mvc.perform(post(PATH, p.projectId())
                        .with(Users.user(p.owner()))
                        .header("Idempotency-Key", "first")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"generationContractId\":\"" + contractId + "\"}"))
                .andReturn();
        Answer answer = answer(result);

        assertThat(answer.status()).isEqualTo(202);
        JsonNode data = answer.body().get("data");
        assertThat(data.propertyNames()).containsExactlyInAnyOrder("generationRequestId", "status");
        assertThat(data.get("status").asString()).isEqualTo("captured");
        UUID requestId = requestId(answer);
        assertThat(result.getResponse().getHeader("Location"))
                .isEqualTo("/api/v1/projects/" + p.projectId() + "/generation-requests/" + requestId);

        Map<String, Object> row = jdbc.queryForMap(
                "SELECT idempotency_key, request_hash, generation_contract_id, captured_edit_revision,"
                        + " captured_input::text AS captured_input, captured_input_hash FROM project.generation_requests"
                        + " WHERE generation_request_id = ?",
                requestId);
        assertThat(row.get("idempotency_key")).isEqualTo("first");
        assertThat(row.get("generation_contract_id")).isEqualTo(contractId);
        assertThat(row.get("captured_edit_revision")).isEqualTo(revision);
        assertThat(row.get("request_hash"))
                .isEqualTo(InputHasher.sha256("{\"generationContractId\":\"" + contractId + "\"}"));
        String inputText = (String) row.get("captured_input");
        assertThat(row.get("captured_input_hash")).isEqualTo(InputHasher.sha256(inputText));

        JsonNode input = json.readTree(inputText);
        assertThat(input.propertyNames())
                .containsExactlyInAnyOrder("componentCatalogVersion", "generationContractId", "pages");
        assertThat(input.get("componentCatalogVersion").asString()).isEqualTo("0.1.0");
        assertThat(input.get("generationContractId").asString()).isEqualTo(contractId.toString());
        JsonNode captured = input.get("pages");
        assertThat(captured.size()).isEqualTo(1);
        assertThat(live).hasSize(1);
        for (int i = 0; i < live.size(); i++) {
            PageRecord expected = live.get(i);
            JsonNode page = captured.get(i);
            assertThat(page.propertyNames())
                    .containsExactlyInAnyOrder(
                            "pageId", "path", "name", "sortOrder", "seoTitle", "seoDescription", "puckTree");
            assertThat(page.get("pageId").asString())
                    .isEqualTo(expected.pageId().toString());
            assertThat(page.get("path").asString()).isEqualTo(expected.path());
            assertThat(page.get("name").asString()).isEqualTo(expected.name());
            assertThat(page.get("sortOrder").asInt()).isEqualTo(expected.sortOrder());
            assertThat(page.get("puckTree")).isEqualTo(expected.puckTree());
        }
        assertThat(captured.get(0).get("seoTitle").asString()).isEqualTo("Home title");
        assertThat(captured.get(0).get("seoDescription").asString()).isEqualTo("Home description");

        assertThat(jobRecords(requestId)).isEqualTo(1);
        awaitStatus(requestId, "failed");
    }

    @Test
    void twentyConcurrentCapturesMakeOneRequest() throws Exception {
        holdPayment();
        Owned p = project();
        UUID contractId = contract(p);

        List<Answer> answers = concurrently(20, () -> capture(p, "key-" + UUID.randomUUID(), contractId));

        assertThat(answers).extracting(Answer::status).filteredOn(s -> s == 202).hasSize(1);
        List<Answer> refused = answers.stream().filter(a -> a.status() != 202).toList();
        assertThat(refused).hasSize(19).allSatisfy(a -> {
            assertThat(a.status()).isEqualTo(409);
            assertThat(a.body().get("code").asString()).isEqualTo("GENERATION_IN_PROGRESS");
        });
        assertThat(requestRows(p.projectId())).isEqualTo(1);
    }

    @Test
    void twentyConcurrentSameKeyRequestsMakeOneRow() throws Exception {
        Owned p = project();
        UUID contractId = contract(p);

        List<Answer> answers = concurrently(20, () -> capture(p, "same-key", contractId));

        assertThat(answers).extracting(Answer::status).containsOnly(202, 200);
        assertThat(answers).extracting(Answer::status).filteredOn(s -> s == 202).hasSize(1);
        assertThat(answers.stream().map(CaptureIntegrationTest::requestId).distinct())
                .hasSize(1);
        assertThat(requestRows(p.projectId())).isEqualTo(1);
        UUID requestId = requestId(answers.getFirst());
        assertThat(jobRecords(requestId)).isEqualTo(1);
        awaitStatus(requestId, "failed");
    }

    @Test
    void theSameKeyWithAnotherBodyIsReused() throws Exception {
        Owned p = project();
        UUID first = contract(p);
        UUID second = contract(p);
        Answer created = capture(p, "reused", first);
        assertThat(created.status()).isEqualTo(202);

        Answer reused = capture(p, "reused", second);

        assertThat(reused.status()).isEqualTo(409);
        assertThat(reused.body().get("code").asString()).isEqualTo("IDEMPOTENCY_KEY_REUSED");
        assertThat(requestRows(p.projectId())).isEqualTo(1);
        awaitStatus(requestId(created), "failed");
    }

    /** Review focus 2: the response was lost after the request was admitted; the repeat finds the one request. */
    @Test
    void aRepeatAfterAdmissionReturnsTheOriginalAndSchedulesNothing() throws Exception {
        paymentAnswer = () -> GenerationAdmission.ADMITTED;
        Owned p = project();
        UUID contractId = contract(p);
        Answer created = capture(p, "lost-response", contractId);
        assertThat(created.status()).isEqualTo(202);
        UUID requestId = requestId(created);
        awaitStatus(requestId, "generating");

        Answer repeat = capture(p, "lost-response", contractId);

        assertThat(repeat.status()).isEqualTo(200);
        JsonNode data = repeat.body().get("data");
        assertThat(data.propertyNames()).containsExactlyInAnyOrder("generationRequestId", "status");
        assertThat(requestId(repeat)).isEqualTo(requestId);
        assertThat(data.get("status").asString()).isEqualTo("generating");
        assertThat(requestRows(p.projectId())).isEqualTo(1);
        // One row and its one job record: the repeat scheduled nothing.
        assertThat(jobRecords(requestId)).isEqualTo(1);
    }

    /** J2-19: idempotency answers before the read-only check. */
    @Test
    void aReplayOnAProjectThatBecameReadOnlyAnswers200() throws Exception {
        Owned p = project();
        UUID contractId = contract(p);
        Answer created = capture(p, "before-suspension", contractId);
        assertThat(created.status()).isEqualTo(202);
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", p.projectId());

        Answer replay = capture(p, "before-suspension", contractId);
        Answer fresh = capture(p, "after-suspension", contractId);

        assertThat(replay.status()).isEqualTo(200);
        assertThat(requestId(replay)).isEqualTo(requestId(created));
        assertThat(fresh.status()).isEqualTo(422);
        assertThat(fresh.body().get("code").asString()).isEqualTo("PROJECT_READ_ONLY");
        assertThat(requestRows(p.projectId())).isEqualTo(1);
        awaitStatus(requestId(created), "failed");
    }

    @Test
    void aContractOfAnotherProjectIsNotFound() throws Exception {
        Owned p = project();
        UUID owner = p.owner();
        UUID otherProject = Uuids.v7();
        tx.executeWithoutResult(s -> projects.create(new NewProject(
                otherProject, owner, "Other", TestSubdomains.of(otherProject), UUID.randomUUID(), "0.1.0")));
        testProjects.add(otherProject);
        UUID foreign = rows.contract(otherProject, owner);

        for (UUID contractId : List.of(foreign, Uuids.v7())) {
            Answer answer = capture(p, "contract-" + contractId, contractId);
            assertThat(answer.status()).isEqualTo(404);
            assertThat(answer.body().get("code").asString()).isEqualTo("GENERATION_CONTRACT_NOT_FOUND");
        }
        assertThat(requestRows(p.projectId())).isZero();
    }

    @Test
    void anotherOwnerGetsProjectNotFound() throws Exception {
        Owned p = project();
        UUID contractId = contract(p);
        Answer created = capture(p, "owner-key", contractId);
        assertThat(created.status()).isEqualTo(202);
        UUID requestId = requestId(created);
        UUID stranger = Uuids.v7();

        List<Answer> answers = List.of(
                capture(stranger, p.projectId(), "owner-key", contractId),
                capture(stranger, p.projectId(), "stranger-key", contractId),
                read(stranger, PATH, p.projectId()),
                read(stranger, PATH + "/{generationRequestId}", p.projectId(), requestId));

        assertThat(answers).allSatisfy(a -> {
            assertThat(a.status()).isEqualTo(404);
            assertThat(a.body().get("code").asString()).isEqualTo("PROJECT_NOT_FOUND");
        });
        assertThat(requestRows(p.projectId())).isEqualTo(1);
        // a bad cursor or limit is answered before the owner check (J1 order)
        Answer badCursor = read(stranger, PATH + "?cursor=bm90LWEtY3Vyc29y", p.projectId());
        assertThat(badCursor.status()).isEqualTo(400);
        assertThat(badCursor.body().get("code").asString()).isEqualTo("VALIDATION_FAILED");
        assertThat(read(stranger, PATH + "?limit=0", p.projectId()).status()).isEqualTo(400);
        awaitStatus(requestId, "failed");
    }

    /**
     * Spec 5.3.3: a page save locks the project row first, so a capture sees it whole or not at all. Each round saves
     * page one with a new heading while a capture starts; the captured trees must be those of captured_edit_revision.
     */
    @Test
    void aCaptureAgainstAConcurrentPageSaveIsConsistent() throws Exception {
        Owned p = project();
        UUID contractId = contract(p);
        PageRecord first = seedHome(p, new NewPage("One", "/", heading("r0"), null, null, 0));
        Map<Long, String> headingAt = new HashMap<>();
        long revision = editRevision(p);
        headingAt.put(revision, "r0");
        ExecutorService pool = Executors.newFixedThreadPool(2);
        try {
            for (int round = 1; round <= 50; round++) {
                String text = "r" + round;
                long expected = revision;
                CountDownLatch start = new CountDownLatch(1);
                Future<PageWrite> save = pool.submit(() -> {
                    start.await();
                    return pages.update(
                            p.projectId(),
                            first.pageId(),
                            p.owner(),
                            expected,
                            new PageContent("One", "/", heading(text), null, null),
                            0);
                });
                Future<CaptureOutcome> capture = pool.submit(() -> {
                    start.await();
                    return service.capture(p.projectId(), p.owner(), "round-" + text, contractId);
                });
                start.countDown();
                revision = save.get(30, TimeUnit.SECONDS).editRevision();
                headingAt.put(revision, text);
                UUID requestId = capture.get(30, TimeUnit.SECONDS).generationRequestId();

                Map<String, Object> row = jdbc.queryForMap(
                        "SELECT captured_edit_revision, captured_input::text AS captured_input"
                                + " FROM project.generation_requests WHERE generation_request_id = ?",
                        requestId);
                long capturedRevision = (Long) row.get("captured_edit_revision");
                assertThat(capturedRevision).isIn(expected, revision);
                JsonNode capturedPages =
                        json.readTree((String) row.get("captured_input")).get("pages");
                assertThat(capturedPages.size()).isEqualTo(1);
                assertThat(capturedPages.get(0).get("puckTree")).isEqualTo(heading(headingAt.get(capturedRevision)));
                fail(requestId);
            }
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void listsNewestFirstInCursorPagesAndGetsOne() throws Exception {
        Owned p = project();
        UUID contractId = contract(p);
        List<UUID> created = new ArrayList<>();
        for (int i = 1; i <= 3; i++) {
            Answer answer = capture(p, "list-" + i, contractId);
            assertThat(answer.status()).isEqualTo(202);
            created.add(requestId(answer));
            fail(created.getLast());
        }

        Answer firstPage = read(p.owner(), PATH + "?limit=2", p.projectId());
        assertThat(firstPage.status()).isEqualTo(200);
        JsonNode items = firstPage.body().get("data");
        assertThat(items.size()).isEqualTo(2);
        assertThat(items.get(0).get("generationRequestId").asString())
                .isEqualTo(created.get(2).toString());
        assertThat(items.get(1).get("generationRequestId").asString())
                .isEqualTo(created.get(1).toString());
        String cursor =
                firstPage.body().get("meta").get("page").get("nextCursor").asString();
        assertThat(cursor).isNotBlank();

        Answer lastPage = read(p.owner(), PATH + "?limit=2&cursor=" + cursor, p.projectId());
        JsonNode rest = lastPage.body().get("data");
        assertThat(rest.size()).isEqualTo(1);
        assertThat(rest.get(0).get("generationRequestId").asString())
                .isEqualTo(created.get(0).toString());
        assertThat(lastPage.body().get("meta").get("page").get("nextCursor").isNull())
                .isTrue();
        Answer badCursor = read(p.owner(), PATH + "?cursor=bm90LWEtY3Vyc29y", p.projectId());
        assertThat(badCursor.status()).isEqualTo(400);
        assertThat(badCursor.body().get("errors").get(0).get("field").asString())
                .isEqualTo("cursor");
        assertThat(badCursor.body().get("errors").get(0).get("code").asString()).isEqualTo("VALUE_INVALID");

        List<JsonNode> all = new ArrayList<>();
        items.forEach(all::add);
        rest.forEach(all::add);
        assertThat(all).allSatisfy(item -> {
            assertThat(item.propertyNames()).containsExactlyInAnyOrderElementsOf(RECORD_KEYS);
            assertThat(item.get("status").asString()).isEqualTo("failed");
            assertThat(item.get("failureCode").isString()).isTrue();
        });
        assertThat(firstPage.body().toString()).doesNotContain("capturedInput", "pages");

        Answer one = read(p.owner(), PATH + "/{generationRequestId}", p.projectId(), created.get(1));
        assertThat(one.status()).isEqualTo(200);
        assertThat(one.body().get("data").propertyNames()).containsExactlyInAnyOrderElementsOf(RECORD_KEYS);
        assertThat(one.body().get("data")).isEqualTo(items.get(1));

        Owned other = project();
        UUID otherRequest = requestId(capture(other, "elsewhere", contract(other)));
        for (UUID unknown : List.of(Uuids.v7(), otherRequest)) {
            Answer missing = read(p.owner(), PATH + "/{generationRequestId}", p.projectId(), unknown);
            assertThat(missing.status()).isEqualTo(404);
            assertThat(missing.body().get("code").asString()).isEqualTo("GENERATION_REQUEST_NOT_FOUND");
        }
        awaitStatus(otherRequest, "failed");
    }
}
