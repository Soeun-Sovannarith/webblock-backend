"""Static check of scripts/prototype/run-provisioning.sh for the dead-letter commands (task W6-E2):
``operator-dlq`` runs ``python -m provisioning.hosts.operator dlq ...`` with a cleared environment
and its own file, which holds the broker settings and no database or cluster setting."""

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "prototype" / "run-provisioning.sh"
TEXT = SCRIPT.read_text()


def test_the_dead_letter_commands_run_with_the_broker_settings_only() -> None:
    configure = TEXT[TEXT.index("configure() {") : TEXT.index("start_process() {")]
    block = configure[configure.index("write_env operator_dlq") :]
    # Task PK: the CLI runs on the host in both modes, so its broker URL is the published port's.
    assert '"RABBITMQ_URL=$host_mq_url"' in block
    assert "RABBITMQ_DIAL_TIMEOUT=" in block and "DLQ_CALL_TIMEOUT=" in block
    assert "PG_" not in block and "db[@]" not in block and "KUBECONFIG" not in block
    assert 'operator-dlq) shift; run_operator operator_dlq dlq "$@" ;;' in TEXT
