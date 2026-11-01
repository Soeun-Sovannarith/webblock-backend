// PreToolUse (Bash): stop dangerous or irreversible shell commands. Not a full sandbox — a safety net.
import { readInput, block, ok } from "./lib.mjs";

const cmd = (readInput().tool_input?.command || "").replace(/\s+/g, " ");
const rules = [
  [/\bgit push\b.*(--force\b|-f\b|--force-with-lease)/, "force push is not allowed. Push normally or ask the user."],
  [/\bgit push\b.*\b(main|master|develop|dev)\b/, "don't push straight to main/master/develop/dev. Open a branch + PR."],
  [/\bgit reset --hard\b|\bgit clean -[a-z]*f/, "this throws away work. Ask the user first."],
  [/\brm -rf? +(\/|~|\$HOME|\.\.?)(\s|$)/, "refusing to delete a root/home/project directory."],
  [/\bdocker(-| )compose\b.*\bdown\b.*(-v\b|--volumes)/, "`down -v` deletes all local databases. Ask the user first."],
  [/\bdocker (system|volume) prune\b/, "prune deletes data. Ask the user first."],
  [/\bkubectl\b.*\bdelete\b.*\b(ns|namespace|namespaces|pv|pvc)\b/, "deleting namespaces/volumes is destructive. Ask the user first."],
  [/\bkubectl\b.*--context[= ]\S*prod/, "no direct kubectl on production. Use GitOps (Argo CD)."],
  [/\bflyway(Clean| clean)\b|flyway\.clean/, "flyway clean wipes the database."],
  [/\b(drop|truncate) (database|schema|table)\b/i, "destructive SQL. Write a migration or ask the user."],
  [/(^|[;&|]\s*)(printenv|env)\s*$/, "printing all env vars can expose secrets."],
  // added with the services/ + contracts/ setup: protect other people's uncommitted work and the machine
  [/\bgit (checkout|restore)\b[^|;&]*(\s--\s+\.|\s\.)(\s|$)/, "this overwrites every uncommitted change in the folder, including other people's. Ask the user first."],
  [/\bgit stash\b(?!\s+(list|show)\b)/, "stashing hides other people's uncommitted work. Ask the user first."],
  [/\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b/, "never pipe a download into a shell. Download, read, then run."],
  [/\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+[^|;&]*\b(services|contracts|commons|docs|\.claude|\.git)\b\/?(\s|$)/, "deleting a top-level project folder. Ask the user first."],
  [/\bchmod\s+-R\s+0?777\b/, "world-writable permissions are a security risk."],
  [/\bdocker (run|create)\b[^|;&]*(--privileged|-v\s+\/:|--pid=host|--net(work)?=host)/, "privileged / host-mounting containers are not allowed here."],
  [/\bnpm publish\b|\bgradle(w)?\b[^|;&]*\bpublish\b|\btwine upload\b/, "publishing a package is outward-facing. Ask the user first."],
  [/\bgit (config|remote)\b[^|;&]*(--global|set-url|add)\b/, "changing git identity or remotes. Ask the user first."],
  [/\bgit -c\s+(core\.hooksPath|commit\.gpgsign)|--no-verify\b|--no-gpg-sign\b/, "do not skip hooks or signing. Fix the cause."],
];
for (const [re, why] of rules) if (re.test(cmd)) block(`Blocked: ${why}\nCommand: ${cmd}`);

// Any command that names a real .env file (".env", ".env.local" …) can expose or change secrets,
// whatever the tool (cat, python, cp, type, Get-Content, base64 …). ".env.example" / ".env.sample" is fine.
// Only git and plain existence checks may mention it.
const envFiles = cmd.match(/(?:^|[\s/\\'"=])\.env(?:\.[\w-]+)?(?=$|[\s'";|&])/g) || [];
if (envFiles.some(f => !/\.(example|sample|template)$/.test(f.trim())) && !/^(git|ls|test|\[) /.test(cmd))
  block(`Blocked: .env holds secrets; only the user reads or edits it. Use .env.example to see variable names.\nCommand: ${cmd}`);
// Same for key files, keystores and Keycloak realm exports
if (/\b(cat|less|more|head|tail|bat|grep|sed|awk|strings|xxd|base64|type|Get-Content|gc|cp|copy|mv|scp)\b[^|;&]*(\.(pem|key|p12|jks|keystore)\b|realm-export[\w.-]*\.json|\bsecrets\/)/i.test(cmd))
  block(`Blocked: this file can hold credentials. Ask the user to read it.\nCommand: ${cmd}`);
ok();
