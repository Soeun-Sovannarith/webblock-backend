# Sample site variant `preview-only` (test fixture only)

**A test fixture, never a pattern for a real site.** A real site must never behave differently on its preview hostname: the preview exists to prove the candidate exactly as visitors will see it (decision 0014 section 17).

This variant answers `/readyz` with 200 only when the request's `Host` is a preview host `<subdomain>-preview.<site domain>` (FR-20), and 503 on every other host. `/healthz` and `/` answer 200 everywhere, so Kubernetes finds it ready and the deploy succeeds.

It exists for one end-to-end case (task T2): a candidate that passes its preview probe and then fails the probe of the site's primary hostname after the switch. The promotion then points the route back at the live release, and the candidate ends `rolled_back` with `site_not_answering`. The trigger is deterministic: no timing and no intervention by the test.

Used by `tests/e2e/test_flows.py` through `scripts/prototype/sample-repo.sh NAME preview-only VERSION`.
