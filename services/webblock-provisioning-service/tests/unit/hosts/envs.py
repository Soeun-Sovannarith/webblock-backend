"""Complete environments of the worker, build worker, bootstrap and operator processes for the
tests (no tests here). The optional wave 6 keys are left out, so their defaults apply. Every
value is valid; secrets carry ``MARKER`` so that a test can prove no value is ever printed.

Task H2: every environment names PROVISIONING_PROFILE=prototype; ``production`` turns one into the
production profile by setting the profile and removing the stand-in keys of ``STAND_IN_KEYS``.

Decision 0016 section 9 ruling 2: the worker's and the bootstrap's environments connect from the
host (KUBE_CONNECTION=kubeconfig); ``in_cluster`` turns one into the pod's form, and
``production`` does so too, because production refuses the kubeconfig."""

from __future__ import annotations

from pathlib import Path

MARKER = "VALUE-MARKER-71c2"

PROFILE = {"PROVISIONING_PROFILE": "prototype"}

STAND_IN_KEYS = (
    "ENTITLEMENT_MODE",
    "ENTITLEMENT_FILE",
    "HOSTING_ENTITLEMENT_MODE",
    "PAYMENT_GRPC_TARGET",
    "REPOSITORY_MODE",
    "PROTOTYPE_REPOSITORIES_FILE",
    "PROJECT_ACCESS_MODE",
    "PROJECT_ACCESS_FILE",
    "AUTH_DEV_TOKENS_FILE",
)
"""Every key that selects or feeds a prototype stand-in, refused in production (task H2)."""


IN_CLUSTER = {
    "KUBE_CONNECTION": "in-cluster",
    "KUBE_TOKEN_FILE": "/var/run/secrets/webblock/kube/token",
    "KUBE_CA_FILE": "/var/run/secrets/webblock/kube/ca.crt",
}
"""The projected token's keys of a pod (decision 0016 section 9 ruling 2), without the username."""


def in_cluster(env: dict[str, str], kubeconfig_key: str, service_account: str) -> dict[str, str]:
    """``env`` as a pod of ``service_account`` in webblock has it: the kubeconfig key
    replaced by the projected token's keys and the username its review must return."""
    return (
        {k: v for k, v in env.items() if k != kubeconfig_key}
        | IN_CLUSTER
        | {"KUBE_EXPECTED_USERNAME": f"system:serviceaccount:webblock:{service_account}"}
    )


def production(env: dict[str, str]) -> dict[str, str]:
    """``env`` in the production profile: the profile set, every stand-in key removed, and a
    kubeconfig connection turned into the pod's projected token (production refuses the
    kubeconfig)."""
    out = {k: v for k, v in env.items() if k not in STAND_IN_KEYS} | {
        "PROVISIONING_PROFILE": "production"
    }
    for key, account in (
        ("WORKER_KUBECONFIG", "provisioning-worker"),
        ("BOOTSTRAP_KUBECONFIG", "tenant-bootstrap"),
    ):
        if key in out:
            out = in_cluster(out, key, account)
    return out


def database_env(user: str) -> dict[str, str]:
    return {
        **PROFILE,
        "PG_HOST": "127.0.0.1",
        "PG_PORT": "5432",
        "PG_DATABASE": "provisioning",
        "PG_SSLMODE": "disable",
        "DB_MAX_CONNS": "4",
        "DB_CONNECT_TIMEOUT": "5s",
        "DB_TX_TIMEOUT": "10s",
        "DB_STATEMENT_TIMEOUT": "5s",
        "DB_LOCK_TIMEOUT": "2s",
        "PG_USER": user,
        "PG_PASS": MARKER,
    }


def broker_env() -> dict[str, str]:
    return {
        "RABBITMQ_URL": f"amqp://provisioning:{MARKER}@127.0.0.1:5672/",
        "RABBITMQ_HEARTBEAT": "10s",
        "RABBITMQ_DIAL_TIMEOUT": "5s",
        "RABBITMQ_RECONNECT_BACKOFF_BASE": "200ms",
        "RABBITMQ_RECONNECT_BACKOFF_MAX": "5s",
        "RABBITMQ_DECLARE_TOPOLOGY": "true",
        "RABBITMQ_MAX_IN_FLIGHT_PUBLISHES": "64",
        "RABBITMQ_MAX_MESSAGE_BYTES": "65536",
        "RABBITMQ_QUEUE_MAX_LENGTH": "10000",
        "RABBITMQ_CONSUMER_TIMEOUT": "30m",
        "RABBITMQ_DLQ_MAX_LENGTH": "10000",
        "RABBITMQ_DLQ_MESSAGE_TTL": "168h",
    }


