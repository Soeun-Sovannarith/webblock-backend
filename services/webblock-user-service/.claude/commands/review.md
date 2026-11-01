---
description: Review the current diff for bugs, security and missing tests
---
Run these in parallel and merge the results into one list, most severe first:
- **code-reviewer** agent on the current `git diff`
- **db-schema-reviewer** agent, only if files under `db/migration/` or entities changed

Then tell me: "Ready to merge" or "Fix first" with the items. Do not change code.
