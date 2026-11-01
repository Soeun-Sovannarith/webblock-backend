package org.kshrd.webblock.project.infrastructure.client.payment;

import java.io.IOException;
import java.io.InputStream;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;
import org.kshrd.webblock.project.application.payment.model.AdmissionDecision;
import org.kshrd.webblock.project.application.payment.model.AdmissionRefusal;
import org.kshrd.webblock.project.application.payment.model.AdmissionReleaseOutcome;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.application.payment.model.WebsiteAdmission;
import org.kshrd.webblock.project.application.payment.port.PaymentAdmissions;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.domain.support.RequestIds;
import org.kshrd.webblock.project.infrastructure.config.payment.PaymentProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.core.retry.RetryPolicy;
import org.springframework.core.retry.RetryTemplate;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Payment contract client: JSON in the standard envelope, read field by field so that fields Payment adds later are
 * ignored. Bounded: each attempt times out after webblock.payment.timeout (2 s); an I/O failure or timeout (a
 * connection reset included) is tried once more (both calls are idempotent), at most two attempts in all. Anything
 * Payment did not clearly decide is PAYMENT_UNAVAILABLE. No body is logged. The calls carry no token: Payment's sidecar
 * admits this service by its mesh identity (mTLS), and no Authorization header is ever set, so a user's token never
 * leaves the service (decision 0002).
 */
public final class HttpPaymentAdmissions implements PaymentAdmissions {

    static final int MAX_RETRIES = 1;
    static final int MAX_BODY_BYTES = 65_536;

    private static final Logger log = LoggerFactory.getLogger(HttpPaymentAdmissions.class);

    private final RestClient rest;

    /** Only I/O failures and timeouts are tried again, at once, and only once. */
    private final RetryTemplate retry = new RetryTemplate(RetryPolicy.builder()
            .maxRetries(MAX_RETRIES)
            .delay(Duration.ZERO)
            .includes(ResourceAccessException.class)
            .build());

    /**
     * A plain mapper, not the application's: Payment's answers are only read as a tree, field by field, so the web
     * mapper's strict settings (unknown properties refused) must not apply to them.
     */
    private final JsonMapper json = JsonMapper.builder().build();

    public HttpPaymentAdmissions(PaymentProperties properties) {
        this.rest = restClient(properties);
    }

    private static RestClient restClient(PaymentProperties properties) {
        HttpClient http = HttpClient.newBuilder()
                .connectTimeout(properties.timeout())
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(http);
        factory.setReadTimeout(properties.timeout());

        return RestClient.builder()
                .baseUrl(properties.baseUrl().toString())
                .requestFactory(factory)
                .requestInterceptor((request, body, execution) -> {
                    String requestId = MDC.get(RequestIds.MDC_KEY);
                    if (requestId != null) {
                        request.getHeaders().set(RequestIds.HEADER, requestId);
                    }
                    return execution.execute(request, body);
                })
                .build();
    }

    private record Reply(int status, JsonNode body) {

        boolean success() {
            return body != null
                    && body.path("success").isBoolean()
                    && body.path("success").booleanValue();
        }

        JsonNode data() {
            return body == null ? null : body.path("data");
        }

        String errorCode() {
            return body == null ? null : text(body.path("error"), "code");
        }
    }

