---
description: Run tests for one backend service and explain failures
argument-hint: "[service-name | all]"
---
Run tests for **$ARGUMENTS**:
- service name → `cd services/$ARGUMENTS-service && ./gradlew test` (Java) or `cd services/$ARGUMENTS-service && python -m pytest` (Python: provisioning, webblock-aicode)
- `all` → `./scripts/gradle-all.sh test`, then pytest in each Python service

For each failure: test name → the real error line → likely cause with evidence. Do not change code unless I ask.
