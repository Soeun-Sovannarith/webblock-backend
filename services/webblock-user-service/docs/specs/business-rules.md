# WebBlock — Business Rules (short list)

Each rule names its source: **FD** = feature-detail.md, **DD** = data-dictionary.md, **WF** = workflow.md.
If a task would break one of these rules, stop and ask. Don't work around it.

## Scope
1. Generated websites are **showcase only**: no cart, checkout, orders, bookings, customer accounts or online payment for visitors. CTAs say "Contact us". (FD §1, §3.7)
2. The only money WebBlock handles is the owner's **WebBlock subscription** (Plus / Pro / Ultra, monthly). (FD §3.7)
3. Roles: **User** (business owner), **Admin** (WebBlock staff), **System** (automatic). Admin accounts are created by the dev team, **never** through registration. (FD §2, §3.1)
4. An owner can only see and change **their own** websites and data. Business Portal ≠ Admin Portal: never expose admin features to owners. (FD §3.3, §3.8)

## Website & editor
5. Puck editor uses **predefined blocks only**: Hero, Text, Product Cards, About, Location, Footer. (FD §3.3)
6. Design options are fixed: Product Card columns 2–4 · alignment left/center/right · spacing tight/normal/roomy · palette accent/background/text · font Editorial/Clean/Rounded · button square/rounded/pill. (FD §3.3)
7. AI prompt edits may only use `ADD_COMPONENT`, `UPDATE_PROPS`, `DELETE_COMPONENT`, `MOVE_COMPONENT`, and only on props allowed by `puck_component_definitions.editable_props_schema` / `allowed_operations`. Anything else → change set `REJECTED`, UI shows "Request cannot be done". (DD ai_assistant_*, WF WebBlock AI)
8. Edits use optimistic locking: `projects.edit_revision`. A save based on an old revision is a conflict, not an overwrite. (DD)
9. Business type is chosen at creation and **cannot change**. (DD projects, websites)

## Subdomain & SSL
10. WebBlock subdomain only (e.g. `business.webblock.com`). **No custom domains** in the MVP. (FD §3.4)
11. Subdomain is chosen before the first deploy, is **unique**, max 63 chars, and **cannot be changed** afterwards. (FD §3.3, DD projects.subdomain)
12. SSL is issued and renewed automatically for every subdomain. (FD §3.4)

## Generation & deployment
13. Generation input = saved Puck tree + business info + generation contract. It is **frozen** (`captured_input` + SHA-256 hash) before the AI runs. (DD generation_requests, input_versions)
14. Every generated site uses the **same predefined project structure** (Next.js frontend + API route handlers + DB schema from custom fields). Code is pushed to the project's Git repository (Gitea per DD). (FD §3.5, DD)
15. A harness run succeeds only when all `required` validation runs pass (PUCK_SCHEMA, TYPE_CHECK, BUILD, LINT, SMOKE_TEST, SECURITY). (DD validation_runs)
16. Each website runs on **its own infrastructure** and its own DB login (`websites.db_role_name`). (FD §3.6, DD)
17. Failed deploy: owner may **retry max 2 times** (configurable — never hard-code). After that → "Contact Admin" + email admin. A failed **redeploy** (not first deploy) rolls back to the previous live release. (FD §3.6, WF Deployment)
18. Admin can redeploy any website from the Admin Portal. (FD §3.8)

## Payment
19. **Free trial**: one per client for life, 14 days, first deploy only, max 3 generations, max 30 products. (FD §3.7, DD client_trials)
20. After the trial the site stays online only with an **active paid plan**. Redeploy needs an active plan. (FD §3.7, WF Puck Editor "Is subscription active")
21. Payment = WeBill 365 **KHQR**. A bill has `expires_at`; an unpaid bill becomes `EXPIRED` and the user makes a new one. On success: save billing history, email invoice, activate subscription. (WF Payment, DD bills)
22. Prices are **dated** (`plan_prices.effective_from`). A subscription keeps its `price_usd` until renewal. Admin price change needs a confirmation and is written to `plan_changes`. (FD §3.8, DD)
23. Plan change keeps remaining days ("Days Remaining + Extra Days"). (WF Subscription Plan)

## Accounts & admin
24. Suspending a user also suspends their websites (hosting offline reason `USER_SUSPENDED`); their plan still counts normally. (FD §3.10, DD)
25. Every admin action is audited: who, what, to whom, when. (FD §3.10)

## Reliability (applies to every service)
26. Every command/event that can be retried carries an **idempotency key** (`idempotency_key`, `event_key`) and is processed once. (DD)
27. Version counters (`status_version`, `command_number`, `last_command_number`) only go up; drop events older than the stored number. (DD)
28. `failure_code` / `failure_summary` / `errors` are **sanitized**: no secrets, tokens, stack traces or personal data. (DD)
29. All times are `timestamptz` in UTC; show Phnom Penh time (Asia/Phnom_Penh) in the UI. (DD transactions)

## Notifications (FD §3.9)
| Event | Owner | Admin |
|---|---|---|
| Generation completes | Web + Mobile | — |
| Generation stuck | — | Web + Email |
| Deployment succeeds | Web + Mobile | — |
| Deployment fails | Web + Mobile | Web |
| Monthly payment due | Mobile + Email | — |
| Site crashes (live traffic) | Mobile + Email | Web |
| Site exceeds resource limits | Mobile + Email | Web |
| Platform service down/unhealthy | — | Web + Email |
| New subscription Active | — | Web |
| Subscription expires | — | Web |
