import { useCallback, useEffect, useState, type FormEvent } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { webhooksApi, type Webhook, type WebhookDelivery } from '../../api/outside'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, Field, FormMessage, Section, Spinner, Switch, Toggle } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime } from '../../lib/format'

/**
 * System, "Webhooks" (V4): Ereignisse an andere Programme, signiert. Nur der Besitzer legt ein
 * Ziel an; das Geheimnis zeigt die Seite genau einmal. Nexview braucht keins, es liest den Ereignisstrom.
 */
export function WebhookSettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const [hooks, setHooks] = useState<Webhook[] | null>(null)
  const [types, setTypes] = useState<string[]>([])
  const [problem, setProblem] = useState<unknown>(null)
  const [adding, setAdding] = useState(false)
  const [secret, setSecret] = useState<{ name: string; secret: string } | null>(null)
  const [showing, setShowing] = useState<Webhook | null>(null)

  const load = useCallback(() => {
    webhooksApi.list().then(
      (found) => {
        setHooks(found.items)
        setTypes(found.types)
      },
      (error: unknown) => setProblem(error),
    )
  }, [])

  useEffect(load, [load])

  async function act(action: () => Promise<unknown>, done: string) {
    setProblem(null)
    try {
      await action()
      notify(done)
      load()
    } catch (error) {
      setProblem(error)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Section
        title={t('system.webhooks.title')}
        intro={t('system.webhooks.intro')}
        actions={
          hooks !== null ? (
            <Button onClick={() => setAdding(true)}>
              <Symbol name="plus" />
              {t('system.webhooks.add')}
            </Button>
          ) : undefined
        }
      >
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        {hooks === null ? (
          problem === null && (
            <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : hooks.length === 0 ? (
          <p className="text-sm text-mist-500">{t('system.webhooks.empty')}</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {hooks.map((hook) => (
              <li key={hook.id}>
                <HookRow
                  hook={hook}
                  onToggle={(enabled) => void act(() => webhooksApi.change(hook.id, { enabled }), enabled ? t('system.webhooks.on') : t('system.webhooks.off'))}
                  onTest={() =>
                    void (async () => {
                      setProblem(null)
                      try {
                        const sent = await webhooksApi.test(hook.id)
                        notify(sent.state === 'delivered' ? t('system.webhooks.testOk') : t('system.webhooks.testFailed', { code: sent.error_code ?? '' }))
                        load()
                      } catch (error) {
                        setProblem(error)
                      }
                    })()
                  }
                  onDeliveries={() => setShowing(hook)}
                  onSecret={() =>
                    void (async () => {
                      try {
                        const made = await webhooksApi.newSecret(hook.id)
                        setSecret({ name: made.webhook.name, secret: made.secret })
                      } catch (error) {
                        setProblem(error)
                      }
                    })()
                  }
                  onRemove={() => void act(() => webhooksApi.remove(hook.id), t('system.webhooks.removed', { name: hook.name }))}
                />
              </li>
            ))}
          </ul>
        )}
      </Section>
      {adding && (
        <AddHookDialog
          types={types}
          onClose={() => setAdding(false)}
          onMade={(name, value) => {
            setAdding(false)
            setSecret({ name, secret: value })
            load()
          }}
        />
      )}
      {secret !== null && (
        <Dialog open title={t('system.webhooks.secretTitle', { name: secret.name })} onClose={() => setSecret(null)} footer={<Button onClick={() => setSecret(null)}>{t('common.actions.close')}</Button>}>
          <div className="flex flex-col gap-3">
            <FormMessage tone="info">{t('system.webhooks.secretOnce')}</FormMessage>
            <p data-testid="webhook-secret" className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-xs break-all text-mist-100 select-all">
              {secret.secret}
            </p>
            <p className="text-xs text-mist-500">{t('system.webhooks.signature')}</p>
          </div>
        </Dialog>
      )}
      {showing !== null && <DeliveriesDialog hook={showing} onClose={() => setShowing(null)} />}
    </div>
  )
}

function HookRow({
  hook,
  onToggle,
  onTest,
  onDeliveries,
  onSecret,
  onRemove,
}: {
  hook: Webhook
  onToggle: (enabled: boolean) => void
  onTest: () => void
  onDeliveries: () => void
  onSecret: () => void
  onRemove: () => void
}) {
  const { t, i18n } = useTranslation()
  return (
    <div className="flex min-w-0 flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/60 p-4">
      <div className="flex min-w-0 flex-col gap-1">
        <h3 className="font-semibold wrap-anywhere text-mist-100">{hook.name}</h3>
        <p className="font-mono text-xs break-all text-mist-500">{hook.url}</p>
        <p className="text-xs text-mist-500">
          {hook.types.length === 0 ? t('system.webhooks.allTypes') : t('system.webhooks.someTypes', { count: hook.types.length })}
          {hook.last !== null && ` · ${t('system.webhooks.last', { at: formatDateTime(hook.last.created_at, i18n.language) })}`}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {hook.last !== null && <Badge tone={hook.last.state === 'delivered' ? 'ok' : hook.last.state === 'failed' ? 'bad' : 'info'}>{deliveryState(t, hook.last)}</Badge>}
          {hook.failed > 0 && <Badge tone="bad">{t('system.webhooks.failedCount', { count: hook.failed })}</Badge>}
        </div>
      </div>
      <Switch label={t('system.webhooks.enabled')} checked={hook.enabled} onChange={onToggle} />
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" onClick={onTest}>
          {t('system.webhooks.test')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onDeliveries}>
          {t('system.webhooks.deliveries')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onSecret}>
          {t('system.webhooks.newSecret')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onRemove} aria-label={t('system.webhooks.removeLabel', { name: hook.name })}>
          {t('system.webhooks.remove')}
        </Button>
      </div>
    </div>
  )
}

function deliveryState(t: TFunction, delivery: WebhookDelivery): string {
  if (delivery.state === 'delivered') return t('system.webhooks.state.delivered')
  if (delivery.state === 'failed') return t('system.webhooks.state.failed', { code: delivery.error_code ?? '' })
  return t('system.webhooks.state.pending', { attempts: delivery.attempts })
}

function AddHookDialog({ types, onClose, onMade }: { types: string[]; onClose: () => void; onMade: (name: string, secret: string) => void }) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [all, setAll] = useState(true)
  const [chosen, setChosen] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      const made = await webhooksApi.create({ name, url, types: all ? [] : chosen })
      onMade(made.webhook.name, made.secret)
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      title={t('system.webhooks.add')}
      onClose={() => !busy && onClose()}
      footer={
        <Button type="submit" form="add-webhook" loading={busy}>
          {t('system.webhooks.create')}
        </Button>
      }
    >
      <form id="add-webhook" onSubmit={(event) => void submit(event)} className="flex flex-col gap-4">
        <Field label={t('system.webhooks.name')} value={name} onChange={(event) => setName(event.target.value)} autoComplete="off" />
        <Field label={t('system.webhooks.url')} hint={t('system.webhooks.urlHint')} value={url} onChange={(event) => setUrl(event.target.value)} inputMode="url" autoComplete="off" />
        <Toggle label={t('system.webhooks.everyType')} checked={all} onChange={setAll} />
        {!all && (
          <fieldset className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <legend className="mb-2 text-sm font-medium text-mist-300">{t('system.webhooks.types')}</legend>
            {types.map((type) => (
              <Toggle
                key={type}
                label={type}
                checked={chosen.includes(type)}
                onChange={(value) => setChosen((current) => (value ? [...current, type] : current.filter((item) => item !== type)))}
              />
            ))}
          </fieldset>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </form>
    </Dialog>
  )
}

function DeliveriesDialog({ hook, onClose }: { hook: Webhook; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const [items, setItems] = useState<WebhookDelivery[] | null>(null)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    let current = true
    webhooksApi.deliveries(hook.id).then(
      (found) => current && setItems(found.items),
      (error: unknown) => current && setProblem(error),
    )
    return () => {
      current = false
    }
  }, [hook.id])

  return (
    <Dialog open wide title={t('system.webhooks.deliveriesTitle', { name: hook.name })} onClose={onClose} footer={<Button onClick={onClose}>{t('common.actions.close')}</Button>}>
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {items === null ? (
        problem === null && <Spinner />
      ) : items.length === 0 ? (
        <p className="text-sm text-mist-500">{t('system.webhooks.noDeliveries')}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono text-xs text-mist-400">{item.event_type}</span>
              <Badge tone={item.state === 'delivered' ? 'ok' : item.state === 'failed' ? 'bad' : 'info'}>{deliveryState(t, item)}</Badge>
              <span className="text-xs text-mist-500">{formatDateTime(item.created_at, i18n.language)}</span>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  )
}
