package org.kshrd.webblock.user.application.admin.response;

import org.springframework.data.domain.Page;

import java.util.List;
import java.util.function.Function;

// List answer used by the admin tables: { items, page, size, totalItems } (page starts at 0)
public record PageResponse<T>(List<T> items, int page, int size, long totalItems) {

    public static <E, R> PageResponse<R> of(Page<E> page, Function<E, R> mapper) {
        return new PageResponse<>(page.getContent().stream().map(mapper).toList(),
                page.getNumber(), page.getSize(), page.getTotalElements());
    }
}
