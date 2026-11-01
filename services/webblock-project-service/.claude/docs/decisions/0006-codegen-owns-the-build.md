# 0006 AI Codegen owns the build

## Decision

AI Codegen guarantees that generated code builds: its harness generates and builds in a sandbox, and a run reported
`succeeded` has built. Project does not re-run install, lint, typecheck or `next build`. Before it commits, Project
checks only that overlay files stay inside `allowedPaths`, that the size limits hold, and that no secret is present.
These checks never run generated code. Provisioning's Jenkins build stays the last build gate.

## Reason

Building twice costs minutes and needs a sandbox in Project. The checks Project keeps protect what it commits: the
scaffold's backend wiring, `package.json`, `Dockerfile` and health endpoints stay as the scaffold wrote them.

## Status

Accepted. Not built yet (the scaffold and Codegen exchange come in a later phase).
Details: `.claude/docs/contracts/service-communication.md`, section 2.6.
