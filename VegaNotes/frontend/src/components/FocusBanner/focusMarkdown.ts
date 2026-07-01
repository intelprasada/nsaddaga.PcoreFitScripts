/**
 * Minimal markdown → HTML renderer for the Focus banner (#266).
 *
 * Scope is intentionally tiny: the banner is meant to hold a sentence
 * or two of plain prose, not a full document. We only support the
 * inline marks that team leads reach for when writing a quick goal:
 * bold, italics, inline code, autolinks/explicit links, and
 * line / paragraph breaks. Anything we don't recognise renders as
 * its escaped literal — never as raw HTML — so a hostile or careless
 * editor can't smuggle `<script>` into the banner.
 *
 * Returning a sanitized HTML string keeps the call site simple
 * (`<div dangerouslySetInnerHTML={{__html: render(md)}} />`) without
 * pulling in react-markdown / remark just for this banner.
 */

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeUrl(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (
    trimmed.startsWith("http://") ||
    trimmed.startsWith("https://") ||
    trimmed.startsWith("mailto:") ||
    trimmed.startsWith("/")
  ) {
    return trimmed;
  }
  return null;
}

function renderInline(text: string): string {
  let html = escapeHtml(text);

  html = html.replace(
    /\[([^\]]+?)\]\(([^)]+?)\)/g,
    (_m, label: string, href: string) => {
      const url = safeUrl(href);
      if (!url) return escapeHtml(`[${label}](${href})`);
      return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="text-sky-700 underline">${label}</a>`;
    },
  );

  html = html.replace(
    /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    (_m, lead: string, url: string) =>
      `${lead}<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="text-sky-700 underline">${escapeHtml(url)}</a>`,
  );

  html = html.replace(/`([^`]+?)`/g, (_m, code: string) => `<code class="rounded bg-slate-100 px-1 py-0.5 text-[0.9em]">${code}</code>`);

  html = html.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");

  return html;
}

export function renderFocusMarkdown(md: string): string {
  if (!md || !md.trim()) return "";
  const paragraphs = md.replace(/\r\n/g, "\n").split(/\n\s*\n/);
  return paragraphs
    .map((para) => {
      const lines = para.split("\n").map((l) => renderInline(l));
      return `<p>${lines.join("<br />")}</p>`;
    })
    .join("");
}
