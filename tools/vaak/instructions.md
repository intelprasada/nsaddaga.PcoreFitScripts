Instructions for dictating into CLI sessions with Vaak, the Win+H web bridge.

Goal: speak prompts (or type from a browser) instead of typing into a terminal
that Windows Voice Typing won't dictate into.

Setup once:
  - Run each CLI you want to control inside its own tmux session, e.g.
      tmux new -s copilot     # then start the CLI (clic) inside it
      tmux new -s review
  - Add a stable token and alias to ~/.aliases so the URL never changes:
      setenv VAAK_TOKEN <your-secret>
      alias vaak "$CORE_TOOLS_DIR/bin/vaak"

Run:
  - Start the bridge once (it sees every tmux pane you have):
      vaak
  - Open the printed http://<host-ip>:8781/?token=<your-secret> on your laptop.
  - Pick the session in the dropdown, click the box, press Win+H, speak, Enter.

Tips:
  - Turn off "Press Enter in CLI (submit)" to stitch several dictations into one
    prompt, then submit manually.
  - Only tmux panes show up. If a session is missing, it was not started inside
    tmux — start it under `tmux new -s <name>`.
  - Detach a tmux session with Ctrl-b d; reattach with `tmux attach -t <name>`.
