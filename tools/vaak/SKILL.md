---
name: vaak
description: >
  Launch Vaak (Sanskrit वाक्, "speech/voice"), a browser-based voice bridge that
  lets you use Windows Voice Typing (Win+H) — or any typing — to send prompts
  into your tmux-hosted CLI sessions. Serves a token-gated web text box that
  injects text into a chosen tmux pane via `tmux send-keys`, working around the
  fact that Win+H refuses to dictate directly into terminal emulators. One
  bridge serves every tmux session via a picker.
metadata:
  owner: Navadeep Saddaga
  owner_linux_name: nsaddaga
  ai_note: Written with assistance from Claude Opus 4.8
---

## Scope

Use this skill when asked to:

- Dictate / speak prompts into a CLI session ("let me talk instead of type").
- Work around "this app might not support voice features" (Win+H rejecting the
  terminal) by dictating into a browser field that relays to the terminal.
- Send text into a running CLI from a phone / another window / the browser.
- Drive one or more tmux-hosted sessions from a single web control box, with a
  left session nav bar (busy/ready status) and a per-session draft queue.
- Draft prompts while a session is busy and flush them (individually, in order,
  or auto-sent) once the CLI is ready.

## Prerequisites

- `tmux` on `PATH`. The CLI you want to dictate into **must run inside tmux**
  (e.g. `tmux new -s copilot` then start the CLI in it) — this is what makes a
  running terminal program addressable for keystroke injection.
- The browser opening the page needs network reach to this host's IP/port
  (Intel network / VPN for an internal host). No software install on the laptop;
  Win+H is built into Windows.

## Step-by-step workflow

1. **Run each target CLI inside tmux:**

   ```bash
   tmux new -s copilot
   clic                    # or any CLI, inside that tmux session
   ```

2. **Start the bridge** (from the repo root; set a stable token for a durable
   URL):

   ```bash
   VAAK_TOKEN=mysecret-token bin/vaak
   # or: nohup bin/vaak > /tmp/vaak.log 2>&1 &
   ```

   It prints `http://<host-ip>:8781/?token=mysecret-token`.

3. **On the laptop**, open that URL. Pick the session from the **Session**
   dropdown (every tmux pane is listed with the command running in it). Click
   the text box, press **Win+H**, speak, then press **Enter** (or **Send →**).
   The text is typed into that pane and submitted.

4. **Switch sessions** any time via the dropdown; the list auto-rescans every
   10 s (or press the refresh button). No restart required.

## Scriptable API

```bash
curl -s -XPOST "http://localhost:8781/api/send" -H 'Content-Type: application/json' \
     -d '{"token":"mysecret-token","text":"run the tests","target":"copilot","submit":true}'
```

`GET /api/info` lists all tmux panes as `{id,label}`. See `tools/vaak/README.md`
for the full endpoint list and environment knobs.

## Notes & limitations

- The token gates every send; treat the URL like a password.
- Keys are injected literally (`tmux send-keys -l`), so dictated words such as
  "enter" are typed as text, not interpreted — only the submit option presses
  Enter. Newlines in one utterance are flattened to a single prompt.
- Sessions **not** started inside tmux are invisible to the bridge — that's why
  the dropdown only lists tmux panes.
