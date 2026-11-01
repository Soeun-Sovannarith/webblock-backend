package org.kshrd.webblock.user;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;

// Tests must not need a running discovery-service; PostgreSQL comes from Testcontainers (needs Docker)
@SpringBootTest(properties = {"eureka.client.enabled=false",
        "spring.security.oauth2.resourceserver.jwt.issuer-uri=http://localhost/realms/test"})
@Import(TestcontainersConfiguration.class)
class UserServiceApplicationTests {

    @Test
    void contextLoads() {
    }

}
