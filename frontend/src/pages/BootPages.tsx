import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { AuthFrame } from '../components/AuthFrame'
import { Logo } from '../components/Logo'
import { Symbol } from '../components/Symbol'
import { Button, Card, FormMessage, Spinner } from '../components/ui'

/** Solange der Start fragt, wer angemeldet ist. Kein Formular, das gleich wieder verschwindet. */
export function BootLoading() {
  const { t } = useTranslation()
  return (
    <div className="nc-glow flex min-h-dvh flex-col items-center justify-center gap-5 px-4" role="status" aria-live="polite">
      <Logo className="h-14 w-14 motion-safe:animate-pulse" />
      <span className="relative z-10 flex items-center gap-2 text-sm text-mist-500">
        <Spinner />
        {t('common.loading')}
      </span>
    </div>
  )
}

/**
 * Der Server antwortet nicht oder nur mit einem Fehler. "Erneut versuchen" laesst
 * diesen Bildschirm stehen, bis die Antwort da ist, statt kurz auf einen
 * Ladekreis zu springen.
 */
export function UnreachablePage({ error }: { error: unknown }) {
  const { t } = useTranslation()
  const { retry } = useAuth()
  const [busy, setBusy] = useState(false)
  // Mit Nummer hat der Server selbst geantwortet. Die Nummer fuehrt ins Protokoll.
  const detail = error instanceof ApiError && error.requestId ? errorText(t, error) : null

  async function again() {
    setBusy(true)
    try {
      await retry()
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthFrame>
      <Card className="flex flex-col gap-4">
        <div className="flex items-start gap-3">
          <Symbol name="alert" className="mt-1 h-6 w-6 shrink-0 text-bad-500" />
          <div className="min-w-0">
            <h1 className="text-xl font-bold tracking-tight">{t('auth.unreachable.title')}</h1>
            <p className="mt-1.5 text-sm text-mist-400">{t('auth.unreachable.text')}</p>
          </div>
        </div>
        {detail && <FormMessage>{detail}</FormMessage>}
        <Button onClick={() => void again()} loading={busy} className="w-full">
          {!busy && <Symbol name="refresh" />}
          {t('common.actions.retry')}
        </Button>
      </Card>
    </AuthFrame>
  )
}
