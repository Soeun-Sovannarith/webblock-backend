package org.kshrd.webblock.project.application.generation.model;

import java.util.List;

/** One page of a project's contracts, newest first; nextCursor is null on the last page. */
public record GenerationContractPage(List<GenerationContractRecord> items, String nextCursor) {}
