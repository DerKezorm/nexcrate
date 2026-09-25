import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { CalendarEntry } from '../../api/calendar'
import { Symbol } from '../../components/Symbol'
import { countryName, episodeNumber, kindSymbol, kindText, occasionText } from './calendarText'

/**
 * Eine Zeile der Liste: Titel und Termin, mehr nicht. Was auf der Platte liegt, sagt die Bibliothek;
 * der Kalender beantwortet die Frage "wann kommt das Naechste". Der Klick fuehrt zur Titelseite.
 */
export function EntryLine({ entry }: { entry: CalendarEntry }) {
  const { t, i18n } = useTranslation()
  const number = entry.kind === 'episode' ? episodeNumber(entry.season, entry.episode) : ''
  const second = entry.kind === 'album' ? entry.artist : entry.episode_title
  return (
    <Link
      to={`/titel/${entry.title_id}`}
      className="flex items-center gap-3 rounded-2xl border border-ink-700 bg-ink-850 px-4 py-3 transition-colors hover:border-ink-600"
    >
      <span className="grid h-8 w-8 flex-none place-items-center rounded-xl border border-ink-700 text-mist-400">
        <Symbol name={kindSymbol(entry.kind)} />
      </span>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate text-[0.95rem] font-semibold">
          {entry.title}
          {number !== '' && <span className="ml-2 font-mono text-xs text-mist-500">{number}</span>}
        </span>
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mist-500">
          <span>{kindText(t, entry.kind)}</span>
          <span aria-hidden="true">·</span>
          <span>{occasionText(t, entry.occasion)}</span>
          {second != null && second !== '' && (
            <>
              <span aria-hidden="true">·</span>
              <span className="truncate">{second}</span>
            </>
          )}
          {entry.country !== null && (
            <span className="rounded-full border border-ink-700 px-2 py-0.5 text-[0.7rem] font-semibold">
              {t('calendar.region.foreign', { country: countryName(entry.country, i18n.language) })}
            </span>
          )}
        </span>
      </span>
      <Symbol name="chevron" className="ml-auto h-4 w-4 flex-none text-mist-600" />
    </Link>
  )
}
