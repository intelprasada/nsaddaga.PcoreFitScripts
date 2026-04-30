/**
 * Renders the standalone help tour (served from `/public/help.html` by Vite)
 * inside an iframe so the React app's chrome (sidebar, filter bar, command
 * palette) stays consistent. The HTML file is fully self-contained and works
 * on its own — opening it in a new tab is also supported.
 */
export function HelpView() {
  const url = "/help.html";
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-1.5 border-b border-slate-200 bg-slate-50 text-xs text-slate-600">
        <span>Guided tour — searchable walkthrough of every feature, both web and CLI.</span>
        <a
          className="ml-auto text-sky-700 hover:underline"
          href={url}
          target="_blank"
          rel="noopener"
          title="Open in a new tab"
        >
          open in a new tab ↗
        </a>
      </div>
      <iframe
        title="VegaNotes guided tour"
        src={url}
        className="flex-1 w-full border-0"
      />
    </div>
  );
}
