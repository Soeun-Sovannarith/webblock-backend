# Change note: preview-host

Change: the preview host becomes `<subdomain>-preview.<site domain>`; the bootstrap refuses a sub-domain that ends
in `-preview` or has 56 to 63 characters.
IDs: FR-20, FR-52, FR-55; TR-35, TR-45; decision 0023 G18b amended; OD-71 closed.
Agreed: the owner, in the lead's session.

What changes:
- `infrastructure/kubernetes/render.py`, `site_cluster.py`, `core/application/ports/site_cluster.py`,
  `core/application/routing/background/routing.py`, `infrastructure/config/worker.py`: the host form.
- `core/application/tenants/background/bootstrap_loop.py`: the FR-55 refusal (one error log line with the project
  ID only, no tenant, rows kept), next to the FR-54 check.
- `deploy/prototype/`: wildcard certificate (`*.<site domain>` covers the new host), Gateway listener, route-shape
  and site-params policies, the preview-only sample site; `scripts/prototype/render-examples.py` and golden files.

Tests first: `test_fr20_1_*`, `test_fr52_1_*`, `test_fr55_1_*` to `test_fr55_4_*`, the route-shape policy rows, the
manifest drift test.

Risks: admission policy and certificate changes are proven only on a cluster (the k3d run); the old `.preview.` host
must be refused, not served.

Docs touched: TRD TR-35 and TR-45, decision 0023 G18b, decision 0016 section 10, `open-decisions.md` OD-71, the
prototype runbooks.
Constitution:
- I Spec-driven flow: pass. FR-20, FR-52, FR-55 rows (PREVIEW1) are in the FRD; tests name their rows.
- II Tech stack: not applicable. No library, image or tool change.
- III Architecture limits: pass. III.3: no database change, FR-55 in code and admission only; III.6: only the
  bootstrap labels namespaces, only the worker writes routes; III.7: the token goes only to the project's own preview
  host and is removed before the site.
- IV Coding conventions: pass. One shared rule in the domain (`preview_label`, `subdomain_refusal`); mypy and ruff.
- V Testing and quality: pass. Tests first; policy text tested without a cluster, the live cases wait for the k3d run.
- VI Safety guardrails: pass. No secret; the FR-55 log line names the project ID only; no cluster command.
- VII Docs: pass. Docs state the new host as current fact, in the commits that change it.
- VIII Agents: pass. One implementer; the spec and security reviews are by other agents.
