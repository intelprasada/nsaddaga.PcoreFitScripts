import type { Task } from "../../api/client";
import { buildLinkChips } from "../../lib/linkChips";

/**
 * #314: renders the ordered list of external-link capsule chips for a
 * task. Each chip opens its URL in a new tab. Purely read-only display —
 * editing lives in the popover.
 */
export function LinkChips({ task }: { task: Task }) {
  const chips = buildLinkChips(task.attrs);
  if (chips.length === 0) return null;
  return (
    <>
      {chips.map((c, i) => (
        <a
          key={`${c.kind}-${i}-${c.href}`}
          href={c.href}
          target="_blank"
          rel="noopener noreferrer"
          title={c.title}
          onClick={(e) => e.stopPropagation()}
          className={
            "chip inline-flex items-center gap-0.5 rounded-full border px-1.5 py-0.5 " +
            "text-xs font-mono transition-colors cursor-pointer " +
            c.colorClass
          }
        >
          <span className="uppercase text-[10px] font-semibold opacity-70">
            {c.kind}
          </span>
          <span>{c.label}</span>
          <span aria-hidden="true" className="opacity-60">↗</span>
        </a>
      ))}
    </>
  );
}
