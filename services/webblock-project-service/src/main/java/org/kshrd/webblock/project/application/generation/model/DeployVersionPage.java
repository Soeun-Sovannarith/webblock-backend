package org.kshrd.webblock.project.application.generation.model;

import java.util.List;

/** One page of a project's deploy versions, newest first; nextCursor is null on the last page. */
public record DeployVersionPage(List<DeployVersion> items, String nextCursor) {

    public DeployVersionPage {
        items = List.copyOf(items);
    }
}
