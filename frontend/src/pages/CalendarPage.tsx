import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { CALENDAR_KINDS, calendarApi, type CalendarEntry, type CalendarKind, type CalendarSettings, type RegionCount } from '../api/calendar'
import { errorText } from '../api/client'
import { Segmented } from '../components/Segmented'
import { Symbol, type SymbolName } from '../components/Symbol'
import { Button, FormMessage, PageTitle, Spinner } from '../components/ui'
import { formatNumber } from '../lib/format'
import { AgendaList } from './calendar/AgendaList'
import { CALENDAR_VIEWS, monthOf, readAddress, sameAddress, writeAddress, type CalendarAddress, type CalendarView } from './calendar/address'
import { countryName, kindSymbol, kindText, viewText } from './calendar/calendarText'
import { MonthGrid } from './calendar/MonthGrid'
import { shiftMonth, spanOf } from './calendar/month'
import { Subscription } from './calendar/Subscription'

/**
 * Der Release-Kalender: Monatsgitter und Liste "was kommt als Naechstes",
 * umschaltbar, der Zustand steht in der Adresse wie in der Bibliothek.
 *
 * Ein Eintrag sagt Titel und Termin, sonst nichts: der Kalender ist der Ueberblick, die Bibliothek
 * sagt, was auf der Platte liegt. Die Termine der Filme kommen aus der eingestellten Region; ohne
 * Termin dort steht der frueheste weltweit da, und die Zeile nennt sein Land.
 */
