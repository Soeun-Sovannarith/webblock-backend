import json
import pytest
from app.services.glm import GLMError, apply_changes, parse_response, validate_changes

def test_parse_response_strips_code_fences():
    raw = 'Sure!\n```json\n{"summary": "s", "files": []}\n```'
    assert parse_response(raw) == {"summary": "s", "files": []}

def test_parse_response_rejects_non_json():
    with pytest.raises(GLMError):
        parse_response("I cannot do that")

@pytest.mark.parametrize("path", ["package.json", "../etc/passwd", "components/registry.tsx", "puck-data.json",
                                  "app/../../x.ts", "scripts/run.sh", "/abs/app/x.ts",
                                  "app/test.ts\0.js", "C:\\Windows\\system32.ts", "db/schema.ts",
                                  "drizzle.config.ts"])
def test_validate_changes_blocks_disallowed_paths(path):
    with pytest.raises(GLMError):
        validate_changes({"files": [{"path": path, "action": "upsert", "content": "x"}]})

def test_validate_changes_blocks_protected_delete():
    with pytest.raises(GLMError):
        validate_changes({"files": [{"path": "app/page.tsx", "action": "delete"}]})

def test_apply_changes_upserts_and_deletes():
    files = {"components/blocks/Hero.tsx": "old", "lib/util.ts": "u"}
    changes = validate_changes({"files": [
        {"path": "components/blocks/Hero.tsx", "action": "upsert", "content": "new"},
        {"path": "lib/util.ts", "action": "delete"},
    ]})
    assert apply_changes(files, changes) == ["components/blocks/Hero.tsx", "lib/util.ts"]
    assert files == {"components/blocks/Hero.tsx": "new"}

@pytest.mark.parametrize("path", ["components/blocks/theme.ts", "components/blocks/theme.css"])
def test_validate_changes_blocks_theme_files(path):
    with pytest.raises(GLMError):
        validate_changes({"files": [{"path": path, "action": "upsert", "content": "x"}]})

def test_request_code_from_puck(monkeypatch):
    from app.services.glm import request_code_from_puck
    mock_files = {"components/blocks/Hero.tsx": "old hero"}
    captured = {
        "pages": [{
            "path": "/", "name": "Home",
            "puck_tree": {"root": {"props": {"title": "Test"}}, "content": [{"type": "Hero", "props": {"title": "Hello"}}]}
        }],
        "instructions": "Make hero bold"
    }
    def mock_chat(messages):
        assert "Puck Visual Editor Specification" in messages[1]["content"]
        assert "Make hero bold" in messages[1]["content"]
        return ('{"summary": "generated hero", "files": [{"path": "components/blocks/Hero.tsx", "action": "upsert", '
                '"content": "export default function Hero() { return <h1>Hello</h1>; }"}]}'), {
            "model_name": "glm-test", "input_tokens": 10, "output_tokens": 20
        }
    monkeypatch.setattr("app.services.glm._chat", mock_chat)
    res = request_code_from_puck(mock_files, captured)
    assert res["summary"] == "generated hero"
    assert len(res["changes"]) == 1
    assert res["changes"][0]["path"] == "components/blocks/Hero.tsx"

def test_request_code_update_from_puck(monkeypatch):
    from app.services.glm import request_code_update_from_puck
    mock_files = {"components/blocks/Hero.tsx": "old hero"}
    baseline = {"pages": [{"path": "/", "name": "Home", "puck_tree": {"root": {}, "content": []}}]}
    target = {
        "pages": [{
            "path": "/", "name": "Home",
            "puck_tree": {"root": {}, "content": [{"type": "Hero", "props": {"title": "Updated"}}]}
        }],
        "instructions": None
    }
    diff = {"items": [{"change_type": "COMPONENT", "operation": "ADD", "target": "Hero"}]}
    def mock_chat(messages):
        assert "Detected Visual Editor Changes" in messages[1]["content"]
        assert "Updated Puck Document" in messages[1]["content"]
        return ('{"summary": "updated hero", "files": [{"path": "components/blocks/Hero.tsx", "action": "upsert", '
                '"content": "export default function Hero() { return <h1>Updated</h1>; }"}]}'), {
            "model_name": "glm-test", "input_tokens": 10, "output_tokens": 20
        }
    monkeypatch.setattr("app.services.glm._chat", mock_chat)
    res = request_code_update_from_puck(mock_files, baseline, target, diff)
    assert res["summary"] == "updated hero"
    assert len(res["changes"]) == 1



