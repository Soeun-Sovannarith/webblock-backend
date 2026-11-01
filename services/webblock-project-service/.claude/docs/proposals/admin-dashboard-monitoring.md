# Platform admin dashboard: MVP monitoring

Owner proposal, not yet in the spec. MVP only: what an admin needs to see that the platform works and to answer a
customer. Everything else is listed under "Later".

**How, in general.** The dashboard stores nothing; it reads two sources:
- **Admin read API (new):** read-only endpoints in Project and Provisioning (`/api/v1/admin/...`), allowed only for
  the `ADMIN` role, answering counts and lists from that service's database.
- **Prometheus:** metrics that already exist (Istio sidecar request metrics, Spring Boot `http_server_requests`).

## What it monitors

- **Monitor users:** total users and sign-ups per day. How: Project admin API (distinct `owner_user_id`, first project
  date).
- **Monitor projects:** count by status (`draft`, `active`, `suspended`) and deleted. How: Project admin API over
  `project.projects`.
- **Monitor generation requests:** count by status and the failed ones by `failure_code`. How: Project admin API over
  `project.generation_requests`.
- **Monitor stuck generation requests:** requests in `captured`, `generating` or `publishing` for more than 30 minutes.
  How: Project admin API (`now() - updated_at`).
- **Monitor releases:** count by release status; the failed ones with their `failure_code`. How: Provisioning admin
  API over `provisioning.releases`.
- **Monitor builds:** each release's build step (status, start, duration, failure code, log link). How: Provisioning
  admin API over `provisioning.release_steps` where the step is `build`; the log link goes to the Jenkins build.
- **Monitor commands:** commands by kind and status, with who requested them, when, and the failure code. How:
  Provisioning admin API over `provisioning.commands`.
- **Monitor user websites:** which sites are `online` or `offline`, and which have `uncertain` routing. How:
  Provisioning admin API over `provisioning.project_hosting`.
- **Monitor services:** up/down and error rate of Project, Provisioning, Payment, ai-codegen, Keycloak and Next.js.
  How: Prometheus.
- **Look up a user:** their projects, generation requests and websites, with statuses (read only; never page content).
  How: Project and Provisioning admin APIs.

## Admin actions

- **Suspend a website:** takes a site offline. How: a `suspend_hosting` command through Provisioning's API with the
  `ADMIN` role.
- **Resume a website:** brings a suspended site back online. How: a `resume_hosting` command, same path.
- **Roll back a release:** returns a site to its previous live release. How: a `rollback` command (the `ADMIN` role
  is already required for it); needs decision 3 below.
- **Audit admin actions:** every action records who, what, which site, when and the result. How: the command row
  already stores the requester (`requested_by_subject`) and the outcome; the dashboard lists commands requested by
  admins.

## Status types it shows

- **Project status:** `draft`, `active`, `suspended` (a deleted project has `deleted_at` set).
- **Generation request status:** `captured`, `generating`, `publishing`, `published`, `failed` (`published` and
  `failed` are final).
- **Generation failure codes:** `trial_generation_limit_reached`, `trial_ended`, `no_active_plan`, `client_suspended`,
  `project_not_admitted` (Payment refused), `admission_unavailable`, `admission_error`, `project_unavailable`.
- **Release status:** `accepted`, `building`, `built`, `candidate_ready`, `promoting`, `live`, `superseded`, `failed`,
  `rolled_back`.
- **Release steps:** `build`, `access_setup`, `candidate`, `promotion`, `observation`, `rollback`.
- **Release step status:** `pending`, `running`, `succeeded`, `failed`.
- **Command kind:** `deploy`, `promote`, `rollback`, `suspend_hosting`, `resume_hosting`, `sync_domains`.
- **Command status:** `accepted`, `running`, `succeeded`, `failed`, `skipped`.
- **Hosting status:** `online`, `offline`.
- **Routing status:** `stable`, `changing`, `uncertain`.

## Decisions needed first

