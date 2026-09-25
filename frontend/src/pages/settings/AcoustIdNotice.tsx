import { useEffect, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { AcoustIdState } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, Spinner, Switch } from '../../components/ui'
import { formatDateTime } from '../../lib/format'

/** Hier legt man bei acoustid.org eine eigene Anwendung an und bekommt ihren Schluessel. */
export const ACOUSTID_NEW_APPLICATION = 'https://acoustid.org/new-application'

function acoustIdErrorText(t: TFunction, code: string): string {
  switch (code) {
    case 'acoustid_key_invalid':
      return t('music.privacy.acoustid.error.acoustid_key_invalid')
    case 'acoustid_unreachable':
      return t('music.privacy.acoustid.error.acoustid_unreachable')
    default:
      return t('music.privacy.acoustid.error.acoustid_error')
  }
}

/**
 * Fingerprinting mit AcoustID (M4.9, Entscheidungen 38 bis 41): der Schalter als Weg nach draussen, der eigene
 * Anwendungsschluessel dieser Installation (die Antwort des Besitzers vom 18.09.2026) und ob `fpcalc` im Abbild ist. Der
 * Schluessel kommt nie zurueck; die Seite sagt nur, ob einer hinterlegt ist.
 */
export function AcoustIdNotice() {
  const { t, i18n } = useTranslation()
  const [state, setState] = useState<AcoustIdState | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  const [checking, setChecking] = useState(false)

  async function check() {
    setChecking(true)
    setProblem(null)
    try {
      setState(await musicApi.checkAcoustId())
    } catch (error) {
      setProblem(error)
    } finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    const abort = new AbortController()
    musicApi.acoustId(abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) setState(result)
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setLoadError(error)
      },
    )
    return () => abort.abort()
  }, [])

  async function change(body: { enabled?: boolean; key?: string }) {
    setBusy(true)
    setProblem(null)
    setSaved(false)
    try {
      setState(await musicApi.changeAcoustId(body))
      if (body.key !== undefined) setKey('')
      // Der Server speichert einen Schluessel erst, wenn AcoustID ihn kennt.
      if (body.key) setSaved(true)
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
      <h3 className="text-sm font-semibold text-mist-300">{t('music.privacy.acoustid.title')}</h3>
      {state === null ? (
        loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner className="h-3.5 w-3.5" />
            {t('common.loading')}
          </p>
        )
      ) : (
        <>
          <Switch label={t('music.privacy.acoustid.switch')} hint={t('music.privacy.acoustid.hint')} checked={state.enabled} onChange={(next) => void change({ enabled: next })} disabled={busy} />
          {!state.fpcalc_available && <p className="text-xs text-bad-500">{t('music.privacy.acoustid.noTool')}</p>}
          <div className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
            <p className="text-sm text-mist-200">
              {state.key_source === 'own'
                ? t('music.privacy.acoustid.keySet')
                : state.key_source === 'shipped'
                  ? t('music.privacy.acoustid.keyShipped')
                  : t('music.privacy.acoustid.noKey')}
            </p>
            {state.key_source !== null && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                {state.last_error_code ? (
                  <span className="inline-flex items-center gap-1 text-sm text-bad-500">
                    <Symbol name="alert" className="h-3.5 w-3.5" />
                    {acoustIdErrorText(t, state.last_error_code)}
                  </span>
                ) : state.last_ok_at ? (
                  <span className="inline-flex items-center gap-1 text-sm text-ok-500">
                    <Symbol name="check" className="h-3.5 w-3.5" />
                    {t('music.privacy.acoustid.valid', { time: formatDateTime(state.last_ok_at, i18n.language) })}
                  </span>
                ) : (
                  <span className="text-sm text-mist-500">{t('music.privacy.acoustid.unchecked')}</span>
                )}
                <Button size="sm" variant="ghost" onClick={() => void check()} loading={checking} disabled={busy}>
                  <Symbol name="refresh" />
                  {t('music.privacy.acoustid.check')}
                </Button>
              </div>
            )}
            <p className="text-xs text-mist-500">
              {state.key_source === 'own' ? t('music.privacy.acoustid.ownNote') : t('music.privacy.acoustid.replaceNote')}{' '}
              <a href={ACOUSTID_NEW_APPLICATION} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-accent-400 hover:underline">
                {t('music.privacy.acoustid.getKey')}
                <Symbol name="link" className="h-3 w-3" />
              </a>
            </p>
          </div>
          <form
            className="flex flex-col gap-2 sm:flex-row sm:items-end"
            onSubmit={(event) => {
              event.preventDefault()
              if (key.trim() !== '') void change({ key: key.trim() })
            }}
          >
            <div className="min-w-0 flex-1">
              <Field
                label={t('music.privacy.acoustid.keyLabel')}
                hint={t('music.privacy.acoustid.keyHint')}
                value={key}
                onChange={(event) => setKey(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                maxLength={64}
              />
            </div>
            <div className="flex gap-2">
              <Button type="submit" loading={busy} disabled={key.trim() === ''}>
                {t('common.actions.save')}
              </Button>
              {state.key_set && (
                <Button variant="ghost" onClick={() => void change({ key: '' })} disabled={busy}>
                  {t('music.privacy.acoustid.remove')}
                </Button>
              )}
            </div>
          </form>
          {saved && <FormMessage tone="ok">{t('music.privacy.acoustid.saved')}</FormMessage>}
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        </>
      )}
    </div>
  )
}
