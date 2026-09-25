import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { systemApi } from '../../api/system'
import { FormMessage, Spinner } from '../../components/ui'

/** Wie lange nach dem Neustart gewartet wird, bis die Seite ohne Antwort aufgibt und es sagt. */
const WAIT_LIMIT_MS = 45 * 60 * 1000
const POLL_MS = 5000

/**
 * Nach dem Einspielen: warten, bis ein neuer Prozess antwortet (seine Startzeit ist eine andere als `started`), dann
 * neu laden. Danach ist niemand mehr angemeldet, das Neuladen fuehrt zur Anmeldung.
 */
export function RestartWaiting({ started }: { started: string }) {
  const { t } = useTranslation()
  const [gaveUp, setGaveUp] = useState(false)

  useEffect(() => {
    const begun = Date.now()
    let stopped = false
    const timer = window.setInterval(() => {
      if (stopped) return
      if (Date.now() - begun > WAIT_LIMIT_MS) {
        stopped = true
        window.clearInterval(timer)
        setGaveUp(true)
        return
      }
      systemApi.health().then(
        (health) => {
          if (health.started !== started && !stopped) {
            stopped = true
            window.clearInterval(timer)
            window.location.reload()
          }
        },
        () => undefined,
      )
    }, POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [started])

  return gaveUp ? (
    <FormMessage>{t('restore.gaveUp')}</FormMessage>
  ) : (
    <p className="flex items-center gap-2 text-sm text-mist-300" role="status" aria-live="polite">
      <Spinner />
      {t('restore.restarting')}
    </p>
  )
}
