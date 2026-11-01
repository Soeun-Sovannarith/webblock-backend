package org.kshrd.webblock.project.application.page.model;

import java.util.List;

/** A project's live pages and the edit revision the next save must send. */
public record PageList(long editRevision, List<PageRecord> pages) {}
