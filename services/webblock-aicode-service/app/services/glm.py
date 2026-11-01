import json
import logging
import posixpath
import re
import time
from typing import Any, Literal
import httpx
import instructor
import litellm
from pydantic import BaseModel, Field
from app.core.config import settings
from app.services.catalog import ROOT_PROPS
from app.services.scaffold import HARNESS_OWNED, HARNESS_OWNED_DIRS

log = logging.getLogger("webblock.glm")

ALLOWED_PATH = re.compile(r"^(app|components|lib|db)/[A-Za-z0-9_\-./]+\.(tsx|ts|css)$")
PROTECTED_FROM_DELETE = {"app/layout.tsx", "lib/puck.ts"}
RESERVED_PATHS = {"app/page.tsx"}
# Module paths without extension of the harness-owned TypeScript files. The bundler resolves "@/lib/portal-auth" to
# portal-auth.tsx before portal-auth.ts, so a .tsx sibling would silently replace a harness-owned module.
HARNESS_MODULES = {posixpath.splitext(p)[0] for p in HARNESS_OWNED if p.endswith((".ts", ".tsx"))}
SEED_TIMEOUT_SECONDS = 45  # the seed proposal runs inside the onboarding request
MAX_FILES = 20
MAX_FILE_CHARS = 100_000
MAX_CONTEXT_CHARS = 150_000
MAX_HISTORY = 10
# Files GLM is never sent: it may not change them and the blocks don't import them (the product portal and its
# sign-in, the seed, the theme stylesheet, docs). Leaving them out makes each code request about 40% smaller.
CONTEXT_SKIP = {"puck-data.json", "README.md", ".gitignore", "package-lock.json", "db/seed.sql",
                "lib/portal-auth.ts", "components/blocks/theme.css", "Dockerfile", ".dockerignore",
                "app/healthz/route.ts", "app/readyz/route.ts", "lib/business.ts", "lib/storage.ts", "lib/media.ts"}
CONTEXT_SKIP_DIRS = ("app/portal/", "portal/", "app/media/")


class GLMError(Exception):
    pass


class GLMTransientError(GLMError):
    """A failure worth one retry: the connection dropped, or the server answered 429 or 5xx."""


class GLMGarbledError(GLMError):
    """A code file that came back damaged (its line breaks lost or mangled): worth asking once more."""


class GLMPathError(GLMError):
    """A change to a path GLM may not write (outside the allowed folders, or harness-owned): worth asking once more,
    saying which path was refused. The second answer goes through the same check."""


# --- Structured Pydantic Response Models (Instructor / LiteLLM) --------------

class LLMFileChange(BaseModel):
    path: str = Field(..., description="Repo-relative path matching (app|components|lib|db)/**/*.(tsx|ts|css)")
    action: Literal["upsert", "delete"] = Field(default="upsert", description="Action to perform")
    content: str = Field(default="", description="Full content for upsert, empty string for delete")


class LLMCodeResponse(BaseModel):
    summary: str = Field(..., description="One or two sentences describing the changes")
    files: list[LLMFileChange] = Field(default_factory=list, description="List of proposed file modifications")


class LLMSeedProduct(BaseModel):
    name: str = Field(..., description="Product or service name")
    price: str = Field(default="", description="Display price with currency, e.g. $12.00 or 15,000 KHR")
    description: str = Field(default="", description="One or two sentences for the product detail page")
    category: str = Field(default="General", description="Short category, e.g. Coffee, Bouquets, Consulting")


class LLMSeedResponse(BaseModel):
    products: list[LLMSeedProduct] = Field(default_factory=list, description="The business's starting catalog")


