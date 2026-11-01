// PostToolUse (Edit|Write|MultiEdit): do not log secrets or personal data (security.md, observability.md).
// Looks at log/print calls and warns when a sensitive VARIABLE is passed (words inside plain text messages are fine).
import { readInput, rel, readDisk, warn, ok } from "./lib.mjs";

const file = rel(readInput().tool_input?.file_path);
if (!file || !/\.(java|py|kt)$/.test(file) || /\/src\/test\/|(^|\/)tests?\//.test(file)) ok();

const sensitive = /\b(token|accesstoken|refreshtoken|idtoken|jwt|bearer|authorization|password|passwd|pwd|otp|secret|apikey|api_key|cookie|khqr|qrdata|qr_data|sender_account_no|senderaccountno|accountno|email|phone|phonenumber|fullname)\b/i;
const logCall = /\b(log|logger|LOG|LOGGER)\.(trace|debug|info|warn|warning|error|exception|log)\s*\(|\bSystem\.(out|err)\.print|(^|\s)print\(/;

const hits = [];
readDisk(file).split("\n").forEach((line, i) => {
  if (!logCall.test(line)) return;
  // drop string literals: only identifiers/arguments are checked
  const args = line.replace(/"(\\.|[^"\\])*"/g, '""').replace(/'(\\.|[^'\\])*'/g, "''").replace(/f""/g, '""');
  const afterCall = args.slice(args.search(logCall));
  const m = afterCall.match(sensitive);
  if (m) hits.push(`line ${i + 1}: passes "${m[0]}" to a log call`);
  else if (/\b(request|req|body|payload|dto)\b\s*[,)]/.test(afterCall) && /auth|login|otp|payment|bill|khqr|password/i.test(file))
    hits.push(`line ${i + 1}: logs a whole request object in an auth/payment file`);
});
if (hits.length)
  warn(`Possible secret/personal data in logs in ${file}:\n- ${hits.slice(0, 6).join("\n- ")}\nLog the user_id / bill_id / requestId instead, never the value (see .claude/rules/observability.md).`);
ok();
