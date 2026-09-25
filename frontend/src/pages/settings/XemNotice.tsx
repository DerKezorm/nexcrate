import { useEffect, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { xemApi } from '../../api/numbering'
import type { XemSettings } from '../../api/types'
import { FormMessage, Spinner, Switch } from '../../components/ui'
import { formatDateTime } from '../../lib/format'

function xemErrorText(t: TFunction, code: string): string {
  switch (code) {
    case 'xem_timeout':
      return t('series.numberingDialog.xemState.xem_timeout')
    case 'xem_certificate':
      return t('series.numberingDialog.xemState.xem_certificate')
    case 'xem_blocked':
      return t('series.numberingDialog.xemState.xem_blocked')
    case 'xem_unreachable':
      return t('series.numberingDialog.xemState.xem_unreachable')
    default:
      return t('series.numberingDialog.xemState.unknown', { code })
  }
}

/**
 * Der Schalter fuer TheXEM neben dem fuer die TRaSH Guides (S3, Entscheidung 15): ein Weg nach draussen, deshalb
 * abschaltbar. Darunter, wann TheXEM zuletzt geantwortet hat und der letzte Fehler. Gespeichert wird sofort; lehnt der
 * Server ab, bleibt der alte Stand stehen.
 */
export function XemNotice() {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [state, setState] = useState<XemSettings | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    let current = true
    xemApi.get().then(
      (result) => current && setState(result),
      (error: unknown) => current && setLoadError(error),
    )
    return () => {
      current = false
    }
  }, [])

  async function change(next: boolean) {
    setBusy(true)
    setProblem(null)
    try {
      setState(await xemApi.save(next))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
      <h3 className="text-sm font-semibold text-mist-300">{t('series.xem.title')}</h3>
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
          <Switch label={t('series.xem.switch')} hint={t('series.xem.hint')} checked={state.enabled} onChange={(next) => void change(next)} disabled={busy} />
          <p className="text-xs text-mist-500">{state.last_ok_at ? t('series.xem.lastOk', { time: formatDateTime(state.last_ok_at, language) }) : t('series.xem.never')}</p>
          {state.last_error_code && <p className="text-xs text-bad-500">{t('series.xem.lastError', { text: xemErrorText(t, state.last_error_code) })}</p>}
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        </>
      )}
    </div>
  )
}
