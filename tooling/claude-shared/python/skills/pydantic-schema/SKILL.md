---
name: pydantic-schema
description: Use when defining request/response/event models in a Python service with Pydantic v2, including strict validation and contract compatibility.
---

# Define Pydantic schemas

Paths assume a session started in `services/<python-service>`; `../../contracts/` is the repo-root `contracts/`.

## Purpose
Create Pydantic v2 models that mirror the contract exactly, reject bad input at the boundary, and stay compatible with released contract versions.

## When to use / when NOT to use
- Use for request, response and event payload models, and for LLM output models that must be parsed strictly.
- Do NOT use for persistence models (keep them separate) or for endpoint wiring (skill `fastapi-endpoint`).

## Inputs to confirm with the user
Source contract (`../../contracts/openapi/<service>.vN.yaml` or `../../contracts/events/schemas/<event>.vN.json`), which models are inbound or outbound, whether the payload comes from an event (forward compatible) or a client (strict).

## Procedure
1. **Mirror the contract.** Field names, types and required-ness come from the spec. Use `alias` with `populate_by_name=True` only to bridge camelCase JSON and snake_case Python.
2. **Choose strictness.** Requests: `extra="forbid"`. Consumed events: `extra="ignore"` (forward compatible). Always `str_strip_whitespace=True`.
3. **Constrain every field.** `Field(min_length, max_length, ge, le, pattern)`, `EmailStr`, `UUID`, timezone-aware `datetime`, `Enum` for fixed sets. No bare `str`, `dict` or `Any` for structured data.
4. **Separate by purpose.** `XCreate`, `XUpdate`, `XResponse`, `XEvent`. Never expose a persistence model through the API.
5. **Secrets.** `SecretStr` for tokens and keys; never in `repr`, logs or responses.
6. **Evolve additively.** New optional fields default to `None`. Never rename or retype a field of a released contract (breaking: new version via `contract-change`).
7. **Test** (see Templates) and run `ruff check .`, `ruff format --check .`, `pytest -q`.

## Templates
```python
class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=80)
    owner_email: EmailStr = Field(alias="ownerEmail")
    template: Template = Template.BLANK  # Enum from the contract
    description: str | None = Field(default=None, max_length=500)
```
```python
@pytest.mark.parametrize("bad", [{"name": ""}, {"name": "x" * 81}, {"name": "ok", "unknown": 1}])
def test_project_create_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        ProjectCreate.model_validate({"ownerEmail": "user@example.test", **bad})
```

## Checklist
- [ ] Fields match the contract; example payload from the contract validates
- [ ] `extra` policy chosen on purpose; whitespace stripped
- [ ] Every field constrained; no `Any`
- [ ] Separate create, update, response and event models
- [ ] Secrets are `SecretStr`; optional additions default to `None`
- [ ] Tests: valid, each violation, unknown field, contract example

## Common mistakes
Reusing one model for request and response; `extra="forbid"` on consumed events (breaks on additive changes); naive datetimes; `dict` for structured payloads; renaming a released field; real emails or tokens in test data (use `user@example.test`).

## Definition of done
Models match the contract, tests cover constraints, lint and tests green with real output reported.

## Related
Skills `fastapi-endpoint`, `contract-change`, `microservice-integration`; rules `python-style.md`, `testing.md`, `fastapi-api.md`.
