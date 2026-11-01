export const meta = {
  name: 'review-branch',
  description: 'Multi-lens review of the current branch (contracts, security, tests, docs) with adversarial verification of every finding',
  whenToUse: 'Before opening a PR for a multi-file change; args: optional git range (default "main...HEAD")',
  phases: [
    { title: 'Review', detail: 'four lenses over the same diff, in parallel' },
    { title: 'Verify', detail: 'one skeptic per finding tries to refute it' },
  ],
}

const RANGE = typeof args === 'string' && args.trim() ? args.trim() : 'main...HEAD'
const DIFF = `the changes in \`git diff ${RANGE}\` plus uncommitted changes (\`git diff\`, \`git status\`)`

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'integer' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          summary: { type: 'string' },
          failure_scenario: { type: 'string' },
          evidence: { type: 'string' },
        },
        required: ['file', 'severity', 'summary', 'failure_scenario', 'evidence'],
      },
    },
  },
  required: ['findings'],
}
const VERDICT = {
  type: 'object',
  properties: { refuted: { type: 'boolean' }, reason: { type: 'string' } },
  required: ['refuted', 'reason'],
}

const LENSES = [
  { key: 'contracts', agentType: 'contract-reviewer',
    prompt: `Review ${DIFF} for broken cross-file and cross-service contracts.` },
  { key: 'security', agentType: 'security-reviewer',
    prompt: `Review ${DIFF} for security regressions at the LLM-codegen boundary, the build sandbox, SQL, inputs and secrets. If the diff touches none of these, return no findings.` },
  { key: 'tests', agentType: 'Explore',
    prompt: `Review ${DIFF}: which changed behaviours have no test in tests/ (offline, monkeypatch at the boundary per .claude/rules/tests.md), and which existing tests were weakened? Report only concrete, testable gaps with file:line of the untested code.` },
  { key: 'docs', agentType: 'Explore',
    prompt: `Review ${DIFF}: does README.md (sections 2/3/4/6 and 1.2), .env.example and .claude/rules|skills still match the changed code? Report only concrete mismatches, citing both the code and the doc location.` },
]

const MAX_VERIFY = 4
const results = await pipeline(
  LENSES,
  l => agent(l.prompt, { label: `review:${l.key}`, phase: 'Review', schema: FINDINGS, agentType: l.agentType }),
  (review, l) => {
    const found = (review && review.findings) || []
    if (found.length > MAX_VERIFY) log(`${l.key}: ${found.length - MAX_VERIFY} lower-ranked findings not verified (cap ${MAX_VERIFY})`)
    return parallel(found.slice(0, MAX_VERIFY).map((f, i) => () =>
      agent(
        `A reviewer claims a ${f.severity} issue in this repo at ${f.file}${f.line ? ':' + f.line : ''}:\n"${f.summary}"\n` +
        `Failure scenario: ${f.failure_scenario}\nEvidence given: ${f.evidence}\n\n` +
        `Try to REFUTE it: read the code, follow the call path, check whether the scenario can really happen. ` +
        `Set refuted=true if it is wrong, already handled elsewhere, or you cannot confirm it.`,
        { label: `verify:${l.key}#${i + 1}`, phase: 'Verify', schema: VERDICT, effort: 'medium' },
      ).then(v => ({ ...f, lens: l.key, verdict: v })),
    ))
  },
)

const all = results.flat().filter(Boolean)
const order = { critical: 0, high: 1, medium: 2, low: 3 }
const confirmed = all.filter(f => f.verdict && !f.verdict.refuted).sort((a, b) => order[a.severity] - order[b.severity])
log(`${confirmed.length} confirmed of ${all.length} verified findings`)
return { range: RANGE, confirmed, refuted: all.filter(f => f.verdict && f.verdict.refuted).map(f => ({ file: f.file, summary: f.summary, reason: f.verdict.reason })) }
