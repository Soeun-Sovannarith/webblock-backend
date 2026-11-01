// SessionStart: give Claude a short picture of the current work (stdout is added to context).
import { execFileSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { projectDir } from "./lib.mjs";

const git = (...a) => { try { return execFileSync("git", a, { cwd: projectDir, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim(); } catch { return ""; } };
const branch = git("rev-parse", "--abbrev-ref", "HEAD") || "(no git)";
const changed = git("status", "--porcelain", "-uall").split("\n").filter(Boolean).map(l => l.slice(3));
const areas = [...new Set(changed.map(f => f.match(/^(services\/[^\/]+|contracts|commons|config-repo|apps\/[^/]+|packages\/[^/]+|templates\/[^/]+|infra|docs)/)?.[1]).filter(Boolean))];

let openQ = 0;
const oq = path.join(projectDir, "docs/specs/open-questions.md");
if (existsSync(oq)) openQ = readFileSync(oq, "utf8").split("\n").filter(l => /^\|\s*\d+\s*\|/.test(l) && !/\|\s*(Done|Closed|Decided)/i.test(l)).length;

console.log([
  `WebBlock session — branch: ${branch}`,
  `Uncommitted changes in: ${areas.length ? areas.join(", ") : "nothing"}`,
  openQ ? `Open spec questions: ${openQ} (docs/specs/open-questions.md) — ask before guessing.` : "",
  "Reminder: reproduce before fixing; run the touched service's tests before saying done.",
].filter(Boolean).join("\n"));
