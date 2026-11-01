# Failure codes

What each failure code means and whether it is worth retrying. Task H5a, decision `0015` section 4 step 1.

## Where the codes appear

- **Release:** `releases.failure_code`, the `failure_code` of `get_release`, `list_releases` and
  `get_release_diagnostics`.
- **Step:** `release_steps.failure_code`, a step's `failure_code` in the API.
- **Command:** `commands.failure_code`, the `failure_code` of `get_command`, `list_commands` and the diagnostics'
  commands.

Wherever the API returns one of these codes, it also returns `failure_retryable`.

- **`failure_retryable`** is true when a new deploy or build of the same input may succeed without a code or
  configuration change: the cause can pass by itself.
- It is false when the cause needs a person to act: on the site's code, on the platform's configuration, or on the
  project's state.
- A code the catalogue does not list is reported as not retryable. Rows written before the catalogue may hold such
  codes.

Codes are stable identifiers. Their meaning never comes from free text, and no condition message, log line or site
answer is ever copied into a code.

## The precise deploy causes

`insufficient_resources` and `quota_exceeded` replace the generic `progress_deadline_exceeded` or `candidate_not_ready`
when the cluster shows their evidence. Nothing else changes: the candidate is deleted, the command fails, and the
release stays `built`.

- **`insufficient_resources`:** a pod of the candidate has `PodScheduled=False` with reason `Unschedulable`. Check node
  capacity and cordoned nodes, then deploy again.
- **`quota_exceeded`:** the candidate's Deployment shows `ReplicaFailure=True`, reason `FailedCreate`, and its message
  names a quota (`exceeded quota`). Only the reason and the presence of that phrase are read. Check the ResourceQuotas
  of `wb-<projectId>`; the live release counts against them.

When both causes are present, the quota wins. Before the Deployment's progress deadline, the evidence is kept as a
diagnosis. It names the code only if the attempt times out.

`dependency_unavailable` is a promotion's code. The candidate answered its preview probe (`/readyz`) with 503 and its
own `X-Webblock-Release` header, and the candidate's Service has a ready endpoint, so a dependency it needs, such as the
catalog database, is down.

- **Why the endpoint check:** the Gateway's own 503 for a backend with no ready endpoint carries the route's header too.
  That case stays `candidate_not_answering`, and so does a failed or slow endpoint read.
- **Residual case:** a 503 the Gateway generates while an endpoint is ready (an upstream connect failure or a reset) can
  still be reported as `dependency_unavailable`. Both codes are retryable, so only the diagnosis is less precise. It
  wins over `candidate_not_answering` only when the last failed probe was that 503. Either is a failed go-live try, and
  the site's route is not touched. After the switch, a 503 is still `site_not_answering`.

## A go-live that fails

The go-live try codes are `route_refused`, `route_not_accepted`, `candidate_not_answering`, `dependency_unavailable`
and `site_not_answering` (`GO_LIVE_TRY_CODES` in `core/domain/rules/go_live.py`; decision `0027`).

One promote command makes up to 3 tries, one per claim. After a failed try 1 or 2 the route is back on the live
release, or on the offline page without one. The release stays `promoting` and the command `running`, and only
`command_dispatch.last_failure_code` holds the try's code until the next try, at least 30 s and then 60 s later. A
client never sees a try code before try 3 ends: then the release is `rolled_back` (or `failed` without a live release)
and the command `failed`, both with try 3's code.

When Payment cannot answer the entitlement recheck before a try, the try fails with `transient_failure` before anything
is touched. After 3 such tries the command ends `attempts_exhausted` and the release stays `candidate_ready`.

`promotion_interrupted` ends a promote whose try 3 was interrupted, for example by a worker stop: the next claim puts
the route back and ends the promotion without a fourth try.

A promote that passes its deadline or its attempt budget while its route is changing is ended by the worker's abandon
hook. It points the route back at the live release, or at the offline page without one.

- **Restore confirmed:** when the dispatch row's `last_failure_code` still holds a try code, the release and the
  command end with it, so the client sees why the go-live failed. Otherwise they end with `deadline_exceeded` or
  `attempts_exhausted`. A worker stop or a database error after the failed try overwrites the row's code with
  `worker_stopping` or `transient_failure`, and then the consumer's code is written.
- **Restore not confirmed:** `route_uncertain` with `deadline_exceeded` or `attempts_exhausted`; the release stays
  `promoting` until an operator rolls back.

The worker's log line `abandon hook ran` names the failure code the hook wrote, or says that it wrote none.

## The catalogue

The tables below are generated from `src/provisioning/core/domain/values/failure_codes.py`. The test
`tests/unit/core/domain/values/test_failure_codes.py` fails when they differ, when the service writes a code that is not
listed, or when a listed code is written nowhere.

The first table lists the codes of the business rows. The second lists codes that are written elsewhere and never reach
a release, step or command.

