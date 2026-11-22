---
name: graph-me-maybe
description: Install Graph-Me-Maybe and connect it to this coding agent as an MCP server, so the agent can search the user's documents. Use when asked to set up, install, connect or repair Graph-Me-Maybe or its MCP connection.
---

# Set up Graph-Me-Maybe and its MCP connection

Never ask for, print or write an API key. The user puts keys in the app, under
**Manage › Keys and model**.

1. Install: `pip install 'graph-me-maybe[app]'`, or the installer from the project's releases.
   Check with `graphme --help`.
2. Fetch what the app draws and stores: `graphme setup`.
3. Run `graphme doctor --json`. It prints a list of `{name, ok, problem, fix, note}` and changes
   nothing.
4. Fix the items with `"ok": false` that concern this machine, each by its `fix`, and run step 3
   again. Skip the `MCP in ...` items of clients other than your own. A `fix` that is a menu path
   is told to the user, not done by you. A `note` is information, not a failure: a client "not
   found here" is none of your business, and "not running" is fine, since `graphme mcp` starts
   the app when a client asks.
5. Connect this agent: `graphme mcp install --client <client>`, where `<client>` is
   `claude-code`, `claude-desktop` or `cursor`, whichever you are. It backs the config file up
   to `.bak`, keeps every other entry, and is safe to run twice. If it says the file is not
   valid JSON, stop and tell the user; do not edit that file yourself. Claude Code rewrites
   `~/.claude.json` while it runs; if that matters, use `claude mcp add --scope user
   graph-me-maybe -- <command and args>` with the entry the install prints, not the default
   per-project scope, which `graphme doctor` does not see.
6. Run `graphme doctor` again: your client's `MCP in ...` item must be `ok`.
7. Restart the client so it loads the server, then call the MCP tool `list_workspaces`. A list
   of workspaces means it works. A workspace with `mcp: false` is still switched off: the user
   switches it on in the app, under **Manage › Keys and model › Connect an MCP client**.
