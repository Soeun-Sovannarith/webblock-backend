// PreToolUse (Edit|Write|MultiEdit|NotebookEdit): stop edits to secrets, lockfiles and applied Flyway migrations.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { readInput, rel, block, ok, projectDir } from "./lib.mjs";

const input = readInput();
const file = rel(input.tool_input?.file_path || input.tool_input?.notebook_path);
if (!file) ok();
const base = path.posix.basename(file);

// 1. Secrets
if (/^\.env(\..*)?$/.test(base) && !/\.example$|\.sample$/.test(base))
  block(`Blocked: ${file} holds secrets. Edit ${base}.example instead and tell the user which variable to set.`);
if (/\.(pem|key|p12|jks|keystore)$/.test(base) || /(^|\/)secrets?\//.test(file))
  block(`Blocked: ${file} looks like a key or secret file. Ask the user to change it by hand.`);
// Keycloak realm exports, cloud/service-account credentials, WeBill credential files
if (/^realm-export.*\.json$/.test(base) || /credentials?.*\.(json|ya?ml)$/i.test(base) || /^service-account.*\.json$/.test(base) || base === ".netrc")
  block(`Blocked: ${file} can hold credentials. Ask the user to change it by hand.`);

// 2. Lockfiles (change them through the package manager)
if (["package-lock.json", "pnpm-lock.yaml", "yarn.lock", "gradle.lockfile"].includes(base))
  block(`Blocked: don't hand-edit ${base}. Run the package manager (npm install / ./gradlew --write-locks) instead.`);

// 3. Applied Flyway migrations: a V*.sql that is already committed must never change.
if (/\/db\/migration\/V[^/]*\.sql$/.test(file) && existsSync(path.join(projectDir, file))) {
  let tracked = false;
  try { execFileSync("git", ["ls-files", "--error-unmatch", file], { cwd: projectDir, stdio: "ignore" }); tracked = true; } catch {}
  if (tracked)
    block(`Blocked: ${file} is a committed Flyway migration. Never edit it — create a new V<yyyyMMddHHmm>__*.sql migration instead.`);
}
ok();
