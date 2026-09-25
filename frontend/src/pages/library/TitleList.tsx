import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { TitleSummary } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { VersionChip } from '../../components/VersionChip'
import { formatNumber } from '../../lib/format'
import { albumQualityText } from '../../lib/musicSteps'
import { sizeText } from '../../lib/size'

const COLUMNS = 'md:grid md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] md:gap-4'

/**
 * Die Bibliothek als Liste, fuer grosse Sammlungen: Titel und Jahr, daneben jede
 * Fassung mit Zustand, Qualitaet und Groesse. Am Telefon stehen die Fassungen unter
 * dem Titel, nichts schiebt die Seite zur Seite. Bei einer Serienfassung zeigt der Chip,
 * wie viele gelaufene Folgen da sind.
 */
export function TitleList({
  titles,
  selection = null,
}: {
  titles: TitleSummary[]
  /** Rueckmeldung 20.09.2026: Im Auswahlmodus bekommt jede Zeile ein Kaestchen statt eines Verweises. */
  selection?: { ids: ReadonlySet<number>; whole: boolean; toggle: (id: number) => void } | null
}) {
  const { t } = useTranslation()
  return (
    <div className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-850/60">
      <div className={'hidden border-b border-ink-700 px-4 py-2 text-xs font-medium tracking-wide text-mist-600 uppercase ' + COLUMNS} aria-hidden="true">
        <span>{t('library.list.title')}</span>
        <span>{t('library.list.versions')}</span>
      </div>
      <ul>
        {titles.map((title) => (
          <li key={title.id} className={'flex min-w-0 flex-col gap-2 border-b border-ink-700/60 px-4 py-3 last:border-b-0 md:items-start ' + COLUMNS}>
            <TitleCell title={title} selection={selection} />
            <ul className="flex min-w-0 flex-col gap-1.5">
              {title.versions.map((version) => (
                <VersionRow key={version.id} version={version} album={title.kind === 'album'} />
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  )
}

function TitleCell({
  title,
  selection,
}: {
  title: TitleSummary
  selection: { ids: ReadonlySet<number>; whole: boolean; toggle: (id: number) => void } | null
}) {
  const { t, i18n } = useTranslation()
  const marked = selection !== null && (selection.whole || selection.ids.has(title.id))
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        {selection !== null && (
          <input
            type="checkbox"
            checked={marked}
            onChange={() => selection.toggle(title.id)}
            aria-label={t('library.select.markLabel', { title: title.title })}
            className="mr-1 h-4 w-4 shrink-0 self-center accent-accent-500"
          />
        )}
        <Link to={`/titel/${title.id}`} className="font-semibold wrap-anywhere text-mist-100 hover:text-accent-400">
          {title.title}
        </Link>
        {title.year !== null && title.year > 0 && <span className="text-xs text-mist-500 tabular-nums">{title.year}</span>}
        {typeof title.imdb_rating === 'number' && (
          <span className="text-xs text-mist-500 tabular-nums">{t('library.imdb', { value: formatNumber(title.imdb_rating, i18n.language, 1) })}</span>
        )}
      </div>
      {/* Musik M1: der Kuenstler unter dem Titel, wie auf der Kuenstlerseite. */}
      {title.kind === 'album' && title.artist && <span className="truncate text-xs text-mist-500">{title.artist}</span>}
    </div>
  )
}

function VersionRow({ version, album = false }: { version: TitleSummary['versions'][number]; album?: boolean }) {
  const { t, i18n } = useTranslation()
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <VersionChip version={version} />
      {/* Rueckmeldung 20.09.2026: In der Liste soll zu sehen sein, was nexcrate in Ruhe laesst. */}
      {version.monitored === false && (
        <span className="inline-flex items-center gap-1 text-xs text-mist-500">
          <Symbol name="eyeOff" className="h-3.5 w-3.5" />
          {t('library.list.notWatched')}
        </span>
      )}
      {/* Eine Serienfassung hat viele Dateien. Wie viele Folgen da sind, sagt schon der Chip, "Noch keine Datei" waere falsch. */}
      {version.quality ? (
        // Bei Musik steht hier die Stufe des Albums (Musik M2), in Worten statt als Kennung.
        <span className="wrap-anywhere text-mist-300">{album ? albumQualityText(t, version.quality) : version.quality}</span>
      ) : version.counts ? null : (
        <span className="wrap-anywhere text-mist-500">{t('library.list.noFile')}</span>
      )}
      {version.size_bytes ? <span className="text-mist-500 tabular-nums">{sizeText(t, version.size_bytes, i18n.language)}</span> : null}
      {(version.unclear_files ?? 0) > 0 && (
        <span className="text-accent-400 tabular-nums">
          {t('library.list.unclear', { count: version.unclear_files, value: formatNumber(version.unclear_files ?? 0, i18n.language) })}
        </span>
      )}
    </li>
  )
}
