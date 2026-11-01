package org.kshrd.webblock.project.application.generation.model;

import java.util.List;

/** One page of a project's generation requests, newest first; nextCursor is null on the last page. */
public record GenerationRequestPage(List<GenerationRequestRecord> items, String nextCursor) {

    public GenerationRequestPage {
        items = List.copyOf(items);
    }
}
