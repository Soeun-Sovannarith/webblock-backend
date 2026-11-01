---
name: investigate-bug
description: Evidence-first bug investigation for WebBlock - reproduce, hypothesis, isolate, prove, then fix with a regression test. Use when the user reports an error, failing test, or "it doesn't work".
argument-hint: "[short bug description]"
---
# Investigate: $ARGUMENTS

Rules: never fix what isn't proven broken. No reproduction = no fix.

1. **Reproduce.** Find the exact failing test, request or UI step. Capture the real error (stack trace, response body, log line with time, request id).
2. **Locate.** Which service/app? For deploy issues follow the `publish-flow` table order. For payment, the `khqr-checkout` flow.
3. **Hypothesis.** One sentence: "X fails because Y, shown by Z."
4. **Isolate.** Change one variable at a time. Write down each try and result.
5. **Prove.** Show the evidence that confirms the cause. If it doesn't confirm it, go back to step 3.
6. **Fix.** Write a failing regression test first, then the smallest fix, then run the service's tests.
7. **Report** (short): symptom → evidence → cause (file:line) → fix → test.

For a long investigation, hand steps 1–5 to the `bug-investigator` agent.
