package org.kshrd.webblock.notification.infrastructure.config;

import io.swagger.v3.oas.annotations.OpenAPIDefinition;
import io.swagger.v3.oas.annotations.enums.SecuritySchemeType;
import io.swagger.v3.oas.annotations.info.Info;
import io.swagger.v3.oas.annotations.security.SecurityRequirement;
import io.swagger.v3.oas.annotations.security.SecurityScheme;
import io.swagger.v3.oas.annotations.servers.Server;
import org.springframework.context.annotation.Configuration;

// Swagger UI: http://localhost:<port>/swagger-ui.html, or all services at http://localhost:8080/swagger-ui.html
// Server "/" = "same host that served the page", so Try it out works both direct and via the gateway
@Configuration
@OpenAPIDefinition(info = @Info(title = "notification-service API", version = "v1", description = "Web, push and email notifications"),
        servers = @Server(url = "/"),
        security = @SecurityRequirement(name = "bearerAuth"))
// "Authorize" button in Swagger UI: paste a Keycloak access token
@SecurityScheme(name = "bearerAuth", type = SecuritySchemeType.HTTP, scheme = "bearer", bearerFormat = "JWT")
public class OpenApiConfig {
}