export function CalendarPage() {
  const { t, i18n } = useTranslation()
  const [params, setParams] = useSearchParams()
  // Ein fester Tag je Seitenaufbau: waehrend man blaettert, darf "heute" nicht wandern.
  const today = useMemo(() => new Date(), [])
  const [address, setAddress] = useState<CalendarAddress>(() => readAddress(params, today))
  const [items, setItems] = useState<CalendarEntry[] | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [settings, setSettings] = useState<CalendarSettings | null>(null)
  const [regions, setRegions] = useState<RegionCount[] | null>(null)
  // Hochgezaehlt, wenn die Region sich aendert: die Termine der Filme haengen daran.
  const [again, setAgain] = useState(0)

  const setParamsRef = useRef(setParams)
  setParamsRef.current = setParams
  const written = useRef<string | null>(null)

  // Die Adresse folgt dem Zustand, ohne einen Eintrag im Verlauf zu hinterlassen.
  useEffect(() => {
    const next = writeAddress(address, today)
    const text = next.toString() === '' ? '' : `?${next.toString()}`
    if (text === written.current) return
    written.current = text
    setParamsRef.current(next, { replace: true })
  }, [address, today])

  // Und der Zustand folgt der Adresse, wenn sie von aussen kommt (Zurueck, ein Lesezeichen).
  useEffect(() => {
    const fromAddress = readAddress(params, today)
    setAddress((current) => (sameAddress(current, fromAddress) ? current : fromAddress))
  }, [params, today])

  const span = spanOf(address, today)

  useEffect(() => {
    const abort = new AbortController()
    setError(null)
    calendarApi.span({ from: span.from, to: span.to, kinds: address.kinds, monitored: address.watched }, abort.signal).then(
      (page) => {
        setItems(page.items)
        setTruncated(page.truncated)
      },
      (problem: unknown) => {
        if (!abort.signal.aborted) {
          setItems([])
          setError(problem)
        }
      },
    )
    return () => abort.abort()
  }, [span.from, span.to, address.kinds, address.watched, again])

  useEffect(() => {
    const abort = new AbortController()
    calendarApi.settings(abort.signal).then(setSettings, () => undefined)
    return () => abort.abort()
  }, [])

  const update = useCallback((changes: Partial<CalendarAddress>) => setAddress((current) => ({ ...current, ...changes })), [])

  const toggleKind = useCallback(
    (kind: CalendarKind) => {
      setAddress((current) => {
        const on = current.kinds.includes(kind)
        const next = on ? current.kinds.filter((one) => one !== kind) : [...CALENDAR_KINDS.filter((one) => current.kinds.includes(one) || one === kind)]
        // Ohne eine einzige Art bliebe die Seite leer; die letzte laesst sich nicht abwaehlen.
        return next.length === 0 ? current : { ...current, kinds: next }
      })
    },
    [],
  )

  async function chooseRegion(country: string) {
    setSettings(await calendarApi.saveSettings({ region: country }))
    setItems(null)
    setAgain((count) => count + 1)
  }

  const monthName = new Intl.DateTimeFormat(i18n.language, { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(
    Date.UTC(Number(address.month.slice(0, 4)), Number(address.month.slice(5, 7)) - 1, 1),
  )

  return (
    <div className="flex flex-col gap-8">
      <PageTitle sub={address.view === 'monat' ? t('calendar.sub') : t('calendar.subList')}>{t('calendar.title')}</PageTitle>

      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {address.view === 'monat' ? (
            <div className="flex items-center gap-2">
              <Button type="button" variant="ghost" size="sm" aria-label={t('calendar.previousMonth')} onClick={() => update({ month: shiftMonth(address.month, -1) })}>
                <Symbol name="back" />
              </Button>
              <span className="min-w-[10ch] text-center text-base font-bold">{monthName}</span>
              <Button type="button" variant="ghost" size="sm" aria-label={t('calendar.nextMonth')} onClick={() => update({ month: shiftMonth(address.month, 1) })}>
                <Symbol name="arrow" />
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => update({ month: monthOf(today) })}>
                {t('calendar.today')}
              </Button>
            </div>
          ) : (
            <span className="text-sm text-mist-500">{t('calendar.listRange', { days: 60 })}</span>
          )}
          <Segmented
            value={address.view}
            options={CALENDAR_VIEWS}
            onChange={(next: CalendarView) => update({ view: next })}
            label={(option) => viewText(t, option)}
            ariaLabel={t('calendar.view.label')}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {CALENDAR_KINDS.map((kind) => (
            <FilterChip key={kind} on={address.kinds.includes(kind)} symbol={kindSymbol(kind)} onClick={() => toggleKind(kind)}>
              {kindText(t, kind)}
            </FilterChip>
          ))}
          <span className="mx-1 h-5 w-px bg-ink-700" aria-hidden="true" />
          <FilterChip on={address.watched} symbol="eye" onClick={() => update({ watched: !address.watched })}>
            {t('calendar.filter.watched')}
          </FilterChip>
          <RegionChooser
            region={settings?.region ?? ''}
            regions={regions}
            onOpen={() => {
              if (regions === null) calendarApi.regions().then((answer) => setRegions(answer.items), () => setRegions([]))
            }}
            onChoose={chooseRegion}
          />
          {items !== null && (
            <span className="ml-auto text-xs text-mist-500">{t('calendar.entries', { count: items.length })}</span>
          )}
        </div>

        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
        {truncated && <FormMessage tone="info">{t('calendar.truncated', { count: formatNumber(items?.length ?? 0, i18n.language) })}</FormMessage>}

        {items === null ? (
          <div className="flex items-center gap-2 py-10 text-sm text-mist-500">
            <Spinner />
            {t('calendar.loading')}
          </div>
        ) : address.view === 'monat' ? (
          <MonthGrid month={address.month} today={today} items={items} />
        ) : items.length === 0 ? (
          <p className="rounded-2xl border border-ink-700 bg-ink-850 px-4 py-10 text-center text-sm text-mist-500">{t('calendar.emptyList')}</p>
        ) : (
          <AgendaList items={items} today={today} />
        )}
      </div>

      {settings !== null && <Subscription settings={settings} onChange={setSettings} />}
    </div>
  )
}

/** Die Region: welches Landes Termine ein Film bekommt. Die Liste kommt erst, wenn sie gebraucht wird. */
function RegionChooser({
  region,
  regions,
  onOpen,
  onChoose,
}: {
  region: string
  regions: RegionCount[] | null
  onOpen: () => void
  onChoose: (country: string) => void
}) {
  const { t, i18n } = useTranslation()
  return (
    <label className="flex items-center gap-2 text-xs text-mist-500">
      {t('calendar.region.label')}
      <select
        value={region}
        // Die Liste kostet den Server Zeit, also erst beim Anfassen. Drei Wege, weil ein Fokus allein
        // nicht bei jeder Bedienung kommt.
        onFocus={onOpen}
        onMouseDown={onOpen}
        onTouchStart={onOpen}
        onKeyDown={onOpen}
        onChange={(event) => onChoose(event.target.value)}
        className="rounded-full border border-ink-700 bg-ink-850 px-3 py-1.5 text-xs font-semibold text-mist-200"
      >
        <option value="">{t('calendar.region.any')}</option>
        {region !== '' && (regions ?? []).every((one) => one.country !== region) && (
          <option value={region}>{countryName(region, i18n.language)}</option>
        )}
        {(regions ?? []).map((one) => (
          <option key={one.country} value={one.country}>
            {countryName(one.country, i18n.language)}
          </option>
        ))}
      </select>
    </label>
  )
}

/**
 * Ein Filter, der ansieht, ob er an ist: wie die Zustands-Chips der Bibliothek. ⚠️ Nicht als `Button` mit
 * uebergelegten Farben — dessen eigener Rahmen und Hintergrund gewinnen, je nachdem, welche Regel spaeter im
 * Stylesheet steht, und der Filter sah in beiden Zustaenden gleich aus.
 */
function FilterChip({
  on,
  symbol,
  onClick,
  children,
}: {
  on: boolean
  symbol: SymbolName
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={
        'inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-xs font-semibold transition-colors ' +
        (on ? 'border-accent-500/60 bg-accent-500/15 text-accent-400' : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
      }
    >
      <Symbol name={symbol} className="h-3.5 w-3.5" />
      {children}
    </button>
  )
}
