import { useMemo } from "react";
import quotesData from "../../data/quotes.json";
import { useQuotePrefs } from "../../store/quotePrefs";

export interface Quote {
  id: string;
  text: string;
  attribution: string;
  culture: string;
  original?: string;
}

const QUOTES: Quote[] = quotesData as Quote[];

// Deterministic daily seed: same day of year -> same starting quote
// for everyone on the team (water-cooler effect).
function dayOfYear(d: Date): number {
  const start = new Date(d.getFullYear(), 0, 0);
  const diff = d.getTime() - start.getTime();
  return Math.floor(diff / 86_400_000);
}

function rotated<T>(arr: T[], offset: number): T[] {
  if (arr.length === 0) return arr;
  const n = ((offset % arr.length) + arr.length) % arr.length;
  return arr.slice(n).concat(arr.slice(0, n));
}

export function QuoteBar() {
  const enabled = useQuotePrefs((s) => s.enabled);
  const toggle = useQuotePrefs((s) => s.toggle);

  const ordered = useMemo(() => rotated(QUOTES, dayOfYear(new Date())), []);

  if (!enabled) {
    return (
      <button
        type="button"
        onClick={toggle}
        title="Show inspirational quotes"
        aria-label="Show inspirational quotes"
        className="vega-quote-toggle"
      >
        ✨
      </button>
    );
  }

  // Duplicate the ordered list so the marquee animation seamlessly loops.
  const stream = [...ordered, ...ordered];

  return (
    <div className="vega-quote-bar" role="region" aria-label="Inspirational quotes">
      <div className="vega-quote-bar__gradient" aria-hidden="true" />
      <div className="vega-quote-bar__track" aria-live="off">
        {stream.map((q, i) => (
          <span key={`${q.id}-${i}`} className="vega-quote-bar__item">
            <span className="vega-quote-bar__sparkle" aria-hidden="true">✦</span>
            <span className="vega-quote-bar__text">
              {q.original ? (
                <span lang={inferLang(q)} className="vega-quote-bar__original">
                  {q.original}
                </span>
              ) : null}
              {q.original ? <span className="vega-quote-bar__sep">·</span> : null}
              &ldquo;{q.text}&rdquo;
            </span>
            <span className="vega-quote-bar__attr">
              — {q.attribution}
              <span className="vega-quote-bar__culture">, {q.culture}</span>
            </span>
          </span>
        ))}
      </div>
      <button
        type="button"
        onClick={toggle}
        title="Hide inspirational quotes"
        aria-label="Hide inspirational quotes"
        className="vega-quote-bar__close"
      >
        ×
      </button>
    </div>
  );
}

function inferLang(q: Quote): string | undefined {
  if (!q.original) return undefined;
  if (/[\u3040-\u30ff\u4e00-\u9faf]/.test(q.original)) return "ja";
  if (/^[A-Za-z\s,.'-]+$/.test(q.original)) return "la";
  return undefined;
}
