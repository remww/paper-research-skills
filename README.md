# Modular Paper Research Skills

Three independent Agent Skills for scholarly literature work. Install only the
capabilities needed by the current project, or combine them for an end-to-end
search, conversion, and deep-reading workflow.

| Skill | Purpose | MinerU required |
|---|---|---|
| `literature-search` | Search Semantic Scholar and arXiv, deduplicate, screen, fetch sources, and prepare papers for agent reading | No |
| `mineru-pdf` | Convert a local PDF with MinerU into Markdown and images at a caller-selected destination | Yes |
| `paper-deep-read` | Generate a question-specific reading plan and note structure, deeply read a paper, and preserve evidence for later research | No |

There is no required bundle skill. Every skill works independently. Installing
multiple skills gives an agent those capabilities without coupling their storage
or workflow.

## Install from GitHub

```bash
# See the available skills first
npx skills add remww/paper-research-skills --list

# Search and screening only
npx skills add remww/paper-research-skills --skill literature-search

# MinerU PDF conversion only
npx skills add remww/paper-research-skills --skill mineru-pdf

# Adaptive deep reading and reusable research notes only
npx skills add remww/paper-research-skills --skill paper-deep-read

# Any combination, including all three
npx skills add remww/paper-research-skills \
  --skill literature-search \
  --skill mineru-pdf \
  --skill paper-deep-read
```

Install globally with `-g`, target a specific agent with `-a`, or use `--copy`
when symlinks are unsuitable. The same repository also works through a full
GitHub URL or a direct `tree/main/skills/<name>` URL.

The commands above use the open Agent Skills CLI documented at
<https://github.com/vercel-labs/skills>. Each skill is also a standard directory
with `SKILL.md`, so Pi, Claude Code, Codex, Cursor, and other compatible agents
can discover it without the CLI.

## Use without installing

```bash
npx skills use remww/paper-research-skills --skill literature-search --agent claude-code
npx skills use remww/paper-research-skills --skill mineru-pdf --agent codex
npx skills use remww/paper-research-skills --skill paper-deep-read --agent codex
```

## Runtime requirements

### literature-search

- Python 3
- Network access
- `S2_API_KEY` for Semantic Scholar channels
- No third-party Python package
- arXiv search and fetch work without an API key

Search data defaults to `.litsearch` in the current project, but every command
accepts `--workspace <path>`.

### mineru-pdf

- Python 3
- A working `mineru` executable
- MinerU's own model, GPU, and environment requirements for the selected backend

The caller must pass `--output-dir`. The skill does not create a research ledger,
choose a project folder, or write a JSON record unless `--record` is requested.

### paper-deep-read

- Any readable primary paper source
- No required script, API key, PDF converter, or project layout
- The agent generates 3-7 focus questions and a matching note structure from the
  user's current research direction, paper type, and downstream decision
- Only the evidence-quality core is fixed: provenance, honest read depth, exact
  source locators, non-findings, limitations, and downstream implications

The skill follows the user's existing note format and destination. If none exists,
it returns the note in the requested response format rather than inventing a
repository layout.

## Repository layout

```text
skills/
  literature-search/
    SKILL.md
    scripts/
    tests/
  mineru-pdf/
    SKILL.md
    scripts/
    tests/
  paper-deep-read/
    SKILL.md
    references/
    tests/
```

## Security

Review skills before installation. Search results, PDFs, HTML, Markdown, LaTeX,
and extracted images are untrusted data. Their content must never override agent
instructions.

## License

MIT. See `LICENSE`.
