import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { LOG_DOWNLOAD_URL, LOG_LEVELS, LOG_LIMITS, logsApi } from '../../api/logs'
import type { LogLevel, LogLine, LogMode, LogsResponse } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, Section, SelectField, Spinner, Toggle } from '../../components/ui'
import { formatLogTime, formatTime } from '../../lib/format'
import { LogModePanel } from './LogModePanel'

const AUTO_REFRESH_MS = 5000
const SEARCH_DELAY_MS = 300

/**
 * Farben je Stufe, nur aus den Tokens. Warnungen sind rosa wie Fehler, nie gelb:
 * Gelb ist der Akzent, eine gelbe Warnung saehe aus wie ein gewaehlter Knopf.
 */
const LEVEL_STYLE: Record<string, string> = {
  DEBUG: 'border-ink-600 bg-ink-900 text-mist-500',
  INFO: 'border-info-500/40 bg-info-500/10 text-info-500',
  WARNING: 'border-bad-500/40 bg-bad-500/10 text-bad-500',
  ERROR: 'border-bad-500 bg-bad-500/20 text-bad-500',
  CRITICAL: 'border-bad-500 bg-bad-500/20 text-bad-500',
}

const LOUD_LEVELS = new Set(['WARNING', 'ERROR', 'CRITICAL'])

function isLogLevel(value: string): value is LogLevel {
  return (LOG_LEVELS as readonly string[]).includes(value)
}

