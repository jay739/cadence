import "./StatStrip.css";

/**
 * A row of headline numbers on the shared glass surface.
 *
 * Shared by the home page and the admin insight page rather than duplicated: both open with
 * "here are four numbers", and having them drift apart in size or spacing would make two pages
 * that are meant to read as one product look like two.
 *
 * `tone` is never the only thing carrying meaning -- every tile states its own label and value
 * in text, so the colour is reinforcement for people who can see it and nothing is lost for
 * people who cannot.
 */
export type StatTone = "neutral" | "good" | "warn" | "bad";

export interface Stat {
  label: string;
  value: number | string;
  tone?: StatTone;
  /** Optional second line, for a number that needs a unit or a caveat to be honest. */
  detail?: string;
}

export function StatStrip({ stats }: { stats: Stat[] }) {
  return (
    <div className="stat-strip">
      {stats.map((stat) => (
        <div
          key={stat.label}
          className={`card stat-strip__item stat-strip__item--${stat.tone ?? "neutral"}`}
        >
          <span className="stat-strip__value">{stat.value}</span>
          <span className="stat-strip__label">{stat.label}</span>
          {stat.detail && <span className="stat-strip__detail">{stat.detail}</span>}
        </div>
      ))}
    </div>
  );
}
