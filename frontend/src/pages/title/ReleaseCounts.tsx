import { useTranslation } from 'react-i18next'

import type { SearchSummaryRefused } from '../../api/types'
import { formatNumber } from '../../lib/format'
import { rejectionPhrase } from './automaticText'

/**
 * Wie viele Releases der letzten Suche eine Fassung nehmen durfte und, je Grund, wie viele er abgelehnt hat. Seit dem
 * 26.09.2026 bei Filmen, Staffeln und Alben: Das beste Release allein sagte bei 108 gefundenen nicht, dass keins in
 * erlaubter Qualitaet dabei war. Ein Ergebnis von vorher hat keine Zahlen und zeigt nichts.
 */
export function ReleaseCounts({ fitting, refused }: { fitting?: number | null; refused?: SearchSummaryRefused[] }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const count = typeof fitting === 'number' && Number.isFinite(fitting) ? fitting : null
  const reasons = Array.isArray(refused)
    ? refused.filter((item) => typeof item.code === 'string' && item.code !== '' && typeof item.releases === 'number')
    : []
  if (count === null && reasons.length === 0) return null

  return (
    <>
      {count !== null && <p className="text-sm text-mist-300">{t('title.automatic.summary.fitting', { count, value: formatNumber(count, language) })}</p>}
      {reasons.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-semibold text-mist-400">{t('title.automatic.summary.refusedTitle')}</p>
          <ul aria-label={t('title.automatic.summary.refusedTitle')} className="flex flex-col gap-1">
            {reasons.map((item) => (
              <li key={item.code} className="flex items-baseline justify-between gap-3 text-sm text-mist-200">
                <span className="min-w-0 wrap-anywhere">{rejectionPhrase(t, item.code)}</span>
                <span className="shrink-0 text-mist-400 tabular-nums">
                  {t('title.automatic.summary.refusedCount', { count: item.releases, value: formatNumber(item.releases, language) })}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}
