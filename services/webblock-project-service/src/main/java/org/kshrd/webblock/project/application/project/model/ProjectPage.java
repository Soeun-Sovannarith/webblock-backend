package org.kshrd.webblock.project.application.project.model;

import java.util.List;

/** One page of "my projects"; nextCursor is null on the last page. The controller wraps it in PagedResult. */
public record ProjectPage(List<ProjectRecord> items, String nextCursor) {}
