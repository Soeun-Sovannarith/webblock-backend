package org.kshrd.webblock.project.presentation.rest.catalog;

import java.util.List;
import org.kshrd.webblock.project.application.catalog.handler.CatalogReference;
import org.kshrd.webblock.project.application.catalog.response.BusinessTypeDetailResponse;
import org.kshrd.webblock.project.application.catalog.response.BusinessTypeDetailResponse.ProductTypeResponse;
import org.kshrd.webblock.project.application.catalog.response.BusinessTypeResponse;
import org.kshrd.webblock.project.common.domain.error.ErrorCode;
import org.kshrd.webblock.project.common.domain.error.WebBlockException;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

/** Wizard step 1: the business types a client may choose, and the product fields of one of them. */
@RestController
class BusinessTypeController {

    private final CatalogReference catalog;

    BusinessTypeController(CatalogReference catalog) {
        this.catalog = catalog;
    }

    @GetMapping("/api/v1/business-types")
    List<BusinessTypeResponse> list() {
        return catalog.activeBusinessTypes().stream()
                .map(type -> new BusinessTypeResponse(type.code(), type.name()))
                .toList();
    }

    @GetMapping("/api/v1/business-types/{code}")
    BusinessTypeDetailResponse get(@PathVariable String code) {
        var type = catalog.activeBusinessType(code)
                .orElseThrow(() -> new WebBlockException(ErrorCode.BUSINESS_TYPE_NOT_FOUND));
        List<ProductTypeResponse> productTypes = catalog.activeProductTypes(type.businessTypeId()).stream()
                .map(p -> new ProductTypeResponse(p.code(), p.name(), p.fieldSchema()))
                .toList();
        return new BusinessTypeDetailResponse(type.code(), type.name(), productTypes);
    }
}
