import { useTranslation } from 'react-i18next'

import type { EpisodeCounts, VersionState } from '../api/types'
import { formatCalendarDate, formatNumber } from '../lib/format'
import { percentOf, STATE_LOOK } from '../lib/states'
import { versionStateText } from '../lib/stateText'
import { Symbol } from './Symbol'

export type ChipVersion = {
  label: string
  state: VersionState
  /** 0 bis 100, wie der Server ihn schickt. */
  progress?: number | null
  /** Nur bei einer Serienfassung: ihre Folgen. Fehlt bei Filmen. */
  counts?: EpisodeCounts | null
}

/**
 * Die Folgen einer Serienfassung im Chip: "3/5" fuer gelaufene Folgen, die da sind. Ist noch keine
 * ueberwachte Folge gelaufen, aber eine angekuendigt, steht ihr Datum da. Sonst nichts.
 */
function EpisodeCountsPart({ counts }: { counts: EpisodeCounts }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  if (counts.aired_watched > 0) {
    const values = { have: formatNumber(counts.have, language), total: formatNumber(counts.aired_watched, language) }
    return (
      <>
        <span className="font-medium tabular-nums opacity-80" aria-hidden="true">
          {t('series.counts.chip', values)}
        </span>
        <span className="sr-only">, {t('series.counts.chipLabel', values)}</span>
      </>
    )
  }
  if (counts.next_air_date) {
    return <span className="font-medium tabular-nums opacity-80">{t('series.counts.chipNext', { date: formatCalendarDate(counts.next_air_date, language) })}</span>
  }
  return null
}

/** Name der Fassung mit ihrem Zustand. `compact` zeigt den Zustand nur als Symbol, fuer Vorleseprogramme als Text. */
export function VersionChip({ version, compact = false }: { version: ChipVersion; compact?: boolean }) {
  const { t } = useTranslation()
  const look = STATE_LOOK[version.state]
  const stateText =
    version.state === 'downloading' ? t('common.state.downloading', { percent: percentOf(version.progress) }) : versionStateText(t, version.state)
  return (
    <span
      className={'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold whitespace-nowrap ' + look.chip}
      title={stateText}
    >
      <Symbol name={look.symbol} className="h-3.5 w-3.5" />
      {version.label}
      {version.counts && <EpisodeCountsPart counts={version.counts} />}
      {compact && version.state === 'downloading' && (
        <span className="font-medium tabular-nums opacity-80" aria-hidden="true">
          {t('common.state.percent', { percent: percentOf(version.progress) })}
        </span>
      )}
      {compact ? <span className="sr-only">: {stateText}</span> : <span className="font-medium opacity-80">· {stateText}</span>}
    </span>
  )
}
