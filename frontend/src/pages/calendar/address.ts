import { CALENDAR_KINDS, type CalendarKind } from '../../api/calendar'

/** Monat oder Liste. Wie in der Bibliothek steht die Wahl in der Adresse, damit "Zurueck" sie wiederbringt. */
export type CalendarView = 'monat' | 'liste'
export const CALENDAR_VIEWS: readonly CalendarView[] = ['monat', 'liste']

/** So weit reicht die Liste "was kommt als Naechstes" nach vorn. Der Server nimmt hoechstens 100 Tage. */
export const LIST_DAYS = 60

export type CalendarAddress = {
  view: CalendarView
  /** Der gezeigte Monat als `YYYY-MM`; in der Liste ohne Bedeutung. */
  month: string
  /** Welche Arten zu sehen sind. Leer waere nichts, deshalb faellt Leeres auf alle drei zurueck. */
  kinds: CalendarKind[]
  /** Nur Titel, die eine Fassung beobachtet. */
  watched: boolean
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback
}

/** `YYYY-MM` des Monats, in dem der Tag liegt. */
export function monthOf(day: Date): string {
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}`
}

function readMonth(value: string | null, today: Date): string {
  if (value === null || !/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) return monthOf(today)
  const year = Number(value.slice(0, 4))
  return year >= 1900 && year <= 2999 ? value : monthOf(today)
}

function readKinds(value: string | null): CalendarKind[] {
  if (value === null) return [...CALENDAR_KINDS]
  const wanted = value.split(',').filter((part): part is CalendarKind => (CALENDAR_KINDS as readonly string[]).includes(part))
  return wanted.length > 0 ? wanted : [...CALENDAR_KINDS]
}

/** Unbekannte oder kaputte Werte gelten als nicht gesetzt. */
export function readAddress(params: URLSearchParams, today: Date): CalendarAddress {
  return {
    view: oneOf(params.get('ansicht'), CALENDAR_VIEWS, 'monat'),
    month: readMonth(params.get('monat'), today),
    kinds: readKinds(params.get('art')),
    watched: params.get('beobachtet') === '1',
  }
}

/** Nur was vom Normalfall abweicht, kommt in die Adresse. So bleibt `/kalender` der laufende Monat. */
export function writeAddress(address: CalendarAddress, today: Date): URLSearchParams {
  const params = new URLSearchParams()
  if (address.view !== 'monat') params.set('ansicht', address.view)
  if (address.view === 'monat' && address.month !== monthOf(today)) params.set('monat', address.month)
  if (address.kinds.length !== CALENDAR_KINDS.length) params.set('art', address.kinds.join(','))
  if (address.watched) params.set('beobachtet', '1')
  return params
}

export function sameAddress(a: CalendarAddress, b: CalendarAddress): boolean {
  return (
    a.view === b.view &&
    a.month === b.month &&
    a.watched === b.watched &&
    a.kinds.length === b.kinds.length &&
    a.kinds.every((kind, index) => kind === b.kinds[index])
  )
}
