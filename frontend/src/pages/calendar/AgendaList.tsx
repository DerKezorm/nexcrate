import { useTranslation } from 'react-i18next'

import type { CalendarEntry } from '../../api/calendar'
import { formatCalendarDate } from '../../lib/format'
import { EntryLine } from './EntryLine'
import { addDays, byDay, dayText, entryKey } from './month'

/** "Was kommt als Naechstes": die Tage ab heute, jeder mit seinen Eintraegen. Leere Tage fehlen. */
export function AgendaList({ items, today }: { items: CalendarEntry[]; today: Date }) {
  const { t, i18n } = useTranslation()
  const perDay = byDay(items)
  const todayText = dayText(today)
  const tomorrowText = dayText(addDays(today, 1))
  const days = [...perDay.keys()].sort()

  return (
    <div className="flex flex-col gap-6">
      {days.map((day) => (
        <section key={day} className="flex flex-col gap-3">
          <h2 className="flex flex-wrap items-baseline gap-3 border-b border-ink-700 pb-2">
            <span className="text-base font-bold">{dayHeading(day)}</span>
            <span className="text-xs text-mist-500">{formatCalendarDate(day, i18n.language)}</span>
          </h2>
          <div className="flex flex-col gap-2">
            {(perDay.get(day) ?? []).map((entry) => (
              <EntryLine key={entryKey(entry)} entry={entry} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )

  function dayHeading(day: string): string {
    if (day === todayText) return t('calendar.today')
    if (day === tomorrowText) return t('calendar.tomorrow')
    const [year, month, date] = day.split('-').map(Number)
    return new Intl.DateTimeFormat(i18n.language, { weekday: 'long', timeZone: 'UTC' }).format(Date.UTC(year, month - 1, date))
  }
}
