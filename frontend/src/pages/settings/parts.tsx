/** Kleine Bausteine, die mehrere Reiter der Einstellungen teilen. */

import type { ReactNode } from 'react'


/** Eine Karte innerhalb einer Section, flacher als `Card`. */
export function Tile({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={'flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-4 ' + className}>{children}</div>
}

export function TileHeader({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        <h3 className="text-base font-semibold wrap-anywhere text-mist-100">{title}</h3>
        {sub && <p className="mt-0.5 text-xs text-mist-500">{sub}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-1.5">{children}</div>}
    </div>
  )
}

/** Beschriftung und Wert, innerhalb eines `<dl>`. Adressen und Pfade brechen, statt die Seite zu verbreitern. */
export function Detail({ label, children, mono = false }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs text-mist-600">{label}</dt>
      <dd className={'text-sm break-all text-mist-200 ' + (mono ? 'font-mono' : '')}>{children}</dd>
    </div>
  )
}
