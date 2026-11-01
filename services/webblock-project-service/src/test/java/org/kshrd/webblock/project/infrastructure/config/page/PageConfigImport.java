package org.kshrd.webblock.project.infrastructure.config.page;

import org.springframework.context.annotation.Import;

/** Lets a slice test outside this package import the package-private PageConfig. */
@Import(PageConfig.class)
public final class PageConfigImport {}
