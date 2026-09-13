// Pure date phrasing used by the home page, kept out of HomePage.tsx on purpose: a file that
// exports both a component and a plain function breaks React Fast Refresh, and this is the
// piece worth unit-testing on its own anyway.

/**
 * Coarse "when did this land" phrasing. Deliberately vague past a week: the exact timestamp is
 * on the request's own page, and what this column answers is "recently, or a while back?".
 */
export function landedWhen(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const days = Math.floor((now - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 14) return "last week";
  if (days < 60) return `${Math.round(days / 7)} weeks ago`;
  return `${Math.round(days / 30)} months ago`;
}
