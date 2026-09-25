/** Zahlen, Groessen und Zeiten in der Schreibweise der eingestellten Sprache. */

export function formatNumber(value: number, language: string, fractionDigits = 0): string {
  return new Intl.NumberFormat(language, { minimumFractionDigits: fractionDigits, maximumFractionDigits: fractionDigits }).format(value)
}

/** Gigabyte mit einer Nachkommastelle unter 100, darueber ohne. */
export function formatGb(value: number, language: string): string {
  return formatNumber(value, language, value < 100 ? 1 : 0)
}

/** Ein Datum aus dem Server. Ist es nicht lesbar, steht der Text selbst da, statt dass die Seite abstuerzt. */
function readDate(value: string | number): Date | null {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

const FORMATS = new Map<string, Intl.DateTimeFormat>()

/** Ein Protokoll hat bis zu 1000 Zeilen. Ein Format je Sprache und Art genuegt. */
function dateFormat(language: string, kind: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${language}|${kind}`
  let format = FORMATS.get(key)
  if (!format) {
    format = new Intl.DateTimeFormat(language, options)
    FORMATS.set(key, format)
  }
  return format
}

export function formatDateTime(iso: string, language: string): string {
  const date = readDate(iso)
  if (!date) return iso
  return dateFormat(language, 'datetime', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}

export function formatDate(iso: string, language: string): string {
  const date = readDate(iso)
  if (!date) return iso
  return dateFormat(language, 'date', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(date)
}

/**
 * Ein Kalendertag wie "2026-08-20", als genau dieser Tag. Nicht als Mitternacht in UTC gelesen: Sonst zeigte ein
 * Browser westlich von UTC den Vortag. Ist er nicht lesbar, steht der Text selbst da.
 */
export function formatCalendarDate(day: string, language: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(day)
  if (!match) return day
  const time = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  if (Number.isNaN(time)) return day
  return dateFormat(language, 'calendar', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' }).format(time)
}

/** Uhrzeit ohne Datum, etwa fuer "schaltet sich um 14:30 ab". */
export function formatTime(value: string | number, language: string, withSeconds = false): string {
  const date = readDate(value)
  if (!date) return String(value)
  const options: Intl.DateTimeFormatOptions = withSeconds ? { hour: '2-digit', minute: '2-digit', second: '2-digit' } : { hour: '2-digit', minute: '2-digit' }
  return dateFormat(language, withSeconds ? 'time-seconds' : 'time', options).format(date)
}

/** Zeitstempel einer Protokollzeile: Tag, Monat und Uhrzeit mit Sekunden, in der Zeitzone des Browsers. */
export function formatLogTime(iso: string, language: string): string {
  const date = readDate(iso)
  if (!date) return iso
  return dateFormat(language, 'log', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(date)
}

export function isToday(iso: string): boolean {
  const date = readDate(iso)
  return date !== null && date.toDateString() === new Date().toDateString()
}

/** Spieldauer als m:ss. */
export function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

const RELATIVE = new Map<string, Intl.RelativeTimeFormat>()

/**
 * Wie alt etwas ist, in der Sprache: "vor 3 Tagen", "2 hours ago". Minuten unter einer Stunde,
 * Stunden unter zwei Tagen, Tage unter einem Jahr, danach Jahre. Eine Testsuche hat bis zu 50 Zeilen.
 */
export function formatAge(iso: string, language: string, now: number = Date.now()): string {
  const date = readDate(iso)
  if (!date) return iso
  return ageText(Math.max(0, Math.round((now - date.getTime()) / 60000)), language)
}

/** Wie `formatAge`, aus einem Alter in Stunden, wie die Suche es schickt. */
export function formatAgeHours(hours: number, language: string): string {
  return ageText(Math.max(0, Math.round(hours * 60)), language)
}

function ageText(minutes: number, language: string): string {
  let format = RELATIVE.get(language)
  if (!format) {
    format = new Intl.RelativeTimeFormat(language, { numeric: 'always' })
    RELATIVE.set(language, format)
  }
  if (minutes < 60) return format.format(-minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (hours < 48) return format.format(-hours, 'hour')
  const days = Math.round(hours / 24)
  if (days < 365) return format.format(-days, 'day')
  return format.format(-Math.floor(days / 365), 'year')
}

/** Eine Aufzaehlung in der Sprache: "Full-HD und 4K", "Full HD and 4K". */
export function formatList(items: readonly string[], language: string): string {
  return new Intl.ListFormat(language, { type: 'conjunction' }).format(items)
}
