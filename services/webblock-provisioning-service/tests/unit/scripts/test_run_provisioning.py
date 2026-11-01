"""Static checks of scripts/prototype/run-provisioning.sh (task M; decision 0014 s16 and s17).

The script itself needs the prototype stack, so these read its text: the bootstrap process logs in
as a member of ``provisioning_bootstrap`` (not ``provisioning_api``), and every ``start`` writes its
logs under its own timestamped folder behind a ``latest`` link that ``stop`` never removes.

Task H5b adds the replica options; a few of its checks run one of the script's shell functions or
its embedded Python in a clean bash or interpreter, with stubs and temporary files only (no stack,
no container, no cluster).

Task HF-A (H-V finding F-1) adds the bootstrap's supervisor; its tests run the script's process
functions the same way.
"""

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "prototype" / "run-provisioning.sh"
TEXT = SCRIPT.read_text()


def test_the_bootstrap_login_is_a_member_of_the_bootstrap_role_alone() -> None:
    logins = re.findall(r"^\s*ensure_login (\S+) (\S+) (\S+)$", TEXT, re.MULTILINE)
    by_login = {login: group for login, group, _ in logins}
    assert by_login["prov_bootstrap_login"] == "provisioning_bootstrap"
    assert by_login["prov_api_login"] == "provisioning_api"
    # No two logins share a role (the design allows no login in more than one service role).
    assert len(set(by_login.values())) == len(by_login)


def test_the_operator_login_is_a_member_of_the_operator_role_with_its_own_password() -> None:
    """Wave 6 (decision 0014 section 21 ruling 11): the inbox re-drive's login. Its password is
    generated for a new secrets file and added to an existing one, never printed."""
    logins = re.findall(r"^\s*ensure_login (\S+) (\S+) (\S+)$", TEXT, re.MULTILINE)
    by_login = {login: (group, key) for login, group, key in logins}
    assert by_login["prov_operator_login"] == ("provisioning_operator", "OPERATOR_PASSWORD")
    secrets = TEXT[TEXT.index("ensure_secrets() {") : TEXT.index("remove_if_stopped() {")]
    assert '"OPERATOR"' in secrets
    assert "add_secret_if_missing OPERATOR_PASSWORD" in secrets
    assert not re.search(r"\b(echo|printf|log)\b[^\n]*OPERATOR_PASSWORD", TEXT)
    setup = TEXT[TEXT.index("setup_database() {") : TEXT.index("start_rabbitmq() {")]
    # The role comes from bootstrap-roles.sql, which runs before any login is created.
    assert setup.index("bootstrap-roles.sql") < setup.index("ensure_login prov_operator_login")


def test_each_start_has_its_own_log_folder_and_stop_keeps_it() -> None:
    assert 'LOGS="$RUNS/latest"' in TEXT
    assert 'ln -sfn "$stamp" "$LOGS"' in TEXT
    assert "timings.txt" in TEXT
    stop = TEXT[TEXT.index("cmd_stop() {") : TEXT.index("cmd_restart() {")]
    assert "$RUNS" not in stop and "$LOGS" not in stop.replace("logs kept in $LOGS", "")


def test_the_worker_gets_the_task_t_settings_and_the_token_is_generated_never_echoed() -> None:
    """Task T2: the HTTPS Gateway and its CA and the preview probe token reach the worker; the
    custom-domain settings are gone (decision 0014 section 23)."""
    configure = TEXT[TEXT.index("configure() {") : TEXT.index("start_process() {")]
    for key in (
        "SITE_GATEWAY_HTTPS_URL=",
        "SITE_GATEWAY_CA_FILE=",
        "PREVIEW_PROBE_TOKEN=",
    ):
        assert key in configure, key
    for key in (
        "SITE_GATEWAY_URL=",
        "SITE_CERTIFICATE_ISSUER=",
        "DOMAINS_FILE=",
        "DOMAIN_READY_DEADLINE=",
        "DOMAIN_EVENT_",
        "domains-changed",
    ):
        assert key not in TEXT, key
    assert "secrets.token_urlsafe(32)" in TEXT[TEXT.index("ensure_secrets() {") :]
    assert not re.search(r"(echo|printf|log)[^\n]*PREVIEW_TOKEN", TEXT)


def test_the_worker_requires_three_consecutive_good_probes() -> None:
    """QA-08 (task W6-M, passed on by the lead): a promotion needs three consecutive 200s with the
    right release header, so a flapping /readyz is not promoted."""
    configure = TEXT[TEXT.index("configure() {") : TEXT.index("start_process() {")]
    worker = configure[
        configure.index("write_env worker") : configure.index("write_env build_worker")
    ]
    assert re.findall(r"\bSITE_PROBE_SUCCESSES=(\S+)", worker) == ["3"]
    assert "SITE_PROBE_SUCCESSES" not in configure.replace(worker, "")


