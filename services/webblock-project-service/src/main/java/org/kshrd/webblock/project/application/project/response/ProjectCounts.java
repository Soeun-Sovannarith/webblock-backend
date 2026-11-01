package org.kshrd.webblock.project.application.project.response;

/** The caller's live websites in total and per status (FR-14.10). */
public record ProjectCounts(long all, long draft, long active, long suspended) {}
