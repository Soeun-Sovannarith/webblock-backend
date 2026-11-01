# Recipe for the Provisioning requirement documents

The same recipe serves the BRD, PRD, FRD and TRD. `SKILL.md` next to this file says what is special about one
document; `template.md` gives its shape.

1. **Read the sources in this order.** Notion first, read-only (fetch with the Notion `notion-fetch` tool, never edit):
   "5 · Provisioning" https://app.notion.com/p/40058175966e81769596d1ebcadb333d and "Web Block architecture"
   https://app.notion.com/p/40358175966e8198a088f123a9ab23fe. For sign-in and access rules, the authentication contract
   (`../project-service/.claude/docs/contracts/authentication-contract.md`) ranks with Notion and wins over older
   records. Then the decision records that matter here (`.claude/docs/decisions/README.md` lists titles and which are
   Current), then contracts (`src/contracts/`), then code, plans and handoffs. If a Notion page cannot be fetched, stop
   and tell the owner. If a path has moved, cite the path you actually read.
2. **Give every item a source** and list each one in the Sources table at the end:
   - `S1 <heading>` = Notion "5 · Provisioning", `S2 §n` = Notion "Web Block architecture";
   - `AUTH §n` = the authentication contract;
   - `0023 §4` or `0023 G9` = a decision record by number and its section or rule;
   - `OD-n` = an entry of `.claude/docs/decisions/open-decisions.md`;
   - any other file by its path (contracts, code, runbooks, handoffs).
   An item with no source becomes a question under "Questions for the owner".
3. **Write the requirement as the highest source says** (Notion and AUTH, then decisions, then code). Under "Drift seen"
   list at most 7 disagreements (between sources, or between a source and the code), the ones that change what
   the owner decides, one line each with paths. The direction check (`DIRECTION.md`) keeps the full list.
4. **Ask each owner question once.** A question has: why it matters, and your suggested answer labelled
   "suggestion". When an upper document already asks it, write "see BRD Q-n" instead of asking again.
5. **Keep IDs stable forever.** Plans, tests and commits cite these IDs, so an ID keeps one meaning for good.
   If the file exists, update it in place: keep numbers, add new items at the end, retire an item with
   "Removed: <why>; see <new ID>" (no date; git history is the record). A changed meaning gets a new ID. Never
   renumber or reuse a number. Numbers run in one sequence across all sections of the document; in a new file,
   number the items in their final order before saving.
6. **Link every item up.** Read the upper IDs from the upper document in `.claude/docs/requirements/`. If that file is
   missing, say so in "In short", write `none` in the link column, and add a question. An item that serves no
   upper item gets `none (Q-n)`; Q-n asks whether the upper document needs a new item or this one should go.
7. **Plain English.** Short sentences. Prose lines at most 120 characters; table cells are short phrases. Commas,
   colons or brackets instead of em or en dashes. Project words go in the Glossary (skip basics like API or
   table).
8. **Fill every slot of `template.md`, in order.** At most one small Mermaid diagram.
9. **Check before saving:**
   - "In short" has at most 5 sentences a non-engineer understands.
   - Every item row has its upward link and its own source, written out (not "same" or "as above").
   - `grep -nP '[\x{2013}\x{2014}]' <file>` prints nothing.
   - `wc -w <file>` is within the limit in `SKILL.md`.
   - "Read next" links the next document.
