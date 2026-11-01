package org.kshrd.webblock.project.common.presentation.envelope;

import java.util.List;

/** What a controller returns for a list; the envelope moves the paging into meta.page. */
public record PagedResult<T>(List<T> items, int limit, String nextCursor) {

    public PagedResult {
        items = List.copyOf(items);
    }
}