def telemetry_env(ops_port: int) -> dict[str, str]:
    """Task H4: the operational listener and no Sentry client (SENTRY_DSN=none)."""
    return {"OPS_LISTEN_ADDR": f"127.0.0.1:{ops_port}", "SENTRY_DSN": "none"}


def worker_env(kubeconfig: Path) -> dict[str, str]:
    return {
        **database_env("worker_login"),
        **broker_env(),
        "PG_HOSTING_INTAKE_USER": "hosting_login",
        "PG_HOSTING_INTAKE_PASS": MARKER,
        # TR-58: with the route and lease values below, 3 go-live tries need above 900 s.
        "COMMAND_DEADLINE": "20m",
        "ENTITLEMENT_MODE": "prototype-allow",
        "HOSTING_ENTITLEMENT_MODE": "prototype-payment-events",
        "DISPATCH_POLL_INTERVAL": "500ms",
        "DISPATCH_BATCH_SIZE": "50",
        "DISPATCH_PUBLISH_CONCURRENCY": "8",
        "DISPATCH_PUBLISH_CONFIRM_TIMEOUT": "5s",
        "DISPATCH_VISIBILITY_TIMEOUT": "30s",
        "DISPATCH_REDELIVERY_INTERVAL": "30s",
        "DISPATCH_BACKOFF_BASE": "500ms",
        "DISPATCH_BACKOFF_MAX": "30s",
        "WORKER_ID": "worker-1",
        "WORKER_CONCURRENCY": "4",
        "WORKER_LEASE_TTL": "30s",
        "WORKER_HEARTBEAT_INTERVAL": "5s",
        "WORKER_ATTEMPT_TIMEOUT": "10m",
        "WORKER_MAX_ATTEMPTS": "10",
        "WORKER_RETRY_BACKOFF_BASE": "1s",
        "WORKER_RETRY_BACKOFF_MAX": "30s",
        "WORKER_STORE_HOLD_BASE": "200ms",
        "WORKER_STORE_HOLD_MAX": "5s",
        "WORKER_STORE_FAILURE_LIMIT": "5",
        "DEPLOY_IMAGE_REGISTRY": "webblock-proto-registry.test:5000",
        "DEPLOY_READY_TIMEOUT": "90s",
        "DEPLOY_POLL_INTERVAL": "1s",
        "DEPLOY_CALL_TIMEOUT": "10s",
        "DEPLOY_DELETE_TIMEOUT": "30s",
        "DEPLOY_FINISH_MARGIN": "30s",
        "DEPLOY_ABANDON_TIMEOUT": "30s",
        "SITE_CPU_MILLICORES": "100",
        "SITE_MEMORY_MIB": "128",
        "SITE_PROGRESS_DEADLINE": "60s",
        "SITE_OFFLINE_SERVICE_PORT": "8080",
        "SITE_DOMAIN": "sites.webblock.test",
        "SITE_GATEWAY_HTTPS_URL": "https://172.18.255.200",
        "SITE_GATEWAY_CA_FILE": str(kubeconfig.parent / "local-ca.crt"),
        "PREVIEW_PROBE_TOKEN": f"preview-token-{MARKER}-0123456789abcdef",
        "SITE_PROBE_PATH": "/readyz",
        "SITE_PROBE_TIMEOUT": "5s",
        "SITE_DELETE_RETRY_BASE": "250ms",
        "ROUTE_SWITCH_DEADLINE": "60s",
        "ROUTE_RESTORE_DEADLINE": "60s",
        "ROUTE_POLL_INTERVAL": "500ms",
        "PAYMENT_CONSUMER_CONCURRENCY": "2",
        "PAYMENT_RETRY_BACKOFF_BASE": "1s",
        "PAYMENT_RETRY_BACKOFF_MAX": "30s",
        "PAYMENT_MAX_RETRIES": "50",
        "KUBE_CONNECTION": "kubeconfig",
        "WORKER_KUBECONFIG": str(kubeconfig),
        "KUBE_REQUEST_TIMEOUT": "10s",
        **telemetry_env(18082),
    }


def worker_file_mode_env(kubeconfig: Path, entitlement_file: Path) -> dict[str, str]:
    """The worker as ``run-provisioning.sh`` configures it since wave 6 (W6-D's reloadable
    entitlement file). Its stand-in is W6-D's; until that merges, only load the configuration from
    it and never build the process."""
    return {
        **worker_env(kubeconfig),
        "ENTITLEMENT_MODE": "prototype-file",
        "ENTITLEMENT_FILE": str(entitlement_file),
    }


