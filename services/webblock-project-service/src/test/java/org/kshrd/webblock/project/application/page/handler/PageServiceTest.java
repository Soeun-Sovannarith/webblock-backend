package org.kshrd.webblock.project.application.page.handler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.function.Consumer;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.kshrd.webblock.project.application.page.model.NewPage;
import org.kshrd.webblock.project.application.page.model.PageContent;
import org.kshrd.webblock.project.application.page.model.PageList;
import org.kshrd.webblock.project.application.page.model.PageRecord;
import org.kshrd.webblock.project.application.page.model.PageWrite;
import org.kshrd.webblock.project.application.project.handler.ProjectService;
import org.kshrd.webblock.project.application.project.model.NewProject;
import org.kshrd.webblock.project.application.project.model.ProjectRecord;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.Uuids;
import org.kshrd.webblock.project.infrastructure.config.page.PageConfigImport;
import org.kshrd.webblock.project.infrastructure.config.project.ProjectConfigImport;
import org.kshrd.webblock.project.support.JpaSliceTest;
import org.kshrd.webblock.project.support.TestSubdomains;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.ImportAutoConfiguration;
import org.springframework.boot.jackson.autoconfigure.JacksonAutoConfiguration;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.NullNode;

@JpaSliceTest
@Import({
    PageService.class,
    PageRules.class,
    PageConfigImport.class,
    ProjectService.class,
    ProjectConfigImport.class,
    PuckValidator.class
})
@ImportAutoConfiguration(JacksonAutoConfiguration.class)
@TestPropertySource(properties = "webblock.pages.max-puck-tree-bytes=4096")
class PageServiceTest {

    @Autowired
    PageService pages;

    @Autowired
    ProjectService projects;

    @Autowired
    TransactionTemplate tx;

    @Autowired
    JdbcTemplate jdbc;

    @Autowired
    JsonMapper json;

    private ProjectRecord project(String catalogVersion) {
        UUID id = Uuids.v7();
        return tx.execute(s -> projects.create(
                new NewProject(id, UUID.randomUUID(), "P", TestSubdomains.of(id), UUID.randomUUID(), catalogVersion)));
    }

    private JsonNode heading(String text) {
        return json.readTree(
                "{\"root\":{\"props\":{}},\"content\":[{\"type\":\"Heading\",\"props\":{\"id\":\"h\",\"text\":\"" + text
                        + "\"}}]}");
    }

    /** A valid tree whose nesting depth (the tree object itself is 1) is exactly {@code depth}. */
    private JsonNode nested(int depth) {
        int arrays = depth - 3; // the tree, root and props are three levels
        return json.readTree(
                "{\"root\":{\"props\":{\"n\":" + "[".repeat(arrays) + "]".repeat(arrays) + "}},\"content\":[]}");
    }

    /** A valid tree with {@code number} (JSON text) at root.props.n. */
    private JsonNode withNumber(String number) {
        return json.readTree("{\"root\":{\"props\":{\"n\":[[" + number + "]]}},\"content\":[]}");
    }

    private PageContent content(String path) {
        return new PageContent("Page " + path, path, heading("Hi"), null, null);
    }

    private long revision(ProjectRecord p) {
        return projects.find(p.projectId()).orElseThrow().editRevision();
    }

    private static Consumer<Throwable> code(ErrorCode expected) {
        return e -> assertThat(((WebBlockException) e).errorCode()).isEqualTo(expected);
    }

    private static Consumer<Throwable> detail(String fieldAndCode) {
        return e -> assertThat(((WebBlockException) e).details())
                .extracting(d -> d.field() + ":" + d.code())
                .containsExactly(fieldAndCode);
    }

    /** The one page a website has, put in by the wizard's own path; the project's revision stays 0. */
    private PageRecord seedHome(ProjectRecord p) {
        tx.executeWithoutResult(s -> pages.insertInitialPages(
                p.projectId(),
                p.componentCatalogVersion(),
                List.of(new NewPage("Home", "/", heading("Hi"), null, null, 0))));
        return pages.list(p.projectId(), p.ownerUserId()).pages().getFirst();
    }