def test_fr50_the_api_scopes_name_every_action_and_rollback_needs_admin() -> None:
    """FR-50: the development token's scopes cover every action, ``list_releases`` and
    ``list_commands`` included, or the API refuses to start (W6-D). The nine client actions need
    ``provisioning:all``; rollback is ``ADMIN`` only, like the four admin actions (0023 G10), so
    only ``prototype-admin`` may roll back."""
    loops = re.findall(r"^\s*for a in ([a-z_ ]+); do\n\s*(.+)$", TEXT, re.MULTILINE)
    assert [(names.split(), body) for names, body in loops[:2]] == [
        (
            [
                "deploy_release",
                "promote_release",
                "get_release",
                "get_command",
                "get_deployment",
                "get_release_diagnostics",
                "build_release",
                "list_releases",
                "list_commands",
            ],
            'scopes="${scopes:+$scopes,}$a=provisioning:all"',
        ),
        (
            [
                "rollback_release",
                "suspend_website",
                "resume_website",
                "redeploy_release",
                "admin_list_commands",
            ],
            'scopes="$scopes,$a=ADMIN"',
        ),
    ]


def test_the_access_and_entitlement_files_are_created_once_and_used_in_file_mode() -> None:
    """W6-D (passed on by the lead): the reloadable stand-ins. ``write_files`` creates
    ``access.json`` and ``entitlement.json`` once (0600, allow-all content); the API reads both
    files and the worker the entitlement file; ``stop`` removes them."""
    files = TEXT[TEXT.index("write_files() {") : TEXT.index("# q VALUE")]
    assert '[ -f "$PROV/access.json" ] ||' in files
    assert '[ -f "$PROV/entitlement.json" ] ||' in files
    assert files.count("umask 077") >= 3
    assert '{"subjects": [{"subject": "prototype-operator", "projects": "*"}]}' in files
    assert '{"default_entitled": true, "projects": {}}' in files
    configure = TEXT[TEXT.index("configure() {") : TEXT.index("start_process() {")]
    api = configure[configure.index("write_env api") : configure.index("write_env worker")]
    worker = configure[
        configure.index("write_env worker") : configure.index("write_env build_worker")
    ]
    assert "prototype-allow" not in api + worker
    # Task PA: the API's project-access keys come from access_settings, whose default is the
    # access file (its other modes are tested below).
    assert '"${access[@]}"' in api
    assert 'access_settings "$standins/access.json"' in configure
    assert '"ENTITLEMENT_FILE=$standins/entitlement.json"' in _env_block("api")
    assert '"ENTITLEMENT_FILE=$standins/entitlement.json"' in _env_block("worker")
    stop = TEXT[TEXT.index("cmd_stop() {") : TEXT.index("cmd_restart() {")]
    assert 'rm -f "$PROV/access.json" "$PROV/entitlement.json"' in stop


def test_the_tenant_and_reader_tokens_are_generated_and_never_echoed() -> None:
    secrets_block = TEXT[TEXT.index("ensure_secrets() {") : TEXT.index("remove_if_stopped() {")]
    assert "add_secret_if_missing TENANT_TOKEN" in secrets_block
    assert "add_secret_if_missing READER_TOKEN" in secrets_block
    files = TEXT[TEXT.index("write_files() {") : TEXT.index("# q VALUE")]
    assert '"prototype-tenant"' in files and '"provisioning:all"' in files
    assert '"prototype-reader"' in files and '"provisioning:none"' in files
    assert 'get_var "$PROV_ENV" TENANT_TOKEN' in files
    assert 'get_var "$PROV_ENV" READER_TOKEN' in files
    assert not re.search(r"\b(echo|printf|log)\b[^\n]*(TENANT|READER)_TOKEN", TEXT)


