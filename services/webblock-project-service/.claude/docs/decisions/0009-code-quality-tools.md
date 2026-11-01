# 0009 Code quality tools

## Decision

Four checks find what Error Prone, Spotless, ArchUnit and JaCoCo do not. They are dev tools only: none is on an
application classpath or in the jar.

| Tool | Gradle task | Settings | Report |
| --- | --- | --- | --- |
| PMD 7.28.0 (Gradle `pmd` plugin) | `pmdMain`, `pmdTest` | `config/pmd/ruleset.xml` | `build/reports/pmd/` |
| CPD from PMD 7.28.0 | `cpd` | main code, 100 tokens, annotations ignored | `build/reports/cpd/cpd-main.xml` |
| SpotBugs 4.10.5, Gradle plugin 6.5.13 | `spotbugsMain` (`spotbugsTest` on request) | effort max, threshold medium, `config/spotbugs/exclude.xml` | `build/reports/spotbugs/` |
| javac `-Xlint:deprecation,removal` | every `compileJava`, `compileTestJava` | warnings only | the compile output |

- The PMD rule set holds only rules that point at real problems: unused private code, cognitive and NPath
  complexity, swallowed or lost errors, wrong comparisons, unclosed resources, thread mistakes and hard-coded crypto
  keys. No naming, layout or brace rule: Spotless owns the layout.
- PMD and SpotBugs are report-only (`ignoreFailures = true`) and stay out of `check` (`pmd.sourceSets = []`,
  `spotbugs.runOnCheck = false`). The `qa-sweep` skill runs them and triages each finding. A tool joins `check` only
  when it runs clean, through a new decision record.
- SpotBugs skips generated code. The build generates none today; the filter excludes classes Spring AOT marks
  `@Generated`. The filter never hides a finding in our own code.
- CPD has its own `cpd` configuration (pmd-java, pmd-ant, slf4j-simple 1.7.36): Gradle's `pmd` configuration leaves
  out SLF4J, which the CPD Ant task needs. SLF4J 1.7.36 with slf4j-simple is what the PMD 7.28.0 distribution ships.
- The tool configurations (`pmd`, `cpd`, `spotbugs`, `spotbugsPlugins`, `spotbugsSlf4j`) resolve the versions the
  tools declare. Without this, the Spring dependency-management plugin moves their libraries to Spring Boot's
  versions (log4j-core, gson, slf4j). They are locked in `gradle.lockfile`, verified in
  `gradle/verification-metadata.xml` and scanned by `scripts/osv-scan.sh`.
- No further tool. NullAway needs nullness annotations across the code and prints in every compile; Spring 7's
  JSpecify annotations already feed SpotBugs' null checks. No ArchUnit cycle rule: the module cycle check left with
  the feature modules (decision `0016`), and the layer rules state the direction. ArchUnit has no reliable rule for
  unused public code.

## Reason

The blocking gates miss whole classes of problems: copied code, unused private members, null dereferences on some
paths, unclosed resources, response splitting and deprecated calls. Report-only first means a person judges each
finding before any tool can block a build. Every tool is open source (PMD: BSD-style; SpotBugs: LGPL-2.1; SpotBugs
Gradle plugin: Apache-2.0; SLF4J: MIT), at least 14 days old, pinned in `gradle/libs.versions.toml` and recorded in
`.claude/docs/dependency-versions.md`.

Known limits: the SpotBugs plugin 6.5.13 calls `Configuration.setVisible`, which Gradle 11 removes; it works on
Gradle 9.8 and needs a plugin upgrade before Gradle 11. PMD prints a notice that it adds the running JDK's
`jrt-fs.jar` to its class path; that JDK is the toolchain's Java 25, so the notice is harmless.

## Status

Accepted.
