package org.kshrd.webblock.project.infrastructure.config.project;

import org.springframework.context.annotation.Import;

/** Lets a slice test outside this package import the package-private ProjectConfig. */
@Import(ProjectConfig.class)
public final class ProjectConfigImport {}