def test_the_admin_token_is_generated_and_holds_the_admin_role(tmp_path: Path) -> None:
    """Phase C (decision 0023 G10): the admin actions need the ADMIN role, so the prototype has a
    fourth development token, prototype-admin, with the client scope and ADMIN; it has no user ID
    and no entry in access.json, so it reaches other projects only through the admin paths."""
    secrets_block = TEXT[TEXT.index("ensure_secrets() {") : TEXT.index("remove_if_stopped() {")]
    assert "add_secret_if_missing ADMIN_TOKEN" in secrets_block
    assert 'f.write(f"ADMIN_TOKEN={secrets.token_urlsafe(32)}\\n")' in secrets_block
    assert 'get_var "$PROV_ENV" ADMIN_TOKEN' in _function("write_files")
    assert not re.search(r"\b(echo|printf|log)\b[^\n]*ADMIN_TOKEN", TEXT)
    done, out = _tokens(tmp_path, "", "", "")
    assert done.returncode == 0, done.stderr
    entries = {e["subject"]: e for e in json.loads(out.read_text())["tokens"]}
    assert entries["prototype-admin"] == {
        "token": "a" * 40,
        "subject": "prototype-admin",
        "scopes": ["provisioning:all", "ADMIN"],
    }
    assert all("ADMIN" not in e["scopes"] for s, e in entries.items() if s != "prototype-admin")


def test_the_operator_cli_runs_with_its_own_login_and_a_cleared_environment() -> None:
    """Wave 6 task W6-E: ``operator`` runs the CLI as ``prov_operator_login`` only, with the
    database settings and no broker or cluster setting; ``operator-as-worker`` uses the worker's
    login, from its own host-side file (the proof that the worker's login is refused a re-drive)."""
    configure = TEXT[TEXT.index("configure() {") : TEXT.index("start_process() {")]
    block = configure[configure.index("write_env operator") :]
    block = block[: block.index("write_env bootstrap")]
    assert "PG_USER=prov_operator_login" in block
    assert '"PG_PASS=$(get_var "$PROV_ENV" OPERATOR_PASSWORD)"' in block
    assert "RABBITMQ" not in block and "mq[@]" not in block and "KUBECONFIG" not in block
    runner = TEXT[TEXT.index("run_operator() {") : TEXT.index("cmd_start() {")]
    assert "env -i PATH=/usr/bin:/bin" in runner
    assert "-m provisioning.hosts.operator" in runner
    assert 'operator) shift; run_operator operator "$@" ;;' in TEXT
    assert 'operator-as-worker) shift; run_operator operator_as_worker "$@" ;;' in TEXT


def _env_block(name: str) -> str:
    """The arguments of ``write_env <name>``, up to the next ``write_env`` or the end of
    ``configure``."""
    configure = TEXT[TEXT.index("configure() {") : TEXT.index("start_process() {")]
    start = configure.index(f"write_env {name} ")
    following = configure.find("write_env ", start + 1)
    return configure[start : following if following != -1 else len(configure)]


def test_every_process_runs_with_the_prototype_profile() -> None:
    """Task H2: PROVISIONING_PROFILE is required and has no default; the prototype sets it for
    every process and every operator environment."""
    assert "local profile=PROVISIONING_PROFILE=prototype" in TEXT
    for name in ("api", "worker", "build_worker", "operator", "bootstrap", "operator_dlq"):
        assert _env_block(name).startswith(f'write_env {name} "$profile" '), name


def test_the_api_holds_neither_the_jenkins_token_nor_the_build_login() -> None:
    """Decision 0016 section 5 item 2: only the build worker starts builds."""
    api = _env_block("api")
    for word in ("JENKINS", "PG_BUILD", "BUILD_", "build[@]", "REPOSITORY", "BUILD_PASSWORD"):
        assert word not in api, word
    build_worker = _env_block("build_worker")
    assert '"${build[@]}"' in build_worker
    assert "PG_BUILD_USER=prov_build_login" in build_worker
    for key in (
        "BUILD_HINT_QUEUE_SIZE=",
        "BUILD_HINT_LIVENESS=",
        "BUILD_HINT_RECONNECT_BASE=",
        "BUILD_HINT_RECONNECT_MAX=",
    ):
        assert key in build_worker, key


def test_every_long_lived_process_sends_nothing_to_sentry_and_has_its_ops_listener() -> None:
    """Task H4: SENTRY_DSN=none for the API, the worker, the build worker and the bootstrap
    process; each has its own OPS_LISTEN_ADDR (the values, per mode, are pinned by
    test_cluster_mode.py::test_the_host_mode_is_as_before and the cluster-mode test beside it);
    the operator tools get neither."""
    configure = TEXT[TEXT.index("configure() {") : TEXT.index("start_process() {")]
    assert "local sentry=SENTRY_DSN=none" in configure
    for name in ("api", "worker", "build_worker", "bootstrap"):
        block = _env_block(name)
        assert '"$sentry"' in block, name
        assert "SENTRY_ENVIRONMENT" not in block and "SENTRY_TRACES" not in block, name
        assert '"OPS_LISTEN_ADDR=$' in block, name
    for name in ("operator", "operator_dlq"):
        block = _env_block(name)
        assert "SENTRY_DSN" not in block and "$sentry" not in block and "OPS_LISTEN" not in block


