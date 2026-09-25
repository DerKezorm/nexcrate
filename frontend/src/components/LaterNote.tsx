import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Symbol } from './Symbol'

/**
 * Ruhiger Satz dazu, was an dieser Stelle spaeter kommt. Nie mit Beispielkarten oder
 * erfundenen Daten daneben. Blau statt Rosa: Hier ist nichts kaputt, es kommt nur spaeter.
 */
export function LaterNote({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div role="note" className="flex items-start gap-3 rounded-2xl border border-info-500/30 bg-info-500/5 px-4 py-3">
      <Symbol name="clock" className="mt-0.5 h-5 w-5 shrink-0 text-info-500" />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-mist-100">{t('common.later.title')}</p>
        <div className="mt-0.5 text-sm text-mist-400">{children}</div>
      </div>
    </div>
  )
}
