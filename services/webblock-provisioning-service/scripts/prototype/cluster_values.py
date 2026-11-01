"""cluster_values.py: the chart's values and Secrets from the run script's env files (task PK;
decision 0016 sections 8 and 9; .claude/docs/runbooks/prototype-in-cluster.md). Standard library
only; run as ``python3 -I``. Never reads .env.

    cluster_values.py values ENV_DIR    prints the values JSON: for api, worker, buildWorker and
                                        bootstrap, the non-secret keys (env) and the names of the
                                        secret keys (secretEnv)
    cluster_values.py secrets ENV_DIR   prints a v1 List of the Secrets
                                        provisioning-<component>-secret (the migration Job's
                                        included), for kubectl apply over a pipe; refused when
                                        standard output is a terminal

ENV_DIR holds the files configure() writes in cluster mode (api.env, worker.env, build_worker.env,
bootstrap.env, migrate.env), one KEY='value' per line. A key is secret when it is in SECRET_KEYS;
tests/unit/scripts/test_cluster_mode.py checks that these are exactly the keys the processes read as
secrets, plus the Job's GOOSE_DBSTRING, and tests/chart/test_cluster_values.py renders the values
through the chart and its values.schema.json. Exit 2 on a usage error, 1 when a file is missing or
holds a line that is not KEY='value'. No message ever contains a value.
"""

import base64
import json
import re
import sys
from pathlib import Path
from typing import Any

SECRET_KEYS = frozenset(
    {
        "PG_PASS",
        "PG_HOSTING_INTAKE_PASS",
        "PG_INTAKE_PASS",
        "PG_BUILD_PASS",
        "CATALOG_ADMIN_PASS",
        "RABBITMQ_URL",
        "JENKINS_API_TOKEN",
        "PREVIEW_PROBE_TOKEN",
        "GOOSE_DBSTRING",
    }
)
# The run script's env file, the chart's values key (None: the Job, whose other settings the chart
# fixes) and the component in object names.
PROCESSES = (
    ("api", "api", "api"),
    ("worker", "worker", "worker"),
    ("build_worker", "buildWorker", "build-worker"),
    ("bootstrap", "bootstrap", "bootstrap"),
    ("migrate", None, "migrate"),
)
LINE = re.compile(r"([A-Z][A-Z0-9_]*)='([^']*)'")


class Refused(Exception):
    pass


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise Refused(f"{path.name} is missing")
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        match = LINE.fullmatch(line)
        if match is None:
            raise Refused(f"{path.name} line {number} is not KEY='value'")
        if match.group(1) in values:
            raise Refused(f"{path.name} names {match.group(1)} twice")
        values[match.group(1)] = match.group(2)
    return values


def chart_values(env_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, key, _ in PROCESSES:
        if key is None:
            continue
        env = read_env(env_dir / f"{name}.env")
        out[key] = {
            "env": {k: v for k, v in sorted(env.items()) if k not in SECRET_KEYS},
            "secretEnv": sorted(k for k in env if k in SECRET_KEYS),
        }
    return out


def secrets(env_dir: Path) -> dict[str, Any]:
    items = []
    for name, _, component in PROCESSES:
        env = read_env(env_dir / f"{name}.env")
        data = {
            k: base64.b64encode(v.encode()).decode()
            for k, v in sorted(env.items())
            if k in SECRET_KEYS
        }
        items.append(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "type": "Opaque",
                "metadata": {
                    "name": f"provisioning-{component}-secret",
                    "labels": {
                        "app.kubernetes.io/part-of": "webblock-provisioning",
                        "app.kubernetes.io/component": component,
                    },
                },
                "data": data,
            }
        )
    return {"apiVersion": "v1", "kind": "List", "items": items}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("values", "secrets"):
        print("usage: cluster_values.py values|secrets ENV_DIR", file=sys.stderr)
        return 2
    if argv[1] == "secrets" and sys.stdout.isatty():
        print(
            "cluster_values: refusing to print Secrets to a terminal; pipe them to kubectl apply",
            file=sys.stderr,
        )
        return 2
    try:
        document = chart_values(Path(argv[2])) if argv[1] == "values" else secrets(Path(argv[2]))
    except Refused as error:
        print(f"cluster_values: {error}", file=sys.stderr)
        return 1
    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
