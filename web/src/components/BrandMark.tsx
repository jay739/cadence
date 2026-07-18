/**
 * The Cadence icon: a single eighth-note locked inside camera-focus brackets, the product's
 * actual idea (one track, precisely selected, not a whole album) rendered as a mark rather
 * than generic music/vinyl imagery. Inlined rather than referenced as an <img> so it stays
 * crisp at any header size with no extra network request.
 */
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 512 512" aria-hidden="true" className="brand-mark">
      <rect x="41" y="26" width="430" height="460" rx="28" fill="var(--color-bg-inset)" />
      <radialGradient id="brandNoteGlow" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stopColor="var(--color-accent-strong)" stopOpacity="0.55" />
        <stop offset="55%" stopColor="var(--color-accent)" stopOpacity="0.22" />
        <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
      </radialGradient>
      <circle cx="256" cy="256" r="145" fill="url(#brandNoteGlow)" />
      <g
        fill="none"
        stroke="var(--color-text)"
        strokeWidth="16"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M 89 150 L 89 96 L 143 96" />
        <path d="M 369 96 L 423 96 L 423 150" />
        <path d="M 423 362 L 423 416 L 369 416" />
        <path d="M 143 416 L 89 416 L 89 362" />
      </g>
      <g fill="var(--color-accent)">
        <ellipse cx="233" cy="330" rx="46" ry="34" transform="rotate(-18 233 330)" />
        <rect x="271" y="140" width="22" height="196" rx="8" />
        <path
          d="M 293 140
             C 311 160, 351 168, 359 210
             C 364 236, 353 258, 333 268
             C 347 240, 341 214, 319 198
             C 303 187, 291 178, 293 158
             Z"
          fill="var(--color-accent-strong)"
        />
      </g>
    </svg>
  );
}
