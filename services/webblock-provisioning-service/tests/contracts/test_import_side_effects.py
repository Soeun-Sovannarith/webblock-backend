"""Importing any provisioning module opens no socket, file or thread (common rules;
review-port-arch.md section 1: "importing every provisioning module opens no socket").

Mechanism: a fresh interpreter (``-I -B``) installs an audit hook (``sys.addaudithook``, PEP 578),
then imports every module of the package found by ``pkgutil.walk_packages``. The hook records
- any ``socket.*`` event (creation, connect, bind, name lookups);
- any thread start (``_thread.*``) and any process start (``subprocess.Popen``, ``os.system``,
  ``os.fork``, ``os.exec``, ``os.posix_spawn``, ``os.spawn``);
- any ``open`` event except the import system reading a ``.py`` or ``.pyc`` file read-only (with
  ``-B`` nothing writes bytecode, so a write is always a module's own);
and the thread count before and after must be equal. An event counts only when the nearest frame
that is not the standard library or the import machinery is in ``src/provisioning``: a third-party
library's own import behaviour (reading its package metadata, for example) is not ours (decision
0014; found by prototype task C with httpx2, aio_pika, psycopg, fastapi, mcp and
kubernetes_asyncio). Modules named ``__main__`` are not imported: running a host is their purpose,
not a side effect of importing the package.

The checker is also run on temporary copies of the package with a planted side effect, so it cannot
pass vacuously.
"""

import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

type EnvFactory = Callable[[Mapping[str, str] | None], dict[str, str]]

CHECKER = r"""
import os
import pkgutil
import sys
import sysconfig
import threading

src = sys.argv[1]
sys.path.insert(0, src)
WATCHED = ("socket.", "_thread.", "subprocess.", "os.system", "os.fork", "os.exec",
           "os.posix_spawn", "os.spawn", "pty.", "ctypes.dlopen")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
found = []


SRC = os.path.realpath(src) + os.sep
LIBS = tuple(
    os.path.realpath(p) + os.sep
    for p in {sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]}
)


def origin():
    # The nearest frame that is ours or a third-party library's decides who caused the event; the
    # standard library and the import machinery in between are skipped (decision 0014: a locked
    # library's own import behaviour, such as reading its metadata, is not our side effect).
    f = sys._getframe(2)
    while f is not None:
        name = f.f_code.co_filename
        if not name.startswith("<"):
            real = os.path.realpath(name)
            if real.startswith(SRC):
                return "ours"
            if real.startswith(LIBS):
                return "library"
        f = f.f_back
    return "unknown"


def hook(event, args):
    if event == "open" or event.startswith(WATCHED):
        if origin() != "ours":
            return
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        reading_code = (
            mode == "r"
            and isinstance(path, str)
            and path.endswith((".py", ".pyc"))
            and not ((flags or 0) & WRITE_FLAGS)
        )
        if not reading_code:
            found.append(f"open {path!r} mode={mode!r}")
    elif event.startswith(WATCHED):
        found.append(event)


threads_before = threading.active_count()
sys.addaudithook(hook)
import provisioning

if not os.path.realpath(provisioning.__file__).startswith(os.path.realpath(src) + os.sep):
    print(f"WRONG-PACKAGE {provisioning.__file__}")
    sys.exit(3)
names = ["provisioning"] + [
    m.name
    for m in pkgutil.walk_packages(provisioning.__path__, "provisioning.")
    if m.name.rsplit(".", 1)[-1] != "__main__"
]
for name in names:
    __import__(name)
if threading.active_count() != threads_before:
    found.append(f"threads {threads_before} -> {threading.active_count()}")
print("IMPORTED", len(names), " ".join(names))
for item in found:
    print("SIDE-EFFECT", item)
sys.exit(1 if found else 0)
"""


def run_checker(
    venv_bin: Path, src: Path, make_env: EnvFactory
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(venv_bin / "python"), "-I", "-B", "-c", CHECKER, str(src)],
        env=make_env(None),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_importing_every_module_has_no_side_effect(
    repo_root: Path, venv_bin: Path, make_env: EnvFactory
) -> None:
    result = run_checker(venv_bin, repo_root / "src", make_env)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    expected = sorted(
        "provisioning."
        + ".".join(p.relative_to(repo_root / "src" / "provisioning").with_suffix("").parts)
        for p in (repo_root / "src" / "provisioning").rglob("*.py")
        if p.name != "__main__.py"
    )
    expected = sorted(name.removesuffix(".__init__") for name in expected)
    imported = sorted(output.split("IMPORTED", 1)[1].split("\n", 1)[0].split()[1:])
    assert imported == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import socket\n\n_s = socket.socket()\n_s.close()\n",
            "SIDE-EFFECT socket.__new__",
        ),
        (
            "import socket\n\nsocket.getaddrinfo('localhost', 5432)\n",
            "SIDE-EFFECT socket.getaddrinfo",
        ),
        (
            "from pathlib import Path\n\n_DATA = (Path(__file__).parent / 'data.json').exists()\n"
            "_F = open(Path(__file__).with_suffix('.json'), 'w')\n_F.close()\n",
            "SIDE-EFFECT open",
        ),
        (
            "_F = open('/etc/hostname')\n_F.close()\n",
            "SIDE-EFFECT open '/etc/hostname'",
        ),
        (
            "import threading\n\n_T = threading.Thread(target=lambda: None)\n"
            "_T.start()\n_T.join()\n",
            "SIDE-EFFECT _thread.",
        ),
        (
            "import threading\nimport time\n\n"
            "threading.Thread(target=time.sleep, args=(5,), daemon=True).start()\n",
            "SIDE-EFFECT threads",
        ),
        (
            "import subprocess\n\nsubprocess.run(['true'], check=False)\n",
            "SIDE-EFFECT subprocess.Popen",
        ),
    ],
)
def test_a_planted_side_effect_is_detected(
    package_copy: Path, venv_bin: Path, make_env: EnvFactory, source: str, expected: str
) -> None:
    (
        package_copy / "src" / "provisioning" / "core" / "application" / "jobs" / "planted.py"
    ).write_text('"""Planted by test_import_side_effects.py."""\n\n' + source)
    result = run_checker(venv_bin, package_copy / "src", make_env)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "provisioning.core.application.jobs.planted" in output, output
    assert expected in output, output
