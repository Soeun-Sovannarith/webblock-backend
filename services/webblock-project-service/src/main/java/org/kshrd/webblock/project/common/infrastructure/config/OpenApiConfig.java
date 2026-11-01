package org.kshrd.webblock.project.common.infrastructure.config;

import io.swagger.v3.oas.models.Components;
import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.security.SecurityRequirement;
import io.swagger.v3.oas.models.security.SecurityScheme;
import io.swagger.v3.oas.models.servers.Server;
import java.util.List;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

/**
 * TR-40: the title, the note on the authority and the Bearer scheme of the OpenAPI description springdoc builds from
 * the controllers in the prototype profile. The scheme applies to every described operation (only {@code /api/v1/**}
 * is described), so Swagger UI offers Authorize for a dev token or a Keycloak token. The one server is the relative
 * {@code /}: springdoc never writes a request's Host into the shared description, and Swagger UI sends the token only
 * to the origin it was loaded from.
 */
@Configuration(proxyBeanMethods = false)
@Profile("prototype")
class OpenApiConfig {

    static final String BEARER = "bearer";

    private static final String DESCRIPTION = "The user REST API of the Project service, generated from the code in"
            + " the prototype profile. Notion \"Web Block architecture\" section 2 stays the authority for the API"
            + " contract. Every success body is wrapped in the envelope {success, data, meta}; the schemas here show"
            + " the data before the wrapping. Errors are RFC 9457 Problem Details.";

    @Bean
    OpenAPI webBlockOpenApi() {
        return new OpenAPI()
                .servers(List.of(new Server().url("/")))
                .info(new Info().title("WebBlock Project API").version("v1").description(DESCRIPTION))
                .components(new Components()
                        .addSecuritySchemes(
                                BEARER,
                                new SecurityScheme()
                                        .type(SecurityScheme.Type.HTTP)
                                        .scheme("bearer")
                                        .description("A Keycloak access token with audience project, or a dev token"
                                                + " of the prototype profile")))
                .addSecurityItem(new SecurityRequirement().addList(BEARER));
    }
}
