package org.kshrd.webblock.project.presentation.rest.access;

import jakarta.validation.Valid;
import org.kshrd.webblock.project.application.access.handler.AccessCheckService;
import org.kshrd.webblock.project.application.access.model.AccessDecision;
import org.kshrd.webblock.project.application.access.request.AccessCheckRequest;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Provisioning's ProjectAccess port (OD-01). A denial is a normal 200 answer; an unknown project is never a 404. */
@RestController
@RequestMapping("/internal/v1/access-checks")
class AccessCheckController {

    private final AccessCheckService service;

    AccessCheckController(AccessCheckService service) {
        this.service = service;
    }

    @PostMapping
    AccessDecision check(@Valid @RequestBody AccessCheckRequest body) {
        return service.check(body.user(), body.project(), body.accessAction());
    }
}
