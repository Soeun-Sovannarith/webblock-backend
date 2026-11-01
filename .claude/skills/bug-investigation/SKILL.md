---
name: bug-investigation
description: Use for ANY bug, failing test, exception, or unexpected behavior. Enforces evidence-first debugging - collect evidence, reproduce, state a hypothesis, isolate, then fix. Do not propose a fix before the failure point is proven.
---

# Bug investigation protocol

## Purpose
Find the real cause of a defect with proof, then fix it with the smallest safe change and a regression test. Guessing, "try this and see", and fixing symptoms are the failure modes this skill prevents.

## When to use / when NOT to use
- Use for: failing test, exception, wrong response, wrong data, flaky behavior, performance regression, a message that is lost or duplicated, a failed integration between services.
- Do NOT use for: new features (use `/plan`), pure refactors, or questions about how code works (just read the code).
- If the bug lives in another service, you may investigate it read-only but you must not edit it (see "Cross-service bugs").

## Inputs to confirm with the user
Ask for whatever is missing before step 1 ends:
- Exact error text or stack trace, and the request (method, path, redacted body) or message that triggered it.
- Environment and profile (local, dev, test), service version or commit, and when it started.
- Expected vs actual behavior.
- Correlation id (`X-Request-Id` / `correlationId`) if the call crossed services.
- What changed recently (deploy, config, migration, contract version).
Never ask for or paste secrets, tokens, OTPs, or real personal data; ask for redacted samples.

## Procedure
1. **Evidence.** Gather facts, not opinions: error, stack trace, logs with timestamps and correlation id, input data, versions. Run `git log --oneline -20` and `git diff` to see recent changes. Write the facts down as quotes.
2. **Reproduce.** Build the smallest repeatable trigger, in this order of preference: a failing automated test, a request against a local instance, a script. Run it and capture the failing output. No reproduction means no production-code change: say what information is missing instead.
3. **Hypothesis.** One sentence: "The failure happens because <mechanism> at <file:line>, which causes <symptom>." Add the evidence that supports it and the observation that would disprove it.
4. **Eliminate suspects.** Change one variable at a time (input, config/profile, dependency, database state, message payload, concurrency, time zone, contract version). Run a targeted check for each and record confirmed or ruled out. Stop when one cause remains and an observation (log line, debugger, test output) proves it, not reasoning alone.
5. **Fix.** Propose the smallest change that removes the proven cause. State what it touches. Wait for approval unless it is a one-line fix (see `.claude/rules/scope.md`).
6. **Regression test.** Convert the reproduction into a test that fails before the fix and passes after. Do not weaken or delete other tests.
7. **Verify.** Run the service test command from its `CLAUDE.md` and report the real summary line. If the failing area has integration tests, run them too.
8. **Report** using the template below.

## Templates
Hypothesis log (keep it in the conversation):

| # | Suspect | Check performed | Result |
|---|---|---|---|
| 1 | Bad input from client | replayed request with valid body | ruled out |
| 2 | Null `ownerId` after mapping | unit test on mapper | confirmed |

Final report:
```
Symptom:       what the user saw
Evidence:      quoted log/stack lines (secrets and PII redacted)
Root cause:    mechanism, file:line
Ruled out:     suspects eliminated and how
Fix:           what changed and why it is safe
Verification:  command and real result
Follow-ups:    related risks NOT fixed here
```

## Cross-service bugs
Trace the correlation id across services, compare both sides with the contract in `../../contracts/` (repo root: `contracts/`), and prove which side violates it (`../../docs/service-map.md` lists provider and consumers). Change only your own service. For the other side write a short note: owner, what is wrong, evidence, contract file.

## Checklist
- [ ] Evidence quoted, not paraphrased
- [ ] Reproduction exists and fails for the stated reason
- [ ] Hypothesis names file:line and what would disprove it
- [ ] Other suspects explicitly ruled out
- [ ] Fix is minimal and inside this service
- [ ] Regression test fails without the fix
- [ ] Full test command run, real output reported
- [ ] No secrets or PII in logs, tests or the report

## Common mistakes
- Fixing the first plausible thing; "it works now" without knowing why.
- Reproducing with different data than the report and declaring it fine.
- Adding a try/catch or retry that hides the error instead of removing the cause.
- Changing several things at once, so the proof is lost.
- Editing a committed Flyway migration to fix data; add a new `V<n>__*.sql` instead.
- Pasting real tokens or user data into the report.

## Definition of done
Root cause proven, fix applied, regression test added and green, full service tests run, report delivered, follow-ups listed. Nothing committed unless the user asked (`/commit`).

## Related
Command `/fix-bug`, command `/test`, agent `code-reviewer`, skill `microservice-integration`, rule `scope.md`, `../../docs/auth-flow.md` for auth-related failures.
