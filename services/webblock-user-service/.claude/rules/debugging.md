# Debugging protocol (always on)

Follow this order. Don't skip steps.
1. **Reproduce.** Get the exact error: failing test, request + response, or log line with timestamp. If you can't reproduce it, say so and stop. Don't guess a fix.
2. **Hypothesis.** Write one sentence: "X fails because Y, shown by Z."
3. **Isolate.** Change one variable at a time (service, input, config, version) until one cause is left.
4. **Prove.** Show the evidence (stack trace, query result, test output) before you edit code.
5. **Fix + guard.** Make the smallest fix, then add a test that failed before and passes now.
6. **Report.** Cause → evidence → fix → test, in short simple English.
