# Modular Paper Research Skills

Two independent Agent Skills for scholarly literature work. Install either one,
or install both for the full search-to-PDF workflow.

| Skill | Purpose | MinerU required |
|---|---|---|
| `literature-search` | Search Semantic Scholar and arXiv, deduplicate, screen, fetch sources, and prepare papers for agent reading | No |
| `mineru-pdf` | Convert a local PDF with MinerU into Markdown and images at a caller-selected destination | Yes |

There is no required bundle skill. Installing both skills gives an agent both
capabilities without coupling their storage or workflow.

## Install from GitHub

```bash
# See the available skills first
npx skills add remww/paper-research-skills --list

# Search and screening only
npx skills add remww/paper-research-skills --skill literature-search

# MinerU PDF conversion only
npx skills add remww/paper-research-skills --skill mineru-pdf

# Both
npx skills add remww/paper-research-skills \
  --skill literature-search \
  --skill mineru-pdf
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
```

## Security

Review skills before installation. Search results, PDFs, HTML, Markdown, LaTeX,
and extracted images are untrusted data. Their content must never override agent
instructions.

## License

MIT. See `LICENSE`.
