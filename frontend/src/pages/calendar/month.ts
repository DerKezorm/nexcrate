import type { CalendarEntry } from '../../api/calendar'
import { LIST_DAYS, type CalendarAddress } from './address'

/**
 * Das Monatsgitter und der Zeitraum, den die Seite holt. Alles hier rechnet in Ortszeit mit
 * `YYYY-MM-DD` als Text: der Server liefert Tage ohne Uhrzeit, und eine Zeitzone darf sie nicht
 * verschieben.
 *
 * Die Woche beginnt am Montag. Der Kalender zeigt immer sechs Wochen, damit das Gitter beim
 * Blaettern nicht springt.
 */
export const WEEKS = 6
export const WEEK_DAYS = 7

export function dayText(day: Date): string {
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`
}

export function addDays(day: Date, days: number): Date {
  const next = new Date(day.getFullYear(), day.getMonth(), day.getDate())
  next.setDate(next.getDate() + days)
  return next
}

export function firstOfMonth(month: string): Date {
  const [year, index] = month.split('-').map(Number)
  return new Date(year, index - 1, 1)
}

/** Der Montag, mit dem das Gitter dieses Monats beginnt. */
export function gridStart(month: string): Date {
  const first = firstOfMonth(month)
  return addDays(first, -((first.getDay() + 6) % 7))
}

export function gridDays(month: string): Date[] {
  const start = gridStart(month)
  return Array.from({ length: WEEKS * WEEK_DAYS }, (_, index) => addDays(start, index))
}

export function shiftMonth(month: string, months: number): string {
  const first = firstOfMonth(month)
  const moved = new Date(first.getFullYear(), first.getMonth() + months, 1)
  return `${moved.getFullYear()}-${String(moved.getMonth() + 1).padStart(2, '0')}`
}

/** Welchen Zeitraum die Ansicht braucht: das ganze Gitter, oder ab heute nach vorn. */
export function spanOf(address: CalendarAddress, today: Date): { from: string; to: string } {
  if (address.view === 'liste') {
    return { from: dayText(today), to: dayText(addDays(today, LIST_DAYS - 1)) }
  }
  const days = gridDays(address.month)
  return { from: dayText(days[0]), to: dayText(days[days.length - 1]) }
}

export function byDay(items: CalendarEntry[]): Map<string, CalendarEntry[]> {
  const found = new Map<string, CalendarEntry[]>()
  for (const item of items) {
    const list = found.get(item.day)
    if (list) list.push(item)
    else found.set(item.day, [item])
  }
  return found
}

/** Ein Schluessel, der einen Eintrag eindeutig macht: ein Titel kann an einem Tag mehrfach stehen. */
export function entryKey(entry: CalendarEntry): string {
  return `${entry.day}|${entry.kind}|${entry.title_id}|${entry.occasion}|${entry.season ?? ''}|${entry.episode ?? ''}`
}
