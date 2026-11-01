package org.kshrd.webblock.project.architecture.fixture;

import jakarta.annotation.security.DenyAll;
import jakarta.annotation.security.PermitAll;
import jakarta.annotation.security.RolesAllowed;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.util.List;
import org.springframework.security.access.annotation.Secured;
import org.springframework.security.access.prepost.PostAuthorize;
import org.springframework.security.access.prepost.PostFilter;
import org.springframework.security.access.prepost.PreFilter;
import org.springframework.security.authorization.method.AuthorizeReturnObject;

/** Each nested class deliberately violates the rule with one method-security annotation, on a method or the class. */
public final class MethodSecurityFixtures {

    private MethodSecurityFixtures() {}

    public static class PostAuthorizeOnAMethod {
        @PostAuthorize("true")
        public String read() {
            return "x";
        }
    }

    public static class PreFilterOnAMethod {
        @PreFilter("true")
        public void write(List<String> values) {}
    }

    public static class PostFilterOnAMethod {
        @PostFilter("true")
        public List<String> read() {
            return List.of();
        }
    }

    @Secured("ROLE_ADMIN")
    public static class SecuredOnTheClass {}

    @RolesAllowed("ADMIN")
    public static class RolesAllowedOnTheClass {}

    public static class PermitAllOnAMethod {
        @PermitAll
        public void open() {}
    }

    @DenyAll
    public static class DenyAllOnTheClass {}

    public static class AuthorizeReturnObjectOnAMethod {
        @AuthorizeReturnObject
        public Object read() {
            return "x";
        }
    }

    /** A composed annotation: the rule must find @Secured through it. */
    @Retention(RetentionPolicy.RUNTIME)
    @Secured("ROLE_ADMIN")
    public @interface AdminOnly {}

    public static class MetaAnnotatedMethod {
        @AdminOnly
        public void admin() {}
    }
}
