import { useCallback, useEffect, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { errorText } from '../api/client'
import { pairingsApi, type WaitingPairing } from '../api/outside'
import { Dialog } from './Dialog'
import { Symbol } from './Symbol'
import { Button, FormMessage, Toggle } from './ui'
import { useNotice } from './useNotice'

/** Wie oft die Seite nachsieht, ob ein Programm verbunden werden will. */
export const PAIRING_POLL_MS = 5000

function scopeLabel(t: TFunction, scope: string): string {
  if (scope === 'read') return t('apikeys.scopes.read.label')
  if (scope === 'request') return t('apikeys.scopes.request.label')
  if (scope === 'operate') return t('apikeys.scopes.operate.label')
  return scope
}

/**
 * Koppeln in einem Schritt (V4): will ein Programm wie Nexview verbunden werden, steht oben auf
 * jeder Seite ein Hinweis mit seinem Namen und dem Code, den das Programm auch zeigt. Bestaetigen legt einen Schluessel
 * mit den gewaehlten Rechten an, den nur das Programm zu sehen bekommt.
 */
export function PairingBanner() {
  const { t } = useTranslation()
  const [waiting, setWaiting] = useState<WaitingPairing[]>([])
  const [open, setOpen] = useState<WaitingPairing | null>(null)

  const load = useCallback(() => {
    pairingsApi.waiting().then(
      (found) => setWaiting(found.items),
      () => undefined,
    )
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, PAIRING_POLL_MS)
    return () => window.clearInterval(timer)
  }, [load])

  if (waiting.length === 0) return null
  return (
    <div className="flex flex-col gap-2" role="region" aria-label={t('apikeys.pairing.region')}>
      {waiting.map((item) => (
        <div
          key={item.pairing_id}
          className="flex flex-col gap-2 rounded-2xl border border-accent-500/40 bg-accent-500/10 px-4 py-3 text-sm sm:flex-row sm:items-center"
        >
          <Symbol name="link" className="h-4 w-4 shrink-0 text-accent-400" />
          <p className="min-w-0 flex-1 wrap-anywhere text-mist-100">
            {t('apikeys.pairing.asks', { app: item.app })} <span className="font-mono font-semibold">{item.code}</span>
          </p>
          <Button size="sm" onClick={() => setOpen(item)}>
            {t('apikeys.pairing.look')}
          </Button>
        </div>
      ))}
      {open !== null && (
        <PairingDialog
          pairing={open}
          onClose={() => {
            setOpen(null)
            load()
          }}
        />
      )}
    </div>
  )
}

function PairingDialog({ pairing, onClose }: { pairing: WaitingPairing; onClose: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [request, setRequest] = useState(pairing.scopes.includes('request'))
  const [operate, setOperate] = useState(pairing.scopes.includes('operate'))
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function answer(confirm: boolean) {
    setBusy(true)
    setProblem(null)
    try {
      if (confirm) {
        const scopes = ['read', ...(request ? ['request'] : []), ...(operate ? ['operate'] : [])]
        const made = await pairingsApi.accept(pairing.pairing_id, scopes)
        notify(t('apikeys.pairing.confirmed', { name: made.name }))
      } else {
        await pairingsApi.refuse(pairing.pairing_id)
        notify(t('apikeys.pairing.denied', { app: pairing.app }))
      }
      onClose()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      title={t('apikeys.pairing.title', { app: pairing.app })}
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={() => void answer(false)} disabled={busy}>
            {t('apikeys.pairing.deny')}
          </Button>
          <Button onClick={() => void answer(true)} loading={busy}>
            {t('apikeys.pairing.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-300">{t('apikeys.pairing.explain', { app: pairing.app })}</p>
        <p className="text-center font-mono text-2xl font-semibold tracking-widest text-mist-100" data-testid="pairing-code">
          {pairing.code}
        </p>
        <p className="text-sm text-mist-400">{t('apikeys.pairing.compare')}</p>
        <div className="flex flex-col gap-3">
          <Toggle label={scopeLabel(t, 'read')} hint={t('apikeys.scopes.read.hint')} checked onChange={() => undefined} disabled />
          <Toggle label={scopeLabel(t, 'request')} hint={t('apikeys.scopes.request.hint')} checked={request} onChange={setRequest} />
          <Toggle label={scopeLabel(t, 'operate')} hint={t('apikeys.scopes.operate.hint')} checked={operate} onChange={setOperate} />
        </div>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
