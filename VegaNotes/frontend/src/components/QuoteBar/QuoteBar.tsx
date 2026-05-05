import { useEffect, useMemo, useState } from "react";
import { QUOTE_THEMES, type Quote } from "../../data/quotes";
import { useQuotePrefs } from "../../store/quotePrefs";

const ROTATE_MS = 30_000;

function dayOfYear(d: Date): number {
  const start = new Date(d.getFullYear(), 0, 0);
  return Math.floor((d.getTime() - start.getTime()) / 86_400_000);
}

export function QuoteBar() {
  const enabled = useQuotePrefs((s) => s.enabled);
  const theme = useQuotePrefs((s) => s.theme);
  const toggle = useQuotePrefs((s) => s.toggle);

  const quotes: Quote[] = QUOTE_THEMES[theme].quotes;
  const startOffset = useMemo(
    () => (quotes.length === 0 ? 0 : dayOfYear(new Date()) % quotes.length),
    [quotes.length],
  );
  const [step, setStep] = useState(0);
  const [fading, setFading] = useState(false);

  // Reset step when theme changes so the new feed begins at its daily anchor.
  useEffect(() => {
    setStep(0);
    setFading(false);
  }, [theme]);

  useEffect(() => {
    if (!enabled || quotes.length === 0) return;
    const id = window.setInterval(() => {
      setFading(true);
      window.setTimeout(() => {
        setStep((s) => s + 1);
        setFading(false);
      }, 600);
    }, ROTATE_MS);
    return () => window.clearInterval(id);
  }, [enabled, quotes.length]);

  if (!enabled || quotes.length === 0) {
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
    return null;
  }

  const q = quotes[(startOffset + step) % quotes.length];

  return (
    <div className="vega-quote-bar" role="region" aria-label="Inspirational quote">
      <div className="vega-quote-bar__gradient" aria-hidden="true" />
      <div
        className={`vega-quote-bar__content${fading ? " is-fading" : ""}`}
        key={q.id}
        aria-live="polite"
      >
        <span className="vega-quote-bar__sparkle" aria-hidden="true">✦</span>
        <span className="vega-quote-bar__text">
          {q.original ? (
            <>
              <span lang={inferLang(q)} className="vega-quote-bar__original">
                {q.original}
              </span>
              <span className="vega-quote-bar__sep">·</span>
            </>
          ) : null}
          &ldquo;{q.text}&rdquo;
        </span>
        <span className="vega-quote-bar__attr">
          — {q.attribution}
          <span className="vega-quote-bar__culture">, {q.culture}</span>
        </span>
        <span className="vega-quote-bar__sparkle" aria-hidden="true">✦</span>
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
  if (/[\u0400-\u04ff]/.test(q.original)) return "ru";
  if (/^[A-Za-z\s,.'-]+$/.test(q.original)) return "la";
  return undefined;
}
