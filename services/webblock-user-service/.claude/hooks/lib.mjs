// Shared helpers for WebBlock hooks. Node only — no jq needed (works on macOS/Linux/Windows).
import { readFileSync } from "node:fs";
import path from "node:path";

export function readInput() {
  try { return JSON.parse(readFileSync(0, "utf8") || "{}"); } catch { return {}; }
}
export const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
export function rel(p) {
  if (!p) return "";
  const abs = path.isAbsolute(p) ? p : path.join(projectDir, p);
  return path.relative(projectDir, abs).split(path.sep).join("/");
}
/** Block the tool call: exit 2, reason on stderr goes back to Claude. */
export function block(reason) { process.stderr.write(reason + "\n"); process.exit(2); }
export function ok() { process.exit(0); }

// ---- helpers added for the contract / layer / secret hooks -------------------------------------------------------
import { existsSync, readFileSync as _read } from "node:fs";

/** Text of a file on disk, or "" if it does not exist. */
export function readDisk(relPath) {
  const p = path.join(projectDir, relPath);
  try { return existsSync(p) ? _read(p, "utf8") : ""; } catch { return ""; }
}

/**
 * What the file will contain AFTER this Write/Edit/MultiEdit, and what it contains now.
 * { file, before, after, added } where `added` is only the new text (for scanning secrets).
 */
export function pendingChange(input) {
  const ti = input.tool_input || {};
  const file = rel(ti.file_path);
  const before = file ? readDisk(file) : "";
  let after = before, added = "";
  if (typeof ti.content === "string") { after = ti.content; added = ti.content; }
  else {
    const edits = Array.isArray(ti.edits) ? ti.edits : (typeof ti.old_string === "string" ? [ti] : []);
    for (const e of edits) {
      if (typeof e.new_string !== "string") continue;
      added += e.new_string + "\n";
      if (typeof e.old_string === "string" && e.old_string !== "") {
        after = e.replace_all ? after.split(e.old_string).join(e.new_string) : after.replace(e.old_string, () => e.new_string);
      }
    }
  }
  return { file, before, after, added };
}

/** Tell Claude about a problem AFTER an edit (PostToolUse). Does not undo the edit. */
export function warn(reason) {
  process.stdout.write(JSON.stringify({ decision: "block", reason }));
  process.exit(0);
}

/** Remove string literals and comments so keyword checks do not fire on prose. */
export function stripCode(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1")
    .replace(/#[^\n]*/g, (m) => (m.startsWith("#!") ? m : ""))
    .replace(/"""[\s\S]*?"""/g, '""')
    .replace(/"(\.|[^"\\n])*"/g, '""')
    .replace(/'(\.|[^'\\n])*'/g, "''");
}
