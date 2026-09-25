/** nexcrate-Zeichen: eine Kiste mit Diagonalstrebe im Bernsteinverlauf, im Rahmen der nexapps-Zeichen. */
export function Logo({ className = 'h-8 w-8', withWordmark = false }: { className?: string; withWordmark?: boolean }) {
  // ⚠️ userSpaceOnUse ist Pflicht: Mit dem Standard zeichnen die waagrechten Latten
  // nichts, weil ihr Umriss null hoch ist.
  const mark = (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="nexcrate-mark" gradientUnits="userSpaceOnUse" x1="8" y1="8" x2="56" y2="56">
          <stop offset="0" stopColor="#fcd34d" />
          <stop offset=".55" stopColor="#f59e0b" />
          <stop offset="1" stopColor="#b45309" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="16" fill="#15110a" />
      <rect x="2" y="2" width="60" height="60" rx="16" fill="none" stroke="url(#nexcrate-mark)" strokeWidth="2.5" strokeOpacity=".55" />
      <rect x="14" y="17" width="36" height="30" rx="4" fill="none" stroke="url(#nexcrate-mark)" strokeWidth="3.2" />
      <path d="M14 24.5H50M14 39.5H50" stroke="url(#nexcrate-mark)" strokeWidth="2.2" strokeOpacity=".7" />
      <path d="M20 21.5L44 42.5" stroke="url(#nexcrate-mark)" strokeWidth="3.4" strokeLinecap="round" />
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
