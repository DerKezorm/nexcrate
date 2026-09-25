import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { logsApi } from '../../api/logs'
import type { LogMode, LogModeMinutes, LogModeName } from '../../api/types'
import { Button, FormMessage, Section } from '../../components/ui'
import { formatDate, formatTime, isToday } from '../../lib/format'

/** Von sparsam nach gespraechig, wie im Server. */
const MODES: readonly LogModeName[] = ['quiet', 'normal', 'detailed', 'trace']

/** Diese beiden schalten sich nach der gewaehlten Zeit selbst ab. */
const DEEP_MODES: readonly LogModeName[] = ['detailed', 'trace']

const DURATIONS: readonly LogModeMinutes[] = [30, 120, 480]

const ENV_VARIABLE = 'NEXCRATE_LOG_LEVEL'

/**
 * Wie ausfuehrlich mitgeschrieben wird. Sparsam und Normal gelten mit einem Klick.
 * Die beiden tiefen Stufen fragen erst dann nach der Dauer: Ein immer sichtbares
 * Dauerfeld liest sich wie "Normal fuer zwei Stunden", obwohl Normal keine Frist hat.
 */
export function LogModePanel({ mode, onChanged, onReload }: { mode: LogMode | null; onChanged: (mode: LogMode) => void; onReload: () => void }) {
  const { t, i18n } = useTranslation()
  const [asking, setAsking] = useState<LogModeName | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  // Der Server kann 409 `log_mode_fixed` melden, bevor die Seite davon weiss.
  const [fixedByAnswer, setFixedByAnswer] = useState(false)
  const fixed = (mode?.fixed_by_env ?? false) || fixedByAnswer
  const language = i18n.language

  const names: Record<LogModeName, string> = {
    quiet: t('system.logs.mode.quiet'),
    normal: t('system.logs.mode.normal'),
    detailed: t('system.logs.mode.detailed'),
    trace: t('system.logs.mode.trace'),
  }
  const descriptions: Record<LogModeName, string> = {
    quiet: t('system.logs.mode.descQuiet'),
    normal: t('system.logs.mode.descNormal'),
    detailed: t('system.logs.mode.descDetailed'),
    trace: t('system.logs.mode.descTrace'),
  }
  const durations: Record<number, string> = {
    30: t('system.logs.mode.minutes30'),
    120: t('system.logs.mode.minutes120'),
    480: t('system.logs.mode.minutes480'),
  }

  async function apply(next: LogModeName, minutes: LogModeMinutes) {
    setBusy(true)
    setProblem(null)
    try {
      onChanged(await logsApi.setMode({ mode: next, minutes }))
      setAsking(null)
    } catch (error) {
      if (error instanceof ApiError && error.code === 'log_mode_fixed') {
        setFixedByAnswer(true)
        setAsking(null)
        onReload()
      } else {
        setProblem(error)
      }
    } finally {
      setBusy(false)
    }
  }

  function pick(next: LogModeName) {
    if (fixed || busy || !mode) return
    if (DEEP_MODES.includes(next)) {
      // Auch bei der schon laufenden Stufe: So laesst sich die Frist verlaengern.
      setAsking(asking === next ? null : next)
      return
    }
    setAsking(null)
    if (next !== mode.mode) void apply(next, 0)
  }

  let until: string | null = null
  if (mode?.until) {
    const time = formatTime(mode.until, language)
    until = isToday(mode.until)
      ? t('system.logs.mode.until', { time })
      : t('system.logs.mode.untilDate', { date: formatDate(mode.until, language), time })
  }
  const description = mode && Object.hasOwn(descriptions, mode.mode) ? descriptions[mode.mode] : null

  return (
    <Section title={t('system.logs.mode.title')} intro={t('system.logs.mode.intro')}>
      <div className="flex flex-wrap gap-2" role="group" aria-label={t('system.logs.mode.label')}>
        {MODES.map((value) => {
          const active = mode?.mode === value
          return (
            <button
              key={value}
              type="button"
              aria-pressed={active}
              disabled={fixed || busy || !mode}
              onClick={() => pick(value)}
              className={
                'inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                'disabled:cursor-not-allowed disabled:opacity-50 ' +
                (active
                  ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                  : asking === value
                    ? 'border-accent-500/40 bg-ink-800 text-mist-100'
                    : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
              }
            >
              {names[value]}
              {DEEP_MODES.includes(value) && <span className="text-xs font-normal opacity-70">{t('system.logs.mode.temporary')}</span>}
            </button>
          )
        })}
      </div>

      {asking && !fixed && (
        <div className="flex flex-col gap-2 rounded-xl border border-accent-500/30 bg-ink-900/60 p-3 sm:flex-row sm:flex-wrap sm:items-center">
          <span className="text-sm text-mist-200">{t('system.logs.mode.question', { mode: names[asking] })}</span>
          <div className="flex flex-wrap gap-2">
            {DURATIONS.map((minutes) => (
              <Button key={minutes} size="sm" variant="ghost" disabled={busy} onClick={() => void apply(asking, minutes)}>
                {durations[minutes]}
              </Button>
            ))}
            <Button size="sm" variant="ghost" onClick={() => setAsking(null)}>
              {t('common.actions.cancel')}
            </Button>
          </div>
        </div>
      )}

      {fixed ? (
        <FormMessage tone="info">{t('system.logs.mode.fixed', { variable: ENV_VARIABLE })}</FormMessage>
      ) : (
        (description || until) && <p className="text-sm text-mist-400">{[description, until].filter(Boolean).join(' ')}</p>
      )}

      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </Section>
  )
}
