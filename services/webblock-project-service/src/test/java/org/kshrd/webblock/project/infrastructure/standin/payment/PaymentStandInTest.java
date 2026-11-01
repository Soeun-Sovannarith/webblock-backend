package org.kshrd.webblock.project.infrastructure.standin.payment;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.kshrd.webblock.project.application.payment.model.AdmissionDecision;
import org.kshrd.webblock.project.application.payment.model.AdmissionRefusal;
import org.kshrd.webblock.project.application.payment.model.AdmissionReleaseOutcome;
import org.kshrd.webblock.project.application.payment.model.GenerationAdmission;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.kshrd.webblock.project.common.infrastructure.config.StandIn;
import tools.jackson.databind.json.JsonMapper;

class PaymentStandInTest {

    static final JsonMapper JSON = JsonMapper.builder().build();

    @TempDir
    Path dir;

    @Test
    void withoutAFileItAdmitsEverythingAndIsAStandIn() {
        PaymentStandIn standIn = new PaymentStandIn("", JSON);
        assertThat(standIn).isInstanceOf(StandIn.class);
        assertThat(standIn.admitWebsite(UUID.randomUUID(), UUID.randomUUID()).decision())
                .isEqualTo(AdmissionDecision.ADMITTED);
    }

    @Test
    void aRepeatReturnsTheOriginalDecisionAndAnotherClientConflicts() {
        PaymentStandIn standIn = new PaymentStandIn("", JSON);
        UUID client = UUID.randomUUID();
        UUID project = UUID.randomUUID();
        assertThat(standIn.admitWebsite(client, project)).isEqualTo(standIn.admitWebsite(client, project));
        assertThatThrownBy(() -> standIn.admitWebsite(UUID.randomUUID(), project))
                .isInstanceOfSatisfying(
                        WebBlockException.class,
                        e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PROJECT_ID_CONFLICT));
    }

    @Test
    void theRefusalFileIsReadAgainWhenItChanges() throws Exception {
        UUID refused = UUID.randomUUID();
        Path file = dir.resolve("refusals.json");
        Files.writeString(file, "{\"websiteRefusals\":[]}");
        PaymentStandIn standIn = new PaymentStandIn(file.toString(), JSON);
        assertThat(standIn.admitWebsite(refused, UUID.randomUUID()).decision()).isEqualTo(AdmissionDecision.ADMITTED);
        Files.writeString(
                file, "{\"websiteRefusals\":[{\"clientUserId\":\"" + refused + "\",\"reason\":\"TRIAL_ENDED\"}]}");
        Files.setLastModifiedTime(file, FileTime.from(Instant.now().plusSeconds(5)));
        var refusal = standIn.admitWebsite(refused, UUID.randomUUID());
        assertThat(refusal.decision()).isEqualTo(AdmissionDecision.REFUSED);
        assertThat(refusal.reason()).isEqualTo(AdmissionRefusal.TRIAL_ENDED);
    }

    @Test
    void anUnreadableOrInvalidFileFailsClosed() throws Exception {
        Path file = dir.resolve("refusals.json");
        Files.writeString(file, "{\"websiteRefusals\":[{\"clientUserId\":\"x\",\"reason\":\"NOPE\"}]}");
        PaymentStandIn standIn = new PaymentStandIn(file.toString(), JSON);
        assertThatThrownBy(() -> standIn.admitWebsite(UUID.randomUUID(), UUID.randomUUID()))
                .isInstanceOfSatisfying(
                        WebBlockException.class,
                        e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PAYMENT_UNAVAILABLE));
    }

    @Test
    void onlyAContractReasonIsAccepted() throws Exception {
        for (String reason : new String[] {"NOPE", "OTHER", "trial_ended"}) {
            Path file = dir.resolve("reason-" + reason + ".json");
            Files.writeString(
                    file,
                    "{\"websiteRefusals\":[{\"clientUserId\":\"" + UUID.randomUUID() + "\",\"reason\":\"" + reason
                            + "\"}]}");
            PaymentStandIn standIn = new PaymentStandIn(file.toString(), JSON);
            assertThatThrownBy(() -> standIn.admitWebsite(UUID.randomUUID(), UUID.randomUUID()))
                    .as(reason)
                    .isInstanceOfSatisfying(
                            WebBlockException.class,
                            e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PAYMENT_UNAVAILABLE));
        }
    }

    @Test
    void releaseFreesAnAdmissionOnce() {
        PaymentStandIn standIn = new PaymentStandIn("", JSON);
        UUID project = UUID.randomUUID();
        standIn.admitWebsite(UUID.randomUUID(), project);
        assertThat(standIn.releaseWebsiteAdmission(project)).isEqualTo(AdmissionReleaseOutcome.RELEASED);
        assertThat(standIn.releaseWebsiteAdmission(project)).isEqualTo(AdmissionReleaseOutcome.NOT_ADMITTED);
    }

    @Test
    void aNullEntryOrAnOversizedFileFailsClosed() throws Exception {
        Path file = dir.resolve("bad.json");
        Files.writeString(file, "{\"websiteRefusals\":[null]}");
        PaymentStandIn nullEntry = new PaymentStandIn(file.toString(), JSON);
        assertThatThrownBy(() -> nullEntry.admitWebsite(UUID.randomUUID(), UUID.randomUUID()))
                .isInstanceOfSatisfying(
                        WebBlockException.class,
                        e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PAYMENT_UNAVAILABLE));
        Path big = dir.resolve("big.json");
        Files.writeString(big, "{\"websiteRefusals\":[]}" + " ".repeat(70_000));
        PaymentStandIn oversized = new PaymentStandIn(big.toString(), JSON);
        assertThatThrownBy(() -> oversized.admitWebsite(UUID.randomUUID(), UUID.randomUUID()))
                .isInstanceOfSatisfying(
                        WebBlockException.class,
                        e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PAYMENT_UNAVAILABLE));
    }

    @Test
    void anEntryWithoutAReasonFailsClosed() throws Exception {
        Path file = dir.resolve("noreason.json");
        Files.writeString(file, "{\"websiteRefusals\":[{\"clientUserId\":\"" + UUID.randomUUID() + "\"}]}");
        PaymentStandIn standIn = new PaymentStandIn(file.toString(), JSON);
        assertThatThrownBy(() -> standIn.admitWebsite(UUID.randomUUID(), UUID.randomUUID()))
                .isInstanceOfSatisfying(
                        WebBlockException.class,
                        e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.PAYMENT_UNAVAILABLE));
    }

    private PaymentStandIn withFile(String content) throws Exception {
        Path file = dir.resolve("gen-" + UUID.randomUUID() + ".json");
        Files.writeString(file, content);
        return new PaymentStandIn(file.toString(), JSON);
    }

    private static String generationRefusal(UUID client, String reason) {
        return "{\"generationRefusals\":[{\"clientUserId\":\"" + client + "\",\"reason\":\"" + reason + "\"}]}";
    }

    @Test
    void withoutAFileEveryGenerationIsAdmitted() {
        PaymentStandIn standIn = new PaymentStandIn("", JSON);
        assertThat(standIn.admitGeneration(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID()))
                .isEqualTo(GenerationAdmission.ADMITTED);
    }

    @Test
    void aRepeatOfOneGenerationRequestReturnsTheFirstAdmission() {
        PaymentStandIn standIn = new PaymentStandIn("", JSON);
        UUID client = UUID.randomUUID();
        UUID project = UUID.randomUUID();
        UUID request = UUID.randomUUID();
        assertThat(standIn.admitGeneration(client, project, request)).isEqualTo(GenerationAdmission.ADMITTED);
        assertThat(standIn.admitGeneration(client, project, request)).isEqualTo(GenerationAdmission.ADMITTED);
    }

    @Test
    void theSameRequestIdForAnotherClientOrProjectIsAnError() {
        PaymentStandIn standIn = new PaymentStandIn("", JSON);
        UUID client = UUID.randomUUID();
        UUID project = UUID.randomUUID();
        UUID request = UUID.randomUUID();
        standIn.admitGeneration(client, project, request);
        assertThat(standIn.admitGeneration(UUID.randomUUID(), project, request)).isEqualTo(GenerationAdmission.ERROR);
        assertThat(standIn.admitGeneration(client, UUID.randomUUID(), request)).isEqualTo(GenerationAdmission.ERROR);
        assertThat(standIn.admitGeneration(client, project, request)).isEqualTo(GenerationAdmission.ADMITTED);
    }

    @Test
    void theFileRefusesAClientsGenerations() throws Exception {
        UUID refused = UUID.randomUUID();
        PaymentStandIn standIn = withFile(generationRefusal(refused, "TRIAL_ENDED"));
        assertThat(standIn.admitGeneration(refused, UUID.randomUUID(), UUID.randomUUID()))
                .isEqualTo(GenerationAdmission.refused("TRIAL_ENDED"));
        assertThat(standIn.admitGeneration(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID()))
                .isEqualTo(GenerationAdmission.ADMITTED);
    }

    @Test
    void websiteAndGenerationEntriesAreIndependent() throws Exception {
        UUID client = UUID.randomUUID();
        PaymentStandIn websiteOnly =
                withFile("{\"websiteRefusals\":[{\"clientUserId\":\"" + client + "\",\"reason\":\"TRIAL_ENDED\"}]}");
        assertThat(websiteOnly.admitGeneration(client, UUID.randomUUID(), UUID.randomUUID()))
                .isEqualTo(GenerationAdmission.ADMITTED);
        PaymentStandIn generationOnly = withFile(generationRefusal(client, "TRIAL_ENDED"));
        assertThat(generationOnly.admitWebsite(client, UUID.randomUUID()).decision())
                .isEqualTo(AdmissionDecision.ADMITTED);
    }

    @Test
    void onlyAContract23ReasonIsAccepted() throws Exception {
        for (String reason : new String[] {"WEBSITE_LIMIT_REACHED", "X", "OTHER"}) {
            PaymentStandIn standIn = withFile(generationRefusal(UUID.randomUUID(), reason));
            assertThat(standIn.admitGeneration(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID()))
                    .as(reason)
                    .isEqualTo(GenerationAdmission.UNAVAILABLE);
        }
    }

    @Test
    void anInvalidFileIsUnavailableForGenerations() throws Exception {
        for (String content : new String[] {
            "not json",
            "{\"generationRefusals\":[null]}",
            "{\"generationRefusals\":[{\"clientUserId\":\"" + UUID.randomUUID() + "\"}]}",
            "{\"generationRefusals\":[]}" + " ".repeat(70_000)
        }) {
            PaymentStandIn standIn = withFile(content);
            assertThat(standIn.admitGeneration(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID()))
                    .isEqualTo(GenerationAdmission.UNAVAILABLE);
        }
    }
}
