package org.kshrd.webblock.project.application.page.response;

import java.util.List;

public record PageListResponse(long editRevision, List<PageResponse> pages) {}
