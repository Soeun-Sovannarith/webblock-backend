# Security review: one-label preview host (preview-host)

Stage 5 (Verify). Branch `preview-host`, range `17c8b2c..8877933` (commits 16f6aa7, 1013ed9, 8877933), reviewer
model claude-opus-5-5. IDs in scope: FR-20, FR-52, FR-55; TR-35, TR-45; constitution III.3, III.6, III.7; decision
0023 G18b and G18c, 0016 section 10. Checked against `.claude/docs/changes/preview-host.md`.

Lead rulings accepted: no extra policy guard for namespaces labelled before this change (the MVP has none); the
181-character site domain bound stays.

## Method

- Skills: `differential-review` (triage, diff analysis, blast radius, adversarial pass done by me; the named
  adversarial agent does not exist here), `sharp-edges` (probe host test, policy inputs), `k8s-security-policies`
  (deploy/ changes).
- Read the whole diff of `src/`, `deploy/`, `scripts/` and the tests, plus the unchanged code around it:
  `site_cluster.py` `probe_site` and `_answers_once`, `render.py` route and preview renderers, `bootstrap_loop.py`
  `_ensure`, `site-route-shape.yaml` (whole policy), `bootstrap-scope.yaml` label rule, `config/worker.py` gateway URL.
- Grepped the whole tree for the old `.preview.` host: only historical reviews, the handoff and the DIRECTION row
  remain; no code, policy, certificate or manifest names it.
- Host algebra: a site host `X.<d>` equals a preview host `Y-preview.<d>` only when `X = Y-preview`, which FR-55
  refuses; two preview hosts are equal only when the sub-domains are (FR-54); the preview label is at most 63 when
  the sub-domain is at most 55.
- Gates, run in this worktree (`CONTRACTS_REPO=/home/roa/Documents/web-block/web-block-contracts`):
  `make py-fmt-check` 636 files already formatted; `make py-lint` all checks passed; `make py-typecheck` no issues
  in 630 files; `make py-layers` 16 kept, 0 broken; `make py-test` 7245 passed; `make chart-check` 23 passed.
- Not run: `py-vuln`, `py-age`, `image-scan` (no change to `uv.lock`, tool pins or images); the Kind or k3d
  suites (`test_dry_run_live.py`, `test_site_policies_live.py`; cluster work is for the later k3d run, see the list
  below). The CEL of the route-shape policy is checked by unit tests only as text, not evaluated.
- Not checked: the production Cloudflare, Traefik and tunnel settings (in web-block-config, not in this repo).

## Findings

### Medium

**M1. Public previews behind Cloudflare can be served from the edge cache without the token.**
`.claude/docs/decisions/0016-production-environment.md:296` makes `<subdomain>-preview.roaa15.xyz` public through
the Cloudflare tunnel. The preview route keys access only on a request header (`render.py:521-527`), and nothing on
the preview route or in the decision stops Cloudflare caching its answers (`render.py:449-459` sets only
`X-Webblock-Release`). Cloudflare caches static file types by default, keyed on host, path and query, not on
request headers. Breaks FR-52 (FR-52.1: with no token, the candidate is not reached) and 0023 G18c.
Abuse: an owner opens `shop-preview.roaa15.xyz` with the token; `/app.js`, images and CSS of the unreleased
candidate are cached at the edge; anyone then GETs the same URLs without a token and receives them. The worker's
probe goes to Traefik directly, so it does not cause this; a token holder through the public path does.
Before this change previews were internal (`0016` item 4 said so), so this exposure is new.
Fix: on `preview-<projectId>` add a `ResponseHeaderModifier` that sets `Cache-Control: private, no-store` (the
route-shape policy already admits that filter type; tighten it to that one header on previews), and record in
0016 section 10 that the zone has a Cloudflare cache rule bypassing the cache for hosts ending in
`-preview.<site domain>`; owner to pass the rule to web-block-config. Add a renderer unit test and a k3d check.

### Low

