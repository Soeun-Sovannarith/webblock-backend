# Provisioning event contracts (v1)

Since decision `0022` the shared event contracts live in the repository `web-block-contracts`. Provisioning pins one commit of it (`contracts.lock`) and keeps a copy of the files it reads in `src/contracts/shared/` (`make contracts-sync`; `make contracts-check` fails when the copy differs). Do not edit that copy by hand.

This folder keeps only what is Provisioning's own: the internal job message that tells the release worker an accepted command is ready.

| File | Purpose |
| --- | --- |
| `command-job.v1.schema.json` | The job for the release worker. All command kinds: `deploy`, `promote`, `rollback`, `suspend_hosting`, `resume_hosting`, `redeploy`. Message type `provisioning.command_job`, queue `provisioning.command_job.v1`, dead letter queue `provisioning.command_job.v1.dlq` |
| `envelope.v1.schema.json` | The envelope the job extends. The shared envelope is `src/contracts/shared/events/envelope.v1.schema.json` |

## Where the other schemas are

All under `src/contracts/shared/events/`. Their status (accepted, owned by Provisioning, or draft that waits for its owner) is in the header of each file and in the README of `web-block-contracts`.

| Event | Owner | Routing key |
| --- | --- | --- |
| `project/source_published` | Project (draft) | `source_published.v1` on `project.events` |
| `project/project_deleted` | Project (draft) | `project_deleted.v1` on `project.events` |
| `project/owner_status_changed` | Project (draft) | `owner_status_changed.v1` on `project.events` |
| `payment/hosting_entitlement_ended` | Payment (draft) | `hosting_entitlement_ended.v1` on `payment.events` |
| `payment/hosting_entitlement_restored` | Payment (draft) | `hosting_entitlement_restored.v1` on `payment.events` |
| `provisioning/publish_completed` | Provisioning | `publish_completed.v1` on `provisioning.events` |

Every event has the same envelope (snake_case): `schema_version` (1), `message_id`, `message_type` (`<service>.<event>.v1`), `occurred_at`, `correlation_id` and `project_id`. Each event also has a pydantic model in `src/provisioning/presentation/messaging/models.py`. A contract test runs every example through both the schema and the model.

Messages carry identifiers only. The database row is the authority; a consumer must compare the identifiers with durable state and must not act on a message merely because its shape is valid.

## The command job

| Field | Rule |
| --- | --- |
| Envelope fields | required, as in the shared envelope |
| `fencing_generation` | 1 to 2^53-1: the generation the server assigned when the command was accepted. 0 is never a fencing generation |
| `command_id` | required |
| `release_id` | present exactly when the kind is not a hosting kind. A `suspend_hosting` or `resume_hosting` job that names a release is rejected, and so is any other job without one |
| `kind` | the command kinds above |

No message carries release content, a commit, an image or an input hash.

## Examples

`examples/valid` and `examples/invalid` hold the command job examples and are exercised by `tests/contracts`, which pins both sets by name. The examples of the shared events are in `src/contracts/shared/events/examples`.

Valid: `envelope-basic`, `envelope-minimal`, and one command job for each kind. The two hosting jobs carry no `release_id`. The prototype worker runs a redeploy job as a rolling restart of the live release (decision 0023 G12); the example pins its shape. `sync_domains` has no example: it is never sent.

Each invalid example differs from one valid example in exactly one property and is named for the rule it breaks (for example `command-job-hosting-with-release-id`, `command-job-zero-generation`, `command-job-extra-property`, `command-job-nil-uuid`).
