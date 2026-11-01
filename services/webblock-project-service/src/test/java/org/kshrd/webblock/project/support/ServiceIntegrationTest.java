package org.kshrd.webblock.project.support;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import org.junit.jupiter.api.Tag;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.ContextConfiguration;

/** The whole application against real PostgreSQL 18 (both databases) and RabbitMQ, in the prototype profile. */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@Documented
@Tag("integration")
@SpringBootTest(properties = "webblock.generation.stuck-sweep-interval=24h")
@AutoConfigureMockMvc
@ActiveProfiles("prototype")
@ContextConfiguration(initializers = ServiceTestDatabases.class)
public @interface ServiceIntegrationTest {}