**L1. The probe decides "preview" from the host string, not from the project.**
`site_cluster.py:575-585` and `:592`: any `<label>-preview.<site domain>` gets the calling project's token, not only
the project's own preview host. FR-52 and G18c say the token goes only to the project's own preview host. Today the
only caller (`routing.py:292`, `:684`) builds the host from the project's own sub-domain, the request goes only to
`SITE_GATEWAY_HTTPS_URL`, and another project's preview route needs another token, so there is no leak now. Abuse
needs a caller bug or a namespace labelled `x-preview` (excluded by the lead's ruling): the probe of that site host
would carry the project's token to the site, since a site route does not remove the header.
Fix: give `probe_site` an explicit `preview: bool` (or the sub-domain) from `RoutePointer` and send the token only
when `hostname == preview_hostname(subdomain, site_domain)`; add a test that `probe_site(PROJECT,
"other-shop-preview.<d>")` sends no token.

**L2. The cluster does not enforce FR-55 for new labels.** `deploy/prototype/policy/bootstrap-scope.yaml:137`
accepts any one-label sub-domain, including `shop-preview` and 56 to 63 characters, so the only guard against the
collision is the bootstrap code (`bootstrap_loop.py:184`) and Project. TR-35 describes admission as the
hostname-takeover guard; a bootstrap code bug (not a stolen identity) would let the site route of `shop-preview`
claim `shop`'s preview host, and both routes would be admitted. This is about new labels, not the ruled-out guard
for old namespaces.
Fix: add `&& !variables.newSubdomain.endsWith('-preview') && size(variables.newSubdomain) <= 55` to that rule, with a
k3d case. The lead may rule it out under the MVP; then record it in 0023 G18b.

No High findings.

## Checked and clean

- Host form everywhere: `preview_label` (`text.py:152`) feeds `preview_hostname` (`routing.py:185-190`), the renderer
  check (`render.py:530-534`) and the probe check; the policy builds `subdomain + '-preview.' + siteDomain`
  (`site-route-shape.yaml:73-76`); manifest example, sample site regex, params comment and certificate agree.
- Old dotted host and every other host refused for `preview-<projectId>`: exact equality on every hostname
  (`site-route-shape.yaml:105`); `preview-<subdomain>` and the site host also refused; unit and live cases added.
- No site route can claim another project's preview host: a site route admits only `<own label>.<d>`, which is a
  preview host only for a `-preview` label, refused by FR-55 (`text.py:157-165`, `bootstrap_loop.py:184-192`).
- `preview` alone and `shop-previews` are allowed (`test_fr55_1`) and their site hosts end in no `-preview`, so they
  are no one's preview host.
- 56 to 63 characters: refused at the bootstrap; the renderer refuses a preview label over 63 anyway
  (`render.py:553`, `is_hostname`), so it fails closed.
- Probe targets (FR-52): token only for `<label longer than 8, ending -preview>.<site domain>`; the old dotted host,
  `preview.<d>`, `shop-previews.<d>`, another domain and a deeper name get none (`test_fr52_*`). Requests go only to
  the configured https Gateway URL, never to the host itself; SNI set, redirects not followed.
- Header removal (FR-52.2, S1, S4, L1): the preview rule still carries the `RequestHeaderModifier` removal
  (`render.py:563`); the policy still requires exactly one removal per preview rule and exactly one Exact token
  match; unchanged by this diff.
- FR-55 log line: `project_id` and a fixed reason only (`bootstrap_loop.py:186-191`); tests assert the sub-domain
  is in no record (`test_preview_subdomain.py:121-122`, `:158`).
- TLS (TR-45): the certificate now names only `*.sites.webblock.test` (one label covers site and preview hosts);
  the old dotted host has no certificate; listener unchanged; private key, issuer and durations unchanged.
- III.3: no file under `src/migrations/`, no grant, no schema change; the DB check stays 1 to 63.
- III.6: no RBAC, NetworkPolicy, field manager or identity change; only the bootstrap still labels namespaces.
- III.7 and secrets (VI.2, VII.1): no token, secret or `.env` value added; example IDs only.
- Supply chain: `uv.lock`, `pyproject.toml`, images and Helm chart unchanged.

## What the k3d run must prove

1. `preview-<pid>` on `<subdomain>-preview.<d>` is admitted; on `<subdomain>.preview.<d>`, `preview-<subdomain>.<d>`,
   `<subdomain>.<d>` and `<other>-preview.<d>` it is denied by `webblock-site-route-shape`.
2. `route-<pid>` naming `<subdomain>-preview.<d>` or another project's preview host is denied.
3. Through the Gateway over HTTPS with SNI `<subdomain>-preview.<d>`: the certificate `*.<d>` verifies; with the
   project's token the candidate answers with its release header; with no token or another project's token, 404.
4. The site backend never receives `X-Webblock-Preview` (echo or log check on the candidate).
5. The TLS handshake for `<subdomain>.preview.<d>` fails verification (no name covers it).
6. A project with sub-domain `x-preview` or of 56 characters gets no namespace and one error line with its ID only;
   `preview` and `shop-previews` get theirs.
7. The full promotion (preview probe, switch, site probe) passes, and the `preview-only` sample site ends
   `rolled_back` with `site_not_answering`.
8. If M1 is fixed: preview answers carry `Cache-Control: private, no-store`.

Verdict: needs fixes (M1 before previews go public in production; L1 and L2 are the implementer's and lead's call).
