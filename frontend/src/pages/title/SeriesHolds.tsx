import { useTranslation } from 'react-i18next'

import type { FolderReading, SearchHold, TitleVersion } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { ProgressBar } from '../../components/ui'
import { formatCalendarDate, formatNumber } from '../../lib/format'
import { definitionIdOf } from './seriesText'

type Props = {
  /** Was eine eigene Fassung zurueckhaelt (S6, Entscheidungen 18 und 23). */
  held: readonly SearchHold[]
  /** Fassungen, deren Serienordner gerade eingelesen wird, mit Fortschritt. */
  reading: readonly FolderReading[]
  versions: readonly TitleVersion[]
}

/**
 * Was die Automatik einer Serie zurueckhaelt, im Kasten "Automatische Suche": Wird der Serienordner einer Fassung noch
 * eingelesen, steht der Fortschritt dabei; halten Dateien ohne Folge sie zurueck, sagt der Satz, dass nexcrate nur neue
 * Folgen laedt und aeltere erst, wenn nichts mehr unklar ist. Solange etwas zurueckhaelt, laesst der Kasten die Zeile
 * zur naechsten Suche weg: Sie wuerde etwas anderes behaupten.
 */
export function SeriesHolds({ held, reading, versions }: Props) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  if (held.length === 0) return null

  return (
    <ul aria-label={t('title.automatic.series.heldLabel')} className="flex flex-col gap-2">
      {held.map((hold) => {
        const version = versions.find((item) => definitionIdOf(item) === hold.version_id) ?? null
        const label = version?.label ?? t('series.unassigned.someVersion')
        const job = reading.find((item) => item.version_id === hold.version_id) ?? null
        const count = typeof hold.count === 'number' && Number.isFinite(hold.count) && hold.count > 0 ? hold.count : 0
        const since = typeof hold.since === 'string' && hold.since !== '' ? formatCalendarDate(hold.since, language) : null
        const text =
          hold.reason === 'reading_files'
            ? t('title.automatic.series.held.reading', { label })
            : since !== null
              ? t('title.automatic.series.held.unclear', { count, value: formatNumber(count, language), label, date: since })
              : t('title.automatic.series.held.unclearNoDate', { count, value: formatNumber(count, language), label })
        return (
          <li key={`${hold.version_id}-${hold.reason}`} className="flex min-w-0 flex-col gap-2 rounded-xl border border-accent-500/40 bg-accent-500/5 p-3">
            <p className="flex items-start gap-2 text-sm text-mist-200">
              <Symbol name={hold.reason === 'reading_files' ? 'folder' : 'alert'} className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
              <span className="min-w-0 wrap-anywhere">{text}</span>
            </p>
            {hold.reason === 'reading_files' && <ReadingProgress job={job} />}
          </li>
        )
      })}
    </ul>
  )
}

/** Der Fortschritt einer Einlesung: wartet, liest, oder liest mit Balken und Zahlen. */
function ReadingProgress({ job }: { job: FolderReading | null }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  if (job === null) return null
  const total = typeof job.total === 'number' && Number.isFinite(job.total) && job.total > 0 ? job.total : null
  const done = typeof job.done === 'number' && Number.isFinite(job.done) && job.done > 0 ? job.done : 0
  if (job.state === 'queued') return <p className="text-xs text-mist-500">{t('series.read.queued')}</p>
  if (total === null) return <p className="text-xs text-mist-500">{t('series.read.running')}</p>
  const text = t('series.read.progress', { done: formatNumber(done, language), total: formatNumber(total, language) })
  return (
    <>
      <ProgressBar value={done / total} tone="accent" label={text} />
      <p className="text-xs text-mist-500 tabular-nums">{text}</p>
    </>
  )
}