/** Protokoll: Stufe umschalten, Zeilen filtern, herunterladen, leeren. */
export function LogSettings() {
  const { t, i18n } = useTranslation()
  const [level, setLevel] = useState<LogLevel>('INFO')
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [limit, setLimit] = useState<number>(200)
  const [auto, setAuto] = useState(false)
  const [data, setData] = useState<LogsResponse | null>(null)
  const [loadedAt, setLoadedAt] = useState<number | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [clearBusy, setClearBusy] = useState(false)
  const [clearProblem, setClearProblem] = useState<unknown>(null)
  // Nur die neueste Anfrage zaehlt. Eine langsame alte ueberschreibt keinen neuen Filter.
  const generation = useRef(0)
  const language = i18n.language

  const load = useCallback(async () => {
    const run = ++generation.current
    setLoading(true)
    try {
      const result = await logsApi.read({ level, search, limit })
      if (run !== generation.current) return
      setData(result)
      setLoadError(null)
      setLoadedAt(Date.now())
    } catch (error) {
      if (run === generation.current) setLoadError(error)
    } finally {
      if (run === generation.current) setLoading(false)
    }
  }, [level, search, limit])

  useEffect(() => {
    void load()
  }, [load])

  // Suchen erst, wenn eine Weile nichts getippt wurde, nicht bei jedem Buchstaben.
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchInput), SEARCH_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [searchInput])

  useEffect(() => {
    if (!auto) return
    const timer = window.setInterval(() => void load(), AUTO_REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [auto, load])

  function showRequest(requestId: string) {
    setSearchInput(requestId)
    setSearch(requestId)
  }

  function modeChanged(mode: LogMode) {
    setData((previous) => (previous ? { ...previous, mode } : previous))
    void load()
  }

  async function clear() {
    setClearBusy(true)
    setClearProblem(null)
    try {
      await logsApi.clear()
      setClearing(false)
      void load()
    } catch (error) {
      setClearProblem(error)
    } finally {
      setClearBusy(false)
    }
  }

  function closeClear() {
    if (clearBusy) return
    setClearing(false)
    setClearProblem(null)
  }

  const lines = data?.lines ?? []

  return (
    <div className="flex flex-col gap-4">
      <LogModePanel mode={data?.mode ?? null} onChanged={modeChanged} onReload={() => void load()} />

      <Section title={t('system.logs.lines.title')} intro={t('system.logs.intro')}>
        {/* Der Hinweis zur Stufe steht unter ihrer Auswahl. Unter der ganzen Reihe las er sich wie ein Hinweis zur Suche. */}
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,13rem)_auto] sm:items-start">
          <Field
            label={t('system.logs.lines.search')}
            type="search"
            value={searchInput}
            placeholder={t('system.logs.lines.searchPlaceholder')}
            onChange={(event) => setSearchInput(event.target.value)}
          />
          <SelectField
            label={t('system.logs.lines.level')}
            hint={t('system.logs.lines.levelHint')}
            value={level}
            onChange={(event) => {
              if (isLogLevel(event.target.value)) setLevel(event.target.value)
            }}
          >
            {LOG_LEVELS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </SelectField>
          <SelectField label={t('system.logs.lines.limit')} value={String(limit)} onChange={(event) => setLimit(Number(event.target.value))}>
            {LOG_LIMITS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </SelectField>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => void load()}>
            <Symbol name="refresh" />
            {t('system.logs.lines.refresh')}
          </Button>
          <a
            href={LOG_DOWNLOAD_URL}
            download
            className="inline-flex items-center justify-center gap-2 rounded-full border border-ink-700 bg-ink-850 px-3.5 py-1.5 text-xs font-semibold text-mist-300 transition-colors hover:bg-ink-800 hover:text-mist-100"
          >
            <Symbol name="download" />
            {t('system.logs.lines.download')}
          </a>
          <Button variant="danger" size="sm" onClick={() => setClearing(true)}>
            <Symbol name="trash" />
            {t('system.logs.lines.clear')}
          </Button>
          <div className="w-full sm:ml-auto sm:w-auto">
            <Toggle label={t('system.logs.lines.auto')} checked={auto} onChange={setAuto} />
          </div>
        </div>

        {loadError !== null && <FormMessage>{errorText(t, loadError)}</FormMessage>}

        {data === null ? (
          loadError === null && (
            <p className="flex items-center justify-center gap-2 py-10 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : (
          <div className="flex flex-col gap-2">
            <p className="flex flex-wrap items-center gap-x-2 text-xs text-mist-500">
              <span>{t('system.logs.lines.count', { count: lines.length })}</span>
              {loadedAt !== null && (
                <>
                  <span aria-hidden="true">·</span>
                  <span>{t('system.logs.lines.loadedAt', { time: formatTime(loadedAt, language, true) })}</span>
                </>
              )}
              {loading && <Spinner className="h-3.5 w-3.5" />}
            </p>
            {lines.length === 0 ? (
              <p className="rounded-xl border border-dashed border-ink-700 px-6 py-10 text-center text-sm text-mist-500">{t('system.logs.lines.empty')}</p>
            ) : (
              <ol className="overflow-hidden rounded-xl border border-ink-700 bg-ink-900/60">
                {lines.map((line, index) => (
                  <LogRow key={`${line.time}-${index}`} line={line} language={language} onRequest={showRequest} />
                ))}
              </ol>
            )}
          </div>
        )}
      </Section>

      <Dialog
        open={clearing}
        title={t('system.logs.clear.title')}
        onClose={closeClear}
        footer={
          <>
            <Button variant="ghost" onClick={closeClear} disabled={clearBusy}>
              {t('common.actions.cancel')}
            </Button>
            <Button variant="danger" onClick={() => void clear()} loading={clearBusy}>
              {t('system.logs.clear.confirm')}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <p className="text-sm text-mist-300">{t('system.logs.clear.text')}</p>
          {clearProblem !== null && <FormMessage>{errorText(t, clearProblem)}</FormMessage>}
        </div>
      </Dialog>
    </div>
  )
}

/**
 * Eine Zeile. Am Telefon Kopf und Meldung untereinander, ab `md` in Spalten.
 * Lange Meldungen brechen um, auch mitten in einem Wort ohne Leerzeichen, damit
 * die Seite nie seitlich scrollt.
 */
function LogRow({ line, language, onRequest }: { line: LogLine; language: string; onRequest: (requestId: string) => void }) {
  const { t } = useTranslation()
  const requestId = line.request_id
  return (
    <li
      className={
        'flex flex-col gap-1 border-b border-ink-700/60 px-3 py-2 last:border-b-0 ' +
        'md:grid md:grid-cols-[9.5rem_5.5rem_minmax(0,11rem)_minmax(0,1fr)] md:gap-x-3 ' +
        (LOUD_LEVELS.has(line.level) ? 'bg-bad-500/5' : '')
      }
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 md:contents">
        <time dateTime={line.time} className="font-mono text-xs text-mist-500 tabular-nums md:pt-0.5">
          {formatLogTime(line.time, language)}
        </time>
        <span className="md:pt-px">
          <span className={'inline-flex rounded-md border px-1.5 py-px font-mono text-[0.7rem] font-semibold ' + (LEVEL_STYLE[line.level] ?? LEVEL_STYLE.DEBUG)}>
            {line.level}
          </span>
        </span>
        <span className="min-w-0 font-mono text-xs wrap-anywhere text-mist-500 md:pt-0.5">{line.logger}</span>
      </div>
      <div className="min-w-0">
        <p className="font-mono text-xs leading-relaxed whitespace-pre-wrap wrap-anywhere text-mist-200">{line.message}</p>
        {requestId && (
          <button
            type="button"
            onClick={() => onRequest(requestId)}
            title={t('system.logs.lines.requestFilter', { id: requestId })}
            className="mt-0.5 max-w-full font-mono text-xs wrap-anywhere text-accent-400 hover:underline"
          >
            #{requestId}
          </button>
        )}
      </div>
    </li>
  )
}
