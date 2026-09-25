import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { tmdbApi } from '../../api/tmdb'
import type { TmdbState } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { TmdbTokenForm } from './TmdbTokenForm'

/** Reiter "TMDB": ob ein Token da ist, wann er zuletzt geprueft wurde, eintragen, ersetzen, entfernen. */
export function TmdbSettings() {
  const { t, i18n } = useTranslation()
  const [state, setState] = useState<TmdbState | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [message, setMessage] = useState<'saved' | 'removed' | null>(null)
  const [removing, setRemoving] = useState(false)

  useEffect(() => {
    let current = true
    tmdbApi.state().then(
      (result) => current && setState(result),
      (problem: unknown) => current && setError(problem),
    )
    return () => {
      current = false
    }
  }, [])

  function stored(next: TmdbState) {
    setState(next)
    setMessage('saved')
  }

  return (
    <Section title={t('tmdb.title')} intro={t('tmdb.intro')}>
      {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
      {state === null ? (
        error === null && (
          <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )
      ) : (
        <>
          {state.configured ? (
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="ok">
                <Symbol name="check" className="h-3.5 w-3.5" />
                {t('tmdb.configured')}
              </Badge>
              <span className="text-sm text-mist-500">
                {state.checked_at ? t('tmdb.checkedAt', { time: formatDateTime(state.checked_at, i18n.language) }) : t('tmdb.neverChecked')}
              </span>
            </div>
          ) : (
            <p className="text-sm text-mist-400">{t('tmdb.missing')}</p>
          )}
          {message === 'saved' && <FormMessage tone="ok">{t('tmdb.saved')}</FormMessage>}
          {message === 'removed' && <FormMessage tone="info">{t('tmdb.removed')}</FormMessage>}

          {state.configured ? (
            <>
              <div className="flex max-w-xl flex-col gap-3 border-t border-ink-700 pt-4">
                <h3 className="text-sm font-semibold text-mist-300">{t('tmdb.replaceTitle')}</h3>
                <TmdbTokenForm configured guide={false} onSaved={stored} />
              </div>
              <div className="border-t border-ink-700 pt-4">
                <Button variant="danger" size="sm" onClick={() => setRemoving(true)}>
                  <Symbol name="trash" />
                  {t('tmdb.remove')}
                </Button>
              </div>
            </>
          ) : (
            <div className="max-w-xl">
              <TmdbTokenForm configured={false} onSaved={stored} />
            </div>
          )}
        </>
      )}

      {removing && (
        <RemoveTokenDialog
          onClose={() => setRemoving(false)}
          onRemoved={() => {
            setRemoving(false)
            setState({ configured: false, checked_at: null })
            setMessage('removed')
          }}
        />
      )}
    </Section>
  )
}

function RemoveTokenDialog({ onClose, onRemoved }: { onClose: () => void; onRemoved: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await tmdbApi.remove()
      onRemoved()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog
      open
      title={t('tmdb.removeTitle')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('tmdb.removeConfirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">{t('tmdb.removeText')}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
