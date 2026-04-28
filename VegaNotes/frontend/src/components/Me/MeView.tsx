import { useEffect, useState } from "react";
import { isGamifyEnabled, setGamifyEnabled, subscribeGamify } from "../../lib/gamify";
import { StatsCard } from "./StatsCard";
import { StreakCard } from "./StreakCard";
import { BadgeGrid } from "./BadgeGrid";
import { HistorySpark } from "./HistorySpark";
import { ActivityList } from "./ActivityList";
import { TZSettings } from "./TZSettings";

export function MeView() {
  const [enabled, setEnabled] = useState<boolean>(() => isGamifyEnabled());

  useEffect(() => subscribeGamify(setEnabled), []);

  return (
    <div className="p-4 space-y-4 max-w-5xl">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Me</h1>
        <label className="text-xs text-slate-600 inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setGamifyEnabled(e.target.checked)}
          />
          show gamification
          <span className="text-slate-400">(client-side only — server keeps recording)</span>
        </label>
      </div>

      {/* TZ is intentionally always visible — it's a general preference,
          not gamification surfacing (mirrors CLI's `vn me tz` exemption). */}
      <TZSettings />

      {!enabled ? (
        <div className="rounded border border-slate-200 bg-slate-50 p-6 text-sm text-slate-600">
          Gamification is hidden. Re-enable it with the toggle above to see
          your stats, streak, badges, history and activity. The server is
          still recording events, so flipping it back on restores your full
          history.
        </div>
      ) : (
        <>
          <StatsCard />
          <StreakCard />
          <BadgeGrid />
          <HistorySpark />
          <ActivityList />
        </>
      )}
    </div>
  );
}