def test_request_seed_products_returns_validated_rows(monkeypatch):
    from app.services import glm
    seen = {}

    def mock_chat(messages, response_model=None, timeout=None):
        seen["model"], seen["user"] = response_model, messages[-1]["content"]
        return '{"products": [{"name": "Rose Bouquet", "price": "$45"}]}', {"model_name": "glm"}
    monkeypatch.setattr("app.services.glm._chat", mock_chat)

    result = glm.request_seed_products({"business_name": "Petal & Stem", "email": "private@example.com"})
    assert seen["model"] is glm.LLMSeedResponse
    assert "Petal & Stem" in seen["user"] and "private@example.com" not in seen["user"]
    assert result["products"] == [{"name": "Rose Bouquet", "price": "$45", "description": "", "category": "General"}]


def test_seed_prompt_asks_only_for_prices_the_catalog_keeps():
    # catalog.products.price is USD; seed_generator.price_value drops any other currency (an e2e cafe in Phnom Penh
    # got KHR prices from the old prompt, and every price was lost).
    import re
    from app.services import glm, seed_generator
    examples = re.findall(r'"([^"\n]{0,20}\d[^"\n]{0,20})"', glm.SEED_PROMPT)
    assert examples and all(seed_generator.price_value(e) is not None for e in examples)
    assert "KHR" not in glm.SEED_PROMPT


def test_request_seed_products_rejects_bad_shapes(monkeypatch):
    from app.services import glm
    monkeypatch.setattr("app.services.glm._chat", lambda messages, **kw: ('{"products": "none"}', {}))
    with pytest.raises(glm.GLMError):
        glm.request_seed_products({"business_name": "X"})


@pytest.mark.parametrize("path", ["lib/products.ts", "lib/db.ts", "lib/portal-auth.ts", "app/portal/page.tsx",
                                  "app/portal/actions.ts", "app/portal/portal.css",
                                  # a .tsx sibling is resolved before the harness-owned .ts module and would replace it
                                  "lib/portal-auth.tsx", "lib/products.tsx", "app/portal/actions.tsx",
                                  "components/registry.ts", "app/[[...slug]]/page.ts",
                                  # nothing may be added to the portal
                                  "app/portal/layout.tsx", "app/portal/edit/page.tsx",
                                  # the platform's health and readiness checks
                                  "app/healthz/route.ts", "app/readyz/route.ts", "app/readyz/page.tsx"])
def test_validate_changes_blocks_fullstack_files(path):
    from app.services import glm
    with pytest.raises(glm.GLMError):
        glm.validate_changes({"files": [{"path": path, "action": "upsert", "content": "x"}]})


class FakeStream:
    """Stands in for httpx.stream(...): a server-sent events response."""
    def __init__(self, lines, status=200, text=""):
        self.lines, self.status_code, self.text = lines, status, text
        self.is_redirect, self.headers = False, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.text.encode()

    def iter_lines(self):
        yield from self.lines


def _sse(*chunks):
    import json
    return [f"data: {json.dumps(c)}" for c in chunks] + ["", "data: [DONE]"]


def _stream_env(monkeypatch, response):
    from app.core.config import settings
    from app.services import glm
    monkeypatch.setattr(settings, "glm_base_url", "https://glm.example/v1")
    monkeypatch.setattr(settings, "glm_api_key", "k")
    monkeypatch.setattr(settings, "glm_stream", True)
    monkeypatch.setattr(glm, "_chat_with_instructor", lambda *a, **k: pytest.fail("Instructor is not used when streaming"))
    sent = {}

    def fake_stream(method, url, json, headers, timeout):
        sent.update(url=url, body=json)
        return response
    monkeypatch.setattr(glm.httpx, "stream", fake_stream)
    return sent


def test_chat_streams_and_joins_the_content(monkeypatch):
    from app.services import glm
    sent = _stream_env(monkeypatch, FakeStream(_sse(
        {"model": "glm-4.7-flash", "choices": [{"delta": {"role": "assistant", "reasoning_content": "thinking..."}}]},
        {"choices": [{"delta": {"content": '{"summary": "s", '}}]},
        {"choices": [{"delta": {"content": '"files": []}'}}]},
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 30}})))

    raw, usage = glm._chat([{"role": "user", "content": "hi"}])
    assert raw == '{"summary": "s", "files": []}'
    assert usage == {"model_name": "glm-4.7-flash", "input_tokens": 120, "output_tokens": 30}
    assert sent["url"] == "https://glm.example/v1/chat/completions"
    assert sent["body"]["stream"] is True and sent["body"]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("response, message", [
    (FakeStream([], status=524, text="<html>A timeout occurred</html>"), "Cloudflare gave up"),
    (FakeStream([], status=500, text="boom"), "HTTP 500: boom"),
    (FakeStream(_sse({"error": {"message": "model overloaded"}})), "model overloaded"),
    (FakeStream(_sse({"choices": [{"delta": {"reasoning_content": "only thinking"}}]})), "empty reply"),
    (FakeStream(["data: {not json"]), "Unexpected GLM stream chunk"),
])
def test_chat_stream_failures_raise_glm_error(monkeypatch, response, message):
    from app.services import glm
    _stream_env(monkeypatch, response)
    with pytest.raises(GLMError, match=message):
        glm._chat([{"role": "user", "content": "hi"}])