# --- Task H5b: replicas (PROV_REPLICAS=2; .claude/docs/runbooks/replicas.md) ---------------------

RESET = SCRIPT.parent / "reset-sites.sh"
MAKEFILE = SCRIPT.parents[2] / "mk" / "prototype.mk"


def _function(name: str) -> str:
    """The text of the shell function ``name`` (from ``name() {`` to its closing brace)."""
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", TEXT, re.MULTILINE | re.DOTALL)
    assert match is not None, name
    return match.group(0)


def _array(name: str) -> str:
    match = re.search(rf"^{name}=\([^)]*\)$", TEXT, re.MULTILINE)
    assert match is not None, name
    return match.group(0)


def _bash(program: str) -> list[str]:
    """Runs ``program`` in a clean bash (no profile, no environment but PATH); its output words."""
    done = subprocess.run(  # noqa: S603  # fixed argument list; the program is the script's own text
        ["/usr/bin/bash", "--noprofile", "--norc", "-c", program],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


def _names(tmp_path: Path, recorded: str | None) -> list[str]:
    replicas = tmp_path / "replicas"
    if recorded is not None:
        replicas.write_text(recorded + "\n")
    program = "\n".join(
        [
            'die() { echo "$*" >&2; exit 1; }',
            _array("PROCESSES"),
            _array("REPLICATED"),
            f"REPLICAS_FILE='{replicas}'",
            _function("replica_count"),
            _function("process_names"),
            "process_names",
        ]
    )
    return _bash(program)


def test_the_default_run_is_one_of_each_and_two_replicas_add_three_second_processes(
    tmp_path: Path,
) -> None:
    """Without a recorded count (the default, and every existing suite) the run is the four
    processes; PROV_REPLICAS=2 adds api-2, worker-2 and build_worker-2, never a second bootstrap."""
    assert _names(tmp_path, None) == ["api", "worker", "build_worker", "bootstrap"]
    assert _names(tmp_path, "1") == ["api", "worker", "build_worker", "bootstrap"]
    assert _names(tmp_path, "2") == [
        "api",
        "worker",
        "build_worker",
        "bootstrap",
        "api-2",
        "worker-2",
        "build_worker-2",
    ]
    start = _function("cmd_start")
    assert 'local replicas="${PROV_REPLICAS:-1}"' in start
    assert 'printf \'%s\\n\' "$replicas" > "$REPLICAS_FILE"' in start
    make = MAKEFILE.read_text()
    run = make[make.index("proto-run:\n") :].split("\n\n", 1)[0]
    assert "PROV_REPLICAS" not in run
    assert "PROV_REPLICAS=2 bash scripts/prototype/run-provisioning.sh start" in make


def test_stop_processes_stops_every_replica_of_every_process(tmp_path: Path) -> None:
    """Lead's ruling (H5b): ``stop-processes`` stops every ``<process>-N`` the script can start,
    and stop, restart and reset-sites go through it, so no replica outlives them."""
    started = set(_names(tmp_path, "2"))
    program = "\n".join(
        [
            _array("ALL_NAMES"),
            'stop_process() { echo "$1"; }',
            _function("stop_processes"),
            "stop_processes",
        ]
    )
    stopped = _bash(program)
    # Task H5c: also the standby bootstrap-2, which only start-process starts.
    assert set(stopped) == started | {"bootstrap-2"}
    assert len(stopped) == len(set(stopped))
    # As before: the bootstraps first (the standby before the holder), the API last.
    assert stopped[:2] == ["bootstrap-2", "bootstrap"] and stopped[-1] == "api"
    for name in ("cmd_stop", "cmd_restart"):
        body = _function(name)
        assert "stop_processes" in body, name
        assert "for name in bootstrap build_worker worker api" not in body, name
    assert "stop-processes) [ $# -eq 1 ] || usage; cmd_stop_processes ;;" in TEXT
    reset = RESET.read_text()
    assert 'bash "$RUN" stop-processes' in reset
    assert 'stop-process "$name"' not in reset


def test_a_second_bootstrap_is_a_standby_that_only_start_process_starts() -> None:
    """Task H5c: the bootstrap holds a singleton lock, so a second one is safe and stands by.
    ``start`` never runs it (it is not replicated); on a replica run its env file exists, so
    ``start-process bootstrap-2`` can start it, ``stop-processes`` stops it, and ``start`` with the
    default count refuses while it still runs."""
    assert "bootstrap" not in _array("REPLICATED")
    valid = _function("valid_process")
    assert "| bootstrap-2) ;;" in valid
    assert "one bootstrap process only" not in TEXT
    assert "bootstrap-2" in _array("ALL_NAMES")
    replicas = _function("configure_replicas")
    assert (
        'derive_env bootstrap bootstrap-2 "OPS_LISTEN_ADDR=127.0.0.1:$PROV_BOOTSTRAP2_OPS_PORT"'
        in (replicas)
    )
    assert 'for name in "${REPLICATED[@]}" bootstrap; do' in _function("cmd_start")


def test_each_second_replica_has_its_own_listeners_and_worker_id() -> None:
    """Each replica differs from the first only in its listeners and, for the worker, its
    WORKER_ID; every port is distinct and clear of 18080-18084 (H4's listeners)."""
    ports: dict[str, int] = {}
    for variable, value in re.findall(
        r'^(PROV_[A-Z0-9_]*PORT)="\$\{\1:-(\d+)\}"$', TEXT, re.MULTILINE
    ):
        ports[variable] = int(value)
    second = {k: v for k, v in ports.items() if "2" in k}
    assert set(second) == {
        "PROV_API2_PORT",
        "PROV_OPS2_PORT",
        "PROV_WORKER2_OPS_PORT",
        "PROV_BUILD_WORKER2_OPS_PORT",
        "PROV_BOOTSTRAP2_OPS_PORT",
    }
    assert len(set(ports.values())) == len(ports)
    assert all(not 18080 <= port <= 18084 for port in second.values())
    replicas = _function("configure_replicas")
    derived = re.findall(r"derive_env (\S+) (\S+)", replicas)
    assert dict((target, source) for source, target in derived) == {
        "api-2": "api",
        "worker-2": "worker",
        "build_worker-2": "build_worker",
        "bootstrap-2": "bootstrap",
    }
    assert '"HTTP_LISTEN_ADDR=127.0.0.1:$PROV_API2_PORT"' in replicas
    assert '"OPS_LISTEN_ADDR=127.0.0.1:$PROV_OPS2_PORT"' in replicas
    assert '"MCP_ALLOWED_HOSTS=127.0.0.1:$PROV_API2_PORT,localhost:$PROV_API2_PORT"' in replicas
    assert "WORKER_ID=prototype-worker-2" in replicas
    assert "worker_id=(WORKER_ID=prototype-worker-1)" in TEXT
    assert '"OPS_LISTEN_ADDR=127.0.0.1:$PROV_WORKER2_OPS_PORT"' in replicas
    assert '"OPS_LISTEN_ADDR=127.0.0.1:$PROV_BUILD_WORKER2_OPS_PORT"' in replicas
    # Only the listener and ID keys are replaced: no secret, login or other setting differs.
    replaced = set(re.findall(r'"?([A-Z_]+)=', replicas))
    assert replaced == {"HTTP_LISTEN_ADDR", "OPS_LISTEN_ADDR", "MCP_ALLOWED_HOSTS", "WORKER_ID"}
    start = _function("start_process")
    assert '"${name%-2}"' in start  # a second replica runs its first replica's module
    assert '"$LOGS/$name.log"' in start and '"$PIDS/$name.pid"' in start
    ready = _function("wait_ready")
    assert 'wait_api_ready api-2 "$PROV_OPS2_PORT"' in ready
    assert "others+=(worker-2 build_worker-2)" in ready


def _derive_program() -> str:
    body = _function("derive_env")
    return body[body.index("<<'PY'\n") + len("<<'PY'\n") : body.index("\nPY\n")]


def _derive(program: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # fixed argument list
        [sys.executable, "-I", str(program), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_derive_env_replaces_only_the_named_keys_and_refuses_a_missing_one(
    tmp_path: Path,
) -> None:
    source = tmp_path / "api.env"
    source.write_text("PG_PASS='s3cret'\nHTTP_LISTEN_ADDR='127.0.0.1:18080'\nWORKER_ID='w1'\n")
    target = tmp_path / "api-2.env"
    program = tmp_path / "derive.py"
    program.write_text(_derive_program())
    done = _derive(
        program, str(source), str(target), "HTTP_LISTEN_ADDR='127.0.0.1:18090'", "WORKER_ID='w2'"
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""  # nothing printed, values included
    assert target.read_text() == (
        "PG_PASS='s3cret'\nHTTP_LISTEN_ADDR='127.0.0.1:18090'\nWORKER_ID='w2'\n"
    )
    missing = _derive(program, str(source), str(tmp_path / "x.env"), "NOPE='1'")
    assert missing.returncode != 0
    assert "NOPE" in missing.stderr and "s3cret" not in missing.stderr
    assert not (tmp_path / "x.env").exists()
    assert "umask 077" in _function("derive_env")


# --- Task HF-A (H-V finding F-1): the run script supervises the bootstrap ---------------------
#
# These run the script's own start_process, stop_process and kill_process functions in a clean
# bash, with a stub "python" (a bash script) in place of the bootstrap module, temporary pid, log
# and env folders, and no stack, container or cluster.

STUB = """#!/usr/bin/bash
# A stand-in for `python -m provisioning.hosts.bootstrap`: it records each start; on its first
# start it exits 1, on later ones it runs until SIGTERM and then exits 3 (a non-zero exit that the
# supervisor must not answer with a restart once it was asked to stop).
dir="$(dirname "$0")"
echo "$$" >> "$dir/starts"
if [ "$(wc -l < "$dir/starts")" -eq 1 ]; then exit 1; fi
trap 'exit 3' TERM
while :; do sleep 0.05; done
"""


def _line(name: str) -> str:
    """A one-line shell function of the script."""
    match = re.search(rf"^{re.escape(name)}\(\) \{{.*\}}$", TEXT, re.MULTILINE)
    assert match is not None, name
    return match.group(0)


def _harness(tmp_path: Path) -> str:
    """The script's process functions, with every path in ``tmp_path``."""
    for folder in ("pids", "logs", "env", "src", "home"):
        (tmp_path / folder).mkdir()
    (tmp_path / "env" / "bootstrap.env").write_text("")
    stub = tmp_path / "python"
    stub.write_text(STUB)
    stub.chmod(0o755)
    return "\n".join(
        [
            'log() { printf "[test] %s\\n" "$*" >&2; }',
            'die() { printf "ERROR: %s\\n" "$*" >&2; exit 1; }',
            f"PIDS='{tmp_path}/pids'",
            f"LOGS='{tmp_path}/logs'",
            f"ENV_DIR='{tmp_path}/env'",
            f"PY='{stub}'",
            f"kind_root='{tmp_path}'",
            f"TOOLHOME='{tmp_path}/home'",
            "PROV_RESTART_BACKOFF_MS=200",
            _array("SUPERVISED"),
            _function("valid_process"),
            _line("pid_of"),
            _line("running"),
            _function("supervised"),
            _function("supervise"),
            _function("start_process"),
            _function("stop_process"),
            _function("kill_process"),
        ]
    )


def _run(harness: str, body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # fixed argument list; the program is the script's own text
        ["/usr/bin/bash", "--noprofile", "--norc", "-c", harness + "\n" + body],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _starts(tmp_path: Path) -> list[int]:
    path = tmp_path / "starts"
    return [int(p) for p in path.read_text().split()] if path.exists() else []


def _wait(what: str, done: Callable[[], bool], within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while not done():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.02)


def _restarts(tmp_path: Path) -> list[dict[str, object]]:
    lines = (tmp_path / "logs" / "bootstrap.log").read_text().splitlines()
    return [json.loads(line) for line in lines if '"supervisor"' in line]


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[str]:
    """The process functions over ``tmp_path``; afterwards every stub and supervisor a test
    started is killed, whether or not the script did it (a failing or planted run)."""
    text = _harness(tmp_path)
    supervisors: list[int] = []
    try:
        yield text
    finally:
        pid_file = tmp_path / "pids" / "bootstrap.pid"
        if pid_file.exists():
            supervisors.append(int(pid_file.read_text()))
        for group in supervisors:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(group, signal.SIGKILL)
        for pid in _starts(tmp_path):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)


def test_a_bootstrap_that_exits_non_zero_is_restarted_into_the_same_log_and_pid_file(
    tmp_path: Path, harness: str
) -> None:
    done = _run(harness, "start_process bootstrap")
    assert done.returncode == 0, done.stderr
    _wait("the second start", lambda: len(_starts(tmp_path)) == 2)
    first, second = _starts(tmp_path)
    assert not _alive(first)
    assert _alive(second)
    supervisor = int((tmp_path / "pids" / "bootstrap.pid").read_text())
    assert _alive(supervisor)
    (restart,) = _restarts(tmp_path)
    assert restart["process"] == "bootstrap"
    assert restart["exit_status"] == 1
    assert restart["level"] == "warning"
    assert "restart" in str(restart["msg"])
    time.sleep(0.5)
    assert len(_starts(tmp_path)) == 2  # one restart for one exit, none while it runs


def test_stop_process_stops_a_supervised_bootstrap_for_good(tmp_path: Path, harness: str) -> None:
    assert _run(harness, "start_process bootstrap").returncode == 0
    _wait("the restarted bootstrap", lambda: len(_starts(tmp_path)) == 2)
    supervisor = int((tmp_path / "pids" / "bootstrap.pid").read_text())
    child = _starts(tmp_path)[1]

    done = _run(harness, "stop_process bootstrap")
    assert done.returncode == 0, done.stderr
    assert not _alive(supervisor)
    assert not _alive(child)
    assert not (tmp_path / "pids" / "bootstrap.pid").exists()
    time.sleep(1.0)  # five back-offs: the child exited 3 on SIGTERM and was not restarted
    assert len(_starts(tmp_path)) == 2
    assert len(_restarts(tmp_path)) == 1  # the first exit's restart only


def test_kill_process_kills_the_supervisor_and_the_bootstrap_at_once(
    tmp_path: Path, harness: str
) -> None:
    assert _run(harness, "start_process bootstrap").returncode == 0
    _wait("the restarted bootstrap", lambda: len(_starts(tmp_path)) == 2)
    supervisor = int((tmp_path / "pids" / "bootstrap.pid").read_text())
    child = _starts(tmp_path)[1]
    assert _run(harness, "kill_process bootstrap").returncode == 0
    _wait("both gone", lambda: not _alive(supervisor) and not _alive(child), 5)
    time.sleep(1.0)
    assert len(_starts(tmp_path)) == 2
    assert not (tmp_path / "pids" / "bootstrap.pid").exists()


def test_only_the_bootstraps_are_supervised() -> None:
    """The bootstrap alone. Every other process's work is durable in SQL and its absence is
    visible at once (the API is waited on by the caller; a stopped worker or build worker leaves
    accepted rows that the next one claims), and the suites start and stop them on purpose. A
    stopped bootstrap leaves every new tenant not ready, which nothing else repairs (H-V F-1)."""
    assert _array("SUPERVISED") == "SUPERVISED=(bootstrap bootstrap-2)"
    start = _function("start_process")
    assert "supervised" in start
    assert "setsid" in start


# --- Task PA (decision 0017 section 2): project access through the Project service -------------


def _access(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Runs the script's access_settings in a clean bash with ``env``; prints the array."""
    program = (
        'set -euo pipefail\ndie() { echo "die: $*" >&2; exit 1; }\nPROV=/prov\n'
        + _function("refuse_stale_token_file")
        + _function("access_settings")
        + 'f() { local access; access_settings; printf "%s\\n" "${access[@]}"; }\nf\n'
    )
    return subprocess.run(  # noqa: S603  # fixed argument list; the program is the script's own text
        ["/usr/bin/bash", "--noprofile", "--norc", "-c", program],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", **env},
        check=False,
    )


def test_project_access_defaults_to_the_access_file_and_passes_the_callers_mode() -> None:
    assert _access({}).stdout.splitlines() == [
        "PROJECT_ACCESS_MODE=prototype-file",
        "PROJECT_ACCESS_FILE=/prov/access.json",
    ]
    allow = _access({"PROJECT_ACCESS_MODE": "prototype-allow"})
    assert allow.stdout.splitlines() == ["PROJECT_ACCESS_MODE=prototype-allow"]
    service = {
        "PROJECT_ACCESS_MODE": "project-service",
        "PROJECT_SERVICE_URL": "http://127.0.0.1:8085",
    }
    done = _access(service)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [
        "PROJECT_ACCESS_MODE=project-service",
        "PROJECT_SERVICE_URL=http://127.0.0.1:8085",
    ]


@pytest.mark.parametrize(
    "mode", [None, "prototype-file", "prototype-allow", "project-service"], ids=str
)
def test_a_stale_service_token_file_stops_the_start_naming_the_variable_not_the_path(
    mode: str | None,
) -> None:
    """Finding N-1 of the PR-3 review: the token is gone (authentication design A3), and the API
    refuses the key; the script stops as loudly instead of dropping it."""
    env = {"PROJECT_SERVICE_TOKEN_FILE": "/prov/stale-MARKER-token"}
    if mode is not None:
        env["PROJECT_ACCESS_MODE"] = mode
    if mode == "project-service":
        env["PROJECT_SERVICE_URL"] = "http://127.0.0.1:8085"
    done = _access(env)
    assert done.returncode != 0
    assert done.stdout == ""
    assert "PROJECT_SERVICE_TOKEN_FILE" in done.stderr
    assert "unset it" in done.stderr
    assert "MARKER" not in done.stderr
    # start checks it first, before it creates any container, file or process.
    start = _function("cmd_start").splitlines()
    assert start[2].strip() == "refuse_stale_token_file"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"PROJECT_ACCESS_MODE": "project-service"}, "needs PROJECT_SERVICE_URL"),
        ({"PROJECT_ACCESS_MODE": "allow-all"}, "PROJECT_ACCESS_MODE must be"),
    ],
)
def test_project_access_refuses_an_incomplete_or_unknown_setting(
    env: dict[str, str], message: str
) -> None:
    done = _access(env)
    assert done.returncode != 0
    assert message in done.stderr


def _tokens_program() -> str:
    body = _function("write_files")
    start = body.index("<<'PY'\n") + len("<<'PY'\n")
    return body[start : body.index("\nPY\n", start)]


USER = "0199e7a0-0000-7000-8000-0000000000aa"


def _tokens(tmp_path: Path, *users: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    program = tmp_path / "tokens.py"
    program.write_text(_tokens_program())
    out = tmp_path / "tokens.json"
    done = subprocess.run(  # noqa: S603  # fixed argument list; the program is the script's own text
        [
            sys.executable,
            "-I",
            str(program),
            str(out),
            "o" * 40,
            "t" * 40,
            "r" * 40,
            "a" * 40,
            *users,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return done, out


def test_the_development_tokens_carry_the_callers_user_ids(tmp_path: Path) -> None:
    done, out = _tokens(tmp_path, "", "", "")
    assert done.returncode == 0, done.stderr
    assert all("user_id" not in e for e in json.loads(out.read_text())["tokens"])
    done, out = _tokens(tmp_path, "", USER, "")
    assert done.returncode == 0, done.stderr
    entries = {e["subject"]: e.get("user_id") for e in json.loads(out.read_text())["tokens"]}
    assert entries == {
        "prototype-operator": None,
        "prototype-tenant": USER,
        "prototype-reader": None,
        "prototype-admin": None,
    }
    for name in ("PROTO_OPERATOR_USER_ID", "PROTO_TENANT_USER_ID", "PROTO_READER_USER_ID"):
        assert f'"${{{name}:-}}"' in _function("write_files")


@pytest.mark.parametrize(
    "bad", [USER.upper(), USER.replace("-", ""), "00000000-0000-0000-0000-000000000000", "x"]
)
def test_a_user_id_that_is_not_a_canonical_uuid_stops_the_start(tmp_path: Path, bad: str) -> None:
    done, out = _tokens(tmp_path, "", bad, "")
    assert done.returncode != 0
    assert "PROTO_TENANT_USER_ID must be" in done.stderr
    assert bad not in done.stderr
    assert not out.exists()


def test_proto_run_passes_the_callers_access_settings_to_the_script_as_they_are(
    tmp_path: Path,
) -> None:
    """Finding L-2 of P-J1: the recipes add nothing; make hands command-line and environment
    variables to the script unchanged (a quote included), and an unset one stays unset."""
    mk = (SCRIPT.parents[2] / "mk" / "prototype.mk").read_text()
    for target, line in (
        ("proto-run", "\tbash scripts/prototype/run-provisioning.sh start\n"),
        (
            "proto-run-replicas",
            "\tPROV_REPLICAS=2 bash scripts/prototype/run-provisioning.sh start\n",
        ),
    ):
        assert f"{target}:\n{line}" in mk, target
    assert "PROJECT_SERVICE_TOKEN_FILE" not in mk
    for name in ("PROJECT_ACCESS_MODE", "PROJECT_SERVICE_URL"):
        assert not re.search(rf"^\s*(export|unexport)\b.*\b{name}\b", mk, re.MULTILINE), name
    # The same recipe shape against a stub that prints what the script would see.
    stub = tmp_path / "run.sh"
    stub.write_text(
        'printf "%s|%s|%s\\n" "${PROJECT_ACCESS_MODE-unset}" '
        '"${PROJECT_SERVICE_URL-unset}" "${PROTO_TENANT_USER_ID-unset}"\n'
    )
    (tmp_path / "Makefile").write_text(f"proto-run:\n\t@bash {stub} start\n")

    def run(args: list[str], env: dict[str, str]) -> str:
        done = subprocess.run(  # noqa: S603  # fixed argument list: make, a folder and variables
            ["/usr/bin/make", "-s", "-C", str(tmp_path), "proto-run", *args],
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", **env},
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    assert run([], {}) == "unset|unset|unset"
    assert (
        run(["PROJECT_ACCESS_MODE=project-service", "PROJECT_SERVICE_URL=http://x/it's"], {})
        == "project-service|http://x/it's|unset"
    )
    assert run([], {"PROTO_TENANT_USER_ID": "0199e7a0-0000-7000-8000-0000000000aa"}) == (
        "unset|unset|0199e7a0-0000-7000-8000-0000000000aa"
    )
