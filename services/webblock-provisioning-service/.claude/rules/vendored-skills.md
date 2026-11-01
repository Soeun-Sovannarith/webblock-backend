# Vendored skills: local rules win

The skills copied from other projects (each has a `SOURCE.txt`) were written for any repo. In this repo these rules
win over their text:

- Never switch branches in the shared working tree (no `git checkout <baseline>`). To look at an older version use
  `git show <ref>:<path>`, `git diff <ref>...HEAD`, or a separate `git worktree` under `.claude/worktrees/`.
- Write every report under `ai/reviews/`, never on the Desktop or in `~/.claude`.
- Cluster commands only against the local Kind cluster (CLAUDE.md section 0). Lines in a vendored skill that run
  `kubectl` or `helm install` against the default kubeconfig are examples, not steps; use `make chart-check` and
  the `make proto-*` targets instead.
- Agents a vendored skill names (for example `differential-review:adversarial-modeler`) may not exist here; do
  those steps yourself.
