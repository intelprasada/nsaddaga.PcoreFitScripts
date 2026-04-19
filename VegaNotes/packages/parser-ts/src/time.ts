// Time / duration / priority helpers. Mirror of backend/app/parser/time_parse.py.

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const REL = /^([+-])(\d+(?:\.\d+)?)([hdwm])$/;
const DOW: Record<string, number> = { mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6, sun: 0 };

// Intel work-week notation: WW1 starts on the Sunday before the first
// Saturday of the year; days 0..6 = Sun..Sat. Examples: "WW16", "ww16.3",
// "2026WW16.0".
const INTEL_WW_RE = /^(?:(\d{4}))?ww(\d{1,2})(?:\.(\d))?$/;

function intelWw1Start(year: number): Date {
  const jan1 = new Date(Date.UTC(year, 0, 1));
  // getUTCDay(): Sun=0..Sat=6.
  const daysToFirstSat = ((6 - jan1.getUTCDay()) % 7 + 7) % 7;
  const firstSat = new Date(jan1); firstSat.setUTCDate(firstSat.getUTCDate() + daysToFirstSat);
  const start = new Date(firstSat); start.setUTCDate(start.getUTCDate() - 6);
  return start;
}

export function parseIntelWw(value: string, today: Date = todayUTC()): string | null {
  if (!value) return null;
  const m = INTEL_WW_RE.exec(value.trim().toLowerCase());
  if (!m) return null;
  const year = m[1] ? parseInt(m[1], 10) : today.getUTCFullYear();
  const week = parseInt(m[2], 10);
  const day = m[3] !== undefined ? parseInt(m[3], 10) : 5; // default → Friday
  if (week < 1 || week > 53 || day < 0 || day > 6) return null;
  const start = intelWw1Start(year);
  start.setUTCDate(start.getUTCDate() + (week - 1) * 7 + day);
  return isoDate(start);
}

function todayUTC(): Date {
  const n = new Date();
  return new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate()));
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function parseEta(value: string, today: Date = todayUTC()): string | null {
  if (!value) return null;
  const v = value.trim().toLowerCase();
  if (ISO_DATE.test(v)) return v;
  if (v === "today") return isoDate(today);
  if (v === "tomorrow") {
    const t = new Date(today); t.setUTCDate(t.getUTCDate() + 1); return isoDate(t);
  }
  const m = REL.exec(v);
  if (m) {
    const sign = m[1] === "-" ? -1 : 1;
    const num = parseFloat(m[2]);
    const unit = m[3];
    const days = sign * (unit === "h" ? num / 24 : unit === "d" ? num : unit === "w" ? num * 7 : num * 30);
    const t = new Date(today); t.setUTCDate(t.getUTCDate() + Math.round(days)); return isoDate(t);
  }
  if (v.startsWith("next ")) {
    const dow = v.slice(5, 8);
    if (dow in DOW) {
      const cur = today.getUTCDay();
      const target = DOW[dow];
      const advance = ((target - cur) % 7 + 7) % 7 || 7;
      const t = new Date(today); t.setUTCDate(t.getUTCDate() + advance); return isoDate(t);
    }
  }
  const ww = parseIntelWw(v, today);
  if (ww) return ww;
  return null;
}

export function parseDuration(value: string): number | null {
  const m = /^(\d+(?:\.\d+)?)([hdwm])$/.exec(value.trim().toLowerCase());
  if (!m) return null;
  const num = parseFloat(m[1]);
  const unit = m[2];
  return unit === "h" ? num : unit === "d" ? num * 8 : unit === "w" ? num * 40 : num * 160;
}

export const PRIORITY_ORDER = ["p0", "p1", "p2", "p3", "high", "med", "medium", "low"];

export function parsePriorityRank(value: string): number {
  const idx = PRIORITY_ORDER.indexOf(value.trim().toLowerCase());
  return idx === -1 ? 999 : idx;
}
