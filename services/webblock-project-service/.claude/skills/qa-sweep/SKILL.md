---
name: qa-sweep
description: Use when running the code quality sweep of the Project service (PMD, CPD duplicate code, SpotBugs,
  deprecated APIs, compiler and Error Prone warnings, coverage gaps, IDE inspections): once per phase before the
  final review, before a dependency upgrade, or when the lead asks for a QA report. Triages; never fixes code.
---

# Code quality sweep (Java)

The sweep finds what the blocking gates do not: copied rules, code nothing calls, deprecated calls, warnings that
do not fail the build, and code no test reaches. It is not a gate: a person judges each finding. You write a
report and turn real problems into tasks. You never edit `src/`, `build.gradle`, `gradle/`, `scripts/` or
`.claude/`, and you never commit. Every command below writes only under `build/`.

## 1. What the build already gives

| Check | How it runs | Blocks the build? |
| --- | --- | --- |
| Error Prone (default checks, incl. `UnusedVariable`, `UnusedMethod`) | inside `compileJava` and `compileTestJava` | errors only; warnings are printed |
| javac `-Xlint:deprecation,removal` | every compile (`build.gradle`) | no, warnings only |
| PMD 7.28.0, rules in `config/pmd/ruleset.xml` | `./gradlew pmdMain pmdTest` | no, report-only, not in `check` |
| CPD (duplicate code, 100 tokens, main code) | `./gradlew cpd` | no, report-only, not in `check` |
| SpotBugs 4.10.5 (effort max, threshold medium) | `./gradlew spotbugsMain` | no, report-only, not in `check` |
| Spotless (palantir-java-format) | `./gradlew spotlessCheck` | yes, in `check` |
| ArchUnit rules | `./gradlew test` (`architecture/`) | yes |
| JaCoCo line coverage rules | `./gradlew check` (`jacocoTestCoverageVerification`) | yes |

Why the tools are report-only and what each covers: decision `0009`. Changing a tool, its rules or its filter is not
your job: report the wish as an owner question.

## 2. Run the tools

From the repo root. Gradle writes only under `build/`; never redirect output into a file outside `ai/reviews/`.

```sh
./gradlew compileJava compileTestJava --rerun-tasks 2>&1 | grep -E ': warning: ' | sort -u
./gradlew spotlessCheck test
./gradlew pmdMain pmdTest spotbugsMain cpd
```

The first line prints Error Prone warnings (`[Name]`) and javac `[deprecation]` and `[removal]` warnings.

Count and list the findings from the reports:

```sh
python3 -I -c 'import xml.etree.ElementTree as E
ns = {"p": "http://pmd.sourceforge.net/report/2.0.0"}
for f in ("main", "test"):
    r = E.parse(f"build/reports/pmd/{f}.xml").getroot()
    v = [(x.get("name"), y) for x in r.findall("p:file", ns) for y in x.findall("p:violation", ns)]
    print("PMD", f, len(v), "errors", len(r.findall("p:error", ns)))
    for n, y in v: print(" ", y.get("rule"), n.split("/project/", 2)[-1] + ":" + y.get("beginline"))
r = E.parse("build/reports/spotbugs/main.xml").getroot()
print("SpotBugs", len(r.findall("BugInstance")), r.find("Errors").attrib)
for b in r.findall("BugInstance"):
    s = b.find("SourceLine")
    print(" ", b.get("type"), b.find("Class").get("classname"), s.get("start") if s is not None else "")
c = E.parse("build/reports/cpd/cpd-main.xml").getroot()
q = "{https://pmd-code.org/schema/cpd-report}"
print("CPD files", len(c.findall(q + "file")), "duplications", len(c.findall(q + "duplication")))'
```

A PMD `error` or a SpotBugs `errors` or `missingClasses` above 0 means the tool did not read every file: report
it. CPD lists every file it scanned, so 0 duplications with all main files listed is a real result. For each CPD
duplication, read both places: a copied rule, query or parser is real; repeated mapping boilerplate may not be.

Then read the classes of each feature side by side where rules tend to be copied below the CPD threshold:
validation in `application/<feature>/request` and `application/<feature>/handler`, cursor classes
(`*Cursor.java`), SQL in `infrastructure/persistence/<feature>`, and constraint-to-code maps in
`infrastructure/config/<feature>`.

Dead code: Error Prone's `UnusedVariable` and `UnusedMethod` and PMD's `Unused*` rules cover private members only.
For public members, use the IDE (section 3) or search each candidate:
`rg -n '\b<name>\b' src/main/java src/test/java`. Code no test reaches is a candidate too; only when the lead asks
for coverage (it runs both suites and needs Docker and a free slot, `project-gates` skill):