<!-- catalogue:begin (generated from core/domain/values/failure_codes.py; checked by a test) -->
| Code | Stage | Recorded on | Retryable | Meaning |
| --- | --- | --- | --- | --- |
| `build_rejected` | build | step | no | The build system refused to start or report the attempt. |
| `build_lost` | build | step | yes | The build system no longer knows the attempt, for example after a restart. |
| `build_result_mismatch` | build | step | no | The build system reported a result for another project, release, attempt or commit, or a malformed digest. |
| `build_failed` | build | step | no | The site's build failed, or the build system reported a failure without a known code. |
| `invalid_parameters` | build | step | no | The build template refused the attempt's parameters. |
| `clone_failed` | build | step | yes | The build template could not clone the repository. |
| `commit_not_found` | build | step | no | The recorded commit is not in the repository. |
| `push_failed` | build | step | yes | The build template could not push the image to the registry. |
| `pipeline_failed` | build | step | yes | The build pipeline failed or was aborted without a code of its own. |
| `result_missing` | build | step | no | The build succeeded without archiving its result file. |
| `result_invalid` | build | step | no | The build's result file is not a valid result. |
| `cancelled` | build | step | yes | The build was cancelled in the build system's queue before it started. |
| `build_attempts_exhausted` | build | release | no | The release used all its build attempts. |
| `candidate_apply_conflict` | deploy | step, command | no | Another field manager owns a field of the candidate's objects; the worker never forces. |
| `candidate_fence_ahead` | deploy | step, command | no | The candidate's objects carry a newer command's number. |
| `candidate_refused` | deploy | release, step, command | no | The cluster refused the release's objects (validation or admission). |
| `candidate_not_ready` | deploy | step, command | yes | The candidate was not ready before the attempt's deadline, or its Deployment disappeared. |
| `progress_deadline_exceeded` | deploy | step, command | yes | The candidate's Deployment stopped progressing, with no more precise cause seen. |
| `insufficient_resources` | deploy | step, command | yes | The candidate's pod cannot be scheduled: no node has the CPU or memory free. |
| `quota_exceeded` | deploy | step, command | no | A ResourceQuota of the tenant namespace refuses the candidate's pods. |
| `image_pull_failed` | deploy | step, command | yes | The candidate's image could not be pulled. |
| `crash_loop` | deploy | step, command | no | The candidate's container keeps crashing. |
| `candidate_attempts_exhausted` | deploy | release, command | no | The release used all its candidate attempts. |
| `release_not_deployable` | deploy | command | no | The release is not built with an image digest when the deploy runs. |
| `route_not_stable` | deploy | command | no | The project's route was changing or uncertain when the deploy ran. |
| `hosting_offline` | deploy | command | no | The project's hosting was offline when the deploy ran. |
| `entitlement_lost` | promote | command | no | Payment refused the promotion on the recheck before the route moved. |
| `route_refused` | promote | release, command | no | The cluster refused the route or the preview route. |
| `route_not_accepted` | promote | release, command | yes | The Gateway did not accept the route at the command's number in time. |
| `candidate_not_answering` | promote | release, command | yes | The candidate did not answer on its preview hostname with its release header in time. |
| `dependency_unavailable` | promote | release, command | yes | The candidate answered its preview probe with 503: a dependency it needs, such as the catalog, is down. |
| `site_not_answering` | promote | release, command | yes | After the switch, the site did not answer with the candidate's release header in time. |
| `promotion_interrupted` | promote | release, command | yes | The last go-live try was interrupted; the next claim restored the route and ended the promotion. |
| `route_restore_unconfirmed` | promote | command | no | The route could not be confirmed after a failed promotion or a rollback; it is uncertain until an operator rolls back. |
| `operator_rollback` | rollback | release | no | An operator rollback ended the promotion this release was in. |
| `entitlement_restored` | hosting | command | no | Skipped: a later Payment event restored the entitlement before the suspend ran. |
| `entitlement_ended` | hosting | command | no | Skipped: a later Payment event ended the entitlement before the resume ran. |
| `hosting_already_offline` | hosting | command | no | Skipped: hosting was already offline. |
| `hosting_already_online` | hosting | command | no | Skipped: hosting was already online. |
| `route_change_unconfirmed` | hosting | command | no | The hosting change of the route could not be confirmed; it is uncertain until an operator rolls back. |
| `redeploy_unready` | redeploy | command | no | The restarted pods of the live release were not all ready by the command's deadline, or the cluster refused the restart; the rolling update keeps the old pods serving. |
| `redeploy_target_changed` | redeploy | command | no | The live release was no longer the command's release when it ran; nothing was restarted. |
| `deadline_exceeded` | any command | release, step, command | yes | The command's or the attempt's deadline passed before it finished. |
| `attempts_exhausted` | any command | release, step, command | yes | The command used its execution budget on transient failures. |
| `executor_failed` | any command | command | no | The worker met an unexpected error while executing the command; see the worker log. |
| `executor_unavailable` | any command | command | no | The worker has no executor for the command in this build. |
| `operation_unavailable` | any command | command | no | The worker does not enable the command's kind. |

| Code | Written to | Meaning |
| --- | --- | --- |
| `tenant_not_ready` | command_dispatch.last_failure_code | The tenant namespace or the worker's access to it does not exist yet; the deploy is retried. |
| `cluster_unavailable` | command_dispatch.last_failure_code | The API server was unreachable or slow; the deploy is retried. |
| `transient_failure` | command_dispatch.last_failure_code | A transient failure released the claim; the command is retried. |
| `attempt_timeout` | command_dispatch.last_failure_code | One execution attempt ran out of time; the command is retried. |
| `worker_stopping` | command_dispatch.last_failure_code | The worker stopped and released its claim. |
| `repository_mismatch` | source_published_events.outcome_code | The event names a repository other than the project's. |
| `repository_not_registered` | source_published_events.outcome_code | Project does not know the event's repository. |
| `repository_unconfirmed` | source_published_events.outcome_code | The repository could not be confirmed with Project. |
| `release_conflict` | source_published_events.outcome_code | A release with the event's key exists for another source. |
| `cancel_failed` | audit event | A build cancel did not reach the build system or was refused. |
| `route_reclaim_unconfirmed` | audit event | An operator rollback's forced reclaim of the route was not confirmed. |
| `route_uncertain` | audit event | The route was left uncertain; the alert's code when the command has none. |
| `publish_failed` | usage_outbox | A usage event could not be published; it is retried. |
| `publish_unconfirmed` | usage_outbox | The broker did not confirm a usage event; it is retried. |
<!-- catalogue:end -->
