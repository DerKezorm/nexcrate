import { Fragment, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { downloadClientsApi } from '../../api/downloadClients'
import type { DownloadClient, DownloadClientTestResult, PathMapping } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import { ClientDialog, type ClientTemplate } from '../clients/ClientDialog'
import { clientErrorText, clientKindText, protocolText, retentionText } from '../clients/clientText'
import { RadarrClientsDialog } from '../clients/RadarrClientsDialog'
import { useDownloadClients } from '../clients/useDownloadClients'
import { useSources } from '../import/useSources'
import { CardTags } from './CardTags'

/**
 * Reiter "Download-Programme": SABnzbd und qBittorrent eintragen, pruefen, aendern, entfernen, dazu
 * "Aus Radarr, Sonarr oder Lidarr holen", sobald es eine Verbindung gibt. Das Geheimnis zeigt die Seite nie,
 * nur ob eines gespeichert ist. Entfernen ist gesperrt, solange Downloads des Programms laufen.
 */
export function ClientSettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const { clients, error, reload } = useDownloadClients()
  // Alle Verbindungen, auch uebernommene: Ihre Einstellungen gibt eine uebernommene weiter heraus (Musik-Abschluss).
  const { sources } = useSources()
  // undefined: kein Dialog. null: neues Programm.
  const [editing, setEditing] = useState<DownloadClient | null | undefined>(undefined)
  const [template, setTemplate] = useState<ClientTemplate | null>(null)
  const [fetching, setFetching] = useState(false)
  const [removing, setRemoving] = useState<DownloadClient | null>(null)

  function closeDialog() {
    setEditing(undefined)
    setTemplate(null)
  }

  return (
    <div className="flex flex-col gap-4">
      <Section
        title={t('settings.clients.title')}
        intro={t('settings.clients.intro')}
        actions={
          clients !== null ? (
            <>
              {sources !== null && sources.length > 0 && (
                <Button variant="ghost" onClick={() => setFetching(true)}>
                  <Symbol name="import" />
                  {t('settings.clients.fromRadarr')}
                </Button>
              )}
              <Button
                onClick={() => {
                  setTemplate(null)
                  setEditing(null)
                }}
              >
                <Symbol name="plus" />
                {t('settings.clients.add')}
              </Button>
            </>
          ) : undefined
        }
      >
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
        {clients === null ? (
          error === null && (
            <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : clients.length === 0 ? (
          <p className="text-sm text-mist-500">{t('settings.clients.empty')}</p>
        ) : (
          <ul className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
            {clients.map((client) => (
              <li key={client.id} className="min-w-0">
                <ClientCard client={client} onEdit={() => setEditing(client)} onRemove={() => setRemoving(client)} onChanged={reload} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      {editing !== undefined && (
        <ClientDialog
          client={editing}
          template={template}
          onClose={closeDialog}
          onSaved={(saved) => {
            if (template !== null) notify(t('settings.clients.radarr.added', { name: saved.name }))
            closeDialog()
            reload()
          }}
        />
      )}
      {fetching && sources !== null && (
        <RadarrClientsDialog
          sources={sources}
          onClose={() => setFetching(false)}
          onPick={(next) => {
            setFetching(false)
            setTemplate(next)
            setEditing(null)
          }}
        />
      )}
      {removing && (
        <RemoveClientDialog
          client={removing}
          onClose={() => setRemoving(null)}
          onRemoved={() => {
            setRemoving(null)
            reload()
          }}
        />
      )}
    </div>
  )
}

function mappingKey(mapping: PathMapping): string {
  return `${mapping.remote}\u0000${mapping.local}`
}

function ClientCard({ client, onEdit, onRemove, onChanged }: { client: DownloadClient; onEdit: () => void; onRemove: () => void; onChanged: () => void }) {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const removeReasonId = useId()
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<DownloadClientTestResult | null>(null)
  const [testProblem, setTestProblem] = useState<unknown>(null)
  const [unmapping, setUnmapping] = useState<string | null>(null)
  const [mappingProblem, setMappingProblem] = useState<unknown>(null)
  const failed = client.last_error_code !== null
  const inUse = client.active_downloads > 0

  async function test() {
    setTesting(true)
    setTested(null)
    setTestProblem(null)
    try {
      // Ohne Geheimnis: Mit `id` nimmt der Server das gespeicherte.
      setTested(
        await downloadClientsApi.test({
          id: client.id,
          name: client.name,
          kind: client.kind,
          url: client.url,
          category: client.category,
          priority: client.priority,
          enabled: client.enabled,
          ...(client.username ? { username: client.username } : {}),
        }),
      )
    } catch (error) {
      setTestProblem(error)
    } finally {
      setTesting(false)
      // Ein Test aendert den letzten Fehler, die Liste zieht nach.
      onChanged()
    }
  }

  async function removeMapping(mapping: PathMapping) {
    const key = mappingKey(mapping)
    setUnmapping(key)
    setMappingProblem(null)
    try {
      await downloadClientsApi.update(client.id, { path_mappings: client.path_mappings.filter((entry) => mappingKey(entry) !== key) })
      notify(t('settings.clients.mappings.removed'))
      onChanged()
    } catch (error) {
      setMappingProblem(error)
    } finally {
      setUnmapping(null)
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/60 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-ink-700 bg-ink-850 text-accent-400">
          <Symbol name="download" className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold wrap-anywhere text-mist-100">{client.name}</h3>
          <p className="text-xs break-all text-mist-500">{client.url}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Badge>{clientKindText(t, client.kind)}</Badge>
        <Badge>{protocolText(t, client.protocol)}</Badge>
        {client.enabled ? (
          <Badge tone="ok">{t('settings.clients.state.enabled')}</Badge>
        ) : (
          <Badge>
            <Symbol name="eyeOff" className="h-3.5 w-3.5" />
            {t('settings.clients.state.disabled')}
          </Badge>
        )}
        {failed ? (
          <Badge tone="bad">
            <Symbol name="alert" className="h-3.5 w-3.5" />
            {t('settings.clients.state.failed')}
          </Badge>
        ) : (
          <Badge tone="ok">
            <Symbol name="check" className="h-3.5 w-3.5" />
            {t('settings.clients.state.ok')}
          </Badge>
        )}
        <Badge>{t('settings.clients.state.priority', { value: client.priority })}</Badge>
        {!client.has_secret && <Badge>{t('settings.clients.state.noSecret')}</Badge>}
        {client.from_source && (
          <Badge tone="accent">
            <Symbol name="import" className="h-3.5 w-3.5" />
            {t('settings.clients.state.fromSource', { name: client.from_source.name })}
          </Badge>
        )}
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
        <dt className="text-mist-500">{t('settings.clients.details.category')}</dt>
        <dd className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{client.category}</dd>
        {client.username && (
          <Fragment>
            <dt className="text-mist-500">{t('settings.clients.details.username')}</dt>
            <dd className="wrap-anywhere text-mist-200">{client.username}</dd>
          </Fragment>
        )}
        {client.retention && (
          <Fragment>
            <dt className="text-mist-500">{t('settings.clients.details.retention')}</dt>
            <dd className="text-mist-200">{retentionText(t, client, i18n.language)}</dd>
          </Fragment>
        )}
      </dl>
      {client.tags !== undefined && (
        <CardTags
          id={client.id}
          tags={client.tags}
          hint={t('settings.tagField.clientHint')}
          onSave={async (next) => (await downloadClientsApi.update(client.id, { tags: next })).tags ?? next}
        />
      )}
      {inUse && (
        <p className="flex items-center gap-2 text-sm text-info-500">
          <Symbol name="download" className="h-4 w-4 shrink-0" />
          {t('settings.clients.state.active', { count: client.active_downloads, value: formatNumber(client.active_downloads, i18n.language) })}
        </p>
      )}
      {failed && client.last_error_code !== null && (
        <p className="flex items-start gap-2 text-sm text-bad-500">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0 wrap-anywhere">{t('settings.clients.lastError.label', { text: clientErrorText(t, client.last_error_code) })}</span>
        </p>
      )}
      {client.last_error_code === 'client_auth_failed' && (
        <div className="flex flex-col items-start gap-2">
          {/* qBittorrent sperrt eine Adresse nach fuenf Fehlversuchen. Deshalb fragt nexcrate erst nach Speichern oder Pruefen wieder. */}
          {client.kind === 'qbittorrent' && <p className="text-sm text-mist-300">{t('settings.clients.authPaused')}</p>}
          <Button size="sm" onClick={onEdit} aria-label={t('settings.clients.actions.fixAccessLabel', { name: client.name })}>
            {t('settings.clients.actions.fixAccess')}
          </Button>
        </div>
      )}
      {client.path_mappings.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-xs font-semibold text-mist-400">{t('settings.clients.mappings.title')}</h4>
          <ul className="flex flex-col gap-2">
            {client.path_mappings.map((mapping) => {
              const key = mappingKey(mapping)
              return (
                <li key={key} className="flex min-w-0 flex-col gap-2 rounded-lg border border-ink-700 bg-ink-850 p-2.5 text-xs sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <p className="text-mist-500">
                      {t('settings.clients.mappings.clientSees', { name: client.name })} <span className="font-mono break-all text-mist-200">{mapping.remote}</span>
                    </p>
                    <p className="text-mist-500">
                      {t('settings.clients.mappings.nexcrateSees')} <span className="font-mono break-all text-mist-200">{mapping.local}</span>
                    </p>
                  </div>
                  <div className="shrink-0">
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={unmapping === key}
                      disabled={unmapping !== null}
                      onClick={() => void removeMapping(mapping)}
                      aria-label={t('settings.clients.mappings.removeLabel', { remote: mapping.remote })}
                    >
                      {t('settings.clients.mappings.remove')}
                    </Button>
                  </div>
                </li>
              )
            })}
          </ul>
          {mappingProblem !== null && <FormMessage>{errorText(t, mappingProblem)}</FormMessage>}
        </div>
      )}
      {tested !== null && (
        <FormMessage tone={tested.category_exists ? 'ok' : 'info'}>
          {t('settings.clients.test.ok', { kind: clientKindText(t, client.kind), version: tested.version })}{' '}
          {tested.category_exists
            ? t('settings.clients.test.categoryExists', { category: client.category })
            : t('settings.clients.test.categoryGone', { category: client.category })}
        </FormMessage>
      )}
      {testProblem !== null && <FormMessage>{errorText(t, testProblem)}</FormMessage>}
      <div className="mt-auto flex flex-col gap-1.5">
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" size="sm" loading={testing} onClick={() => void test()} aria-label={t('settings.clients.actions.testLabel', { name: client.name })}>
            {t('common.actions.test')}
          </Button>
          <Button variant="ghost" size="sm" onClick={onEdit} aria-label={t('settings.clients.actions.editLabel', { name: client.name })}>
            {t('common.actions.edit')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onRemove}
            disabled={inUse}
            aria-describedby={inUse ? removeReasonId : undefined}
            aria-label={t('settings.clients.actions.removeLabel', { name: client.name })}
          >
            {t('common.actions.remove')}
          </Button>
        </div>
        {inUse && (
          <p id={removeReasonId} className="text-xs wrap-anywhere text-mist-500">
            {t('settings.clients.actions.removeBlocked', { name: client.name })}
          </p>
        )}
      </div>
    </div>
  )
}

/** Entfernen mit Rueckfrage. Laufen inzwischen doch Downloads, sagt der Server 409 `client_in_use`. */
function RemoveClientDialog({ client, onClose, onRemoved }: { client: DownloadClient; onClose: () => void; onRemoved: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await downloadClientsApi.remove(client.id)
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
      title={t('settings.clients.remove.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('settings.clients.remove.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm wrap-anywhere text-mist-300">{t('settings.clients.remove.text', { name: client.name })}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