```sh
./gradlew jacocoTestReport
python3 -I -c 'import sys, xml.etree.ElementTree as E
for c in E.parse(sys.argv[1]).getroot().iter("class"):
    for k in c.findall("counter"):
        if k.get("type") == "LINE" and k.get("covered") == "0": print(c.get("name"), k.get("missed"), "lines")' \
  "$(find build/reports/jacoco -name '*.xml' | head -1)"
```

## 3. IDE inspections (IntelliJ IDEA or PyCharm, through the `pycharm` MCP server)

The server is configured at user level. Never add a project `.mcp.json` for it.

1. Call `mcp__pycharm__get_project_modules` with `projectPath` the repo root. If it answers that the path "doesn't
   correspond to any open project", stop this part: "IDE does not have this project open; inspections not run".
   Do not use `mcp__pycharm__get_python_environment`: it is for Python projects only.
2. Java needs an IDE that indexes Java (IntelliJ IDEA, or an IDE with the Java plugin). Probe one file:
   `mcp__pycharm__get_file_problems` on `src/main/java/org/kshrd/webblock/project/WebBlockApplication.java`. If
   the answer says the file was not analyzed or the type is unsupported, stop this part and say so.
3. Otherwise call `mcp__pycharm__lint_files` with `min_severity` `warning`, a timeout of at least 120000 ms, and
   batches of about 40 project-relative paths: the branch's changed files
   (`git diff --name-only main...HEAD -- src`) or, for a full sweep, `git ls-files 'src/*.java'`. Retry a batch
   that returns `more: true` or files with `timedOut: true`, with fewer files.
4. If the tools are missing or the server does not answer, write "IDE MCP not connected; inspections not run".
   Never report IDE inspections as passed when they did not run.

## 4. Triage every finding

Put each finding in exactly one class:

- **Real problem**: it breaks a repo rule (`.claude/rules/java-code.md`) or an article of the constitution
  (`.claude/docs/CONSTITUTION.md`). It becomes an implementer task (section 6).
- **False positive**: Spring, JPA or Jackson uses it by reflection (controller methods, `@Bean` methods, entity
  constructors, record components read by Jackson, `package-info`), or the tool is wrong. Say why.
- **Owner ruling**: fixing it changes a decision, a contract, or which code is kept. Ask, with your
  recommendation.

Rules of thumb here: enum values that no code names may still be stored values (Notion "2 · Project" enums;
check the migrations before calling one dead). Repeated `@Column` attribute text and repeated property keys are
usually false positives; a repeated rule (a regex, a limit, an SQL condition) is real. A deprecated call in a test
is real but small. A warning in code the branch did not touch is reported, not fixed in the branch.

Tool notes for this repo. SpotBugs `EI_EXPOSE_REP2` on a Spring bean that keeps an injected collaborator is a false
positive; on a record holding a `List`, check whether every caller passes an immutable list. `CT_CONSTRUCTOR_THROW`
is a finalizer attack, which needs a subclass and finalization: usually a false positive. `HRS_*` and other
`SECURITY` findings are read line by line, never dismissed in bulk. PMD `PreserveStackTrace` is deliberate when the
code drops a cause that could echo customer input, a token file or an upstream body (the comment says so). PMD
`CloseResource` on an `ExecutorService` shut down in `finally`, or on a shared Testcontainers container, is a
false positive.

Find the IDs each finding touches: the FR in the test of that code, else the TR of the layer, else P-n.

## 5. Write the report

`ai/reviews/qa-<branch>.md` (`git branch --show-current`). Plain English, no em or en dashes, lines at most 120
characters, no dates.

```
# QA sweep: <branch> at <short sha>
Stage: 5 Verify. IDs: <FR, TR and P IDs the findings touch>
## Tools: table of step, command, exit, findings (compile warnings, spotless, tests, PMD, CPD, SpotBugs,
   coverage, IDE)
## Summary: counts per class (real problem, false positive, owner ruling)
## Findings: table of #, tool, file:line, what, class, IDs, reason
## Tasks: numbered implementer tasks for the real problems
## Owner questions: numbered, each with a recommendation
## Not checked: what the tools cannot see (for example IDE inspections not run, public dead code, CPD on tests)
```

## 6. Turn real problems into tasks

Each task names its FR or TR IDs, the files, the failing test (or the test to delete with dead code), and the
acceptance check (for example "the warning is gone from the `compileJava` output; `./gradlew test` passes"). Group
small findings of one feature into one task. A change that alters a rule goes to stage 1 instead.

## Never

- Edit code, tests, the build or the configuration; add a tool or plugin to the build; commit; push.
- Suppress a warning, lower a gate or delete a test to make a finding go away.
- Change a cluster, open `~/.kube`, a kubeconfig or `.env`, or print a secret value.
