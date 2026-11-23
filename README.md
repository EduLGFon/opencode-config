# OpenCode config

Shareable OpenCode configuration: global agent rules (`AGENTS.md`), subagent
definitions (`agents/`), slash commands (`commands/`), and the orchestration skill
(`skills/orchestrate/`).

Secrets and machine-local settings (`cli.json`, `service.json`) are intentionally
excluded.

## Install

```sh
git clone https://github.com/<you>/opencode-config ~/.config/opencode-config
cd ~/.config/opencode-config
./install.sh
```

`install.sh` symlinks `AGENTS.md`, `opencode.jsonc`, `agents/`, `commands/`, and
`skills/` into `~/.config/opencode` (override the destination with
`OPENCODE_CONFIG_DIR`). Existing real files are renamed to `*.bak`.

## Sync changes

Edit files under `~/.config/opencode` (they are symlinks into this repo), then:

```sh
cd ~/.config/opencode-config
git add -A && git commit -m "..." && git push
```

## Layout

- `AGENTS.md` - global rules loaded by OpenCode.
- `agents/` - subagent prompts (orchestrator, worker, reviewer, utility).
- `commands/` - slash commands.
- `skills/orchestrate/` - orchestration skill and its scripts/tests.
- `opencode.jsonc` - non-secret OpenCode settings.
