import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { MusicBrainzState } from '../../api/types'
import { FormMessage, Spinner, Switch } from '../../components/ui'
import { formatDateTime } from '../../lib/format'

/**
 * Die Schalter fuer MusicBrainz und das Cover Art Archive (Musik M1, Entscheidung 16), im selben Aufbau wie
 * `XemNotice.tsx`: ein Weg nach draussen, deshalb abschaltbar, ab Werk beide an. Aus bei MusicBrainz: nichts wird
 * gefragt, Hinzufuegen und Aktualisieren sagen `musicbrainz_disabled`, Gespeichertes bleibt sichtbar. Aus beim
 * Cover Art Archive: nur gespeicherte Cover werden gezeigt.
 */
export function MusicBrainzNotice() {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [state, setState] = useState<MusicBrainzState | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState<'musicbrainz' | 'covers' | null>(null)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    let current = true
    musicApi.settings().then(
      (result) => current && setState(result),
      (error: unknown) => current && setLoadError(error),
    )
    return () => {
      current = false
    }
  }, [])

  async function change(which: 'musicbrainz' | 'covers', next: boolean) {
    setBusy(which)
    setProblem(null)
    try {
      setState(await musicApi.changeSettings(which === 'musicbrainz' ? { enabled: next } : { covers_enabled: next }))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
      <h3 className="text-sm font-semibold text-mist-300">{t('music.privacy.title')}</h3>
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
          <Switch label={t('music.privacy.musicbrainz.switch')} hint={t('music.privacy.musicbrainz.hint')} checked={state.enabled} onChange={(next) => void change('musicbrainz', next)} disabled={busy !== null} />
          <p className="text-xs text-mist-500">{state.last_ok_at ? t('music.privacy.lastOk', { time: formatDateTime(state.last_ok_at, language) }) : t('music.privacy.never')}</p>
          {state.last_error_code && <p className="text-xs text-bad-500">{t('music.privacy.lastError', { text: errorText(t, new ApiError(0, state.last_error_code)) })}</p>}
          <Switch label={t('music.privacy.covers.switch')} hint={t('music.privacy.covers.hint')} checked={state.covers_enabled} onChange={(next) => void change('covers', next)} disabled={busy !== null} />
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        </>
      )}
    </div>
  )
}
