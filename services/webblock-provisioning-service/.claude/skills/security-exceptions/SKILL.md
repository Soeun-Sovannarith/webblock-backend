---
name: security-exceptions
description: Use when make py-vuln, make image-scan or make py-age fails on a finding or a young pin, when an
  exception or waiver is near its expiry date, or when asked to renew or remove one.
---

# Exceptions of the supply-chain gates

Three files, each with its own header that defines the fields (read the header first):

| Gate | File in `scripts/py/` | Script | An entry names |
| --- | --- | --- | --- |
| `make py-vuln` (osv-scanner, `uv.lock`) | `vuln-exceptions.toml` | `vuln.py` | package, version, vulnerability |
| `make image-scan` (Grype, High+) | `image-scan-exceptions.toml` | `image_scan.py` | image, vulnerability, package |
| `make py-age` (14-day rule) | `age-waivers.toml` | `age.py` | kind, name, version |

Runbook: `.claude/docs/runbooks/python-p0.md` (sections py-vuln, py-age, image-scan). `mk/image-scan.mk` shows how to
scan the service image by reference after a cluster run.

Owner rules: only the lead adds an entry, and only after the owner signs it. Never sign for the owner. Never
lower a gate instead. An exception lasts at most 30 days (`expires` is an unquoted date, valid through that day).

## 1. Get the exact finding

Run the failing gate and copy the exact advisory id, package, locked version and image name. Do not guess from
an advisory page.

## 2. Prefer a fix

Check whether a fixed version exists that is at least 14 days old today.

- Python package: raise the pin and run `uv lock` with the environment of `mk/python.mk` (`py_prelude`); the
  `exclude-newer` date in `pyproject.toml` may need to move. Never edit `uv.lock` by hand.
- Tool binary: `scripts/py/versions.sh` or `deploy/kind/versions.sh` (checksums too).
- Base image: `deploy/image/versions.sh`; follow "Moving the base image" in
  `.claude/docs/runbooks/prototype-in-cluster.md` (`BASE_IMAGE` and `BASE_IMAGE_PUBLISHED` in one change).

Then rerun the gate plus the fast tier of the `provisioning-gates` skill. If the fix works, stop here.

## 3. Otherwise draft the entry

Every field, as the file header lists them:

- vuln and image-scan: `reason`, `reviewer`, `expires`, `follow_up`.
- age waiver: `reason`, `approved_by`, `expires`.

The `reason` is a real exposure analysis for this service: which code reads the package, whether that path is on
by default, what an attacker would need, and the impact if abused. Look at the existing entries for the level of
detail. `follow_up` says what removes the need and who does it (for example "re-check when the base image moves;
delete the entry if the new base clears it").

## 4. Ask the owner to sign

Give the owner the drafted entries in one list (file, ids, expiry, one-line reason). Add them to the file only
after the owner signs; the `reviewer` (or `approved_by`) field records who signed and the date. An entry the
owner has not signed yet says so in its `reviewer` text.

## 5. Clean up

- An expired entry fails the gate even if it still matches: renew it (owner signs again) or remove it.
- An entry that matches nothing is printed for removal: remove it.
- A waiver whose pin has aged past 14 days is printed for removal: remove it.

Rerun the gate and report the command and result.

## Known expiry dates (check again; they change)

- The two certifi entries in `vuln-exceptions.toml` expire 2026-11-03 (the fixed certifi
  becomes 14 days old that day, so upgrade then); the 55 image entries for `provisioning` expire 2026-11-17.
- `age-waivers.toml` holds no waiver.

Find them with `grep -n '^expires' scripts/py/*.toml`.