def test_chat_without_streaming_tries_instructor_first(monkeypatch):
    from app.core.config import settings
    from app.services import glm
    _stream_env(monkeypatch, FakeStream([]))
    monkeypatch.setattr(settings, "glm_stream", False)
    monkeypatch.setattr(glm, "_chat_with_instructor", lambda *a, **k: ('{"files": []}', {"model_name": "m"}))
    assert glm._chat([{"role": "user", "content": "hi"}]) == ('{"files": []}', {"model_name": "m"})


def test_chat_turns_thinking_off_unless_enabled(monkeypatch):
    from app.core.config import settings
    from app.services import glm
    sent = _stream_env(monkeypatch, FakeStream(_sse({"choices": [{"delta": {"content": "{}"}}]})))
    glm._chat([{"role": "user", "content": "hi"}])
    assert sent["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["body"]["thinking"] == {"type": "disabled"}

    monkeypatch.setattr(settings, "glm_thinking", True)
    glm._chat([{"role": "user", "content": "hi"}])
    assert "chat_template_kwargs" not in sent["body"] and "thinking" not in sent["body"]


def test_chat_retries_a_transient_failure_once(monkeypatch):
    from app.services import glm
    answers = [FakeStream([], status=503, text="busy"), FakeStream(_sse({"choices": [{"delta": {"content": "{}"}}]}))]
    _stream_env(monkeypatch, None)
    monkeypatch.setattr(glm.httpx, "stream", lambda *a, **k: answers.pop(0))
    assert glm._chat([{"role": "user", "content": "hi"}])[0] == "{}"

    answers[:] = [FakeStream([], status=502, text="bad gateway"), FakeStream([], status=502, text="bad gateway")]
    with pytest.raises(GLMError, match="HTTP 502"):
        glm._chat([{"role": "user", "content": "hi"}])
    assert answers == []


def test_chat_does_not_retry_a_client_error(monkeypatch):
    from app.services import glm
    answers = [FakeStream([], status=400, text="bad request"), FakeStream(_sse({"choices": [{"delta": {"content": "{}"}}]}))]
    _stream_env(monkeypatch, None)
    monkeypatch.setattr(glm.httpx, "stream", lambda *a, **k: answers.pop(0))
    with pytest.raises(GLMError, match="HTTP 400"):
        glm._chat([{"role": "user", "content": "hi"}])
    assert len(answers) == 1


def test_context_leaves_out_files_glm_never_needs():
    from app.services import glm
    files = {"components/blocks/Hero.tsx": "hero", "lib/products.ts": "products", "lib/portal-auth.ts": "secret sauce",
             "app/portal/page.tsx": "portal", "db/seed.sql": "INSERT", "puck-data.json": "{}", "README.md": "docs"}
    context = glm._context(files)
    assert "=== components/blocks/Hero.tsx ===" in context and "=== lib/products.ts ===" in context
    for skipped in ("lib/portal-auth.ts", "app/portal/page.tsx", "db/seed.sql", "puck-data.json", "README.md"):
        assert skipped not in context


@pytest.mark.parametrize("path", [
    "portal/features/products/api.ts", "app/portal/(owner)/products/page.tsx", "app/media/products/[id]/route.ts",
    "lib/business.ts", "lib/business.tsx",
])
def test_validate_changes_blocks_the_owner_portal(path):
    with pytest.raises(GLMError):
        validate_changes({"files": [{"path": path, "action": "upsert", "content": "x"}]})


def test_owner_portal_is_not_sent_as_context():
    from app.services.glm import _context
    context = _context({"portal/features/products/api.ts": "PORTAL", "app/media/products/[id]/route.ts": "MEDIA",
                        "lib/business.ts": "BUSINESS", "components/blocks/Hero.tsx": "HERO"})
    assert "HERO" in context and not any(word in context for word in ("PORTAL", "MEDIA", "BUSINESS"))


# --- garbled files: line breaks lost or mangled (seen with Qwen3-Coder), caught before the build and asked once more ---

GOOD_BLOCK = 'export type TextProps = { text?: string };\n\n// A paragraph of text.\nexport default function Text({ text = "" }: TextProps) {\n  return <p className="wb-text">{text}</p>;\n}\n'
ONE_LINE_BLOCK = ('export type ProductCardsProps = { title?: string }; // Each card links to /products/<slug>. '
                  'export default function ProductCards({ title = "Products" }: ProductCardsProps) { return (<section className="wb-section">'
                  '<div className="wb-container"><h2>{title}</h2></div></section>); }' + " " * 40)
STRAY_N_BLOCK = GOOD_BLOCK.replace('  return <p', 'n                  return <p')


def _reply(content: str) -> str:
    return json.dumps({"summary": "s", "files": [{"path": "components/blocks/ProductCards.tsx", "action": "upsert", "content": content}]})


@pytest.mark.parametrize("content", [ONE_LINE_BLOCK, STRAY_N_BLOCK])
def test_validate_changes_rejects_garbled_files(content):
    from app.services.glm import GLMGarbledError
    with pytest.raises(GLMGarbledError):
        validate_changes(json.loads(_reply(content)))


@pytest.mark.parametrize("path, content", [
    ("components/blocks/Text.tsx", GOOD_BLOCK),
    ("lib/short.ts", 'export const x = 1;'),  # a short one-line file is fine
    ("components/blocks/blocks.css", ".wb-x{color:red}" * 40),  # CSS may be minified
])
def test_validate_changes_accepts_normal_files(path, content):
    assert validate_changes({"files": [{"path": path, "action": "upsert", "content": content}]})[0]["content"] == content


def test_code_requests_ask_once_more_after_a_garbled_file(monkeypatch):
    from app.services import glm
    replies = [(_reply(ONE_LINE_BLOCK), {"model_name": "m", "input_tokens": 100, "output_tokens": 10}),
               (_reply(GOOD_BLOCK), {"model_name": "m", "input_tokens": 100, "output_tokens": 20})]
    monkeypatch.setattr(glm, "_chat", lambda messages, **kw: replies.pop(0))
    result = glm.request_compiler_repair({"components/blocks/ProductCards.tsx": "old"}, "error")
    assert replies == [] and result["changes"][0]["content"] == GOOD_BLOCK
    assert result["usage"] == {"model_name": "m", "input_tokens": 200, "output_tokens": 30}


def test_code_requests_give_up_after_two_garbled_files(monkeypatch):
    from app.services import glm
    calls = []
    monkeypatch.setattr(glm, "_chat", lambda messages, **kw: calls.append(1) or (_reply(ONE_LINE_BLOCK), {"model_name": "m"}))
    with pytest.raises(glm.GLMGarbledError):
        glm.request_code_from_puck({"components/blocks/ProductCards.tsx": "old"}, {"pages": []})
    assert len(calls) == 2


def test_compiler_repair_names_the_files_it_may_change(monkeypatch):
    """Qwen3-Coder kept trying to fix a registry error by editing the harness-owned page route; the prompt now says
    which files it may change, and which files named in the error are harness-owned."""
    from app.services import glm
    seen = []
    monkeypatch.setattr(glm, "_chat", lambda messages, **kw: seen.append(messages[-1]["content"]) or (_reply(GOOD_BLOCK), {}))
    files = {"components/registry.tsx": "r", "components/blocks/ProductCards.tsx": "p", "app/[[...slug]]/page.tsx": "x",
             "lib/products.ts": "l"}
    log = ("./components/registry.tsx:9:1\nError: Export default doesn't exist in target module\n"
           "The export default was not found in module [project]/components/blocks/ProductCards.tsx")
    glm.request_compiler_repair(files, log)
    prompt = seen[0]
    assert "Files you may change (any other path is rejected): components/blocks/ProductCards.tsx." in prompt
    assert "components/registry.tsx (harness-owned, never change it" in prompt
    assert "components/blocks/ProductCards.tsx (you may change it)" in prompt


def test_code_requests_ask_once_more_after_a_refused_path_saying_why(monkeypatch):
    from app.services import glm
    bad = json.dumps({"summary": "s", "files": [{"path": "puck-data.json", "action": "upsert", "content": "{}"}]})
    replies, prompts = [(bad, {}), (_reply(GOOD_BLOCK), {})], []
    monkeypatch.setattr(glm, "_chat", lambda messages, **kw: prompts.append(messages) or replies.pop(0))
    result = glm.request_code_from_puck({"components/blocks/ProductCards.tsx": "old"}, {"pages": []})
    assert result["changes"][0]["path"] == "components/blocks/ProductCards.tsx"
    assert "Your answer was rejected: GLM tried to change a disallowed path: 'puck-data.json'" in prompts[1][-1]["content"]


def test_a_second_refused_path_still_fails(monkeypatch):
    from app.services import glm
    bad = json.dumps({"summary": "s", "files": [{"path": "app/[[...slug]]/page.tsx", "action": "upsert", "content": "x"}]})
    monkeypatch.setattr(glm, "_chat", lambda messages, **kw: (bad, {}))
    with pytest.raises(glm.GLMPathError):
        glm.request_code_update_from_puck({}, {"pages": []}, {"pages": []}, {})
