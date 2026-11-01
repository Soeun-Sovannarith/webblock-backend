// PreToolUse (Edit|Write|MultiEdit): stop real secrets from being written into files.
// Config uses placeholders (${WEBILL_API_KEY}); real values live in env vars / Kubernetes secrets (security.md).
import { readInput, pendingChange, block, ok } from "./lib.mjs";

const { file, added } = pendingChange(readInput());
if (!file || !added) ok();
// docs, rules and tests may show example shapes; the scan is for code and config.
if (/^(\.claude|docs)\//.test(file) || /\/src\/test\//.test(file) || /\.(md|example|sample|template)$/.test(file)) ok();

const strong = [
  [/-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----/, "a private key"],
  [/\bAKIA[0-9A-Z]{16}\b/, "an AWS access key id"],
  [/\bghp_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b/, "a GitHub token"],
  [/\bglpat-[A-Za-z0-9_-]{20,}\b/, "a GitLab token"],
  [/\bxox[baprs]-[A-Za-z0-9-]{10,}\b/, "a Slack token"],
  [/\bsk-[A-Za-z0-9]{32,}\b/, "an API secret key"],
  [/\beyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\b/, "a JWT (token)"],
  [/\bAIza[0-9A-Za-z_-]{35}\b/, "a Google API key"],
];
for (const [re, what] of strong)
  if (re.test(added)) block(`Blocked: this edit to ${file} contains ${what}. Never write real secrets into files. Use an env var placeholder like \${NAME} and tell the user which variable to set.`);

// key = literal value in config files
if (/\.(ya?ml|properties|env|json|toml|ini|gradle|conf)$/i.test(file) || /(^|\/)Dockerfile/.test(file)) {
  const safe = /^(\$\{.*|<.*|\{\{.*|changeme|change-me|example.*|your[-_].*|webblock|postgres|password|secret|test|dummy|localhost|none|null|true|false|\*+|x+)$/i;
  for (const line of added.split("\n")) {
    const m = line.match(/\b(password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token|client[_-]?secret)\b["']?\s*[:=]\s*["']?([^\s"'#,]+)/i);
    if (m && m[2].length >= 8 && !safe.test(m[2]))
      block(`Blocked: ${file} sets "${m[1]}" to what looks like a real value. Use a placeholder such as \${${m[1].toUpperCase().replace(/[^A-Z0-9]+/g, "_")}} and let the user set the variable.`);
  }
}
ok();
