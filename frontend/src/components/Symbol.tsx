/**
 * Die Symbole an einer Stelle, wie in Nexview und nexbeat: 24×24, Strich statt
 * Flaeche, `currentColor`. So nimmt jedes Symbol die Farbe seines Knopfes an.
 * Neues Symbol: hier eintragen, nicht in einer Seite zeichnen.
 */

type Path = { d: string; dot?: boolean; fill?: boolean }

const SYMBOLS = {
  library: [{ d: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z' }],
  download: [{ d: 'M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14' }],
  import: [{ d: 'M3.5 12h10M9.5 8l4 4-4 4' }, { d: 'M13 4.5h6.5v15H13' }],
  settings: [{ d: 'M4 7h9M17 7h3M4 17h3M11 17h9' }, { d: 'M15 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM9 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z' }],
  search: [{ d: 'M10.5 17.5a7 7 0 1 0 0-14 7 7 0 0 0 0 14ZM15.5 15.5 20.5 20.5' }],
  plus: [{ d: 'M12 5v14M5 12h14' }],
  film: [{ d: 'M4 5h16v14H4z' }, { d: 'M8 5v14M16 5v14M4 9.5h4M4 14.5h4M16 9.5h4M16 14.5h4' }],
  tv: [{ d: 'M3.5 6.5h17v11h-17z' }, { d: 'M8 20.5h8' }],
  note: [{ d: 'M9 17.5V6l10-2.5V15' }, { d: 'M6.5 20a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM16.5 17.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z' }],
  check: [{ d: 'M5 12.5l4.5 4.5L19 7.5' }],
  clock: [{ d: 'M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17Z' }, { d: 'M12 7.5V12l3 2' }],
  alert: [{ d: 'M12 4 21 19.5H3L12 4Z' }, { d: 'M12 10v4' }, { d: 'M12 17v0', dot: true }],
  info: [{ d: 'M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17Z' }, { d: 'M12 11v5' }, { d: 'M12 8v0', dot: true }],
  arrowUp: [{ d: 'M12 19V5M6 11l6-6 6 6' }],
  arrow: [{ d: 'M5 12h14M13 6l6 6-6 6' }],
  back: [{ d: 'M19 12H5M11 6l-6 6 6 6' }],
  chevron: [{ d: 'M9 6l6 6-6 6' }],
  chevronDown: [{ d: 'M6 9l6 6 6-6' }],
  close: [{ d: 'M6 6l12 12M18 6 6 18' }],
  folder: [{ d: 'M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2h8A1.5 1.5 0 0 1 20.5 9v8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 17V7Z' }],
  server: [{ d: 'M4 4.5h16v6H4zM4 13.5h16v6H4z' }, { d: 'M7.5 7.5v0M7.5 16.5v0', dot: true }],
  refresh: [{ d: 'M19.5 12a7.5 7.5 0 1 1-2.2-5.3' }, { d: 'M19.5 4.5v4h-4' }],
  layers: [{ d: 'M12 4 20.5 8.5 12 13 3.5 8.5 12 4Z' }, { d: 'M3.5 12.5 12 17l8.5-4.5' }],
  eye: [{ d: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z' }, { d: 'M12 14.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z' }],
  play: [{ d: 'M8 5.5v13l10.5-6.5L8 5.5Z', fill: true }],
  pause: [{ d: 'M7.5 5.5h3v13h-3zM13.5 5.5h3v13h-3z', fill: true }],
  tag: [{ d: 'M3.5 11.6V4.5a1 1 0 0 1 1-1h7.1a1 1 0 0 1 .7.3l8.2 8.2a1 1 0 0 1 0 1.4l-7.1 7.1a1 1 0 0 1-1.4 0l-8.2-8.2a1 1 0 0 1-.3-.7z' }, { d: 'M8 8h.01' }],
  trash: [{ d: 'M5 7h14' }, { d: 'M9.5 7V5h5v2' }, { d: 'M6.5 7l.8 12.1a1 1 0 0 0 1 .9h7.4a1 1 0 0 0 1-.9L17.5 7' }],
  link: [{ d: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1' }],
  globe: [
    { d: 'M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17Z' },
    { d: 'M3.5 12h17' },
    { d: 'M12 3.5c2.2 2.3 3.4 5.3 3.4 8.5s-1.2 6.2-3.4 8.5c-2.2-2.3-3.4-5.3-3.4-8.5S9.8 5.8 12 3.5Z' },
  ],
  star: [{ d: 'M12 3.5l2.2 5.3 5.8.5-4.4 3.8 1.3 5.6L12 15.8l-4.9 2.9 1.3-5.6L4 9.3l5.8-.5L12 3.5Z' }],
  bell: [{ d: 'M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15l1.5-2Z' }, { d: 'M10 21h4' }],
  sparkle: [{ d: 'M12 3.5c.6 3.9 2.6 5.9 6.5 6.5-3.9.6-5.9 2.6-6.5 6.5-.6-3.9-2.6-5.9-6.5-6.5 3.9-.6 5.9-2.6 6.5-6.5Z' }],
  calendar: [{ d: 'M4.5 6h15v14h-15z' }, { d: 'M4.5 10h15M8.5 3.5v4M15.5 3.5v4' }],
  shield: [{ d: 'M12 3.5 19 6v5.5c0 4.3-3 7.8-7 9-4-1.2-7-4.7-7-9V6l7-2.5Z' }, { d: 'M9 12l2 2 4-4' }],
  swap: [{ d: 'M4 8h13M13 4l4 4-4 4' }, { d: 'M20 16H7M11 12l-4 4 4 4' }],
  eyeOff: [
    { d: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z' },
    { d: 'M12 14.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z' },
    { d: 'M4 4l16 16' },
  ],
  logout: [{ d: 'M13.5 4.5H5.5v15h8' }, { d: 'M10 12h10.5M17 8.5l3.5 3.5-3.5 3.5' }],
  pulse: [{ d: 'M3.5 12h4l2.5-6 4 12 2.5-6h4' }],
} satisfies Record<string, Path[]>

export type SymbolName = keyof typeof SYMBOLS

/**
 * ⚠️ Eine Klasse ohne Groesse (etwa nur `rotate-180`) behaelt die Standardgroesse. Vorher ersetzte sie `h-4 w-4`, und
 * ein Pfeil fuellte die ganze Breite der Seite (18.09.2026).
 */
export function Symbol({ name, className = '' }: { name: SymbolName; className?: string }) {
  const sized = /(^|\s)(h|size)-/.test(className) ? className : `h-4 w-4 ${className}`.trim()
  return (
    <svg viewBox="0 0 24 24" className={sized} aria-hidden="true" focusable="false">
      {(SYMBOLS[name] as Path[]).map((path, index) => (
        <path
          key={index}
          d={path.d}
          fill={path.fill ? 'currentColor' : 'none'}
          stroke={path.fill ? 'none' : 'currentColor'}
          strokeWidth={path.dot ? 2.5 : 1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </svg>
  )
}