def build_worker_env(repositories: Path) -> dict[str, str]:
    env = {
        **database_env("unused"),
        **broker_env(),
        "PG_INTAKE_USER": "intake_login",
        "PG_INTAKE_PASS": MARKER,
        "PG_BUILD_USER": "build_login",
        "PG_BUILD_PASS": MARKER,
        "BUILD_IMAGE_REGISTRY": "webblock-proto-registry.test:5000",
        "BUILD_DURATION": "20m",
        "BUILD_CALL_TIMEOUT": "30s",
        "BUILD_MAX_ATTEMPTS": "3",
        "JENKINS_URL": "http://127.0.0.1:8090",
        "JENKINS_USER": "provisioning",
        "JENKINS_API_TOKEN": MARKER,
        "JENKINS_JOB": "webblock-site-build",
        "JENKINS_REQUEST_TIMEOUT": "10s",
        "JENKINS_OPERATION_TIMEOUT": "25s",
        "JENKINS_QUEUE_POLLS": "10",
        "JENKINS_QUEUE_POLL_INTERVAL": "500ms",
        "JENKINS_RECENT_BUILDS": "50",
        "REPOSITORY_MODE": "prototype-file",
        "PROTOTYPE_REPOSITORIES_FILE": str(repositories),
        "INBOX_CONSUMER_CONCURRENCY": "2",
        "INBOX_STORE_HOLD_BASE": "200ms",
        "INBOX_STORE_HOLD_MAX": "5s",
        "INBOX_STORE_FAILURE_LIMIT": "5",
        "INBOX_POLL_INTERVAL": "1s",
        "INBOX_BATCH_SIZE": "10",
        "INBOX_MAX_PER_PROJECT": "5",
        "INBOX_VISIBILITY_TIMEOUT": "10m",
        "INBOX_CONCURRENCY": "4",
        "INBOX_REGISTRY_TIMEOUT": "5s",
        "INBOX_TX_TIMEOUT": "60s",
        "INBOX_MAX_ATTEMPTS": "10",
        "INBOX_DEADLINE": "1h",
        "INBOX_BACKOFF_BASE": "1s",
        "INBOX_BACKOFF_MAX": "1m",
        "INTAKE_MAX_PENDING_PER_PROJECT": "20",
        "BUILD_POLL_INTERVAL": "2s",
        "BUILD_POLL_LIMIT": "50",
        "BUILD_POLL_CONCURRENCY": "4",
        "BUILD_SWEEP_INTERVAL": "30s",
        "BUILD_SWEEP_LIMIT": "50",
        "BUILD_SWEEP_CONCURRENCY": "4",
        "BUILD_SWEEP_TX_TIMEOUT": "10s",
        "BUILD_DISPATCH_GRACE": "1m",
        "BUILD_HINT_QUEUE_SIZE": "64",
        "BUILD_HINT_LIVENESS": "30s",
        "BUILD_HINT_RECONNECT_BASE": "500ms",
        "BUILD_HINT_RECONNECT_MAX": "10s",
        **telemetry_env(18083),
    }
    del env["PG_USER"], env["PG_PASS"]
    return env


def bootstrap_env(kubeconfig: Path) -> dict[str, str]:
    return {
        **database_env("bootstrap_reader"),
        "KUBE_CONNECTION": "kubeconfig",
        "BOOTSTRAP_KUBECONFIG": str(kubeconfig),
        "KUBE_REQUEST_TIMEOUT": "10s",
        "TENANT_QUOTA_CPU": "2",
        "TENANT_QUOTA_MEMORY": "2Gi",
        "TENANT_QUOTA_PODS": "10",
        "TENANT_CONTAINER_MAX_CPU": "1",
        "TENANT_CONTAINER_MAX_MEMORY": "1Gi",
        "BOOTSTRAP_INTERVAL": "2s",
        "BOOTSTRAP_BATCH_SIZE": "50",
        "BOOTSTRAP_CONCURRENCY": "4",
        "BOOTSTRAP_CALL_TIMEOUT": "30s",
        "CATALOG_HOST": "127.0.0.1",
        "CATALOG_PORT": "55432",
        "CATALOG_DATABASE": "catalog",
        "CATALOG_SSLMODE": "disable",
        "CATALOG_ADMIN_USER": "provisioning_catalog_admin",
        "CATALOG_ADMIN_PASS": MARKER,
        "CATALOG_CONNECT_TIMEOUT": "5s",
        "CATALOG_STATEMENT_TIMEOUT": "5s",
        "CATALOG_LOCK_TIMEOUT": "2s",
        "CATALOG_RUNTIME_HOST": "webblock-catalog-db",
        "CATALOG_RUNTIME_PORT": "5432",
        **telemetry_env(18084),
    }


def operator_env() -> dict[str, str]:
    """The operator CLI: the database and a login of ``provisioning_operator`` (wave 6)."""
    return database_env("prov_operator_login")