| # | Question | Proposal |
| --- | --- | --- |
| 1 | Where does it live? | **Owner:** in the same Next.js app (one deployment). `/admin` is served only on `admin.<domain>`, which comes through the Cloudflare tunnel and Access, and only to a user with `ADMIN` (checked server-side by Next.js and again by the admin APIs). On `app.<domain>`, `/admin` answers 404. |
| 2 | Project ignores roles today (auth decision A5) | Admin read endpoints in Project and Provisioning that require `ADMIN` (a small design change) |
| 3 | Admin actions on another user's site (OD-45): today an `ADMIN` who does not own the site is refused, and production refuses every mutation | **Owner:** an admin may suspend, resume and roll back any site, owner or not. Provisioning skips Project's owner check for `suspend_hosting`, `resume_hosting` and `rollback` when the token has `ADMIN`; every other action and every other user keep the owner check. No database change: the role comes from the token and `commands.requested_by_subject` records the admin. Production enables these three for admins only. |
| 4 | Mesh path for the dashboard | **Settled by decision 1:** the Next.js server's ServiceAccount is already admitted to Project and Provisioning; the admin pages call the admin APIs server-side with the admin's own token. One addition: the internal gateway routes `admin.<domain>` to the Next.js app (a tunnel hostname, an Access application, and the Next.js policy admits `<internal-gateway-sa>`). |

## Corrections

These replace the parts above that they name. The database design records them (`PLATFORM_DB_DESIGN.md`, sections 1
and 5).

### Owner rulings

- **Decision 3 is replaced.**
  - There is no per-site admin suspend or resume. "Suspend" means an admin suspends a **user** (`account.users.status
    → suspended`, with `status_reason_code` `abuse`, `fraud`, `owner_request` or `other`, and an optional
    `status_note`).
  - Every website the user owns goes offline through `suspend_hosting`, with hosting reason `user_suspended`.
  - When the admin sets the user back to `active`, each site gets `resume_hosting`. It comes back only if the plan
    still covers it: a site offline for billing stays offline.
  - A suspension ends only when an admin reactivates the user; there is no timer.
  - The admin rollback stays as it is.
  - So "Suspend a website" and "Resume a website" under "Admin actions" are replaced by "Suspend a user" and
    "Reactivate a user" (Account's admin API, planned).
- **Hosting label.** "Offline (billing)" becomes "Offline" followed by its reasons: `billing`, `user_suspended` and
  `project_deleted` (`provisioning.project_hosting.offline_reasons`, planned migration `00018`). A site can have more
  than one reason, and it is online only when none is left.
- **Audit of a user suspension.** Hosting commands are system-started. For a user suspension, the record of who and
  when is `account.users.status_changed_by` and `status_changed_at`. That row holds only the current suspension. For a
  past one, who and when come from the status-change event that the command's key names, which Provisioning's audit
  log records.

### Further rules

- **A suspended user cannot sign in.** "Suspend a user" also disables the user in Keycloak, and "Reactivate a user"
  enables it again. Access tokens issued before the suspension stay valid until they expire.
- **Project `suspended`** stays a reserved per-project block with no writer and no hosting effect; the dashboard shows
  it but offers no action for it.

### Corrections from review

- **Rollback (under "Admin actions") does not "return a site to its previous live release".** A `rollback` is accepted
  only while the route is `uncertain`. It points the route at the *current* live release, or at the offline page when
  hosting is offline or there is no live release. It cannot revert a healthy release to an older one.
- **Who started a rollback**. Rollback commands show who started them through a planned admin-only read of
  `requested_by_subject`; the client API never returns it.
- **Command kinds shown.** `sync_domains` stays in the list but is never used.
- **Release steps shown.** Four step names are never written: `promotion` and `rollback` (allowed, but no code writes
  them), and `access_setup` and `observation` (refused by the database). Only `build` and `candidate` rows exist. Hide
  the four names.
- **Production runs no command yet.** "Production enables these three for admins only" (decision 3) does not hold
  today: the production API refuses every mutation, and the production worker runs no command kind.

## Later (not MVP)

Plans and trials, usage per plan, storage per site, stage timings, site health probes, message backlogs, cluster and
certificate tiles (Grafana covers these), security events (Cloudflare and Keycloak dashboards cover these).
