# vaak — Win+H web voice bridge for tmux CLI sessions

**Vaak** (Sanskrit वाक्, "speech / voice") is a stdlib-only Python HTTP server
that serves a single browser text box and injects whatever you type — or
**speak with Windows Voice Typing (Win+H)** — into any of your **tmux-hosted CLI
sessions** via `tmux send-keys`.

It exists because Win+H refuses to dictate into terminal emulators ("this app
might not support voice features") but works perfectly into a browser field. One
bridge serves **every** tmux session at once; you pick the target session from a
dropdown in the web UI.

- Zero third-party runtime dependencies (Python 3.9+ standard library only).
- `tmux` is the enabler: running a CLI inside tmux is the only reliable way to
  "type" into an already-running terminal program, and it makes every session
  individually addressable.
- Token-gated: the send endpoint requires the secret embedded in the URL.
- A **left session nav bar** lists every tmux session with a live busy/ready
  status dot and a queued-draft count, and a **per-session draft queue** lets
  you compose items while a session is busy and send them (individually, in
  order, or auto-flushed) once it's ready.
- A read-only **terminal mirror** shows the selected tmux pane via
  `capture-pane`, including scrollback history (choose 40–5000 lines in the
  mirror's "scrollback" selector), and optional in-page **ready alerts** ping
  when a session transitions from busy to ready.
- **Mobile-friendly**: a responsive layout and a **QR** button (top bar) that
  encodes the current tokenized URL so you can open Vaak on your phone and
  dictate from there. The QR is rendered client-side from a locally-vendored
  `qrcode.js` (no CDN, no external call — your token never leaves the host).

```
                +----------------------------------------------+
 left nav bar   |  Vaak  -> dictate & queue into tmux sessions |
 (sessions +    +------------------+---------------------------+
  status dots)  | ● copilot [node] | selected session: ready   |
                | ● review  [node] | [ big Win+H text box ]     |
                | ● build   [node] | Send now | + Add to queue  |
                |                  | [ terminal mirror panel ]  |
                +------------------+  Queue: item1 [copy|send]  |
                                   |         item2 [copy|send]  |
                                   |  Send all | Auto-send [x]  |
                                   +---------------------------+
```

## Quick start

```tcsh
# 1. Run each CLI you want to dictate into inside its own tmux session:
tmux new -s copilot        # then start your CLI (e.g. `clic`) inside it

# 2. Start the bridge (foreground). Set a stable token so the URL is durable:
setenv VAAK_TOKEN mysecret-token
bin/vaak

# ...or background it with a log:
nohup bin/vaak > /tmp/vaak.log 2>&1 &
```

The server prints a tokenized URL, e.g. `http://<host-ip>:8781/?token=mysecret-token`.
Open it on your laptop. The **left nav bar** lists every tmux session with a
live status dot (🟢 ready / 🟠 busy). Click a session, click the box, press
**Win+H**, speak, and press **Enter** to send it right now — or, while the
session is busy, **+ Add to queue** to stage items and flush them later.

> Tip: run `bin/vaak` *inside* the tmux pane you use most; it defaults the
> selected session to that pane (via `$TMUX_PANE`).

## Working with multiple sessions & the draft queue

- **Left nav bar** — one row per tmux session, with a busy/ready status dot and
  a badge showing how many drafts are queued for it. Click to select; it
  auto-rescans every few seconds. Sessions not started inside tmux don't appear.
- **Send now →** — types the box into the selected session and (optionally)
  presses Enter immediately.
- **+ Add to queue** — stages the box as a draft for the selected session. Works
  even while that session is **busy**, so you can line up your next prompts while
  the CLI is still responding.
- **Queue** — each item has **Copy** (to clipboard), **Send** (gated: refuses if
  the session is busy and removes the item once sent), **↑ / ↓** to reorder, and
  **✕** to delete. Copy first tries the Clipboard API and falls back to a hidden
  textarea + `execCommand('copy')` so it also works from plain HTTP LAN URLs.
- **Send all in order** — flushes the whole queue sequentially, stopping if the
  session becomes busy. Each submitted item is separated by a short settle wait
  and an acceptance check, so queued prompts do not concatenate on one CLI input
  line.
- **Auto-send when ready** — per-session toggle; a background poller sends the
  next queued item automatically each time the session transitions busy → ready.
  It uses the same queue-send line discipline as **Send all in order**.
- **Terminal mirror** — polls `/api/pane` about every 1.5 seconds and shows the
  last captured lines from the selected tmux pane. It is read-only, auto-scrolls
  to the bottom, and can be hidden with the toggle.
- **Alert when ready** — browser-local checkbox (persisted in `localStorage`) to
  flash the tab title, play a short WebAudio beep, and show an in-page toast on a
  busy → ready transition. It does not use the Notification API, so it works over
  plain HTTP.

Drafts persist to disk (see `VAAK_DRAFTS`) keyed by session **name**, so they
survive a Vaak restart and re-associate with the same session.

## Page controls

| Control | Effect |
|---------|--------|
| **Session nav / Rescan** | Left bar; click a session to target it. Status dot = busy/ready; badge = queued drafts. Auto-rescans every ~2.5s. |
| **Enter = send now** | Enter sends the box to the selected session immediately (Shift+Enter inserts a newline). |
| **Submit in CLI** | Off = only type the text into the CLI, don't press Enter — lets you stitch several dictations into one prompt, then submit manually. |
| **Keep focus** | Refocus the box after sending so you can immediately dictate again. |
| **Alert when ready** | Flash title + beep + toast when a watched session changes busy → ready. Persisted in this browser. |
| **Show/Hide terminal mirror** | Toggle the read-only selected-pane mirror. Persisted in this browser. |
| **Scrollback (lines)** | How many lines of tmux scrollback the mirror pulls (40–5000). Persisted per browser. tmux only retains up to its own `history-limit` (default 2000 — raise it with `tmux set -g history-limit 10000` for new sessions). |
| **+ Add to queue** | Stage the box as a draft (allowed even while busy). |
| **Send all in order / Auto-send when ready** | Flush the queue manually, or auto-flush on each busy→ready edge. |

## Configuration

The server takes no CLI flags (except the `--port N` shortcut on `bin/vaak`);
all configuration is via environment variables:

| Env var | Default | Description |
|---------|---------|-------------|
| `VAAK_PORT` | `8781` | HTTP port to bind (binds on `0.0.0.0`). |
| `VAAK_TARGET` | `$TMUX_PANE`, else `copilot` | Session selected by default in the nav bar. |
| `VAAK_TOKEN` | random per start | Shared secret embedded in the URL and required on every send. Set it for a durable link. |
| `VAAK_HOST` | `0.0.0.0` | Bind address (0.0.0.0 so a laptop browser can reach this host by IP). |
| `VAAK_DRAFTS` | `~/.vaak/drafts.json` | Where per-session draft queues persist. |
| `VAAK_BUSY_RE` | Copilot-CLI footer regex | Regex marking a pane as *busy* when found in its tail. Override for other CLIs. |
| `VAAK_STATUS_TAIL` | `8` | How many trailing non-blank pane lines to scan for the busy marker. |
| `VAAK_PANE_LINES` | `1000` | Default number of lines exposed by `/api/pane` and shown in the terminal mirror (requests are capped at 5000). |
| `VAAK_AUTOFLUSH_INTERVAL` | `2.0` | Seconds between auto-flush poller ticks. |
| `VAAK_AUTOFLUSH_GRACE` | `0.8` | Delay after a session goes ready before auto-sending the next draft. |
| `VAAK_SEND_SETTLE` | `0.35` | Small delay before/after queued submits so the target CLI can settle. |
| `VAAK_SEND_ACCEPT_TIMEOUT` | `4.0` | Max seconds for a queued submit to show acceptance (busy marker or pane change) before stopping the queue. |
| `VAAK_SEND_READY_TIMEOUT` | `300.0` | Max seconds **Send all in order** waits for a busy target to become ready before sending the next queued item. |
| `VAAK_SEND_POLL_INTERVAL` | `0.15` | Poll interval while waiting for queued-submit acceptance/ready. |

## HTTP API

Everything below returns JSON.

| Endpoint | Description |
|----------|-------------|
| `GET /api/sessions` | Nav-bar payload: each tmux session with `{name, command, status, drafts, autoflush}`. |
| `GET /api/status?target=` | `busy` / `ready` / `gone` for one target. |
| `GET /api/pane?target=&lines=` | Last captured tmux pane lines as `{target, lines}`. `lines` defaults to `VAAK_PANE_LINES` and is capped at 5000. |
| `GET /api/drafts?session=` | Queued drafts for a session + its autoflush flag. |
| `GET /api/info` | Default target, its resolved pane id, and all tmux panes as `[{id,label}]`. |
| `POST /api/send` | `{token, text, target?, submit?}` — types `text` into `target` now. |
| `POST /api/drafts/add` | `{token, session, text}` — enqueue a draft. |
| `POST /api/drafts/update` | `{token, session, id, text}` — edit a draft. |
| `POST /api/drafts/delete` | `{token, session, id}` — remove a draft. |
| `POST /api/drafts/move` | `{token, session, id, dir}` — reorder (`up`/`down`). |
| `POST /api/drafts/send` | `{token, session, id, submit?}` — send one draft (gated on ready; removed on success). |
| `POST /api/drafts/send_all` | `{token, session, submit?}` — flush the queue in order. |
| `POST /api/autoflush` | `{token, session, enabled}` — toggle auto-flush for a session. |
| `GET /api/health` | Liveness probe. |

```bash
curl -s -XPOST "http://localhost:8781/api/drafts/add" \
     -H 'Content-Type: application/json' \
     -d '{"token":"mysecret-token","session":"copilot","text":"run the tests"}'
```

## Install (alias)

`bin/vaak` is wired into `utils/aliases.csh`, so after the standard core-tools
setup you can simply run `vaak` from any shell. Set a stable `VAAK_TOKEN` in your
`~/.aliases` to keep the URL constant across restarts.

## Security & behavior notes

- The **token gates every send**, so someone on the network can't inject into
  your terminal without the URL. Treat the URL like a password; set a strong
  `VAAK_TOKEN`.
- Keys are sent with `tmux send-keys -l` (**literal**), so spoken words like
  "enter" or "control c" are typed as text, never interpreted as key presses —
  only the explicit submit option presses Enter.
- Queued sends clear the current input line with `C-u` before typing each draft,
  then wait for a busy marker (or a pane change for plain shell targets) before
  sending the next draft. This prevents auto-flush / send-all items from
  concatenating in TUI CLIs that are still settling after the previous Enter.
- Newlines within one dictation are flattened to spaces so a single utterance
  stays a single prompt.

## Requirements

Python 3.9+ (stdlib only) and `tmux` on `PATH`. No build step, no external
packages.
