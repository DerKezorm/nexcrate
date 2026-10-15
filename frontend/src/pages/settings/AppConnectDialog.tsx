import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { deviceCodeApi, type DeviceCode, type DeviceCodeState } from '../../api/apiKeys'
import { API_BASE, errorText } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage, Spinner } from '../../components/ui'

/** Wie oft die Seite nachsieht, ob die App den Code genommen hat. */
const POLL_MS = 2000

/** Die Adresse, unter der dieser Browser nexcrate erreicht, mit Unterpfad, ohne `/api`. */
function ownAddress(): string {
  return new URL(API_BASE.replace(/\/api$/, '') || '/', window.location.origin).href.replace(/\/$/, '')
}

/**
 * System, "API-Schluessel", "iPhone-App verbinden": ein QR-Code mit Adresse und einer vorab bestaetigten Kopplung.
 * Die App scannt ihn und holt ihren eigenen Schluessel; ein nicht gescannter Code geht beim Schliessen wieder weg.
 */
export function AppConnectDialog({ onClose }: { onClose: (connected: boolean) => void }) {
  const { t } = useTranslation()
  const [code, setCode] = useState<DeviceCode | null>(null)
  const [state, setState] = useState<DeviceCodeState | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [round, setRound] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const current = useRef<DeviceCode | null>(null)
  const connected = state?.state === 'connected'

  useEffect(() => {
    let alive = true
    setCode(null)
    setState(null)
    setProblem(null)
    deviceCodeApi.create(ownAddress()).then(
      (made) => {
        if (!alive) return
        current.current = made
        setCode(made)
        setNow(Date.now())
      },
      (error: unknown) => {
        if (alive) setProblem(error)
      },
    )
    return () => {
      alive = false
    }
  }, [round])

  useEffect(() => {
    if (code === null || connected || state?.state === 'expired') return
    const timer = window.setInterval(() => {
      setNow(Date.now())
      deviceCodeApi.state(code.pairing_id).then(setState, () => setState({ state: 'expired', key_name: null, expires_at: code.expires_at }))
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [code, connected, state?.state])

  useEffect(() => {
    if (connected) current.current = null
  }, [connected])

  // Ein Code, den niemand genommen hat, bleibt nicht liegen.
  useEffect(
    () => () => {
      const open = current.current
      if (open !== null) void deviceCodeApi.withdraw(open.pairing_id).catch(() => undefined)
    },
    [],
  )

  const minutes = code === null ? 0 : Math.max(0, Math.ceil((Date.parse(code.expires_at) - now) / 60000))
  const expired = !connected && (state?.state === 'expired' || (code !== null && minutes === 0))

  return (
    <Dialog
      open
      title={t('apikeys.app.title')}
      onClose={() => onClose(connected)}
      footer={
        <>
          {expired && (
            <Button variant="ghost" onClick={() => setRound((value) => value + 1)}>
              {t('apikeys.app.again')}
            </Button>
          )}
          <Button onClick={() => onClose(connected)}>{t('apikeys.app.close')}</Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        {code === null && problem === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )}
        {code !== null && connected && (
          <FormMessage tone="info">{t('apikeys.app.connected', { name: state?.key_name ?? '' })}</FormMessage>
        )}
        {code !== null && !connected && (
          <>
            <p className="text-sm text-mist-300">{t('apikeys.app.scan')}</p>
            {!expired && (
              <img
                src={`data:image/svg+xml;utf8,${encodeURIComponent(code.qr_svg)}`}
                alt={t('apikeys.app.qr')}
                className="mx-auto w-56 rounded-xl bg-white p-2"
              />
            )}
            <p className="text-sm text-mist-500" role="status">
              {expired ? t('apikeys.app.expired') : t('apikeys.app.waiting', { minutes })}
            </p>
            <p className="text-xs wrap-anywhere text-mist-500">{t('apikeys.app.address', { address: code.address })}</p>
            <p className="text-xs text-mist-500">{t('apikeys.app.scopes')}</p>
          </>
        )}
      </div>
    </Dialog>
  )
}
