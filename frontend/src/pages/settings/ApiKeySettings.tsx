import { useState, type FormEvent } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { apiKeysApi } from '../../api/apiKeys'
import { API_BASE, errorText } from '../../api/client'
import type { ApiKey, ApiKeyCreated, ApiKeyScope } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, Field, FormMessage, Section, Spinner, Toggle } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatAge, formatDate } from '../../lib/format'
import { useApiKeys } from './useApiKeys'

/** Die Beschreibung der Schnittstelle liefert das Backend selbst aus, ausserhalb der Oberflaeche. */
const DOCS_PATH = `${API_BASE}/docs`

function scopeLabel(t: TFunction, scope: string): string {
  if (scope === 'read') return t('apikeys.scopes.read.label')
  if (scope === 'request') return t('apikeys.scopes.request.label')
  if (scope === 'operate') return t('apikeys.scopes.operate.label')
  // Ein Recht, das diese Oberflaeche noch nicht kennt, steht mit seinem Namen da.
  return scope
}

function scopeHint(t: TFunction, scope: ApiKeyScope): string {
  if (scope === 'read') return t('apikeys.scopes.read.hint')
  if (scope === 'request') return t('apikeys.scopes.request.hint')
  return t('apikeys.scopes.operate.hint')
}

/**
 * System, "API-Schluessel": Schluessel fuer andere Programme anlegen, benennen und widerrufen. Den Schluessel selbst
 * zeigt die Seite genau einmal, direkt nach dem Anlegen; danach nur noch seine letzten vier Zeichen.
 */
