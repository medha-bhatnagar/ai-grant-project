# The AI: the Judge and its LLMs

```
agents/
  judge.json        the Judge: talks to the user and decides which LLM to ask
  settings.json     database address, reply style (emoji + short bullets)
  llms/             one file per LLM; the file name is the LLM's name
    budget.json  compliance.json  database.json  formatting.json  past_grants.json
    requirements.json  submission.json  writing.json
    _TEMPLATE.json  copy this to make a new one (files starting with _ are ignored)
```

Changes apply to the **next chat message**. There's no restart or rebuild.

## Add an LLM

1. Copy `llms/_TEMPLATE.json` and rename it, for example `llms/biosketch.json`.
2. Fill in:
   - `description`: **when** the Judge should ask it. The Judge picks LLMs by this, so be specific.
   - `instructions`: what it should do, one sentence per line.
   - `model`: `claude-opus-5-5`.
3. Ask the chat something that matches the description.

## Remove an LLM

Delete its file. To switch it off but keep it, rename it to start with `_` (`_budget.json`).

## Change an LLM

Edit its `instructions` or `description`.

## Options in an LLM file

| Option | What it does |
| --- | --- |
| `"database": true` | can read the `grants` database (requests, budgets, deadlines…) |
| `"needs_request": true` | asks "which request?" when none is named |
| `"documents_only": true` | gets only the grant's own text (for summaries) |
| `"library": true` | can read the team's past grants (read only) |

If the chat stops working after an edit, the error names the file and line. It's usually a missing
comma or quote.
