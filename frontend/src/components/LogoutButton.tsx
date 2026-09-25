import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { Symbol } from './Symbol'
import { Spinner } from './ui'
import { useNotice } from './useNotice'

/**
 * Abmelden aus der Kopfzeile. Scheitert es, bleibt die App stehen und sagt es:
 * Wer glaubt, abgemeldet zu sein, waehrend das Cookie noch gilt, laesst ein
 * fremdes Geraet offen zurueck.
 */
export function LogoutButton() {
  const { t } = useTranslation()
  const { logout, me } = useAuth()
  const notify = useNotice()
  const [busy, setBusy] = useState(false)

  async function leave() {
    setBusy(true)
    try {
      await logout()
    } catch (error) {
      notify(errorText(t, error))
      setBusy(false)
    }
  }

  const label = t('common.nav.logout')
  return (
    <button
      type="button"
      onClick={() => void leave()}
      disabled={busy}
      aria-label={label}
      title={me ? t('common.nav.logoutAs', { name: me.username }) : label}
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-ink-700 bg-ink-850 text-mist-500 transition-colors hover:bg-ink-800 hover:text-mist-100 disabled:opacity-60"
    >
      {busy ? <Spinner className="h-4 w-4" /> : <Symbol name="logout" className="h-4 w-4" />}
    </button>
  )
}
