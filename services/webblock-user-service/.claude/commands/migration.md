---
description: Create a new Flyway migration in the right service, matching the data dictionary
argument-hint: "[service] [what to change]"
---
Create a migration for: **$ARGUMENTS**

1. Find the table(s) in `docs/specs/data-dictionary.md`. If the change is not in the dictionary, stop and ask me.
2. Create a NEW file `<service>-service/src/main/resources/db/migration/V<yyyyMMddHHmm>__<snake_description>.sql`. Never edit an old migration. (Python services: a new Alembic revision instead.)
3. Follow `.claude/rules/database.md`.
4. Update the JPA entity if needed and run `./gradlew test` for that service.
5. Ask the **db-schema-reviewer** agent to check it.
