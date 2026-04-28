// Copy `text` to the system clipboard.
//
// Returns true on confirmed success, false if the copy could not be
// performed. Always prefer the async Clipboard API (only available in
// secure contexts — HTTPS or localhost) and fall back to the legacy
// `document.execCommand("copy")` path for plain-HTTP origins (e.g.
// in-cluster k8s service IPs / pod hostnames) where the modern API is
// `undefined`.
//
// Filed as part of issue #142: previously `navigator.clipboard?.writeText()`
// silently no-op'd in non-secure contexts while the UI flashed "Copied!",
// misleading users into thinking the value had been captured.
export async function copyToClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to legacy path — some browsers reject writeText
      // even in secure contexts when the document isn't focused.
    }
  }
  if (typeof document === "undefined") return false;
  const ta = document.createElement("textarea");
  ta.value = text;
  // Off-screen but still focusable / selectable — required by execCommand.
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "0";
  ta.style.width = "1px";
  ta.style.height = "1px";
  ta.style.padding = "0";
  ta.style.border = "none";
  ta.style.outline = "none";
  ta.style.boxShadow = "none";
  ta.style.background = "transparent";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  let ok = false;
  try {
    ta.select();
    ta.setSelectionRange(0, text.length);
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  } finally {
    document.body.removeChild(ta);
  }
  return ok;
}
