package org.kshrd.webblock.project.architecture;

import jakarta.annotation.security.DenyAll;
import jakarta.annotation.security.PermitAll;
import jakarta.annotation.security.RolesAllowed;
import java.lang.annotation.Annotation;
import java.lang.reflect.AnnotatedElement;
import java.util.List;
import org.springframework.core.annotation.AnnotatedElementUtils;
import org.springframework.security.access.annotation.Secured;
import org.springframework.security.access.prepost.PostAuthorize;
import org.springframework.security.access.prepost.PostFilter;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.access.prepost.PreFilter;
import org.springframework.security.authorization.method.AuthorizeReturnObject;

/** Shared by the ArchUnit rule: finds method-security annotations, directly present or through meta-annotations. */
final class MethodSecurity {

    private static final List<Class<? extends Annotation>> ANNOTATIONS = List.of(
            PreAuthorize.class,
            PostAuthorize.class,
            PreFilter.class,
            PostFilter.class,
            Secured.class,
            RolesAllowed.class,
            PermitAll.class,
            DenyAll.class,
            AuthorizeReturnObject.class);

    private MethodSecurity() {}

    static boolean carriesMethodSecurity(AnnotatedElement element) {
        return ANNOTATIONS.stream().anyMatch(type -> AnnotatedElementUtils.hasAnnotation(element, type));
    }
}
