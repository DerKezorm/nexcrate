import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { prowlarrApi } from '../../api/indexers'
import type { ProwlarrConnection, ProwlarrSyncLevel, ProwlarrTag, ProwlarrTestResult } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PasswordField } from '../../components/PasswordField'
import { Segmented } from '../../components/Segmented'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, Field, FormMessage, Section, Spinner, Toggle } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime, formatNumber } from '../../lib/format'
import { countsText, prowlarrErrorText, syncLevelText } from './prowlarrText'

const SYNC_LEVELS: readonly ProwlarrSyncLevel[] = ['full', 'add_remove']

/** Die Prowlarr-Verbindungen, mit `reload` nach jeder Aenderung. */
function useProwlarr() {
  const [connections, setConnections] = useState<ProwlarrConnection[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [token, setToken] = useState(0)

  useEffect(() => {
    let current = true
    prowlarrApi.list().then(
      (result) => {
        if (!current) return
        setConnections(result)
        setError(null)
      },
      (problem: unknown) => {
        if (current) setError(problem)
      },
    )
    return () => {
      current = false
    }
  }, [token])

  const reload = useCallback(() => setToken((count) => count + 1), [])
  return { connections, error, reload }
}

/**
 * Prowlarr als Verbindung, oben im Reiter "Indexer": Adresse und Schluessel einmal, nexcrate haelt je Prowlarr-Indexer
 * einen eigenen aktuell (beim Speichern, auf Knopfdruck und alle 15 Minuten). Je Verbindung eine Karte mit dem letzten
 * Abgleich. `onChanged` laedt die Indexer darunter neu, weil ein Abgleich sie aendert.
 */
export function ProwlarrSection({ onChanged }: { onChanged: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const { connections, error, reload } = useProwlarr()
  // undefined: kein Dialog. null: neue Verbindung.
  const [editing, setEditing] = useState<ProwlarrConnection | null | undefined>(undefined)
  const [removing, setRemoving] = useState<ProwlarrConnection | null>(null)

  function changed() {
    reload()
    onChanged()
  }

  return (
    <Section
      title={t('indexers.prowlarr.title')}
      intro={t('indexers.prowlarr.intro')}
      actions={
        connections !== null ? (
          <Button variant={connections.length > 0 ? 'ghost' : 'primary'} onClick={() => setEditing(null)}>
            <Symbol name="plus" />
            {t('indexers.prowlarr.add')}
          </Button>
        ) : undefined
      }
    >
      {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
      {connections === null ? (
        error === null && (
          <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )
      ) : connections.length === 0 ? (
        <p className="text-sm text-mist-500">{t('indexers.prowlarr.empty')}</p>
      ) : (
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          {connections.map((connection) => (
            <li key={connection.id} className="min-w-0">
              <ProwlarrCard connection={connection} onEdit={() => setEditing(connection)} onRemove={() => setRemoving(connection)} onSynced={changed} />
            </li>
          ))}
        </ul>
      )}

      {editing !== undefined && (
        <ProwlarrDialog
          connection={editing}
          onClose={() => setEditing(undefined)}
          onSaved={(saved) => {
            notify(t('indexers.prowlarr.saved', { name: saved.name }))
            setEditing(undefined)
            changed()
          }}
        />
      )}
      {removing && (
        <RemoveProwlarrDialog
          connection={removing}
          onClose={() => setRemoving(null)}
          onRemoved={() => {
            notify(t('indexers.prowlarr.removed', { name: removing.name }))
            setRemoving(null)
            changed()
          }}
        />
      )}
    </Section>
  )
}

function ProwlarrCard({
  connection,
  onEdit,
  onRemove,
  onSynced,
}: {
  connection: ProwlarrConnection
  onEdit: () => void
  onRemove: () => void
  onSynced: () => void
}) {
  const { t, i18n } = useTranslation()
  const [syncing, setSyncing] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function sync() {
    setSyncing(true)
    setProblem(null)
    try {
      await prowlarrApi.sync(connection.id)
    } catch (error) {
      setProblem(error)
    } finally {
      setSyncing(false)
      // Auch ein gescheiterter Abgleich steht an der Verbindung.
      onSynced()
    }
  }

  const counts = connection.last_counts
  return (
    <div className="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/60 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-ink-700 bg-ink-850 text-accent-400">
          <Symbol name="refresh" className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold wrap-anywhere text-mist-100">{connection.name}</h3>
          <p className="text-xs break-all text-mist-500">{connection.url}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {connection.enabled ? (
          <Badge tone="ok">{t('indexers.prowlarr.enabled')}</Badge>
        ) : (
          <Badge>
            <Symbol name="eyeOff" className="h-3.5 w-3.5" />
            {t('indexers.prowlarr.disabled')}
          </Badge>
        )}
        {connection.last_error_code !== null ? (
          <Badge tone="bad">
            <Symbol name="alert" className="h-3.5 w-3.5" />
            {t('indexers.prowlarr.failed')}
          </Badge>
        ) : connection.last_sync_at !== null ? (
          <Badge tone="ok">
            <Symbol name="check" className="h-3.5 w-3.5" />
            {t('indexers.prowlarr.ok')}
          </Badge>
        ) : null}
        <Badge>{syncLevelText(t, connection.sync_level)}</Badge>
        <Badge>{t('indexers.prowlarr.indexerCount', { count: connection.indexer_count, value: formatNumber(connection.indexer_count, i18n.language) })}</Badge>
        {connection.version !== null && <Badge>{t('indexers.prowlarr.version', { version: connection.version })}</Badge>}
        {connection.tags.map((tag) => (
          <Badge key={tag.id} tone="accent">
            <Symbol name="tag" className="h-3.5 w-3.5" />
            {tag.label}
          </Badge>
        ))}
      </div>
      {connection.last_sync_at !== null && (
        <p className="text-sm text-mist-400">
          {t('indexers.prowlarr.lastSync', { time: formatDateTime(connection.last_sync_at, i18n.language) })}
          {counts !== null && connection.last_error_code === null && <> {countsText(t, counts)}</>}
        </p>
      )}
      {connection.last_error_code !== null && (
        <p className="flex items-start gap-2 text-sm text-bad-500">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0 wrap-anywhere">{prowlarrErrorText(t, connection.last_error_code)}</span>
        </p>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      <div className="mt-auto flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" loading={syncing} onClick={() => void sync()} aria-label={t('indexers.prowlarr.syncLabel', { name: connection.name })}>
          {t('indexers.prowlarr.sync')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onEdit} aria-label={t('indexers.prowlarr.editLabel', { name: connection.name })}>
          {t('common.actions.edit')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onRemove} aria-label={t('indexers.prowlarr.removeLabel', { name: connection.name })}>
          {t('common.actions.remove')}
        </Button>
      </div>
    </div>
  )
}

/**
 * Eine Verbindung eintragen oder aendern. Der Test liest Version und Prowlarrs Tags; erst danach stehen die Tags zum
 * Ankreuzen da. Der Schluessel ist nie vorbelegt: Beim Aendern bleibt der gespeicherte, solange das Feld leer ist.
 */
function ProwlarrDialog({
  connection,
  onClose,
  onSaved,
}: {
  connection: ProwlarrConnection | null
  onClose: () => void
  onSaved: (saved: ProwlarrConnection) => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(connection?.name ?? 'Prowlarr')
  const [url, setUrl] = useState(connection?.url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [enabled, setEnabled] = useState(connection?.enabled ?? true)
  const [level, setLevel] = useState<ProwlarrSyncLevel>(connection?.sync_level ?? 'full')
  const [tags, setTags] = useState<number[]>(connection?.tags.map((tag) => tag.id) ?? [])
  // Die Tags zur Auswahl: die gespeicherten, bis ein Test Prowlarrs eigene bringt.
  const [known, setKnown] = useState<ProwlarrTag[]>(connection?.tags ?? [])
  const [tested, setTested] = useState<ProwlarrTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [testProblem, setTestProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function test() {
    setTested(null)
    setTestProblem(null)
    const cleanUrl = url.trim()
    const key = apiKey.trim()
    if (cleanUrl === '') return setTestProblem(t('indexers.prowlarr.form.missingUrl'))
    if (connection === null && key === '') return setTestProblem(t('indexers.prowlarr.form.missingKey'))
    setTesting(true)
    try {
      const body = connection === null ? { url: cleanUrl, api_key: key } : { id: connection.id, url: cleanUrl, ...(key !== '' ? { api_key: key } : {}) }
      const result = await prowlarrApi.test(body)
      setTested(result)
      setKnown(result.tags)
      setTags((current) => current.filter((id) => result.tags.some((tag) => tag.id === id)))
    } catch (error) {
      setTestProblem(errorText(t, error))
    } finally {
      setTesting(false)
    }
  }

  async function save() {
    if (busy) return
    const cleanName = name.trim()
    const cleanUrl = url.trim()
    const key = apiKey.trim()
    if (cleanName === '') return setProblem(t('indexers.prowlarr.form.missingName'))
    if (cleanUrl === '') return setProblem(t('indexers.prowlarr.form.missingUrl'))
    if (connection === null && key === '') return setProblem(t('indexers.prowlarr.form.missingKey'))
    setBusy(true)
    setProblem(null)
    try {
      const saved =
        connection === null
          ? await prowlarrApi.create({ name: cleanName, url: cleanUrl, api_key: key, enabled, sync_level: level, tags })
          : await prowlarrApi.update(connection.id, {
              name: cleanName,
              enabled,
              sync_level: level,
              ...(cleanUrl !== connection.url ? { url: cleanUrl } : {}),
              ...(key !== '' ? { api_key: key } : {}),
              ...(tags.join(',') !== connection.tags.map((tag) => tag.id).join(',') ? { tags } : {}),
            })
      setApiKey('')
      onSaved(saved)
    } catch (error) {
      setProblem(errorText(t, error))
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void save()
  }

  function close() {
    if (busy) return
    setApiKey('')
    onClose()
  }

  function toggleTag(id: number, on: boolean) {
    setTags((current) => (on ? [...current.filter((entry) => entry !== id), id].sort((a, b) => a - b) : current.filter((entry) => entry !== id)))
  }

  const keyHint = connection === null ? t('indexers.prowlarr.form.apiKeyHint') : connection.has_api_key ? t('indexers.prowlarr.form.keySaved') : t('indexers.prowlarr.form.keyNone')
  return (
    <Dialog
      open
      title={connection ? t('indexers.prowlarr.form.dialogEdit', { name: connection.name }) : t('indexers.prowlarr.form.dialogAdd')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void save()} loading={busy}>
            {connection === null ? t('indexers.prowlarr.form.saveAndSync') : t('common.actions.save')}
          </Button>
        </>
      }
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t('indexers.prowlarr.form.name')} value={name} onChange={(event) => setName(event.target.value)} maxLength={100} autoComplete="off" />
        <Field
          label={t('indexers.prowlarr.form.url')}
          hint={t('indexers.prowlarr.form.urlHint')}
          value={url}
          onChange={(event) => {
            setUrl(event.target.value)
            setTested(null)
          }}
          inputMode="url"
          maxLength={2048}
          autoComplete="off"
          spellCheck={false}
          placeholder="http://prowlarr:9696"
          className="w-full min-w-0"
          autoFocus
        />
        <PasswordField
          label={t('indexers.prowlarr.form.apiKey')}
          hint={keyHint}
          value={apiKey}
          onChange={(event) => {
            setApiKey(event.target.value)
            setTested(null)
          }}
          autoComplete="new-password"
          maxLength={200}
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => void test()} loading={testing}>
            {t('common.actions.test')}
          </Button>
          {testing && (
            <span className="text-xs text-mist-500" role="status">
              {t('indexers.prowlarr.form.checking')}
            </span>
          )}
        </div>
        {tested !== null && (
          <FormMessage tone="ok">{t('indexers.prowlarr.form.tested', { version: tested.version, count: tested.indexer_count })}</FormMessage>
        )}
        {testProblem !== null && <FormMessage>{testProblem}</FormMessage>}

        <div className="flex flex-col gap-1.5">
          <p className="text-sm font-medium text-mist-300">{t('indexers.prowlarr.form.level')}</p>
          <Segmented value={level} options={SYNC_LEVELS} onChange={setLevel} label={(value) => syncLevelText(t, value)} ariaLabel={t('indexers.prowlarr.form.level')} />
          <p className="text-xs text-mist-500">{level === 'full' ? t('indexers.prowlarr.form.levelFullHint') : t('indexers.prowlarr.form.levelAddRemoveHint')}</p>
        </div>

        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-1.5 text-sm font-medium text-mist-300">{t('indexers.prowlarr.form.tags')}</legend>
          <p className="text-xs text-mist-500">{t('indexers.prowlarr.form.tagsHint')}</p>
          {known.length === 0 ? (
            <p className="text-sm text-mist-500">{tested === null ? t('indexers.prowlarr.form.tagsAfterTest') : t('indexers.prowlarr.form.tagsNone')}</p>
          ) : (
            <ul className="grid grid-cols-[repeat(2,minmax(0,1fr))] gap-1.5 sm:grid-cols-[repeat(3,minmax(0,1fr))]">
              {known.map((tag) => {
                const checked = tags.includes(tag.id)
                return (
                  <li key={tag.id} className="min-w-0">
                    <label
                      className={
                        'flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 text-sm ' +
                        (checked ? 'border-accent-500/50 bg-accent-500/5' : 'border-ink-700 bg-ink-900/60')
                      }
                    >
                      <input type="checkbox" className="h-4 w-4 shrink-0 accent-accent-500" checked={checked} onChange={(event) => toggleTag(tag.id, event.target.checked)} />
                      <span className="min-w-0 flex-1 wrap-anywhere text-mist-100">{tag.label}</span>
                    </label>
                  </li>
                )
              })}
            </ul>
          )}
        </fieldset>

        <Toggle label={t('indexers.prowlarr.form.enabled')} hint={t('indexers.prowlarr.form.enabledHint')} checked={enabled} onChange={setEnabled} />
        {problem && <FormMessage>{problem}</FormMessage>}
      </form>
    </Dialog>
  )
}

function RemoveProwlarrDialog({ connection, onClose, onRemoved }: { connection: ProwlarrConnection; onClose: () => void; onRemoved: () => void }) {
  const { t } = useTranslation()
  const [keep, setKeep] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await prowlarrApi.remove(connection.id, keep)
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
      title={t('indexers.prowlarr.remove.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('indexers.prowlarr.remove.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">
          {keep
            ? t('indexers.prowlarr.remove.textKeep', { name: connection.name, count: connection.indexer_count })
            : t('indexers.prowlarr.remove.text', { name: connection.name, count: connection.indexer_count })}
        </p>
        <Toggle label={t('indexers.prowlarr.remove.keep')} hint={t('indexers.prowlarr.remove.keepHint')} checked={keep} onChange={setKeep} />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
