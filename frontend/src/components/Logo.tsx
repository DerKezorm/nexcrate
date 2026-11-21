/*
 * Das nexcrate-Zeichen: eine Holzkiste mit drei Brettern, Strebe im Mittelbrett
 * und Nieten. Dieselbe Form steht in public/logo.svg (Favicon) und in
 * tools/symbole.mjs (Bilder fuer den Home-Bildschirm). ⚠️ Wer sie hier aendert,
 * aendert sie dort mit. Quelle aller Fassungen: nexapps/icons-vergleich/neu.py.
 *
 * Die Farben kommen aus styles/index.css (``--nc-logo-…``) und folgen damit dem
 * Modus der App: weisse Kachel im hellen, fast schwarze im dunklen.
 *
 * ⚠️ userSpaceOnUse ist Pflicht: Mit dem Standard zeichnen die waagrechten
 * Bretter nichts, weil ihr Umriss null hoch ist.
 */
const NIETEN: [number, number][] = [
  [19, 22.8],
  [45, 22.8],
  [19, 43.2],
  [45, 43.2],
]

const VERLAUF = 'url(#nexcrate-mark)'

/** nexcrate-Zeichen: Holzkiste im Bernsteinverlauf. */
export function Logo({ className = 'h-8 w-8', withWordmark = false }: { className?: string; withWordmark?: boolean }) {
  const mark = (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="nexcrate-mark" gradientUnits="userSpaceOnUse" x1="10" y1="10" x2="54" y2="54">
          <stop offset="0" style={{ stopColor: 'var(--nc-logo-verlauf-1)' }} />
          <stop offset=".5" style={{ stopColor: 'var(--nc-logo-verlauf-2)' }} />
          <stop offset="1" style={{ stopColor: 'var(--nc-logo-verlauf-3)' }} />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="16" style={{ fill: 'var(--nc-logo-grund)' }} />
      <rect
        x="2.75"
        y="2.75"
        width="58.5"
        height="58.5"
        rx="15.3"
        fill="none"
        strokeWidth="1.5"
        style={{ stroke: 'var(--nc-logo-rand)', strokeOpacity: 'var(--nc-logo-rand-deckung)' }}
      />
      <rect x="14" y="18" width="36" height="30" rx="4" fill="none" stroke={VERLAUF} strokeWidth="4.2" />
      <path d="M14 27.5H50M14 38.5H50" stroke={VERLAUF} strokeWidth="3" />
      <path
        d="M19 34.5 45 31.5"
        strokeWidth="3.6"
        strokeLinecap="round"
        style={{ stroke: 'var(--nc-logo-strebe)' }}
      />
      {NIETEN.map(([cx, cy]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.5" fill={VERLAUF} />
      ))}
    </svg>
  )
  if (!withWordmark) return mark
  return (
    <span className="flex items-center gap-2.5">
      {mark}
      <span className="hidden text-lg font-bold tracking-tight sm:inline">
        NEX<span className="text-accent-500">CRATE</span>
      </span>
    </span>
  )
}
