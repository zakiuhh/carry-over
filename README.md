<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.png">
    <img alt="Carry-over logo" src="assets/logo-light.png" width="140">
  </picture>
</p>

<h1 align="center">Carry-over</h1>

<p align="center">
  A skill for any agent that reads <code>SKILL.md</code>: hand a long conversation off to a fresh one
  without losing decisions, and without inventing what was never said.
</p>

---

## The problem

Long chats hit limits ("this conversation is too long to continue"), and the usual fix, *"summarize this so I can paste it in a new chat"*, fails in a predictable way. The summary is written from a fuzzy memory of the conversation, so:

- decisions blur and get reopened
- dead ends get retried
- guesses read like facts
- you lose track of what was never actually settled

## What carry-over does differently

| Idea | How |
|---|---|
| **Built from the source** | Re-reads the actual transcript (or the visible conversation), not a memory of it |
| **Every item is tagged** | `[V]` verified, `[I]` inferred, `[U]` unknown, so nothing guessed passes as fact |
| **Every fact has proof** | Each `[V]` carries a short exact quote: `(src: "...")` |
| **Quotes can be checked** | A bundled script confirms each quote really appears in the transcript |
| **Resume mode** | In the new chat it restates the plan, flags what's shaky, and confirms the first step before working |
| **Works anywhere** | The packet carries its own instructions, so it works even if the next session doesn't have this skill |
| **Picks its own format** | A paste-ready message when it can only chat; a `HANDOFF.md` plus a short starter message when it can make files |
| **Only when asked** | It never offers or starts a handoff on its own |

Several handoff skills exist already, mostly for coding agents with hooks, files, and terminals. Carry-over's angle is verification and the resume protocol, with graceful degradation down to "chat only".

## What goes in a packet

Goal · **PICK UP HERE** (one line) · Current state · Decisions *and why* · Dead ends *and why* · Files and artifacts · Preferences and constraints · Open questions · Next steps · Risks and gotchas · Check before relying on this · Glossary · Verification

A short note (about 5 sections) exists for tiny sessions.

## Example

```
## Decisions
- [V] Use Alembic for the schema and a custom copy script for data.
      (src: "use Alembic for the schema and a custom copy script for data")

## Dead ends
- [V] pgloader failed because the tags column stores JSON as plain text; do not retry.
      (src: "it choked on the tags column")

## Preferences and constraints
- [I] Prefers short plans over long design discussion. (basis: terse replies throughout)
```

See [`references/examples.md`](references/examples.md) for a full conversation, its packet, and what the new session says on resume.

## Using it

**Pack:** in the old chat, say *"carry this over"*, *"hand off this chat"*, or *"I'm running out of context, continue in a new chat"*.

**Resume:** in a new chat, paste the packet (or attach `HANDOFF.md`) and say *"resume"*.

If the old chat is already too full to continue, paste an exported copy of it into a fresh chat and run Pack there.

## The checker

```bash
python3 scripts/check_carryover.py HANDOFF.md --transcript chat.txt --strict
python3 scripts/check_carryover.py --find        # list likely transcript files
```

Needs Python 3 and nothing else. It accepts `.txt`, `.md`, `.json`, and `.jsonl` transcripts.

| Checks | Result |
|---|---|
| Required sections present and non-empty; exactly one PICK UP HERE | error |
| Every `[V]` has a quote | error |
| Quote not found in the transcript | error |
| Quote only a near match (not verbatim) | warning |
| Secrets (API keys, tokens, private keys, passwords) | error |
| Leftover placeholders, header counts that don't match, oversized packets | warning or error |

Quotes are searched only in the part of the transcript that comes **before** the packet's own `id:` line, so a packet can't "prove" itself by quoting itself.

**What a pass means:** the quotes exist. It does **not** prove the packet is complete, or that a quote truly supports the claim next to it. A person, or the next session, still has to read it.

## Install

### Claude (web and apps)

1. Download this repo as a ZIP (or zip the `carry-over` folder yourself). Make sure `SKILL.md` sits at the top level of the folder inside the ZIP.
2. Open Claude's settings and find the Skills section.
3. Upload the ZIP and turn the skill on.

### Claude Code

```bash
git clone https://github.com/zakiuhh/carry-over.git
cp -r carry-over ~/.claude/skills/
```

(Change the clone URL if your repo lives somewhere else.)

### Other agents

Any agent that supports the shared `SKILL.md` format: copy the `carry-over` folder into that agent's skills directory (check its documentation for the location).

## Repo structure

```
carry-over/
├── assets/
│   ├── logo-dark.png          # for dark backgrounds
│   └── logo-light.png         # for light backgrounds
├── references/
│   └── examples.md            # worked example and common mistakes
├── scripts/
│   └── check_carryover.py     # quote and structure checker
├── templates/
│   └── packet.md              # the packet structure
├── LICENSE                    # MIT
├── README.md
└── SKILL.md                   # the actual skill
```

## Limits (honest ones)

- The skill is instructions. An agent can still ignore them or tag something `[V]` wrongly. The checker catches missing and fabricated quotes, not misleading ones.
- If an agent can't see the full conversation (earlier parts compacted away), the packet says so; items known only from a summary are tagged `[I]`.
- Transcript locations differ across tools and versions. `--find` looks in common places, but you may need to export or paste the conversation yourself.

## License

MIT. See [LICENSE](LICENSE).
