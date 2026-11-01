package org.kshrd.webblock.project.application.page.model;

/** The page a save wrote and the project's new edit revision. */
public record PageWrite(PageRecord page, long editRevision) {}
