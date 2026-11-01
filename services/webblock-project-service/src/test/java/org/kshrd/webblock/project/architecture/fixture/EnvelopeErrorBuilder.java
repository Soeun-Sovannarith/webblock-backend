package org.kshrd.webblock.project.architecture.fixture;

import org.kshrd.webblock.project.common.presentation.envelope.ApiMeta;
import org.kshrd.webblock.project.common.presentation.envelope.ApiResponse;

/** Builds an envelope outside the success advice: what onlyTheEnvelopeAdviceUsesApiResponse forbids. */
public class EnvelopeErrorBuilder {

    public ApiResponse<String> refused(ApiMeta meta) {
        return ApiResponse.success("refused", meta);
    }
}
