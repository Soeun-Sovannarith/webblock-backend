package org.kshrd.webblock.project.support;

import org.kshrd.webblock.project.common.infrastructure.config.JacksonConfig;
import org.kshrd.webblock.project.common.infrastructure.security.SecurityConfig;
import org.kshrd.webblock.project.common.presentation.error.GlobalExceptionHandler;
import org.kshrd.webblock.project.common.presentation.error.ProblemDetailWriter;
import org.kshrd.webblock.project.common.presentation.error.SecurityErrorHandlers;
import org.kshrd.webblock.project.common.presentation.security.CurrentUser;
import org.springframework.context.annotation.Import;

/** The beans every feature's @WebMvcTest imports: error handling (Problem Details), security, and the current user. */
public final class WebSliceSupport {

    private WebSliceSupport() {}

    @Import({
        GlobalExceptionHandler.class,
        JacksonConfig.class,
        ProblemDetailWriter.class,
        SecurityConfig.class,
        SecurityErrorHandlers.class,
        CurrentUser.class
    })
    public static final class Imports {}
}
