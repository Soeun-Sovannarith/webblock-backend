"""Build settings shared by every process that starts or polls builds (decision 0014 section 3,
"Build"): the build use cases' limits and the Jenkins adapter's connection. The API process reads
them for build_release; the build worker (task G) reads the same sections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from provisioning.core.domain import MAX_BUILD_ATTEMPTS
from provisioning.infrastructure.config.database import MIN_TIMEOUT
from provisioning.infrastructure.config.loader import Loader, has_control, plain_text
from provisioning.infrastructure.config.net import http_url
from provisioning.infrastructure.config.secret import Secret

# An OCI registry location without a scheme or a trailing slash: host[:port][/path...].
_REGISTRY: Final = re.compile(
    r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?(?:/[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?)*",
    re.ASCII,
)
_JOB_SEGMENT: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", re.ASCII)


@dataclass(frozen=True, slots=True)
class BuildConfig:
    """``image_registry`` is ``<registry>`` of ``<registry>/webblock/sites/<projectId>`` (decision
    0014 section 2.6); ``duration`` is a build step's deadline; ``call_timeout`` bounds each call to
    the build system and the repository locator; ``max_attempts`` is BUILD_MAX_ATTEMPTS."""

    image_registry: str
    duration: timedelta
    call_timeout: timedelta
    max_attempts: int


@dataclass(frozen=True, slots=True)
class JenkinsConfig:
    """The Jenkins adapter's settings (``JenkinsBuildSystem``): base URL, user, API token, the job
    path, the per-request and per-operation timeouts, the queue polls and their interval, and how
    many recent builds ``find`` searches."""

    url: str
    user: str
    api_token: Secret
    job: str
    request_timeout: timedelta
    operation_timeout: timedelta
    queue_polls: int
    queue_poll_interval: timedelta
    recent_builds: int


def read_build(loader: Loader) -> BuildConfig:
    """BUILD_IMAGE_REGISTRY, BUILD_DURATION, BUILD_CALL_TIMEOUT and BUILD_MAX_ATTEMPTS."""
    return BuildConfig(
        image_registry=loader.text("BUILD_IMAGE_REGISTRY", registry_location),
        duration=loader.duration("BUILD_DURATION", timedelta(seconds=1), timedelta(hours=24)),
        call_timeout=loader.duration("BUILD_CALL_TIMEOUT", MIN_TIMEOUT, timedelta(minutes=10)),
        max_attempts=loader.integer("BUILD_MAX_ATTEMPTS", 1, MAX_BUILD_ATTEMPTS),
    )


def read_jenkins(loader: Loader) -> JenkinsConfig:
    """The JENKINS_* keys, with the request timeout at most the operation timeout."""
    config = JenkinsConfig(
        url=loader.text("JENKINS_URL", _jenkins_url),
        user=loader.text("JENKINS_USER", plain_text(256)),
        api_token=loader.secret("JENKINS_API_TOKEN", _token),
        job=loader.text("JENKINS_JOB", _job),
        request_timeout=loader.duration(
            "JENKINS_REQUEST_TIMEOUT", MIN_TIMEOUT, timedelta(minutes=5)
        ),
        operation_timeout=loader.duration(
            "JENKINS_OPERATION_TIMEOUT", MIN_TIMEOUT, timedelta(minutes=10)
        ),
        queue_polls=loader.integer("JENKINS_QUEUE_POLLS", 1, 100),
        queue_poll_interval=loader.duration(
            "JENKINS_QUEUE_POLL_INTERVAL", timedelta(0), timedelta(minutes=1)
        ),
        recent_builds=loader.integer("JENKINS_RECENT_BUILDS", 1, 1000),
    )
    if (
        loader.ok("JENKINS_REQUEST_TIMEOUT", "JENKINS_OPERATION_TIMEOUT")
        and config.request_timeout > config.operation_timeout
    ):
        loader.fail("JENKINS_REQUEST_TIMEOUT", "must not exceed JENKINS_OPERATION_TIMEOUT")
    return config


def registry_location(value: str) -> str | None:
    """An OCI registry location host[:port][/path] in lowercase, without a scheme or a trailing
    slash (the image repository is <registry>/webblock/sites/<projectId>)."""
    if len(value) > 255 or _REGISTRY.fullmatch(value) is None:
        return (
            "must be a registry location host[:port][/path] in lowercase, without a scheme or a "
            "trailing slash"
        )
    return None


def _jenkins_url(value: str) -> str | None:
    if not http_url(value, schemes=("http", "https")):
        return "must be an http or https URL with a host, without credentials, query or fragment"
    return None


def _token(value: str) -> str | None:
    if len(value) > 1024 or has_control(value):
        return "must be at most 1024 bytes without control characters"
    return None


def _job(value: str) -> str | None:
    if not all(_JOB_SEGMENT.fullmatch(s) for s in value.split("/")):
        return "must be one or more '/'-separated names of letters, digits, '.', '_' and '-'"
    return None
