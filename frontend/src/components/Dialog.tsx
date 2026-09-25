import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Symbol } from './Symbol'

/**
 * Die offenen Fenster, das oberste zuletzt. Escape schliesst nur das oberste: Liegt die Ordnerauswahl ueber
 * der Uebernahme, geht sonst mit einem Tastendruck beides zu.
 */
const openDialogs: object[] = []

/**
 * Fenster im Nexview-Stil statt eines Browser-Popups. Escape und ein Klick
 * daneben schliessen, das Kreuz oben ist der eine sichtbare Ausgang.
 */
export function Dialog({
  open,
  title,
  onClose,
  children,
  footer,
  wide = false,
  table = false,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
  /** Fuer eine Tabelle mit vielen Spalten (Ausgaben eines Albums): breiter als `wide`. */
  table?: boolean
}) {
  const { t } = useTranslation()
  const panelRef = useRef<HTMLDivElement>(null)
  // ⚠️ Nicht als Abhaengigkeit: Seiten reichen meist eine neue Funktion je Zeichnen
  // herein. Dann liefe der Effekt bei jedem Tastendruck und holte den Fokus aus dem Feld.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    const token = {}
    openDialogs.push(token)
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && openDialogs[openDialogs.length - 1] === token) onCloseRef.current()
    }
    document.addEventListener('keydown', onKeyDown)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    // Ein Feld mit autoFocus darf seinen Fokus behalten.
    if (!panelRef.current?.contains(document.activeElement)) panelRef.current?.focus()
    return () => {
      const index = openDialogs.indexOf(token)
      if (index >= 0) openDialogs.splice(index, 1)
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previous
    }
  }, [open])

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4 backdrop-blur-sm"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={
          'flex max-h-[88vh] w-full flex-col overflow-hidden rounded-2xl border border-ink-700 bg-ink-850 shadow-2xl shadow-black/60 outline-none ' +
          (table ? 'max-w-5xl' : wide ? 'max-w-3xl' : 'max-w-lg')
        }
      >
        <div className="flex items-center justify-between gap-4 border-b border-ink-700 px-6 py-4">
          {/* Ein langer Titel wie ein Releasename bricht um und schiebt den Knopf nicht aus dem Fenster. */}
          <h2 className="min-w-0 text-lg font-bold tracking-tight wrap-anywhere">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('common.actions.close')}
            className="shrink-0 rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100"
          >
            <Symbol name="close" className="h-5 w-5" />
          </button>
        </div>
        <div className="overflow-y-auto px-6 py-5">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-ink-700 px-6 py-4">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}
