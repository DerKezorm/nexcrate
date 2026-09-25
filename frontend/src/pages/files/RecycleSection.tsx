import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { RECYCLE_DAYS_MAX, RECYCLE_DAYS_MIN, recycleApi } from '../../api/recycle'
import { Button, Field, FormMessage, Section } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import { Loading } from './PatternFields'

/** Die Tage aus dem Feld, oder null, wenn es keine ganze Zahl von 1 bis 365 ist. */
function readDays(value: string): number | null {
  const clean = value.trim()
  if (!/^\d{1,3}$/.test(clean)) return null
  const days = Number(clean)
  return days >= RECYCLE_DAYS_MIN && days <= RECYCLE_DAYS_MAX ? days : null
}

/**
 * Der Papierkorb: wie viele Tage ersetzte Dateien in `.nexcrate-recycle` bleiben, bevor nexcrate sie loescht. Eine Zahl
 * ausserhalb von 1 bis 365 geht gar nicht erst hinaus; der Server prueft trotzdem (422 `invalid_input`).
 */
export function RecycleSection() {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const reasonId = useId()
  const [saved, setSaved] = useState<number | null>(null)
  const [value, setValue] = useState('')
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const days = readDays(value)
  const changed = days !== null && days !== saved

  useEffect(() => {
    let current = true
    recycleApi.get().then(
      (result) => {
        if (!current) return
        setSaved(result.days)
        setValue(String(result.days))
      },
      (error: unknown) => {
        if (current) setLoadError(error)
      },
    )
    return () => {
      current = false
    }
  }, [])

  async function save() {
    if (busy || days === null) return
    setBusy(true)
    setProblem(null)
    try {
      const result = await recycleApi.save(days)
      setSaved(result.days)
      setValue(String(result.days))
      notify(t('settings.files.recycle.saved', { count: result.days, value: formatNumber(result.days, i18n.language) }))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={t('settings.files.recycle.title')} intro={t('settings.files.recycle.intro')}>
      {saved === null ? (
        loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : (
          <Loading />
        )
      ) : (
        <form
          noValidate
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          <Field
            label={t('settings.files.recycle.days')}
            hint={t('settings.files.recycle.hint')}
            type="number"
            inputMode="numeric"
            min={RECYCLE_DAYS_MIN}
            max={RECYCLE_DAYS_MAX}
            step={1}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            aria-invalid={days === null}
            className="w-28 tabular-nums"
          />
          {days === null && (
            <p id={reasonId} className="text-sm text-bad-500">
              {t('settings.files.recycle.invalid')}
            </p>
          )}
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
          <div>
            <Button
              onClick={() => void save()}
              loading={busy}
              disabled={!changed}
              aria-describedby={days === null ? reasonId : undefined}
              aria-label={t('settings.files.recycle.saveLabel')}
            >
              {t('common.actions.save')}
            </Button>
          </div>
        </form>
      )}
    </Section>
  )
}
