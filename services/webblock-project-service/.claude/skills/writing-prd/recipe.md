# Recipe for the Project requirement documents

The same recipe serves the BRD, PRD, FRD and TRD. `SKILL.md` next to this file says what is special about one
document; `template.md` gives its shape.

1. **Read the sources in this order.** Notion first, read-only (fetch with the Notion `notion-fetch` tool, never
   edit, comment on or move a page):
   - "2 · Project" https://app.notion.com/p/40058175966e81d4a77bfb48e736757a (tables, statuses, rules, roles);
   - "Web Block architecture" https://app.notion.com/p/40358175966e8198a088f123a9ab23fe (REST shape, gRPC,
     RabbitMQ, who talks to whom, the Java layout in 6.1);
   - "6 · Shared catalog" https://app.notion.com/p/40058175966e81ba92e8e5f6b3e4db2a, only for the catalog
     tables this service migrates and reads.

   Then the contracts in `.claude/docs/contracts/`: `authentication-contract.md` (sign-in and access; it ranks
   with Notion and wins over older records), `payment-contract.md`, `provisioning-contract.md` and
   `service-communication.md`. Then the decision records (`.claude/docs/decisions/README.md` lists them with their
   status; only Accepted ones bind) and any change design in progress (`.claude/docs/designs/`). Then code
   (`src/main/java/org/kshrd/webblock/project/`), plans (`.claude/docs/plans/`) and handoffs (`ai/handoffs/`).
   If a Notion page cannot be fetched, stop and tell the owner. If a path has moved, cite the path you actually
   read.
2. **Give every item a source** and list each one in the Sources table at the end:
   - `S1 <heading>` = Notion "2 · Project", `S2 §n` = Notion "Web Block architecture",
     `S3 <heading>` = Notion "6 · Shared catalog";
   - `AUTH §n` = the authentication contract, `PAY §n` = the Payment contract, `PROV §n` = the Provisioning
     contract, `COMM §n` = `service-communication.md`;
   - `0004` = a decision record by number (records have Decision, Reason and Status sections);
   - `Art. N` = an article of the constitution, `.claude/docs/CONSTITUTION.md`;
   - any other file by its path (designs, code, runbooks, handoffs).
   An item with no source becomes a question under "Questions for the owner".
3. **Write the requirement as the highest source says** (Notion and AUTH, then the other contracts and the
   decisions, then code). Under "Drift seen" list at most 7 disagreements (between sources, or between a source
   and the code), the ones that change what the owner decides, one line each with paths. The direction check
   (`DIRECTION.md`) keeps the full list.
4. **Ask each owner question once.** A question has: why it matters, and your suggested answer labelled
   "suggestion". When an upper document already asks it, write "see BRD Q-n" instead of asking again.
5. **Keep IDs stable forever.** Plans, tests and commits cite these IDs, so an ID keeps one meaning for good.
   If the file exists, update it in place: keep numbers, add new items at the end, retire an item with
   "Removed: <why>; see <new ID>" (no date; git history is the record). A changed meaning gets a new ID. Never
   renumber or reuse a number. Numbers run in one sequence across all sections of the document; in a new file,
   number the items in their final order before saving.
6. **Link every item up.** Read the upper IDs from the upper document in `.claude/docs/requirements/`. If that file
   is missing, say so in "In short", write `none` in the link column, and add a question. An item that serves no
   upper item gets `none (Q-n)`; Q-n asks whether the upper document needs a new item or this one should go.
7. **Plain English.** Short sentences. Prose lines at most 120 characters; table cells are short phrases. Commas,
   colons or brackets instead of em or en dashes. Project words go in the Glossary (skip basics like API or
   table). Current facts only: no "Last updated", no change log, no "(owner, <date>)" notes.
8. **Fill every slot of `template.md`, in order.** At most one small Mermaid diagram.
9. **Check before saving:**
   - "In short" has at most 5 sentences a non-engineer understands.
   - Every item row has its upward link and its own source, written out (not "same" or "as above").
   - `grep -nP '[\x{2013}\x{2014}]' <file>` prints nothing.
   - `wc -w <file>` is within the limit in `SKILL.md`.
   - "Read next" links the next document.
