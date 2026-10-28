---
type: Component
title: browser-harness-core
description: The CDP layer field_pipeline drives a real (or Browser Use Cloud) Chrome through, in-process. Vendored, general-purpose code — kept as a reference table here, not a concept per module.
resource: src/browser_harness/
generated: {by: claude-code/sonnet-5, at: '2026-10-28T00:00:00Z'}
status: stable
---

# browser-harness-core

`agent-workspace/field_pipeline/fetch.py` imports `browser_harness.admin` and `.helpers`
directly (not the `./browser-harness` CLI) to open pages, expand accordions/tabs, and read
rendered HTML through the daemon's CDP websocket. This is the vendored, general-purpose
`browser-harness` project (see root `AGENTS.md`, `SKILL.md`, `install.md`) — kept intact rather
than reimplemented, so it's documented here as one table, not one concept file per module:

| Module | Purpose |
|---|---|
| `admin.py` | daemon lifecycle, diagnostics, updates, profile management |
| `daemon.py` | the long-lived middleman process between the browser and the agent |
| `helpers.py` | CDP wrapper + core browser primitives; re-exports `recorder`'s start/stop |
| `run.py` | the `browser-harness` CLI (`--doctor`, `--reload`, `auth`, `recordings`, `skill`, `mac-approve`, `telemetry`) |
| `auth.py` | Browser Use Cloud sign-in/token storage, used by `admin`/`daemon` for `--browser cloud` |
| `telemetry.py` | anonymous, opt-out CLI usage telemetry (`run.py` only) |
| `recorder.py` | local screenshot/action-trace recording (`start_recording`/`stop_recording`) |
| `macos.py` | approves Chrome's macOS remote-debugging permission sheet |
| `_ipc.py` | low-level IPC between the CLI and the daemon process |
| `paths.py` | runtime/profile directory resolution |

Its video-recording CLI and MCP server were removed as unused by this tool (see
[repository layout](/okf/project/components/repository.md)); `./browser-harness --doctor`
stays as the setup/troubleshooting path.