export function ApiKeySettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const { keys, error, reload } = useApiKeys()
  const [adding, setAdding] = useState(false)
  const [renaming, setRenaming] = useState<ApiKey | null>(null)
  const [revoking, setRevoking] = useState<ApiKey | null>(null)

  return (
    <div className="flex flex-col gap-4">
      <Section
        title={t('apikeys.title')}
        intro={t('apikeys.intro')}
        actions={
          keys !== null ? (
            <Button onClick={() => setAdding(true)}>
              <Symbol name="plus" />
              {t('apikeys.add')}
            </Button>
          ) : undefined
        }
      >
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
        {keys === null ? (
          error === null && (
            <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : keys.items.length === 0 ? (
          <p className="text-sm text-mist-500">{t('apikeys.empty')}</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {keys.items.map((key) => (
              <li key={key.id} className="min-w-0">
                <KeyRow apiKey={key} onRename={() => setRenaming(key)} onRevoke={() => setRevoking(key)} />
              </li>
            ))}
          </ul>
        )}
        <p className="text-sm text-mist-500">
          {t('apikeys.usage')}{' '}
          <a href={DOCS_PATH} target="_blank" rel="noreferrer" className="text-accent-400 underline-offset-2 hover:underline">
            {t('apikeys.docs')}
          </a>
        </p>
      </Section>

      {adding && keys !== null && (
        <AddKeyDialog
          scopes={keys.scopes}
          onClose={() => {
            setAdding(false)
            reload()
          }}
        />
      )}
      {renaming && (
        <RenameKeyDialog
          apiKey={renaming}
          onClose={() => setRenaming(null)}
          onRenamed={(saved) => {
            notify(t('apikeys.rename.done', { name: saved.name }))
            setRenaming(null)
            reload()
          }}
        />
      )}
      {revoking && (
        <RevokeKeyDialog
          apiKey={revoking}
          onClose={() => setRevoking(null)}
          onRevoked={() => {
            notify(t('apikeys.revoke.done', { name: revoking.name }))
            setRevoking(null)
            reload()
          }}
        />
      )}
    </div>
  )
}

function KeyRow({ apiKey, onRename, onRevoke }: { apiKey: ApiKey; onRename: () => void; onRevoke: () => void }) {
  const { t, i18n } = useTranslation()
  return (
    <div className="flex min-w-0 flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/60 p-4 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <h3 className="font-semibold wrap-anywhere text-mist-100">{apiKey.name}</h3>
        <div className="flex flex-wrap gap-1.5">
          {apiKey.scopes.map((scope) => (
            <Badge key={scope} tone={scope === 'read' ? 'neutral' : 'accent'}>
              {scopeLabel(t, scope)}
            </Badge>
          ))}
        </div>
        <p className="text-xs text-mist-500">
          <span className="font-mono">{t('apikeys.hint', { hint: apiKey.hint })}</span>
          {' · '}
          {t('apikeys.created', { date: formatDate(apiKey.created_at, i18n.language) })}
          {' · '}
          {apiKey.last_used_at === null ? t('apikeys.neverUsed') : t('apikeys.lastUsed', { age: formatAge(apiKey.last_used_at, i18n.language) })}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" onClick={onRename} aria-label={t('apikeys.actions.renameLabel', { name: apiKey.name })}>
          {t('apikeys.actions.rename')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onRevoke} aria-label={t('apikeys.actions.revokeLabel', { name: apiKey.name })}>
          {t('apikeys.actions.revoke')}
        </Button>
      </div>
    </div>
  )
}

/** Anlegen in zwei Schritten: erst Name und Rechte, dann der Schluessel, genau einmal. */
function AddKeyDialog({ scopes, onClose }: { scopes: string[]; onClose: () => void }) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [request, setRequest] = useState(false)
  const [operate, setOperate] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)
  const [copied, setCopied] = useState(false)

  async function create(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    const wanted: ApiKeyScope[] = ['read', ...(request ? (['request'] as const) : []), ...(operate ? (['operate'] as const) : [])]
    try {
      setCreated(await apiKeysApi.create({ name, scopes: wanted }))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    if (created === null) return
    try {
      await navigator.clipboard.writeText(created.key)
      setCopied(true)
    } catch {
      // Ohne Zwischenablage bleibt der Schluessel zum Markieren stehen.
      setCopied(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  if (created !== null) {
    return (
      <Dialog open title={t('apikeys.created_dialog.title', { name: created.name })} onClose={close} footer={<Button onClick={close}>{t('apikeys.created_dialog.done')}</Button>}>
        <div className="flex flex-col gap-3">
          <FormMessage tone="info">{t('apikeys.created_dialog.once')}</FormMessage>
          <p data-testid="new-api-key" className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-xs break-all text-mist-100 select-all">
            {created.key}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" onClick={() => void copy()}>
              {t('apikeys.created_dialog.copy')}
            </Button>
            {copied && (
              <span className="text-xs text-ok-500" role="status">
                {t('apikeys.created_dialog.copied')}
              </span>
            )}
          </div>
          <p className="text-sm text-mist-400">{t('apikeys.created_dialog.how')}</p>
        </div>
      </Dialog>
    )
  }

  return (
    <Dialog open title={t('apikeys.add_dialog.title')} onClose={close}>
      <form className="flex flex-col gap-4" onSubmit={(event) => void create(event)}>
        <Field label={t('apikeys.add_dialog.name')} hint={t('apikeys.add_dialog.nameHint')} value={name} onChange={(event) => setName(event.target.value)} maxLength={100} required autoFocus />
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-medium text-mist-300">{t('apikeys.add_dialog.scopes')}</legend>
          <Toggle label={scopeLabel(t, 'read')} hint={scopeHint(t, 'read')} checked disabled onChange={() => undefined} />
          {scopes.includes('request') && <Toggle label={scopeLabel(t, 'request')} hint={scopeHint(t, 'request')} checked={request} disabled={busy} onChange={setRequest} />}
          {scopes.includes('operate') && <Toggle label={scopeLabel(t, 'operate')} hint={scopeHint(t, 'operate')} checked={operate} disabled={busy} onChange={setOperate} />}
        </fieldset>
        <p className="text-xs text-mist-500">{t('apikeys.add_dialog.never')}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button type="submit" loading={busy}>
            {t('apikeys.add_dialog.confirm')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function RenameKeyDialog({ apiKey, onClose, onRenamed }: { apiKey: ApiKey; onClose: () => void; onRenamed: (saved: ApiKey) => void }) {
  const { t } = useTranslation()
  const [name, setName] = useState(apiKey.name)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      onRenamed(await apiKeysApi.rename(apiKey.id, name))
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog open title={t('apikeys.rename.title')} onClose={close}>
      <form className="flex flex-col gap-4" onSubmit={(event) => void save(event)}>
        <Field label={t('apikeys.add_dialog.name')} hint={t('apikeys.rename.hint')} value={name} onChange={(event) => setName(event.target.value)} maxLength={100} required autoFocus />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button type="submit" loading={busy}>
            {t('common.actions.save')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

/** Widerrufen mit Rueckfrage: es laesst sich nicht zuruecknehmen. */
function RevokeKeyDialog({ apiKey, onClose, onRevoked }: { apiKey: ApiKey; onClose: () => void; onRevoked: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function revoke() {
    setBusy(true)
    setProblem(null)
    try {
      await apiKeysApi.revoke(apiKey.id)
      onRevoked()
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
      title={t('apikeys.revoke.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void revoke()} loading={busy}>
            {t('apikeys.revoke.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm wrap-anywhere text-mist-300">{t('apikeys.revoke.text', { name: apiKey.name })}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