SYSTEM_PROMPT = """You are a senior Next.js engineer editing a generated website repository.
The site is Next.js (App Router, TypeScript, React 19) with Drizzle ORM.
- Page content comes from puck-data.json ({"pages": [{"path", "name", "seo_title", "seo_description", "data": <Puck document>}]}), served by app/[[...slug]]/page.tsx and rendered by components/registry.tsx, which maps each block "type" to components/blocks/<Type>.tsx. The harness owns puck-data.json, components/registry.tsx, app/[[...slug]]/page.tsx, components/blocks/theme.ts, components/blocks/theme.css, package.json and the config files; never edit them, and never create app/page.tsx.
- Block components receive their Puck props directly and must keep their existing prop names, default export and file name.
- The site is fullstack: with a database (lib/db.ts), app/[[...slug]]/page.tsx loads the products from the Postgres "products" table (lib/products.ts) and passes them to every ProductCards block as its products prop, and serves a detail page at /products/<slug> for each product. Once the owner has saved the business details in the owner portal, the same route passes them (lib/business.ts) to every Location block as address and mapsUrl and to every About block as text; keep those props working. The owner manages products and business details in the owner portal (app/portal, with its screens in portal/). lib/db.ts, lib/storage.ts, lib/media.ts, lib/products.ts, lib/business.ts, lib/portal-auth.ts, app/portal/*, portal/*, app/media/*, db/schema.ts and db/seed.sql are harness-owned too; never edit them. The database tables are WebBlock's shared catalog, created by the harness: query only the tables and columns in db/schema.ts (without the "catalog." prefix) through getSql() from lib/db.ts, never name website_id (the database fills it in and limits every query to this site), and never create or alter tables. Keep each ProductCards card linking to /products/<slug> with the productSlug function in ProductCards.tsx, unchanged, because lib/products.ts computes the same slug.
- Styles live in components/blocks/blocks.css (classes prefixed wb-) and app/globals.css. The site theme, accent color, font and each section's tone, alignment and spacing are Puck props applied by theme.css through CSS variables (--wb-bg, --wb-fg, --wb-muted, --wb-accent, --wb-surface, --wb-border); use those variables so your styles follow the theme.
- Do not add npm dependencies. Do not fetch remote resources at build time. Keep the code type-safe under "strict": true.
- You may only create, modify or delete files matching (app|components|lib|db)/**/*.(tsx|ts|css).

Respond with a single JSON object and nothing else:
{"summary": "<one or two sentences describing the change>",
 "files": [{"path": "<repo-relative path>", "action": "upsert" | "delete", "content": "<full new file content for upsert>"}]}
Always return the complete file content for every upserted file. Only include files you change."""


def is_configured() -> bool:
    return bool(settings.glm_base_url and settings.glm_api_key)


def _headers() -> dict[str, str]:
    headers = {"Authorization": f"Bearer {settings.glm_api_key}"}
    if settings.glm_cf_access_client_id:
        headers["CF-Access-Client-Id"] = settings.glm_cf_access_client_id
        headers["CF-Access-Client-Secret"] = settings.glm_cf_access_client_secret
    return headers


def _context(files: dict[str, str]) -> str:
    parts, used = [], 0
    for path in sorted(files):
        if path in CONTEXT_SKIP or path.startswith(CONTEXT_SKIP_DIRS):
            continue
        chunk = f"=== {path} ===\n{files[path]}\n"
        if used + len(chunk) > MAX_CONTEXT_CHARS:
            parts.append(f"=== {path} === (omitted: context limit)\n")
            continue
        parts.append(chunk)
        used += len(chunk)
    return "".join(parts)


