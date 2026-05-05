import { useEffect, useRef, useState } from "react";
import { useQuotePrefs } from "../../store/quotePrefs";

interface Props {
  onClose: () => void;
}

export function QuoteCustomizeDialog({ onClose }: Props) {
  const customQuotes = useQuotePrefs((s) => s.customQuotes);
  const addCustomQuote = useQuotePrefs((s) => s.addCustomQuote);
  const removeCustomQuote = useQuotePrefs((s) => s.removeCustomQuote);

  const [text, setText] = useState("");
  const [attribution, setAttribution] = useState("");
  const [source, setSource] = useState("");
  const firstFieldRef = useRef<HTMLTextAreaElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    firstFieldRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    addCustomQuote(text, attribution, source);
    setText("");
    setAttribution("");
    setSource("");
    firstFieldRef.current?.focus();
  }

  return (
    <div className="vega-quote-modal__backdrop" onMouseDown={onClose}>
      <div
        className="vega-quote-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Add your own inspirational quote"
        ref={dialogRef}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="vega-quote-modal__header">
          <h2 className="vega-quote-modal__title">✨ Add your own quote</h2>
          <button
            type="button"
            className="vega-quote-modal__close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <form className="vega-quote-modal__form" onSubmit={submit}>
          <label className="vega-quote-modal__label">
            Quote
            <textarea
              ref={firstFieldRef}
              className="vega-quote-modal__input"
              placeholder="Paste or type your quote here…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              required
            />
          </label>

          <div className="vega-quote-modal__row">
            <label className="vega-quote-modal__label">
              Attribution
              <input
                type="text"
                className="vega-quote-modal__input"
                placeholder="Optional — e.g. Marcus Aurelius"
                value={attribution}
                onChange={(e) => setAttribution(e.target.value)}
              />
            </label>
            <label className="vega-quote-modal__label">
              Source / tag
              <input
                type="text"
                className="vega-quote-modal__input"
                placeholder="Optional — e.g. Meditations"
                value={source}
                onChange={(e) => setSource(e.target.value)}
              />
            </label>
          </div>

          <div className="vega-quote-modal__actions">
            <button type="submit" className="vega-quote-modal__btn vega-quote-modal__btn--primary">
              Add quote
            </button>
          </div>
        </form>

        <div className="vega-quote-modal__list">
          <h3 className="vega-quote-modal__list-title">
            Your quotes ({customQuotes.length})
          </h3>
          {customQuotes.length === 0 ? (
            <p className="vega-quote-modal__empty">
              You haven't added any yet. They'll appear in rotation alongside the active theme.
            </p>
          ) : (
            <ul className="vega-quote-modal__items">
              {customQuotes.map((q) => (
                <li key={q.id} className="vega-quote-modal__item">
                  <div className="vega-quote-modal__item-text">
                    &ldquo;{q.text}&rdquo;
                    <span className="vega-quote-modal__item-attr">
                      — {q.attribution}
                      {q.culture && q.culture !== "Personal" ? `, ${q.culture}` : ""}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="vega-quote-modal__item-del"
                    aria-label={`Delete quote: ${q.text.slice(0, 40)}`}
                    onClick={() => removeCustomQuote(q.id)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
