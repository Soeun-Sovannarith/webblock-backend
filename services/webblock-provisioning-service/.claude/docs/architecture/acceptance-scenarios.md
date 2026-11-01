# Required acceptance scenarios

Every security and recovery scenario below has a passing assertion before a merge (the `provisioning-gates` skill,
"What \"done\" needs"). "Evidence" names where the case is proven: e2e (`tests/e2e`, live Kind stack;
`tests/e2e/faults` for fault injection), Kind (`make proto-test-kind`: `tests/integration/stack`,
`tests/integration/policy`, `tests/integration/kubernetes`), store (real PostgreSQL 18), messaging (real RabbitMQ),
unit. Paths are under `tests/`. Kind never proves DOKS, the cloud load balancer, the production registry, the
owner's Jenkins, production TLS, storage or backups; those cases are marked **Production only**.

| Scenario | Required assertion | Evidence |
| --- | --- | --- |
| Unauthorized project or direct MCP call | No operation or cluster side effect | e2e: no access is 404 and writes no row, a missing role is 403 (`e2e/test_api_surface.py` tests 1 and 2); a token without the role is refused by every tool and route (`e2e/test_scenarios_mcp.py`) |
| Invalid, expired or wrong-audience token | Denied before mutation | unit and transport tests against a local HTTPS issuer (`unit/infrastructure/identity/test_jwt*.py`): 401 for every refused token, 403 for a missing role over HTTP and MCP, 503 `identity_unavailable` without fresh keys |
| MCP asks for a hosting kind or `sync_domains` | Unknown tool; nothing accepted | e2e (`e2e/test_scenarios_mcp.py`: no tool reaches a hosting kind) |
| Same request twice; same key, other request | Original operation reused; conflict, original unchanged | e2e (`e2e/test_edge_cases.py`), store |
| Simultaneous commands | One in flight; the other caller gets the running command's ID | e2e, store |
| Repeated or mismatched "source published" event | Same release; a repeat with another repository or commit changes nothing | e2e (`e2e/test_edge_cases.py`; `e2e/test_scenarios_intake.py`: `test_a_repeat_with_another_commit_is_refused_and_creates_no_second_release`), store |
| Event names a repository that is not the project's | No release | e2e (`e2e/test_scenarios_intake.py`: `test_an_event_naming_another_repository_is_rejected_with_no_release`), store, unit |
| Stale command number, lost claim, finished command | No SQL write; no cluster write at a lower number | store; cluster fence re-check: unit; admission: `webblock-site-fence` requires a well-formed number (Kind); "never lower" is **not built** |
| Build result for the wrong project, release, attempt or commit | Attempt failed; nothing advances | unit, live Jenkins |
| Build fails, times out, attempts exhausted | Attempt failed; the next one starts by itself; release `failed` at the maximum | e2e (`e2e/faults/test_build_failed_attempts.py`), unit, store, live Jenkins (overdue build cancelled) |
| Restart during a running build | Polling resumes from `log_ref`; one build, one attempt | e2e (`e2e/test_edge_cases.py`; Jenkins restarted mid-build: `e2e/faults/test_build_jenkins_restart.py`) |
| Concurrent build requests | One Jenkins build; others `409 build_in_progress` | e2e (`e2e/test_scenarios_build.py`) |
| Branch changed after acceptance | Exact SHA built | Jenkins pipeline check, live Jenkins |
| Candidate never ready, crash loop, image pull failure | Command failed; release stays `built`; route untouched | e2e (`e2e/test_flows.py` test 3), Kind |
| Cluster refuses the intent | Release `failed` | unit, store, Kind (adapter) |
| Candidate fails its preview probe | A failed try (`candidate_not_answering`); after 3 tries the release is `rolled_back`; the site route never moves to it | unit, store (the e2e cases follow in the cluster run) |
| A go-live try fails | Route back on the live release (offline page on a first publish); release `promoting`, command `running`; next try at least 30 s, then 60 s later (decision `0027`) | unit, store (`integration/test_go_live_tries.py`) |
| Old backend answers during a switch | Does not count: the probe requires the candidate's `X-Webblock-Release` | e2e, unit; a site that sends its own header never reaches the visitor with it (`e2e/faults/test_qa_spoofed_release_header.py`) |
| `/readyz` that flaps, or answers with a huge body | Not promoted; worker memory bounded | Kind (`integration/kubernetes/test_probe_live.py`: three answers in a row; 1 GiB body), unit |
| Resumed claim with a failing preview | The claim is the next try: route put back first; after try 3 the candidate is `rolled_back` (`failed` on first publish) | unit |
| Failed public check after the switch | Route restored to the live release and confirmed after each try; after try 3 the replacement is `rolled_back` (`site_not_answering`) | unit, store (the e2e cases follow in the cluster run) |
| First publish fails | Route on the offline backend; release `failed`; site offline | e2e (`e2e/test_scenarios_promote.py`: `test_a_failed_first_publish_removes_the_route_and_the_site_stays_offline`), unit |
| Failed or unknown restore | Routing `uncertain`; one `route_uncertain` alert; only operator rollback accepted | e2e (`e2e/test_scenarios_promote.py`: `test_an_unconfirmed_restore_leaves_the_route_uncertain_until_the_operator_rolls_back`, alert found in Loki), store, unit |
| Route edited by hand, or stamped with a higher number | Project `uncertain`; the operator rollback takes the route back (the one forced apply) | e2e (`e2e/faults/test_recovery_route_reclaim.py`), Kind (`integration/kubernetes/test_inventory_live.py`), unit (`test_forced_apply.py`) |
| Uncertain route after a failed suspend or resume | Operator rollback clears it to the recorded target (`0014` section 13); the hosting command is then retried | e2e (`e2e/faults/test_hosting_retry.py`), store, unit |
| Command past its deadline while `changing` (worker killed, no live claim) | Abandon hook ends the project `stable` or `uncertain`, never stuck; new commands accepted | e2e (`e2e/faults/test_recovery_stuck_project.py`), unit (`test_recovery.py`) |
| Worker killed mid-command | Claim expires; command resumed; one candidate step | e2e (`e2e/test_edge_cases.py`; after the switch: `e2e/faults/test_qa_worker_killed.py`) |
| Deploy refused inside its finish margin | Candidate removed; step ended | e2e (`e2e/faults/test_recovery_deploy_margin.py`), unit |
| Worker timing no command can survive | Refused at start | unit (`unit/infrastructure/config/test_worker_timing.py`) |
| Committed command never published | Published from SQL | e2e, messaging |
| Broker outage, acknowledgment crash, duplicate job | Committed work recovered; executed once | messaging; e2e (`e2e/faults/test_qa_broker_paused.py`; database paused: `test_qa_database_paused.py`) |
| Parked event (failed inbox row or dead-lettered message) | Kept; re-driven only by the audited operator tool; never lost by listing | e2e (`e2e/test_dlq_redrive.py`, `e2e/test_operator.py`), messaging (`messaging/test_dlq.py`); production broker policy OD-70 |
| Suspend while a deploy is in flight | Busy; the event is kept and retried | store |
| Suspend event and a later restore event; worker restarted between | The suspend runs unless a later restore is known (prototype record); production asks Payment | unit; production: **not built** (OD-04) |
| Project deleted | Site offline; images kept; a later restore never resumes it | e2e (`e2e/faults/test_hosting_deletion.py`), messaging |
| Deploy or promote while offline | Refused | store, unit |
| Entitlement lost at acceptance, or while a deploy or promote is queued | `limit_reached` at acceptance; refused when it runs | e2e through the file-based stand-in (`e2e/test_api_surface.py` tests 3 and 4); production: **not built** (OD-04) |
| Release goes live | One `publish_completed` usage event; a crash or broker outage sends again, never loses | e2e (`e2e/test_usage_event.py`), messaging (`messaging/test_usage_events.py`), store (`integration/test_schema_00016.py`) |
| Mutation or operator action | One audit event with actor, action, time and result; no token in any line | e2e (`e2e/test_scenarios_audit.py`, `e2e/test_api_surface.py` test 7, queried from Loki), Kind (`integration/stack/test_loki_live.py`) |
| Bootstrap identity outside its scope | Refused by `webblock-bootstrap-scope` (namespace, Secret type, RoleBinding target, subresources, deletes) | Kind (`integration/policy/test_bootstrap_scope_live.py`) |
| Worker reads a Secret or writes outside tenants | Forbidden by RBAC | Kind (`integration/stack/test_worker_rbac_live.py`), review |
| Anyone but the owning identity writes in a tenant | Refused by `webblock-tenant-writer` | Kind (`integration/stack/test_site_policies_live.py`) |
| Site role reaches another website | Cannot read or write another website's rows or `catalog.websites` | live catalog tests |
| Secret missing while a release is live | `runtime_secret_missing`; nothing rotated, except a namespace that bootstrap saw absent (`0014` section 24) | unit, live catalog; rebuilt namespace: e2e (`e2e/faults/test_bootstrap_namespace_rebuilt.py`), unit (`unit/hosts/bootstrap/test_tenants.py`) |
| Image of another project | Refused by the pipeline check, the renderer and `webblock-site-image` | Jenkins, unit, Kind (`integration/stack/test_site_policies_live.py`) |
| Drift or deletion of the live release's objects | Re-applied by the reconciler; a foreign field manager reported, never overwritten; deleting the routed release refused | e2e (`e2e/faults/test_recovery_reconciler.py`), Kind (`webblock-site-routed-release` in `integration/stack/test_site_policies_live.py`), unit (`test_reconcile.py`) |
| Finished releases' workloads | Deleted, never the live or routed one; a delete refused every round is given up after 10 | e2e (`e2e/faults/test_recovery_reconciler.py`), unit (`test_reconcile.py`, `test_recovery.py`) |
| HTTPRoute without hostnames, with another hostname, or to a ListenerSet | Refused by admission (`webblock-site-route-shape`) | Kind (`integration/stack/test_site_policies_live.py`) |
| Cross-tenant network access | Denied by the tenant network policy; also the API server, the metadata address and the registry | Kind (`integration/stack/test_tenant_network_live.py`) |
| Node pressure, quota, unschedulable candidate | Bounded failure with a precise code; no node creation | unit: `insufficient_resources` (pod `Unschedulable`) and `quota_exceeded` at the progress deadline; the deadline path seen live. No node is created |
| Registry pull failure or bad pull credential | `image_pull_failed`; live site unaffected | **Production only**: not provable on Kind (TR-52) |
| Provisioning database restored to an earlier point | Objects stamped with higher command numbers are detected and resolved by a runbook, never overwritten | **Production only** (OD-63) |
| More than one replica of each process | Claims and fences hold | Kind (`make proto-e2e-replicas`: cross-API serialization, no duplicate work, a stale worker fenced, two build workers, two reconcilers, bootstrap standby and takeover); claim races on real PostgreSQL. More than two replicas, partitions and rolling updates are not tested |
