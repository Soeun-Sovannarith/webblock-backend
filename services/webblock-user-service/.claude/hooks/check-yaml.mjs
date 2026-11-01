// PostToolUse (Edit|Write|MultiEdit): catch broken Spring/K8s YAML right after an edit.
// Tells Claude (does not undo) about properties syntax, tabs and hard-coded secrets.
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { readInput, rel, ok, projectDir } from "./lib.mjs";

const file = rel(readInput().tool_input?.file_path);
if (!/\.ya?ml$/.test(file) || !existsSync(path.join(projectDir, file))) ok();

const lines = readFileSync(path.join(projectDir, file), "utf8").split(/\r?\n/);
const problems = [];
lines.forEach((line, i) => {
  const n = i + 1;
  if (/^\s*[a-zA-Z][\w.-]*=[^=]/.test(line))
    problems.push(`line ${n}: key=value (properties syntax) — use nested "key: value"`);
  if (/^\t/.test(line))
    problems.push(`line ${n}: tab indentation — YAML needs spaces`);
  if (/^\s*[\w-]*(password|secret|token|api-?key|access-key)[\w-]*:\s*[^\s$#{'"]/i.test(line))
    problems.push(`line ${n}: looks like a hard-coded secret — use \${ENV_VAR}`);
});

if (problems.length) {
  process.stdout.write(JSON.stringify({
    decision: "block",
    reason: `YAML check failed for ${file}:\n- ${problems.slice(0, 8).join("\n- ")}`,
  }));
}
ok();
