"""TR-2 (amended; decision 0025 CQ1, CQ3, CQ4): every domain class sits in the folder of its
kind. entities/ holds rows with an identity and a transition table, enums/ only ``StrEnum``,
values/ only frozen dataclasses with no entity inside, rules/ only frozen rule inputs and results,
and errors.py only ``DomainError`` subclasses."""

import dataclasses
import inspect
from enum import StrEnum
from pathlib import Path
from types import ModuleType

import pytest

from provisioning.core.domain import entities, enums, errors, rules, values
from provisioning.core.domain.entities import command, project_hosting, release, release_step
from provisioning.core.domain.enums import candidate as enums_candidate
from provisioning.core.domain.enums import failure_codes as enums_failure_codes
from provisioning.core.domain.enums import states
from provisioning.core.domain.rules import build as rules_build
from provisioning.core.domain.rules import candidate as rules_candidate
from provisioning.core.domain.rules import (
    command_preconditions,
    go_live,
    outcome,
    replaced_candidate,
)
from provisioning.core.domain.values import build as values_build
from provisioning.core.domain.values import candidate as values_candidate
from provisioning.core.domain.values import (
    command_request,
    deploy_failures,
    failure,
    hosting_reason,
    identifiers,
    release_transition,
    step_input,
    text,
)
from provisioning.core.domain.values import failure_codes as values_failure_codes

# Every module of each kind folder, imported by name (dynamic imports are banned); a test checks
# that this list and the files on disk agree, so a new module must be listed here.
MODULES: dict[str, tuple[ModuleType, ...]] = {
    "entities": (command, project_hosting, release, release_step),
    "values": (
        values_build,
        values_candidate,
        command_request,
        deploy_failures,
        failure,
        values_failure_codes,
        hosting_reason,
        identifiers,
        release_transition,
        step_input,
        text,
    ),
    "enums": (enums_candidate, enums_failure_codes, states),
    "rules": (
        rules_build,
        rules_candidate,
        command_preconditions,
        go_live,
        outcome,
        replaced_candidate,
    ),
}
PACKAGES: dict[str, ModuleType] = {
    "entities": entities,
    "values": values,
    "enums": enums,
    "rules": rules,
}


def modules_of(kind: str) -> tuple[ModuleType, ...]:
    return MODULES[kind]


def classes_of(module: ModuleType) -> list[type]:
    """The classes the module defines itself, private ones included."""
    return [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj) and obj.__module__ == module.__name__
    ]


def entity_names() -> frozenset[str]:
    return frozenset(cls.__name__ for m in modules_of("entities") for cls in classes_of(m))


def is_frozen_dataclass(cls: type) -> bool:
    params = vars(cls).get("__dataclass_params__")
    return params is not None and bool(params.frozen)


def field_type_names(cls: type) -> set[str]:
    """Every identifier in the field annotations (they are strings under postponed evaluation)."""
    names: set[str] = set()
    for field in dataclasses.fields(cls):
        annotation = field.type if isinstance(field.type, str) else repr(field.type)
        names.update(_identifiers(annotation))
    return names


def _identifiers(text: str) -> list[str]:
    out, word = [], ""
    for ch in text + " ":
        if ch.isalnum() or ch == "_":
            word += ch
        else:
            if word:
                out.append(word)
            word = ""
    return out


@pytest.mark.parametrize("kind", sorted(MODULES))
def test_tr2_the_listed_modules_are_the_files_of_each_kind_folder(kind: str) -> None:
    folder = Path(str(PACKAGES[kind].__file__)).parent
    on_disk = {p.stem for p in folder.glob("*.py") if p.name != "__init__.py"}
    listed = {m.__name__.rsplit(".", 1)[1] for m in MODULES[kind]}
    assert listed == on_disk
    assert all(m.__name__.startswith(f"{PACKAGES[kind].__name__}.") for m in MODULES[kind])


@pytest.mark.parametrize("module", modules_of("entities"), ids=lambda m: m.__name__)
def test_tr2_an_entity_has_an_identity_and_a_transition_table(module: ModuleType) -> None:
    classes = classes_of(module)
    assert classes, f"{module.__name__} defines no entity"
    for cls in classes:
        assert is_frozen_dataclass(cls), cls
        fields = {f.name for f in dataclasses.fields(cls)}
        assert fields & {"id", "project_id"}, f"{cls.__name__} has no id or project_id key"
    tables = [
        name
        for name, obj in vars(module).items()
        if name.startswith("legal_") and name.endswith("_transition") and callable(obj)
    ]
    assert tables, f"{module.__name__} has no transition table"


@pytest.mark.parametrize("module", modules_of("enums"), ids=lambda m: m.__name__)
def test_tr2_enums_hold_only_str_enums(module: ModuleType) -> None:
    classes = classes_of(module)
    assert classes
    for cls in classes:
        assert issubclass(cls, StrEnum), cls


@pytest.mark.parametrize("module", modules_of("values"), ids=lambda m: m.__name__)
def test_tr2_values_are_frozen_and_hold_no_entity(module: ModuleType) -> None:
    entities = entity_names()
    for cls in classes_of(module):
        assert is_frozen_dataclass(cls), f"{cls.__name__} is not a frozen dataclass"
        assert not issubclass(cls, StrEnum), f"{cls.__name__} is an enum"
        inside = field_type_names(cls) & entities
        assert not inside, f"{cls.__name__} carries entities {sorted(inside)}"


@pytest.mark.parametrize("module", modules_of("rules"), ids=lambda m: m.__name__)
def test_tr2_rule_classes_are_rule_inputs_or_results(module: ModuleType) -> None:
    for cls in classes_of(module):
        if cls.__name__.startswith("_"):
            continue  # a rule's private working copy
        assert is_frozen_dataclass(cls), f"{cls.__name__} is not a frozen dataclass"
        doc = inspect.getdoc(cls) or ""
        assert doc.startswith(("Rule input.", "Rule result.")), cls.__name__


def test_tr2_errors_hold_only_domain_errors() -> None:
    classes = classes_of(errors)
    assert errors.DomainError in classes
    for cls in classes:
        assert issubclass(cls, errors.DomainError), cls


def test_tr2_the_checks_find_a_misplaced_class() -> None:
    @dataclasses.dataclass(frozen=True)
    class CarriesEntity:
        release: "Release"  # type: ignore[name-defined]  # noqa: F821, UP037

    assert field_type_names(CarriesEntity) & entity_names() == {"Release"}

    @dataclasses.dataclass
    class Mutable:
        value: int

    assert not is_frozen_dataclass(Mutable)
