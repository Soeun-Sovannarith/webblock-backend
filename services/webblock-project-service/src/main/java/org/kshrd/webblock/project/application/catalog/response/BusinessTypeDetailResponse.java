package org.kshrd.webblock.project.application.catalog.response;

import java.util.List;
import tools.jackson.databind.JsonNode;

public record BusinessTypeDetailResponse(String code, String name, List<ProductTypeResponse> productTypes) {

    public record ProductTypeResponse(String code, String name, JsonNode fieldSchema) {}
}
