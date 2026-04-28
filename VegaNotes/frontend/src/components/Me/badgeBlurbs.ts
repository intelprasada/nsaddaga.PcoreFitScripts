/** Static catalog of badge titles + flavor text, mirroring the CLI's
 * `_BADGE_BLURBS` table and the backend `app/badges.py` CATALOG.
 *
 * The toast prefers the live server payload from `/api/me/badges` (so
 * titles can drift server-side without a frontend ship) and falls back
 * here when the badge isn't yet present in the user's response — e.g.
 * the very first time a key is awarded, before badges has been refetched.
 */
export const BADGE_BLURBS: Record<string, { title: string; blurb: string }> = {
  first_light:   { title: "First Light",   blurb: "close your first task" },
  hat_trick:     { title: "Hat Trick",     blurb: "close 3 tasks in one day" },
  big_day:       { title: "Big Day",       blurb: "close 5 tasks in one day" },
  marathoner:    { title: "Marathoner",    blurb: "maintain a 10-day streak" },
  centurion:     { title: "Centurion",     blurb: "close 100 tasks lifetime" },
  on_time:       { title: "On Time",       blurb: "close 10 tasks on or before their ETA" },
  ar_wrangler:   { title: "AR Wrangler",   blurb: "close 25 action requests" },
  notebook:      { title: "Notebook",      blurb: "edit notes 50 times" },
  early_bird:    { title: "Early Bird",    blurb: "close a task before 9am local" },
  night_owl:     { title: "Night Owl",     blurb: "close a task after 10pm local" },
  weekend_hero:  { title: "Weekend Hero",  blurb: "close a task on a weekend" },
};

export function lookupBadge(key: string): { title: string; blurb: string } {
  return BADGE_BLURBS[key] ?? { title: key, blurb: "" };
}