def _json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise GLMError("GLM response did not contain a JSON object.")
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise GLMError(f"GLM response was not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise GLMError("GLM response must be a JSON object.")
    return data


def parse_response(raw: str) -> dict[str, Any]:
    data = _json_object(raw)
    if not isinstance(data.get("files"), list):
        raise GLMError("GLM response must be an object with a 'files' array.")
    # Schema validation through Pydantic
    try:
        validated = LLMCodeResponse.model_validate(data)
        return validated.model_dump()
    except Exception as e:
        raise GLMError(f"GLM response failed schema validation: {e}") from e


def _allowed_path(raw_path: Any, i: int = 0) -> str:
    """The normalized path GLM may write, or GLMError: inside (app|components|lib|db), never harness-owned."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise GLMError(f"files[{i}] has an invalid path: {raw_path!r}")
    # Security: reject null bytes, Windows drive letters, or backslash injections
    if "\0" in raw_path or re.search(r"^[a-zA-Z]:", raw_path):
        raise GLMError(f"GLM tried to inject unsafe path characters: {raw_path!r}")
    norm_path = posixpath.normpath(raw_path.strip().replace("\\", "/"))
    if norm_path.startswith("/") or ".." in norm_path.split("/") or not ALLOWED_PATH.match(norm_path):
        raise GLMPathError(f"GLM tried to change a disallowed path: {raw_path!r}")
    if (norm_path in HARNESS_OWNED or norm_path in RESERVED_PATHS
            or posixpath.splitext(norm_path)[0] in HARNESS_MODULES or norm_path.startswith(HARNESS_OWNED_DIRS)):
        raise GLMPathError(f"GLM tried to change a harness-owned or duplicate path: {norm_path}")
    return norm_path


def editable(path: str) -> bool:
    try:
        _allowed_path(path)
        return True
    except GLMError:
        return False


# A model sometimes returns a file with its line breaks lost: the whole file on one line, where the first // comment
# swallows the rest (export default included), or a line break turned into a stray "n" before an indented line.
COLLAPSED_MIN_CHARS = 300
_STRAY_N = re.compile(r"^n[ \t]{2,}\S", re.M)


def _garbled(path: str, content: str) -> str | None:
    if not path.endswith((".ts", ".tsx")):
        return None
    if len(content) >= COLLAPSED_MIN_CHARS and content.count("\n") < 2:
        return f"{path} came back on a single line ({len(content)} characters)"
    if _STRAY_N.search(content):
        return f"{path} has a line break turned into a stray 'n'"
    return None


def validate_changes(data: dict[str, Any]) -> list[dict[str, str]]:
    changes = data.get("files")
    if not isinstance(changes, list):
        raise GLMError("GLM response must be an object with a 'files' array.")
    if len(changes) > MAX_FILES:
        raise GLMError(f"GLM proposed {len(changes)} files (max {MAX_FILES}).")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, change in enumerate(changes):
        if not isinstance(change, dict):
            raise GLMError(f"files[{i}] must be an object.")
        raw_path, action = change.get("path"), change.get("action", "upsert")
        norm_path = _allowed_path(raw_path, i)
        if norm_path in seen:
            raise GLMError(f"GLM tried to change a harness-owned or duplicate path: {norm_path}")
        seen.add(norm_path)

        if action == "delete":
            if norm_path in PROTECTED_FROM_DELETE:
                raise GLMError(f"GLM tried to delete protected file {norm_path}")
            out.append({"path": norm_path, "action": "delete", "content": ""})
        elif action == "upsert":
            content = change.get("content")
            if not isinstance(content, str) or not content.strip():
                raise GLMError(f"files[{i}] upsert for {norm_path} has no content.")
            if len(content) > MAX_FILE_CHARS:
                raise GLMError(f"{norm_path} exceeds {MAX_FILE_CHARS} characters.")
            if problem := _garbled(norm_path, content):
                raise GLMGarbledError(problem)
            out.append({"path": norm_path, "action": "upsert", "content": content})
        else:
            raise GLMError(f"files[{i}] has unknown action {action!r}")
    return out


REPAIR_PROMPT = """You are a senior Next.js and TypeScript engineer fixing build errors.
The site failed to build during 'npm run build'.
Examine the build log and compiler error messages, find the root cause in the repository files, and return the corrected files.
Follow all Next.js App Router and TypeScript strict mode rules.
Never touch harness-owned files (package.json, registry.tsx, theme.ts, etc.).

Respond with a single JSON object and nothing else:
{"summary": "<brief explanation of the fix>",
 "files": [{"path": "<repo-relative path>", "action": "upsert" | "delete", "content": "<full corrected file content>"}]}"""


def _add_tokens(a: int | None, b: int | None) -> int | None:
    return None if a is None and b is None else (a or 0) + (b or 0)


_RETRY_HINT = {
    GLMGarbledError: "Write every file with its line breaks: each line of code on its own line, encoded as \\n inside the JSON string.",
    GLMPathError: "Change only files matching (app|components|lib|db)/**/*.(tsx|ts|css) that the instructions do not list as "
                  "harness-owned; never puck-data.json, components/registry.tsx, app/[[...slug]]/page.tsx or the theme files.",
}


def _code_request(messages: list[dict[str, str]], default_summary: str) -> dict[str, Any]:
    """A code-change call: parse and validate the reply, and ask once more, saying what was wrong, when a file came
    back garbled (GLMGarbledError) or touched a path GLM may not write (GLMPathError). A second such reply raises,
    and the second answer passes the same checks. Usage adds up both calls."""
    usage: dict[str, Any] = {}
    for attempt in (1, 2):
        raw, call_usage = _chat(messages)
        usage = {"model_name": call_usage.get("model_name"),
                 "input_tokens": _add_tokens(usage.get("input_tokens"), call_usage.get("input_tokens")),
                 "output_tokens": _add_tokens(usage.get("output_tokens"), call_usage.get("output_tokens"))}
        data = parse_response(raw)
        try:
            changes = validate_changes(data)
        except (GLMGarbledError, GLMPathError) as e:
            if attempt == 2:
                raise
            log.warning("GLM's answer was rejected, asking once more: %s", e)
            messages = [*messages, {"role": "user", "content": f"Your answer was rejected: {e}. {_RETRY_HINT[type(e)]} "
                                                                "Answer again with the complete JSON object."}]
            continue
        return {"summary": str(data.get("summary") or default_summary), "changes": changes, "usage": usage}
    raise AssertionError("unreachable")


_LOG_PATH = re.compile(r"(?:\./)?((?:app|components|lib|db|portal)/[\w./\[\]()@-]+?\.(?:tsx|ts|css))")


def _repair_scope(files: dict[str, str], error_log: str) -> str:
    """Which files the repair may change, and which files the error names that it may not (fix their cause instead)."""
    named = list(dict.fromkeys(m for m in _LOG_PATH.findall(error_log) if m in files))
    allowed = sorted(p for p in files if editable(p))
    lines = ["Files you may change (any other path is rejected): " + ", ".join(allowed) + "."]
    if named:
        lines.append("Files named in the error: " + "; ".join(
            f"{p} ({'you may change it' if editable(p) else 'harness-owned, never change it: fix the file it imports or uses'})"
            for p in named) + ".")
    return "\n".join(lines)


def request_compiler_repair(files: dict[str, str], error_log: str) -> dict[str, Any]:
    """Ask GLM to inspect build errors and repair TypeScript/Next.js files."""
    trimmed_log = error_log[-4000:] if len(error_log) > 4000 else error_log
    return _code_request([
        {"role": "system", "content": REPAIR_PROMPT},
        {"role": "user", "content": f"Repository files:\n\n{_context(files)}\n\nBuild error log:\n```\n{trimmed_log}\n```\n"
                                    f"{_repair_scope(files, trimmed_log)}\nPlease fix the compile errors."},
    ], "Repaired build error")


def _chat_with_instructor(messages: list[dict[str, str]], response_model: type[BaseModel] = LLMCodeResponse,
                          timeout: float | None = None) -> tuple[str, dict[str, Any]] | None:
    """Attempt completion with Instructor + LiteLLM for strict type validation."""
    if not is_configured():
        return None
    try:
        client = instructor.from_litellm(litellm.completion)
        custom_model = settings.glm_model
        model_name = custom_model if custom_model.startswith("openai/") else f"openai/{custom_model}"
        result, completion = client.chat.completions.create_with_completion(
            model=model_name,
            api_base=settings.glm_base_url,
            api_key=settings.glm_api_key,
            extra_headers=_headers(),
            extra_body=_thinking_options() or None,
            messages=messages,
            response_model=response_model,
            temperature=0.2,
            timeout=timeout or settings.glm_timeout_seconds,
        )
        usage = completion.usage if hasattr(completion, "usage") else None
        return json.dumps(result.model_dump()), {
            "model_name": str(custom_model),
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
        }
    except Exception as e:
        log.debug("Instructor/LiteLLM completion fallback to direct HTTP: %s", e)
        return None


def _thinking_options() -> dict[str, Any]:
    """Request fields that turn a reasoning model's thinking off (GLM_THINKING=false). Servers ignore the one they don't know."""
    if settings.glm_thinking:
        return {}
    return {"chat_template_kwargs": {"enable_thinking": False}, "thinking": {"type": "disabled"}}


def _check_status(resp: httpx.Response) -> None:
    if resp.is_redirect and "cloudflareaccess.com" in resp.headers.get("location", ""):
        raise GLMError("GLM endpoint is behind Cloudflare Access; set GLM_CF_ACCESS_CLIENT_ID and GLM_CF_ACCESS_CLIENT_SECRET.")
    if resp.status_code == 524:
        raise GLMTransientError("GLM returned HTTP 524: Cloudflare gave up after about 100 s without data from the GLM server "
                       "(GLM_STREAM=true avoids this).")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise GLMTransientError(f"GLM returned HTTP {resp.status_code}: {resp.text[:500]}")
    if resp.status_code >= 400:
        raise GLMError(f"GLM returned HTTP {resp.status_code}: {resp.text[:500]}")


def _chat_stream(body: dict[str, Any], timeout: float) -> tuple[str, dict[str, Any]]:
    """Streaming chat completion: the server sends the reply as server-sent events (`data: {chunk}` lines, then
    `data: [DONE]`) while it is generated, including the model's reasoning_content, which is dropped. `timeout` caps
    the whole call and the wait for each line."""
    deadline = time.monotonic() + timeout
    parts: list[str] = []
    usage: dict[str, Any] = {}
    model = settings.glm_model
    try:
        with httpx.stream("POST", f"{settings.glm_base_url.rstrip('/')}/chat/completions",
                          json={**body, "stream": True, "stream_options": {"include_usage": True}},
                          headers=_headers(), timeout=httpx.Timeout(timeout, connect=15)) as resp:
            if resp.is_redirect or resp.status_code >= 400:
                resp.read()
                _check_status(resp)
            for line in resp.iter_lines():
                if time.monotonic() > deadline:
                    raise GLMError(f"GLM did not finish within {timeout:g} s.")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError as e:
                    raise GLMError(f"Unexpected GLM stream chunk: {data[:200]}") from e
                if chunk.get("error"):
                    raise GLMError(f"GLM stream error: {str(chunk['error'])[:500]}")
                model = chunk.get("model") or model
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    parts.append((choice.get("delta") or {}).get("content") or "")
    except httpx.TimeoutException as e:
        raise GLMError(f"GLM sent nothing for {timeout:g} s: {e}") from e
    except httpx.TransportError as e:
        raise GLMTransientError(f"GLM connection failed: {e}") from e
    except httpx.HTTPError as e:
        raise GLMError(f"GLM request failed: {e}") from e
    raw = "".join(parts)
    if not raw.strip():
        raise GLMError("GLM returned an empty reply.")
    return raw, {"model_name": str(model), "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}


def _chat(messages: list[dict[str, str]], response_model: type[BaseModel] = LLMCodeResponse,
          timeout: float | None = None) -> tuple[str, dict[str, Any]]:
    """JSON-mode chat completion. With GLM_STREAM (default) one streaming call; otherwise Instructor + LiteLLM
    (validated against response_model), then a plain HTTP call. Every caller validates the reply itself."""
    if not is_configured():
        raise GLMError("GLM is not configured (set GLM_BASE_URL and GLM_API_KEY).")
    body = {"model": settings.glm_model, "temperature": 0.2, "response_format": {"type": "json_object"}, "messages": messages,
            **_thinking_options()}
    if settings.glm_stream:
        try:
            return _chat_stream(body, timeout or settings.glm_timeout_seconds)
        except GLMTransientError as e:  # a dropped connection or a 429/5xx usually fails fast: try once more
            log.warning("GLM call failed, retrying once: %s", e)
            return _chat_stream(body, timeout or settings.glm_timeout_seconds)

    # Try Instructor + LiteLLM first
    instructor_res = _chat_with_instructor(messages, response_model, timeout)
    if instructor_res:
        return instructor_res

    # Direct fallback via HTTPX
    try:
        resp = httpx.post(
            f"{settings.glm_base_url.rstrip('/')}/chat/completions",
            json=body,
            headers=_headers(),
            timeout=timeout or settings.glm_timeout_seconds,
        )
    except httpx.HTTPError as e:
        raise GLMError(f"GLM request failed: {e}") from e
    _check_status(resp)
    try:
        payload = resp.json()
        raw = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise GLMError(f"Unexpected GLM response shape: {resp.text[:500]}") from e
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return raw, {
        "model_name": str(payload.get("model") or settings.glm_model),
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
    }


def request_code_update(files: dict[str, str], request_text: str) -> dict[str, Any]:
    result = _code_request([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Repository files:\n\n{_context(files)}\n\nCustomization request:\n{request_text.strip()}"},
    ], "AI code update")
    if not result["changes"]:
        raise GLMError("GLM returned no file changes for the request.")
    return result


PUCK_CODEGEN_SYSTEM_PROMPT = """You are a senior Next.js engineer generating React code for a website based on visual Puck editor specifications.
The site is Next.js (App Router, TypeScript, React 19) with Drizzle ORM.
- Page content comes from puck-data.json, served by app/[[...slug]]/page.tsx and rendered by components/registry.tsx, which maps each block "type" to components/blocks/<Type>.tsx. The harness owns puck-data.json, components/registry.tsx, app/[[...slug]]/page.tsx, components/blocks/theme.ts, components/blocks/theme.css, package.json and config files; never edit them, and never create app/page.tsx.
- components/blocks/<Type>.tsx already holds a working component for every block type in the layout. Change a component only where it clearly improves this site, and return only the files you change; an empty files list is a valid answer.
- Every block's tone, align and spacing props are applied by the page around the block (theme.css), so components don't need to accept or handle them.
- The site is fullstack: with a database (lib/db.ts), app/[[...slug]]/page.tsx loads the products from the Postgres "products" table (lib/products.ts) and passes them to every ProductCards block as its products prop, and serves a detail page at /products/<slug> for each product. Once the owner has saved the business details in the owner portal, the same route passes them (lib/business.ts) to every Location block as address and mapsUrl and to every About block as text; keep those props working. The owner manages products and business details in the owner portal (app/portal, with its screens in portal/). lib/db.ts, lib/storage.ts, lib/media.ts, lib/products.ts, lib/business.ts, lib/portal-auth.ts, app/portal/*, portal/*, app/media/*, db/schema.ts and db/seed.sql are harness-owned too; never edit them. The database tables are WebBlock's shared catalog, created by the harness: query only the tables and columns in db/schema.ts (without the "catalog." prefix) through getSql() from lib/db.ts, never name website_id (the database fills it in and limits every query to this site), and never create or alter tables. Keep each ProductCards card linking to /products/<slug> with the productSlug function in ProductCards.tsx, unchanged, because lib/products.ts computes the same slug.
- Each component in components/blocks/<Type>.tsx must:
  1. Have a default export function matching the component type (e.g. `export default function Hero(props) { ... }`).
  2. Accept its Puck props and render rich, responsive, modern JSX with semantic HTML.
  3. Be strictly type-safe under TypeScript.
- Styles can be placed in components/blocks/blocks.css or app/globals.css (classes prefixed wb-). Use theme CSS variables (--wb-bg, --wb-fg, --wb-muted, --wb-accent, --wb-surface, --wb-border) so the look harmonizes with the user's selected theme.
- Do not add npm dependencies. Do not fetch remote resources at build time. Keep the code type-safe under "strict": true.
- You may only create, modify or delete files matching (app|components|lib|db)/**/*.(tsx|ts|css).

Respond with a single JSON object and nothing else:
{"summary": "<one or two sentences describing the generated React components>",
 "files": [{"path": "<repo-relative path>", "action": "upsert" | "delete", "content": "<full new file content>"}]}
Always return the complete file content for every upserted file. Only include files you change."""

PUCK_UPDATE_SYSTEM_PROMPT = """You are a senior Next.js engineer updating a website repository based on visual Puck editor changes.
The site is Next.js (App Router, TypeScript, React 19) with Drizzle ORM.
- Page content is served by app/[[...slug]]/page.tsx and rendered by components/registry.tsx, which maps block types to components/blocks/<Type>.tsx.
- The harness owns puck-data.json, components/registry.tsx, app/[[...slug]]/page.tsx, components/blocks/theme.ts, components/blocks/theme.css, package.json and config files; never edit them, and never create app/page.tsx.
- The user edited the site in the visual editor. You are provided with:
  1. A list of detected changes (blocks added, removed, reordered, or props updated).
  2. The updated visual Puck JSON document.
  3. Any additional user instructions.
- Update the repository's React components in components/blocks/<Type>.tsx and styles in components/blocks/blocks.css or app/globals.css to reflect these visual edits in code.
- If a block component is updated, preserve its default export and ensure type safety under React 19.
- Every block's tone, align and spacing props are applied by the page around the block (theme.css), so components don't need to accept or handle them. A change to only those props, or to text and products, needs no code change: return an empty files list.
- The site is fullstack: with a database (lib/db.ts), app/[[...slug]]/page.tsx loads the products from the Postgres "products" table (lib/products.ts) and passes them to every ProductCards block as its products prop, and serves a detail page at /products/<slug> for each product. Once the owner has saved the business details in the owner portal, the same route passes them (lib/business.ts) to every Location block as address and mapsUrl and to every About block as text; keep those props working. The owner manages products and business details in the owner portal (app/portal, with its screens in portal/). lib/db.ts, lib/storage.ts, lib/media.ts, lib/products.ts, lib/business.ts, lib/portal-auth.ts, app/portal/*, portal/*, app/media/*, db/schema.ts and db/seed.sql are harness-owned too; never edit them. The database tables are WebBlock's shared catalog, created by the harness: query only the tables and columns in db/schema.ts (without the "catalog." prefix) through getSql() from lib/db.ts, never name website_id (the database fills it in and limits every query to this site), and never create or alter tables. Keep each ProductCards card linking to /products/<slug> with the productSlug function in ProductCards.tsx, unchanged, because lib/products.ts computes the same slug.
- Do not add npm dependencies. Keep code type-safe under "strict": true.
- You may only create, modify or delete files matching (app|components|lib|db)/**/*.(tsx|ts|css).

Respond with a single JSON object and nothing else:
{"summary": "<one or two sentences describing the code updates made to reflect the visual edits>",
 "files": [{"path": "<repo-relative path>", "action": "upsert" | "delete", "content": "<full new file content>"}]}
Always return the complete file content for every upserted file. Only include files you change."""


def request_code_from_puck(files: dict[str, str], captured_input: dict[str, Any]) -> dict[str, Any]:
    pages = captured_input.get("pages", [])
    prompt_parts = ["Visual layout pages from Puck:\n"]
    for p in pages:
        prompt_parts.append(f"--- Page: {p.get('name')} ({p.get('path')}) ---\n{json.dumps(p.get('puck_tree'), indent=2)}\n")
    if captured_input.get("instructions"):
        prompt_parts.append(f"\nUser instructions:\n{captured_input['instructions']}\n")
    return _code_request([
        {"role": "system", "content": PUCK_CODEGEN_SYSTEM_PROMPT},
        {"role": "user", "content": f"Repository files:\n\n{_context(files)}\n\nPuck Visual Editor Specification:\n{''.join(prompt_parts)}"},
    ], "Generated React components from visual editor")


def request_code_update_from_puck(files: dict[str, str], baseline: dict[str, Any], target: dict[str, Any],
                                  diff: dict[str, Any]) -> dict[str, Any]:
    prompt_parts = [
        f"Detected Visual Editor Changes:\n{json.dumps(diff, indent=2)}\n\n",
        f"Updated Puck Document:\n{json.dumps(target.get('pages', []), indent=2)}\n",
    ]
    if target.get("instructions"):
        prompt_parts.append(f"\nAdditional Instructions:\n{target['instructions']}\n")
    return _code_request([
        {"role": "system", "content": PUCK_UPDATE_SYSTEM_PROMPT},
        {"role": "user", "content": f"Repository files:\n\n{_context(files)}\n\nVisual Editor Updates:\n{''.join(prompt_parts)}"},
    ], "Updated React components from visual editor")


ASSISTANT_PROMPT = """You are the WebBlock design assistant. You change one page of a website built with the Puck page builder
by proposing operations on its components: content, which sections exist and their order, and the look.
The look is set with props: on "root" the site-wide theme, accent color and font; on every component its tone
(background), align and spacing. Map requests about colors, dark or light mode, fonts, backgrounds and spacing onto
these props (for example "dark theme" is UPDATE root {{"theme": "dark"}}). Only when a request cannot be expressed
with the props below (custom CSS, animations, new behavior or new kinds of components) say in your reply that the user
should send it as a code change instead, and propose no operations.

Root props (use component_id "root"):
{root}

Available component types and their editable props (a list describes an array of objects, "one of" lists the only
allowed values, "default" keeps the component's usual style):
{catalog}

The page document is {{"root": {{"props": {{...}}}}, "content": [{{"type": "<ComponentType>", "props": {{"id": "<component id>", ...}}}}]}}.

Respond with a single JSON object and nothing else:
{{"reply": "<short message to the user>",
 "operations": [
  {{"op": "UPDATE", "component_id": "<id>", "props": {{<only the props that change>}}}},
  {{"op": "ADD", "component_type": "<type>", "index": <position in content>, "props": {{<all editable props>}}}},
  {{"op": "REMOVE", "component_id": "<id>"}},
  {{"op": "MOVE", "component_id": "<id>", "index": <new position>}}
 ]}}
Keep component ids unchanged. Use an empty operations list when nothing on the page should change."""


def propose_change_set(page: dict[str, Any], catalog: dict[str, Any], history: list[dict[str, str]],
                       message: str) -> dict[str, Any]:
    messages = [{"role": "system", "content": ASSISTANT_PROMPT.format(root=json.dumps(ROOT_PROPS, indent=1),
                                                                    catalog=json.dumps(catalog, indent=1))}]
    messages += history[-MAX_HISTORY:]
    messages.append({"role": "user", "content": (
        f"Page {page['name']} ({page['path']}):\n{json.dumps(page['puck_tree'], ensure_ascii=False)}\n\nRequest:\n{message.strip()}")})
    raw, usage = _chat(messages)
    data = _json_object(raw)
    ops = data.get("operations", [])
    if not isinstance(ops, list):
        raise GLMError("GLM response 'operations' must be an array.")
    return {"reply": str(data.get("reply") or "").strip() or "Here is the proposed change.", "operations": ops, "usage": usage}


SEED_PROMPT = """You prepare the starting product catalog for a small business's new website. The site stores it in
a Postgres "products" table that the owner later edits in a product portal, and each product gets its own detail page.
From the business details, propose 4 to 8 products or services this business would really sell:
- Match the business type, description and location. Write in the language of the business description.
- price is in US dollars, written like "$4.50", whatever the business's country: the catalog stores USD only and
  drops any other currency. Use "" when a price does not make sense.
- description is one or two plain sentences for the product's detail page. No markdown, HTML or emoji.
- category is a short grouping such as "Coffee", "Pastries" or "Bouquets".
- Use plain text only. Do not invent contact details, addresses or claims about awards.

Respond with a single JSON object and nothing else:
{"products": [{"name": "<name>", "price": "<price>", "description": "<description>", "category": "<category>"}]}"""


def request_seed_products(business: dict[str, Any]) -> dict[str, Any]:
    """Ask GLM for the business's starting catalog, from the onboarding details (BusinessOnboardingRequest as a dict).

    Returns {"products": [...], "usage": ...}. GLM proposes rows only: seed_generator.clean_products normalizes them
    and seed_generator.render_seed_sql writes the escaped SQL, so GLM output never reaches the database as SQL.
    """
    details = {key: business.get(key) for key in ("business_name", "business_type", "business_description", "address")}
    raw, usage = _chat([
        {"role": "system", "content": SEED_PROMPT},
        {"role": "user", "content": f"Business details:\n{json.dumps(details, indent=2, ensure_ascii=False)}"},
    ], response_model=LLMSeedResponse, timeout=SEED_TIMEOUT_SECONDS)
    data = _json_object(raw)
    try:
        validated = LLMSeedResponse.model_validate(data)
    except Exception as e:
        raise GLMError(f"GLM seed response failed schema validation: {e}") from e
    return {"products": [p.model_dump() for p in validated.products], "usage": usage}


def apply_changes(files: dict[str, str], changes: list[dict[str, str]]) -> list[str]:
    for change in changes:
        if change["action"] == "delete":
            files.pop(change["path"], None)
        else:
            files[change["path"]] = change["content"]
    return [c["path"] for c in changes]
