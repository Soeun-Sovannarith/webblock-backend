package org.kshrd.webblock.project.support;

import org.kshrd.webblock.project.db.PostgresFixture;
import org.springframework.boot.test.util.TestPropertyValues;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;

/** A fresh project database per context; Flyway migrates it as project_migrator, JPA uses project_app. */
public final class ProjectDatabaseInitializer implements ApplicationContextInitializer<ConfigurableApplicationContext> {

    @Override
    public void initialize(ConfigurableApplicationContext context) {
        try {
            PostgresFixture.Database db = PostgresFixture.freshDatabase(false);
            TestPropertyValues.of(
                            "spring.datasource.url=" + db.url(),
                            "spring.datasource.username=" + PostgresFixture.APP_LOGIN,
                            "spring.datasource.password=" + PostgresFixture.PASSWORD,
                            "spring.flyway.url=" + db.url(),
                            "spring.flyway.user=" + PostgresFixture.MIGRATOR_LOGIN,
                            "spring.flyway.password=" + PostgresFixture.PASSWORD)
                    .applyTo(context);
        } catch (Exception e) {
            throw new IllegalStateException("Test database could not be prepared", e);
        }
    }
}
