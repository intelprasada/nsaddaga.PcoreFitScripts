import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { onAwardedBadges, type MeBadges } from "../../api/client";
import { useUI } from "../../store/ui";
import { isGamifyEnabled, subscribeGamify } from "../../lib/gamify";
import { lookupBadge } from "./badgeBlurbs";

interface Toast {
  id: number;
  key: string;
  title: string;
  blurb: string;
}

let _seq = 0;

/**
 * Listens for `awarded_badges` arrays surfaced by any write request via
 * the global `onAwardedBadges` hook. Renders one stacked toast per
 * unlocked key. Click-through routes the user to the Me view and the
 * BadgeGrid query refetches so the new badge shows as earned.
 *
 * Suppressed entirely when the user has flipped gamification OFF
 * (mirrors CLI `gamify=off` behaviour).
 */
export function UnlockToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [enabled, setEnabled] = useState<boolean>(() => isGamifyEnabled());
  const qc = useQueryClient();
  const setView = useUI((s) => s.set);

  useEffect(() => subscribeGamify(setEnabled), []);

  useEffect(() => {
    return onAwardedBadges((keys) => {
      if (!isGamifyEnabled()) return;
      // Pull whatever the cache knows about this user's badges so we can
      // render the live title; fall back to the static blurb table.
      const cached = qc.getQueryData<MeBadges>(["me", "badges"]);
      const known = new Map<string, { title: string; description: string }>();
      cached?.earned.forEach((b) => known.set(b.key, b));
      cached?.locked.forEach((b) => known.set(b.key, b));
      const fresh: Toast[] = keys.map((k) => {
        const live = known.get(k);
        const fb = lookupBadge(k);
        return {
          id: ++_seq,
          key: k,
          title: live?.title ?? fb.title,
          blurb: live?.description ?? fb.blurb,
        };
      });
      setToasts((prev) => [...prev, ...fresh]);
      // Refetch badges so the grid reflects the unlock immediately.
      qc.invalidateQueries({ queryKey: ["me", "badges"] });
      qc.invalidateQueries({ queryKey: ["me", "stats"] });
      qc.invalidateQueries({ queryKey: ["me", "streak"] });
      // Auto-dismiss each toast after 6 seconds.
      const ids = fresh.map((t) => t.id);
      window.setTimeout(() => {
        setToasts((prev) => prev.filter((t) => !ids.includes(t.id)));
      }, 6000);
    });
  }, [qc]);

  if (!enabled || toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          className="text-left rounded-lg border border-amber-300 bg-amber-50 shadow-lg px-4 py-3 hover:bg-amber-100 cursor-pointer"
          onClick={() => {
            setView({ view: "me" });
            setToasts((prev) => prev.filter((x) => x.id !== t.id));
          }}
          title="View badges"
        >
          <div className="text-sm font-semibold text-amber-900">🏆 Unlocked: {t.title}</div>
          {t.blurb && <div className="text-xs text-amber-800/80 mt-0.5">{t.blurb}</div>}
          <div className="text-[10px] text-amber-700 mt-1">click to view</div>
        </button>
      ))}
    </div>
  );
}
