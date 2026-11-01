"""Decision 0014 section 23 item 3: the operator rollback's ``reclaim_route`` is the only forced
server-side apply. Every other write, the reconciler's included, stays never-forced.

Two scans of the source:
- the whole package: every call that passes the keyword ``force`` with anything but the constant
  ``False``. There must be exactly one, in ``KubernetesSiteCluster.reclaim_route``;
- the client wrapper, the only module that talks to the library: the request value ``"true"``
  exists once, as ``_FORCED``, and only ``KubeApi.apply`` uses it.

The client's refusal of a forced apply of any kind but an HTTPRoute is tested in
``test_client.py``."""

import ast
from pathlib import Path

import provisioning

PACKAGE = Path(provisioning.__file__).resolve().parent
CLIENT = PACKAGE / "infrastructure" / "kubernetes" / "_client.py"


class _Scopes(ast.NodeVisitor):
    """Records ``(module, Class.function)`` for each node ``hit`` selects."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.scope: list[str] = []
        self.found: list[tuple[str, str]] = []

    def hit(self, node: ast.AST) -> bool:
        raise NotImplementedError

    def _scoped(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _scoped
    visit_AsyncFunctionDef = _scoped
    visit_ClassDef = _scoped

    def generic_visit(self, node: ast.AST) -> None:
        if self.hit(node):
            self.found.append((self.module, ".".join(self.scope)))
        super().generic_visit(node)


class _ForcedCalls(_Scopes):
    def hit(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and any(
            k.arg == "force" and not (isinstance(k.value, ast.Constant) and k.value.value is False)
            for k in node.keywords
        )


class _Uses(_Scopes):
    def __init__(self, module: str, name: str | None = None, text: str | None = None) -> None:
        super().__init__(module)
        self.name = name
        self.text = text

    def hit(self, node: ast.AST) -> bool:
        if self.name is not None:
            return isinstance(node, ast.Name) and node.id == self.name
        return isinstance(node, ast.Constant) and node.value == self.text


def scan(visitor: _Scopes, path: Path) -> list[tuple[str, str]]:
    visitor.visit(ast.parse(path.read_text(), filename=str(path)))
    return visitor.found


def test_reclaim_route_is_the_only_forced_call_in_the_package() -> None:
    found: list[tuple[str, str]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        found += scan(_ForcedCalls(str(path.relative_to(PACKAGE))), path)

    assert found == [
        ("infrastructure/kubernetes/site_cluster.py", "KubernetesSiteCluster.reclaim_route")
    ]


def test_the_client_sends_force_true_only_from_apply_s_keyword() -> None:
    assert scan(_Uses("_client.py", text="true"), CLIENT) == [("_client.py", "")]
    assert scan(_Uses("_client.py", name="_FORCED"), CLIENT) == [
        ("_client.py", ""),
        ("_client.py", "KubeApi.apply"),
    ]


def test_the_scan_sees_a_forced_call_wherever_it_is() -> None:
    source = (
        "class A:\n"
        "    async def f(self, api):\n"
        "        await api.apply(1, 2, force=True)\n"
        "        await api.apply(1, 2, force=False)\n"
        "        await api.apply(1, 2, force=flag)\n"
        "def g(api):\n"
        "    api.apply(1, force=1)\n"
    )
    visitor = _ForcedCalls("m.py")
    visitor.visit(ast.parse(source))

    assert visitor.found == [("m.py", "A.f"), ("m.py", "A.f"), ("m.py", "g")]
