# 0017 The sub-domain label rule

## Decision

- A website sub-domain is one DNS label of 3 to 55 lowercase letters, digits and dashes
  (`^[a-z0-9][a-z0-9-]{1,53}[a-z0-9]$`). It never ends with `-preview` and never starts with `xn--`.
- `Subdomain` holds the rule. Create and the availability check both call it (FR-13.17). The column
  `projects.subdomain` stays VARCHAR(63), as Notion "2 · Project" has it; `Subdomain.MAX_LENGTH` (63) is the column
  and the DNS label limit, `Subdomain.MAX_LABEL_LENGTH` (55) is the rule.
- `webblock.platform.reserved-subdomains` adds `preview`, `webblock`, `billing` and `support` to the existing list.
- The production profile refuses to start with an empty reserved list (`ProfileGuard`, FR-13.9). The default list
  starts (FR-13.10).
- The site domain limit stays 189 (253 - 63 - 1): the longest host is `<55-character label>-preview.<site domain>`,
  which is 63 + 1 + 189 = 253 characters (FR-13.12).
- Labels already stored fit: the V6 backfill `p-` plus 12 characters has 14.

## Reason

- Provisioning serves a preview of each site on `<label>-preview.<site domain>`. A DNS label is at most 63
  characters and `-preview` takes 8, so a client label is at most 55. Without the cap a long label would have a
  preview host that is not a valid name.
- A client label ending with `-preview` could collide with the preview host of another label (`kiln` previews at
  `kiln-preview`, so a site named `kiln-preview` would share the name). Refusing the suffix removes the collision.
- A label starting with `xn--` is punycode, which the lowercase ASCII rule does not mean to allow, and a reader
  would take it for an internationalised name.
- `preview`, `webblock`, `billing` and `support` are names a client could use to pass for the platform.
- With an empty reserved list a client could take `www` or `auth`; production must fail at start, not run open.

## Status

Accepted. Built: `Subdomain`, `application.yml`, `ProfileGuard`.
