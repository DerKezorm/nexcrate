import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { upgradesApi } from '../../api/automatic'
import { errorText } from '../../api/client'
import type { UpgradesState } from '../../api/types'
import { Button, Field, FormMessage, Section, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime, formatNumber } from '../../lib/format'
import { Loading } from '../files/PatternFields'

/** Wie im Server: hoechstens so viele Verbesserungen am Tag lassen sich eintragen. */
const PER_DAY_MAX = 10000

/**
 * "Verbesserungen bremsen" im Reiter Automatik (entschieden am 24.09.2026): die Pause gilt fuer alles, was beim
 * Einschalten schon da ist; was danach kommt, wird wie gewohnt verbessert. Dazu eine Obergrenze fuer automatische
 * Verbesserungen in 24 Stunden. Fehlendes haelt beides nie auf, eigene Downloads von Hand auch nicht.
 */
export function UpgradeBrakes() {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const [state, setState] = useState<UpgradesState | null>(null)
  const [loadProblem, setLoadProblem] = useState<unknown>(null)
  const [perDay, setPerDay] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    const abort = new AbortController()
    upgradesApi.get(abort.signal).then(
      (result) => {
        if (abort.signal.aborted) return
        setState(result)
        setPerDay(String(result.per_day))
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setLoadProblem(error)
      },
    )
    return () => abort.abort()
  }, [])

  async function save(body: { paused?: boolean; per_day?: number }, done: (next: UpgradesState) => string) {
    setBusy(true)
    setProblem(null)
    try {
      const next = await upgradesApi.save(body)
      setState(next)
      setPerDay(String(next.per_day))
      notify(done(next))
    } catch (error) {
      setProblem(errorText(t, error))
    } finally {
      setBusy(false)
    }
  }

  function submitLimit(event: FormEvent) {
    event.preventDefault()
    const clean = perDay.trim() === '' ? '0' : perDay.trim()
    if (!/^\d{1,5}$/.test(clean) || Number(clean) > PER_DAY_MAX) return setProblem(t('settings.upgrades.perDayInvalid'))
    void save({ per_day: Number(clean) }, (next) =>
      next.per_day === 0 ? t('settings.upgrades.perDaySavedNone') : t('settings.upgrades.perDaySaved', { count: next.per_day, value: formatNumber(next.per_day, language) }),
    )
  }

  return (
    <Section title={t('settings.upgrades.title')} intro={t('settings.upgrades.intro')}>
      {state === null ? (
        loadProblem !== null ? <FormMessage>{errorText(t, loadProblem)}</FormMessage> : <Loading />
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <Switch
              label={t('settings.upgrades.pause')}
              hint={t('settings.upgrades.pauseHint')}
              checked={state.paused}
              disabled={busy}
              onChange={(next) => void save({ paused: next }, (saved) => (saved.paused ? t('settings.upgrades.pausedOn') : t('settings.upgrades.pausedOff')))}
            />
            {state.paused && state.paused_since !== null && (
              <p className="pl-12 text-sm text-mist-300">{t('settings.upgrades.pausedSince', { date: formatDateTime(state.paused_since, language) })}</p>
            )}
          </div>
          <form onSubmit={submitLimit} noValidate className="flex flex-wrap items-end gap-3">
            <div className="min-w-0 flex-1 sm:max-w-xs">
              <Field
                label={t('settings.upgrades.perDay')}
                hint={t('settings.upgrades.perDayHint')}
                inputMode="numeric"
                value={perDay}
                onChange={(event) => setPerDay(event.target.value)}
                maxLength={5}
                className="w-full min-w-0 tabular-nums"
              />
            </div>
            <Button type="submit" variant="ghost" loading={busy}>
              {t('common.actions.save')}
            </Button>
          </form>
          <p className="text-sm text-mist-400 tabular-nums">
            {state.per_day > 0
              ? t('settings.upgrades.usedOf', { used: formatNumber(state.used, language), limit: formatNumber(state.per_day, language) })
              : t('settings.upgrades.used', { count: state.used, value: formatNumber(state.used, language) })}
          </p>
          {problem !== null && <FormMessage>{problem}</FormMessage>}
        </div>
      )}
    </Section>
  )
}
