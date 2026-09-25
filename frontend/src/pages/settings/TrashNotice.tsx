import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { trashApi } from '../../api/trash'
import type { TrashState } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner, Switch } from '../../components/ui'
import { formatDate, formatDateTime, formatList } from '../../lib/format'
import { listValues, shortCommit } from '../profiles/profileText'

/** Laenger als ein Lizenzname ist der ganze Lizenztext; der steht dann aufklappbar da. */
const LICENSE_NAME_MAX = 40

/**
 * Der Hinweis auf die TRaSH Guides in "Ueber nexcrate": Lizenz, Copyright-Zeile aus dem Stand,
 * Link, welcher Stand gilt, ein neuerer zum Uebernehmen und der Schalter fuer die taegliche
 * Nachfrage. Uebernommen wird nur auf Knopfdruck.
 */
export function TrashNotice() {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [state, setState] = useState<TrashState | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [updating, setUpdating] = useState(false)
  const [switching, setSwitching] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null)

  useEffect(() => {
    let current = true
    trashApi.state().then(
      (result) => current && setState(result),
      (error: unknown) => current && setLoadError(error),
    )
    return () => {
      current = false
    }
  }, [])

  function updateProblem(error: unknown): string {
    if (error instanceof ApiError && error.code === 'trash_update_breaks') {
      const versions = listValues(error.values.versions)
      return versions.length > 0 ? t('trash.breaks', { versions: formatList(versions, language) }) : t('trash.breaksPlain')
    }
    if (error instanceof ApiError && error.code === 'trash_unreachable') return t('trash.unreachable')
    return errorText(t, error)
  }

  async function adopt() {
    setUpdating(true)
    setMessage(null)
    try {
      setState(await trashApi.update())
      setMessage({ tone: 'ok', text: t('trash.adopted') })
    } catch (error) {
      setMessage({ tone: 'bad', text: updateProblem(error) })
    } finally {
      setUpdating(false)
    }
  }

  async function switchDaily(next: boolean) {
    setSwitching(true)
    setMessage(null)
    try {
      setState(await trashApi.settings(next))
    } catch (error) {
      setMessage({ tone: 'bad', text: errorText(t, error) })
    } finally {
      setSwitching(false)
    }
  }

  const link = state && /^https:\/\//.test(state.url) ? state.url : null
  const license = state && typeof state.license === 'string' && state.license.length > LICENSE_NAME_MAX ? state.license : null

  return (
    <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
      <h3 className="text-sm font-semibold text-mist-300">{t('trash.title')}</h3>
      <p className="text-sm text-mist-400">{t('trash.text')}</p>
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
          {state.copyright && (
            <p lang="en" className="text-sm wrap-anywhere text-mist-300">
              {state.copyright}
            </p>
          )}
          {license && (
            <details className="text-sm">
              <summary className="w-fit cursor-pointer font-medium text-accent-400 hover:underline">{t('trash.licenseShow')}</summary>
              <pre lang="en" className="mt-2 max-h-64 overflow-auto rounded-xl border border-ink-700 bg-ink-900 p-3 text-xs whitespace-pre-wrap text-mist-400">
                {license}
              </pre>
            </details>
          )}
          {link && (
            <a href={link} target="_blank" rel="noopener noreferrer" className="inline-flex w-fit items-center gap-1 text-sm font-medium text-accent-400 hover:underline">
              {t('trash.link')}
              <Symbol name="link" className="h-3.5 w-3.5" />
            </a>
          )}
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-mist-200">{t('trash.state', { commit: shortCommit(state.commit), date: formatDate(state.date, language) })}</span>
            <Badge>{state.source === 'fetched' ? t('trash.fetched') : t('trash.bundled')}</Badge>
          </div>
          <p className="text-xs text-mist-500">{state.checked_at ? t('trash.checkedAt', { time: formatDateTime(state.checked_at, language) }) : t('trash.neverChecked')}</p>
          {state.update_available && (
            <div className="flex flex-col gap-3 rounded-xl border border-accent-500/40 bg-accent-500/5 p-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-mist-100">{t('trash.updateAvailable', { commit: shortCommit(state.latest_commit) })}</p>
                <p className="mt-0.5 text-xs text-mist-500">{t('trash.adoptHint')}</p>
              </div>
              <div className="shrink-0">
                <Button size="sm" onClick={() => void adopt()} loading={updating}>
                  {t('trash.adopt')}
                </Button>
              </div>
            </div>
          )}
          {message && <FormMessage tone={message.tone}>{message.text}</FormMessage>}
          <Switch label={t('trash.daily')} hint={t('trash.dailyHint')} checked={state.updates_enabled} onChange={(next) => void switchDaily(next)} disabled={switching} />
        </>
      )}
    </div>
  )
}
