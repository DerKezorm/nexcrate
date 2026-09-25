import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { CalendarEntry } from '../../api/calendar'
import { Symbol } from '../../components/Symbol'
import { episodeNumber, kindSymbol, kindText, occasionText } from './calendarText'
import { WEEK_DAYS, byDay, dayText, entryKey, gridDays } from './month'

/** So viele Eintraege stehen in einer Zelle; der Rest wird gezaehlt. */
const PER_DAY = 3

/** Die Namen der Wochentage in der Sprache der Oberflaeche, kurz. Die Woche beginnt am Montag. */
function weekdayNames(language: string): string[] {
  const format = new Intl.DateTimeFormat(language, { weekday: 'short', timeZone: 'UTC' })
  // Der 5.1.1970 war ein Montag.
  return Array.from({ length: WEEK_DAYS }, (_, index) => format.format(Date.UTC(1970, 0, 5 + index)))
}

export function MonthGrid({ month, today, items }: { month: string; today: Date; items: CalendarEntry[] }) {
  const { t, i18n } = useTranslation()
  const days = gridDays(month)
  const perDay = byDay(items)
  const shownMonth = Number(month.slice(5, 7)) - 1
  const todayText = dayText(today)
  const names = weekdayNames(i18n.language)

  return (
    <section className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-850">
      <div className="grid grid-cols-7 border-b border-ink-700 bg-ink-900" aria-hidden="true">
        {names.map((name) => (
          <span key={name} className="px-2 py-2 text-[0.7rem] font-bold uppercase tracking-wide text-mist-500">
            {name}
          </span>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {days.map((day) => {
          const key = dayText(day)
          const mine = perDay.get(key) ?? []
          const outside = day.getMonth() !== shownMonth
          return (
            <div
              key={key}
              className={
                'flex min-h-[6.5rem] flex-col gap-1 border-b border-r border-ink-700 p-1.5 last-of-type:border-r-0 ' +
                (outside ? 'bg-ink-900' : 'bg-ink-850')
              }
            >
              <span className={'text-xs font-bold ' + (outside ? 'text-mist-600' : 'text-mist-400')}>
                {key === todayText ? (
                  <span className="grid h-5 min-w-[1.25rem] place-items-center rounded-full bg-accent-500 px-1 text-on-accent">
                    {day.getDate()}
                  </span>
                ) : (
                  day.getDate()
                )}
              </span>
              {mine.slice(0, PER_DAY).map((entry) => (
                <DayChip key={entryKey(entry)} entry={entry} />
              ))}
              {mine.length > PER_DAY && (
                <span className="px-1 text-[0.7rem] text-mist-500">
                  {t('calendar.more', { count: mine.length - PER_DAY })}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function DayChip({ entry }: { entry: CalendarEntry }) {
  const { t } = useTranslation()
  const number = entry.kind === 'episode' ? episodeNumber(entry.season, entry.episode) : ''
  const title = `${entry.title}${number === '' ? '' : ` ${number}`}, ${kindText(t, entry.kind)}, ${occasionText(t, entry.occasion)}`
  return (
    <Link
      to={`/titel/${entry.title_id}`}
      title={title}
      aria-label={title}
      className="flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-1.5 py-1 text-[0.72rem] leading-tight text-mist-300 transition-colors hover:border-accent-line hover:text-accent-400"
    >
      <Symbol name={kindSymbol(entry.kind)} className="h-3 w-3 flex-none" />
      {number !== '' && <span className="flex-none font-mono text-[0.65rem] text-mist-500">{number}</span>}
      <span className="truncate">{entry.title}</span>
    </Link>
  )
}