    private PageWrite save(ProjectRecord p, PageRecord page, long expected, PageContent content, int sortOrder) {
        return pages.update(p.projectId(), page.pageId(), p.ownerUserId(), expected, content, sortOrder);
    }

    @Test
    @DisplayName("FR-18: a save moves the revision once and keeps the one page")
    void fr18_saveMovesTheRevisionAndKeepsTheOnePage() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        PageWrite saved = save(p, home, 0, new PageContent("Home", "/", heading("New"), null, null), 0);
        assertThat(saved.editRevision()).isEqualTo(1);
        assertThat(saved.page().puckTree()).isEqualTo(heading("New"));
        assertThat(pages.list(p.projectId(), p.ownerUserId()).pages())
                .extracting(PageRecord::path)
                .containsExactly("/");
    }

    @Test
    void aStaleRevisionIsRefusedAndChangesNothing() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        save(p, home, 0, content("/"), 0);
        assertThatThrownBy(() -> save(p, home, 0, content("/x"), 0)).satisfies(code(ErrorCode.STALE_EDIT_REVISION));
        assertThat(revision(p)).isEqualTo(1);
        assertThat(pages.list(p.projectId(), p.ownerUserId()).pages())
                .extracting(PageRecord::path)
                .containsExactly("/");
    }

    @Test
    void aSoftDeletedPageIsNotFound() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        jdbc.update("UPDATE project.pages SET deleted_at = now() WHERE page_id = ?", home.pageId());
        assertThatThrownBy(() -> save(p, home, 0, content("/"), 0)).satisfies(code(ErrorCode.PAGE_NOT_FOUND));
        assertThat(revision(p)).isZero();
    }

    @Test
    void updateChangesThePageAndMovesTheRevision() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        PageWrite updated =
                save(p, home, 0, new PageContent("Team", "/team", heading("Us"), "Our team", "Who we are"), 7);
        assertThat(updated.editRevision()).isEqualTo(1);
        assertThat(updated.page())
                .extracting(PageRecord::name, PageRecord::path, PageRecord::seoTitle, PageRecord::sortOrder)
                .containsExactly("Team", "/team", "Our team", 7);
        assertThat(pages.list(p.projectId(), p.ownerUserId()).pages())
                .extracting(PageRecord::path, PageRecord::puckTree)
                .containsExactly(org.assertj.core.groups.Tuple.tuple("/team", heading("Us")));
    }

    @Test
    void anUnknownPageIsNotFoundAndTheRevisionStays() {
        ProjectRecord p = project("test-1");
        assertThatThrownBy(() -> pages.update(p.projectId(), UUID.randomUUID(), p.ownerUserId(), 0, content("/"), 0))
                .satisfies(code(ErrorCode.PAGE_NOT_FOUND));
        assertThat(revision(p)).isZero();
    }

    @Test
    void aPageOfAnotherProjectIsNotFound() {
        ProjectRecord mine = project("test-1");
        ProjectRecord other = project("test-1");
        UUID foreign = seedHome(other).pageId();
        assertThatThrownBy(() -> pages.update(mine.projectId(), foreign, mine.ownerUserId(), 0, content("/"), 0))
                .satisfies(code(ErrorCode.PAGE_NOT_FOUND));
        assertThat(revision(mine)).isZero();
        assertThat(pages.list(other.projectId(), other.ownerUserId()).pages()).hasSize(1);
    }

    @Test
    void anInvalidTreeIsRefusedBeforeTheRevisionMoves() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        PageContent bad = new PageContent(
                "Bad",
                "/",
                json.readTree(
                        "{\"root\":{\"props\":{}},\"content\":[{\"type\":\"Heading\",\"props\":{\"id\":\"h\"}}]}"),
                null,
                null);
        assertThatThrownBy(() -> save(p, home, 0, bad, 0))
                .satisfies(code(ErrorCode.VALIDATION_FAILED))
                .satisfies(e -> assertThat(((WebBlockException) e).details())
                        .extracting(d -> d.field() + ":" + d.code())
                        .contains("puckTree/content/0/props/text:PUCK_TREE_INVALID"));
        assertThat(revision(p)).isZero();
    }

    /** J1-R10: PuckValidator never sees a null tree; a missing or JSON-null tree is a field error. */
    @Test
    void aMissingOrNullTreeIsAFieldErrorBeforeTheValidatorRuns() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        for (JsonNode missing : new JsonNode[] {null, NullNode.getInstance()}) {
            assertThatThrownBy(() -> save(p, home, 0, new PageContent("Home", "/", missing, null, null), 0))
                    .satisfies(code(ErrorCode.VALIDATION_FAILED))
                    .satisfies(detail("puckTree:FIELD_REQUIRED"));
            List<NewPage> initial = List.of(new NewPage("Home", "/", missing, null, null, 0));
            assertThatThrownBy(() ->
                            tx.executeWithoutResult(s -> pages.insertInitialPages(p.projectId(), "test-1", initial)))
                    .satisfies(code(ErrorCode.VALIDATION_FAILED))
                    .satisfies(detail("puckTree:FIELD_REQUIRED"));
        }
        assertThat(revision(p)).isZero();
        assertThat(pages.list(p.projectId(), p.ownerUserId()).pages()).hasSize(1);
    }

    /**
     * J1-R10: a tree may be nested only so deep that every response that embeds it stays within Jackson's write depth
     * (500); deeper is refused before the validator walks it.
     */
    @Test
    void aTreeNestedTooDeeplyIsRefusedAndTheDeepestAllowedOneIsServed() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        assertThatThrownBy(() -> save(
                        p, home, 0, new PageContent("Deep", "/", nested(PageRules.MAX_TREE_DEPTH + 1), null, null), 0))
                .satisfies(code(ErrorCode.VALIDATION_FAILED))
                .satisfies(detail("puckTree:PUCK_TREE_INVALID"));
        assertThat(revision(p)).isZero();
        PageWrite deepest =
                save(p, home, 0, new PageContent("Deep", "/", nested(PageRules.MAX_TREE_DEPTH), null, null), 0);
        assertThat(deepest.page().puckTree()).isEqualTo(nested(PageRules.MAX_TREE_DEPTH));
        assertThat(pages.list(p.projectId(), p.ownerUserId()).pages().getFirst().puckTree())
                .isEqualTo(nested(PageRules.MAX_TREE_DEPTH));
    }

    /**
     * H-1: jsonb keeps a number as numeric and prints it without an exponent, and it has no Infinity: a number whose
     * plain form is longer than 32 characters, or that is not finite, is refused before anything is stored.
     */
    @Test
    void numbersThatJsonbWouldExpandOrChangeAreRefused() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        for (String number : new String[] {"1e308", "-1e308", "1e1000", "-1e1000", "1e-300", "1e32", "-1e31"}) {
            assertThatThrownBy(() -> save(p, home, 0, new PageContent("N", "/", withNumber(number), null, null), 0))
                    .as(number)
                    .satisfies(code(ErrorCode.VALIDATION_FAILED))
                    .satisfies(detail("puckTree:PUCK_TREE_INVALID"));
        }
        assertThat(revision(p)).isZero();
        PageWrite normal = save(
                p,
                home,
                0,
                new PageContent("N", "/", withNumber("1.5, -0.25, 1e31, 1e-29, 12345678901234567890123"), null, null),
                0);
        assertThat(normal.editRevision()).isEqualTo(1);
        JsonNode stored =
                pages.list(p.projectId(), p.ownerUserId()).pages().getFirst().puckTree();
        JsonNode numbers = stored.get("root").get("props").get("n").get(0);
        assertThat(numbers.get(0).doubleValue()).isEqualTo(1.5);
        assertThat(numbers.get(1).doubleValue()).isEqualTo(-0.25);
        assertThat(numbers.get(2).decimalValue()).isEqualByComparingTo("1e31");
        assertThat(numbers.get(4).bigIntegerValue()).isEqualTo(new java.math.BigInteger("12345678901234567890123"));
    }

    /** L-1: 0 <= sortOrder <= MAX_SORT_ORDER. */
    @Test
    void theSortOrderIsBounded() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        for (int bad : new int[] {-1, PageRules.MAX_SORT_ORDER + 1, Integer.MAX_VALUE}) {
            assertThatThrownBy(() -> save(p, home, 0, content("/"), bad))
                    .satisfies(code(ErrorCode.VALIDATION_FAILED))
                    .satisfies(detail("sortOrder:FIELD_OUT_OF_RANGE"));
        }
        assertThat(save(p, home, 0, content("/"), PageRules.MAX_SORT_ORDER)
                        .page()
                        .sortOrder())
                .isEqualTo(PageRules.MAX_SORT_ORDER);
        assertThat(save(p, home, 1, content("/"), 0).page().sortOrder()).isZero();
        List<NewPage> negative = List.of(new NewPage("Home", "/", heading("Hi"), null, null, -1));
        ProjectRecord fresh = project("test-1");
        assertThatThrownBy(() ->
                        tx.executeWithoutResult(s -> pages.insertInitialPages(fresh.projectId(), "test-1", negative)))
                .satisfies(code(ErrorCode.VALIDATION_FAILED))
                .satisfies(detail("sortOrder:FIELD_OUT_OF_RANGE"));
    }

    @Test
    void anOversizedTreeIsRefused() {
        ProjectRecord p = project("0.1.0");
        PageRecord home = seedHome(p);
        JsonNode big = json.readTree("{\"root\":{\"props\":{\"blob\":\"" + "x".repeat(5000) + "\"}},\"content\":[]}");
        assertThatThrownBy(() -> save(p, home, 0, new PageContent("Big", "/", big, null, null), 0))
                .satisfies(code(ErrorCode.PAYLOAD_TOO_LARGE));
        assertThat(revision(p)).isZero();
    }

    @Test
    void aStrangerSeesNotFoundEverywhere() {
        ProjectRecord p = project("test-1");
        UUID stranger = UUID.randomUUID();
        UUID pageId = seedHome(p).pageId();
        assertThatThrownBy(() -> pages.list(p.projectId(), stranger)).satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
        assertThatThrownBy(() -> pages.update(p.projectId(), pageId, stranger, 0, content("/a"), 0))
                .satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
        assertThatThrownBy(() -> pages.list(UUID.randomUUID(), p.ownerUserId()))
                .satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
        assertThat(revision(p)).isZero();
    }

    @Test
    void aSuspendedProjectIsReadOnlyOnEveryWrite() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        jdbc.update("UPDATE project.projects SET status = 'suspended' WHERE project_id = ?", p.projectId());
        assertThatThrownBy(() -> save(p, home, 0, content("/b"), 3)).satisfies(code(ErrorCode.PROJECT_READ_ONLY));
        PageList list = pages.list(p.projectId(), p.ownerUserId());
        assertThat(list.editRevision()).isZero();
        assertThat(list.pages()).containsExactly(home);
    }

    @Test
    @DisplayName("FR-38.3: every page call on a deleted project is PROJECT_NOT_FOUND")
    void fr38_3_aDeletedProjectIsNotFoundForItsOwnerWhateverTheContent() {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        jdbc.update("UPDATE project.projects SET deleted_at = now() WHERE project_id = ?", p.projectId());
        PageContent invalid = new PageContent("Bad", "/", json.readTree("{\"content\":[]}"), null, null);
        assertThatThrownBy(() -> save(p, home, 0, invalid, 0)).satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
        assertThatThrownBy(() -> save(p, home, 0, content("/x"), 0)).satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
        assertThatThrownBy(() -> pages.list(p.projectId(), p.ownerUserId()))
                .satisfies(code(ErrorCode.PROJECT_NOT_FOUND));
    }

    @Test
    void concurrentSavesWithTheSameRevisionLetExactlyOneWin() throws Exception {
        ProjectRecord p = project("test-1");
        PageRecord home = seedHome(p);
        List<Object> outcomes =
                race(() -> save(p, home, 0, content("/"), 0), () -> save(p, home, 0, content("/other"), 0));
        assertThat(outcomes).filteredOn(PageWrite.class::isInstance).hasSize(1);
        assertThat(outcomes)
                .filteredOn(WebBlockException.class::isInstance)
                .singleElement()
                .satisfies(
                        e -> assertThat(((WebBlockException) e).errorCode()).isEqualTo(ErrorCode.STALE_EDIT_REVISION));
        assertThat(revision(p)).isEqualTo(1);
    }

    @Test
    void initialPagesNeedTheCallersTransaction() {
        ProjectRecord p = project("test-1");
        List<NewPage> initial = List.of(new NewPage("Home", "/", heading("Hi"), null, null, 0));
        assertThatThrownBy(() -> pages.insertInitialPages(p.projectId(), "test-1", initial))
                .isInstanceOf(org.springframework.transaction.IllegalTransactionStateException.class);
        tx.executeWithoutResult(s -> pages.insertInitialPages(p.projectId(), "test-1", initial));
        assertThat(pages.list(p.projectId(), p.ownerUserId()).pages())
                .extracting(PageRecord::path)
                .containsExactly("/");
        assertThat(revision(p)).isZero();
    }

    @Test
    @DisplayName("FR-18: a website has exactly one page, so a template with none or several is refused")
    void fr18_initialPagesAreCheckedAgainstTheCatalogAndLimitedToOne() {
        ProjectRecord p = project("test-1");
        JsonNode invalid = json.readTree("{\"root\":{\"props\":{}}}");
        assertThatThrownBy(() -> tx.executeWithoutResult(s -> pages.insertInitialPages(
                        p.projectId(), "test-1", List.of(new NewPage("Home", "/", invalid, null, null, 0)))))
                .satisfies(code(ErrorCode.VALIDATION_FAILED));
        List<NewPage> five = new ArrayList<>();
        for (int i = 0; i < 5; i++) {
            five.add(new NewPage("P" + i, "/p" + i, heading("Hi"), null, null, i));
        }
        assertThatThrownBy(() -> tx.executeWithoutResult(s -> pages.insertInitialPages(p.projectId(), "test-1", five)))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() ->
                        tx.executeWithoutResult(s -> pages.insertInitialPages(p.projectId(), "test-1", List.of())))
                .isInstanceOf(IllegalArgumentException.class);
        assertThat(pages.list(p.projectId(), p.ownerUserId()).pages()).isEmpty();
    }

    /** ARCH-L-4: a template page obeys the whole page rules, as the request records' Bean Validation does. */
    @Test
    void initialPagesObeyTheNameAndSeoLengths() {
        ProjectRecord p = project("test-1");
        List<NewPage> long1 =
                List.of(new NewPage("n".repeat(256), "/", heading("Hi"), "t".repeat(256), "d".repeat(501), 0));
        assertThatThrownBy(() -> tx.executeWithoutResult(s -> pages.insertInitialPages(p.projectId(), "test-1", long1)))
                .satisfies(code(ErrorCode.VALIDATION_FAILED))
                .satisfies(e -> assertThat(((WebBlockException) e).details())
                        .extracting(d -> d.field() + ":" + d.code())
                        .containsExactly(
                                "name:FIELD_LENGTH_INVALID",
                                "seoTitle:FIELD_LENGTH_INVALID",
                                "seoDescription:FIELD_LENGTH_INVALID"));
        List<NewPage> longest =
                List.of(new NewPage("n".repeat(255), "/", heading("Hi"), "t".repeat(255), "d".repeat(500), 0));
        tx.executeWithoutResult(s -> pages.insertInitialPages(p.projectId(), "test-1", longest));
        assertThat(pages.list(p.projectId(), p.ownerUserId()).pages()).hasSize(1);
    }

    @SafeVarargs
    private static List<Object> race(Callable<Object>... calls) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(calls.length);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Object>> futures = new ArrayList<>();
        for (Callable<Object> call : calls) {
            futures.add(pool.submit(() -> {
                start.await();
                try {
                    return call.call();
                } catch (WebBlockException e) {
                    return e;
                }
            }));
        }
        start.countDown();
        List<Object> outcomes = new ArrayList<>();
        for (Future<Object> future : futures) {
            outcomes.add(future.get(30, java.util.concurrent.TimeUnit.SECONDS));
        }
        pool.shutdown();
        return outcomes;
    }
}
