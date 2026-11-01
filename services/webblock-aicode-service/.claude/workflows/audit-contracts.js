export const meta = {
  name: 'audit-contracts',
  description: 'Whole-repo audit that every cross-file contract (components, enums, events, routes, settings, pipeline markers, .claude docs) is consistent right now',
  whenToUse: 'Periodically, after a large merge, or before a release; not diff-based',
  phases: [
    { title: 'Audit', detail: 'one auditor per contract' },
    { title: 'Verify', detail: 'skeptic per mismatch' },
  ],
}

const MISMATCHES = {
  type: 'object',
  properties: {
    consistent: { type: 'boolean' },
    mismatches: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          side_a: { type: 'string', description: 'file:line and what it says' },
          side_b: { type: 'string', description: 'file:line and what it says' },
          impact: { type: 'string' },
        },
        required: ['side_a', 'side_b', 'impact'],
      },
    },
  },
  required: ['consistent', 'mismatches'],
}
const VERDICT = {
  type: 'object',
  properties: { real: { type: 'boolean' }, reason: { type: 'string' } },
  required: ['real', 'reason'],
}

const CONTRACTS = [
  { key: 'components', check: 'every key of CATALOG in app/services/catalog.py has app/templates/site/components/blocks/<Key>.tsx with matching props, and a row in ai_codegen.puck_component_definitions as seeded/updated by sql/*.sql (editable_props_schema incl. STYLE_PROPS); ROOT_PROPS/STYLE_PROPS options match theme.ts/theme.css data-* handling' },
  { key: 'enums', check: 'every UPPERCASE status/strategy/type string written by Python (store.py, graph_pipeline.py, pipeline.py, assistant.py, harness.py, schemas) exists in the matching Postgres enum in sql/*.sql, and vice versa for values Python reads' },
  { key: 'events', check: 'app/schemas/events.py models and event_bus.py routing keys match README.md section 4 examples field-for-field in camelCase, and tests/test_event_bus.py covers each' },
  { key: 'routes', check: 'every @router route (with prefixes from app/main.py) is documented in README.md section 6 or 3 with the right method, path, body fields and status code, and no documented route is missing from code' },
  { key: 'settings', check: 'every Settings field in app/core/config.py has a commented line in .env.example and an entry in README.md section 2, and no documented variable is missing from Settings' },
  { key: 'pipeline', check: 'step markers written by store.set_step in graph_pipeline.py, SKIPPED: lines, StepError codes and the graph in build_pipeline_graph() match README.md section 1.2 and .claude/rules/python-app.md and .claude/skills/add-pipeline-step' },
  { key: 'claude-docs', check: 'every file path, function, setting and test named in .claude/CLAUDE.md, .claude/rules/*.md, .claude/skills/*/SKILL.md and .claude/agents/*.md still exists in the code (grep each)' },
]

const MAX_VERIFY = 3
const results = await pipeline(
  CONTRACTS,
  c => agent(`Audit this repo (read-only): check that ${c.check}. Read the code; quote file:line for both sides of every mismatch.`,
    { label: `audit:${c.key}`, phase: 'Audit', schema: MISMATCHES, agentType: 'Explore' }),
  (res, c) => {
    const found = (res && res.mismatches) || []
    if (found.length > MAX_VERIFY) log(`${c.key}: ${found.length - MAX_VERIFY} further mismatches not verified (cap ${MAX_VERIFY})`)
    return parallel(found.slice(0, MAX_VERIFY).map((m, i) => () =>
    agent(`Claimed contract mismatch (${c.key}):\nA: ${m.side_a}\nB: ${m.side_b}\nImpact: ${m.impact}\n\nOpen both locations and decide whether this is a real inconsistency today. real=false if either side is misquoted or the difference is intentional and documented.`,
      { label: `verify:${c.key}#${i + 1}`, phase: 'Verify', schema: VERDICT, effort: 'medium' })
      .then(v => ({ contract: c.key, ...m, verdict: v }))))
  },
)

const all = results.flat().filter(Boolean)
const real = all.filter(m => m.verdict && m.verdict.real)
log(`${real.length} confirmed mismatches across ${CONTRACTS.length} contracts`)
return { confirmed: real, clean: CONTRACTS.map(c => c.key).filter(k => !real.some(m => m.contract === k)) }