    @Override
    public WebsiteAdmission admitWebsite(UUID clientUserId, UUID projectId) {
        byte[] request = json.writeValueAsBytes(
                Map.of("clientUserId", clientUserId.toString(), "projectId", projectId.toString()));
        Reply reply = call(
                "admit",
                () -> rest.post()
                        .uri("/internal/v1/website-admissions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .accept(MediaType.APPLICATION_JSON)
                        .body(request)
                        .exchange((req, res) -> read(res.getStatusCode().value(), res.getBody())));
        if (reply.status() == 409 && "IDEMPOTENCY_CONFLICT".equals(reply.errorCode())) {
            throw new WebBlockException(ErrorCode.PROJECT_ID_CONFLICT);
        }
        if (reply.status() != 200 || !reply.success() || !reply.data().isObject()) {
            throw unavailable("admit", reply.status(), null);
        }
        JsonNode data = reply.data();
        AdmissionDecision decision;
        try {
            decision = AdmissionDecision.valueOf(String.valueOf(text(data, "decision")));
        } catch (IllegalArgumentException e) {
            throw unavailable("admit", reply.status(), null);
        }
        String reason = text(data, "reason");
        String echoed = text(data, "projectId");
        if ((decision == AdmissionDecision.REFUSED && reason == null)
                || (echoed != null && !echoed.equals(projectId.toString()))) {
            throw unavailable("admit", reply.status(), null);
        }
        return new WebsiteAdmission(decision, decision == AdmissionDecision.REFUSED ? refusal(reason) : null);
    }

    /**
     * Contract 2.3. Never throws for Payment's answers: a timeout, an I/O failure or a 5xx is UNAVAILABLE; a clear
     * decision is read; anything else (a 409 IDEMPOTENCY_CONFLICT included) is ERROR.
     */
    @Override
    public GenerationAdmission admitGeneration(UUID clientUserId, UUID projectId, UUID generationRequestId) {
        byte[] request = json.writeValueAsBytes(Map.of(
                "clientUserId", clientUserId.toString(),
                "projectId", projectId.toString(),
                "generationRequestId", generationRequestId.toString()));
        Reply reply;
        try {
            reply = call(
                    "admit generation",
                    () -> rest.post()
                            .uri("/internal/v1/generation-admissions")
                            .contentType(MediaType.APPLICATION_JSON)
                            .accept(MediaType.APPLICATION_JSON)
                            .body(request)
                            .exchange((req, res) -> read(res.getStatusCode().value(), res.getBody())));
        } catch (WebBlockException e) {
            return GenerationAdmission.UNAVAILABLE; // already logged by call
        }
        if (reply.status() >= 500) {
            log.warn("Payment admit generation could not be decided (HTTP status {})", reply.status());
            return GenerationAdmission.UNAVAILABLE;
        }
        GenerationAdmission decided = decision(reply);
        if (decided == null) {
            log.warn("Payment admit generation gave an unusable answer (HTTP status {})", reply.status());
            return GenerationAdmission.ERROR;
        }
        return decided;
    }

    private static GenerationAdmission decision(Reply reply) {
        if (reply.status() != 200 || !reply.success() || !reply.data().isObject()) {
            return null;
        }
        String decision = text(reply.data(), "decision");
        if ("ADMITTED".equals(decision)) {
            return GenerationAdmission.ADMITTED;
        }
        String reason = text(reply.data(), "reason");
        return "REFUSED".equals(decision) && reason != null ? GenerationAdmission.refused(reason) : null;
    }

    /** A reason this version does not know (Payment may add one within v1) is OTHER. */
    private static AdmissionRefusal refusal(String reason) {
        try {
            return AdmissionRefusal.valueOf(reason);
        } catch (IllegalArgumentException e) {
            return AdmissionRefusal.OTHER;
        }
    }

    @Override
    public AdmissionReleaseOutcome releaseWebsiteAdmission(UUID projectId) {
        Reply reply = call(
                "release",
                () -> rest.delete()
                        .uri("/internal/v1/website-admissions/{projectId}", projectId)
                        .accept(MediaType.APPLICATION_JSON)
                        .exchange((req, res) -> read(res.getStatusCode().value(), res.getBody())));
        if (reply.status() == 204) {
            return AdmissionReleaseOutcome.RELEASED;
        }
        if (reply.status() == 404 && "NOT_ADMITTED".equals(reply.errorCode())) {
            return AdmissionReleaseOutcome.NOT_ADMITTED;
        }
        if (reply.status() == 409 && "PROJECT_IN_USE".equals(reply.errorCode())) {
            return AdmissionReleaseOutcome.PROJECT_IN_USE;
        }
        throw unavailable("release", reply.status(), null);
    }

    private Reply read(int status, InputStream body) throws IOException {
        byte[] bytes = body == null ? new byte[0] : body.readNBytes(MAX_BODY_BYTES);
        if (bytes.length == 0) {
            return new Reply(status, null);
        }
        try {
            return new Reply(status, json.readTree(bytes));
        } catch (JacksonException e) {
            return new Reply(status, null); // not JSON (for example a gateway's HTML page)
        }
    }

    private Reply call(String operation, Supplier<Reply> attempt) {
        try {
            return retry.invoke(attempt);
        } catch (WebBlockException e) {
            throw e;
        } catch (RuntimeException e) {
            throw unavailable(operation, -1, e); // timeouts spent, protocol error
        }
    }

    private static WebBlockException unavailable(String operation, int status, Throwable cause) {
        log.warn("Payment {} could not be decided (HTTP status {})", operation, status);
        return cause == null
                ? new WebBlockException(ErrorCode.PAYMENT_UNAVAILABLE)
                : new WebBlockException(ErrorCode.PAYMENT_UNAVAILABLE, cause);
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.path(field);
        return value != null && value.isString() ? value.stringValue() : null;
    }
}
