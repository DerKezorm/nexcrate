import { useTranslation } from 'react-i18next'

import type { LoadingStatus } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { loadingStepWord } from './loadingText'

/**
 * Die Zeile am oberen Rand der Musik-Bibliothek (M1.2, M1.5.7): der Kuenstler, der gerade laedt, mit Schritt und
 * Zahl, dazu wie viele warten. Nur sichtbar, solange etwas laeuft oder wartet.
 */
export function LoadingBar({ status }: { status: LoadingStatus | null }) {
  const { t, i18n } = useTranslation()
  if (status === null) return null
  const { current, queued, failed } = status
  if (current === null && queued === 0) return null
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-400" role="status">
      {current !== null && (
        <span className="inline-flex items-center gap-2">
          <Spinner className="h-3.5 w-3.5" />
          {t('music.loadingBar.current', {
            name: current.name,
            step: loadingStepWord(t, current.step),
            done: formatNumber(current.done, i18n.language),
            total: current.total !== null ? formatNumber(current.total, i18n.language) : '?',
          })}
        </span>
      )}
      {queued > 0 && <span>{t('music.loadingBar.queued', { count: queued, value: formatNumber(queued, i18n.language) })}</span>}
      {failed.length > 0 && (
        <span className="inline-flex items-center gap-1.5 text-bad-500">
          <Symbol name="alert" className="h-3.5 w-3.5" />
          {t('music.loadingBar.failed', { count: failed.length, value: formatNumber(failed.length, i18n.language) })}
        </span>
      )}
    </p>
  )
}
