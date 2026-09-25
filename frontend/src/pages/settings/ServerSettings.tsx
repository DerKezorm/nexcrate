import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { mediaServersApi } from '../../api/mediaServers'
import type { MediaServer, MediaServerLibrary } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner, Toggle } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime, formatNumber } from '../../lib/format'
import { ServerDialog } from '../servers/ServerDialog'
import { libraryKindText, notifyText, PATH_UNMATCHED, serverErrorText, serverKindText } from '../servers/serverText'
import { useMediaServers } from '../servers/useMediaServers'

/**
 * Reiter "Medienserver": Plex, Jellyfin und Emby eintragen, pruefen, aendern, entfernen. Je Bibliothek ein Schalter,
 * ob nexcrate ihr Aenderungen meldet. Den Token oder API-Schluessel zeigt die Seite nie, nur ob einer gespeichert ist.
 */
export function ServerSettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const { servers, error, reload } = useMediaServers()
  // undefined: kein Dialog. null: neuer Server.
  const [editing, setEditing] = useState<MediaServer | null | undefined>(undefined)
  const [removing, setRemoving] = useState<MediaServer | null>(null)

  return (
    <div className="flex flex-col gap-4">
      <Section
        title={t('settings.servers.title')}
        intro={t('settings.servers.intro')}
        actions={
          servers !== null ? (
            <Button onClick={() => setEditing(null)}>
              <Symbol name="plus" />
              {t('settings.servers.add')}
            </Button>
          ) : undefined
        }
      >
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
        {servers === null ? (
          error === null && (
            <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : servers.length === 0 ? (
          <p className="text-sm text-mist-500">{t('settings.servers.empty')}</p>
        ) : (
          <ul className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
            {servers.map((server) => (
              <li key={server.id} className="min-w-0">
                <ServerCard server={server} onEdit={() => setEditing(server)} onRemove={() => setRemoving(server)} onChanged={reload} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      {editing !== undefined && (
        <ServerDialog
          server={editing}
          onClose={() => setEditing(undefined)}
          onSaved={(saved) => {
            notify(t('settings.servers.saved', { name: saved.name }))
            setEditing(undefined)
            reload()
          }}
        />
      )}
      {removing && (
        <RemoveServerDialog
          server={removing}
          onClose={() => setRemoving(null)}
          onRemoved={() => {
            notify(t('settings.servers.removed', { name: removing.name }))
            setRemoving(null)
            reload()
          }}
        />
      )}
    </div>
  )
}

function ServerCard({ server, onEdit, onRemove, onChanged }: { server: MediaServer; onEdit: () => void; onRemove: () => void; onChanged: () => void }) {
  const { t, i18n } = useTranslation()
  const [checking, setChecking] = useState(false)
  const [checked, setChecked] = useState<MediaServer | null>(null)
  const [checkProblem, setCheckProblem] = useState<unknown>(null)
  const [switching, setSwitching] = useState<string | null>(null)
  const [switchProblem, setSwitchProblem] = useState<unknown>(null)
  const unmatched = server.last_error_code === PATH_UNMATCHED
  const failed = server.last_error_code !== null && !unmatched

  async function check() {
    setChecking(true)
    setChecked(null)
    setCheckProblem(null)
    try {
      setChecked(await mediaServersApi.check(server.id))
    } catch (error) {
      setCheckProblem(error)
    } finally {
      setChecking(false)
      // Eine Pruefung aendert den letzten Fehler und die Bibliotheken, die Liste zieht nach.
      onChanged()
    }
  }

  async function switchLibrary(library: MediaServerLibrary, refresh: boolean) {
    setSwitching(library.id)
    setSwitchProblem(null)
    try {
      await mediaServersApi.update(server.id, { libraries: [{ id: library.id, refresh }] })
      onChanged()
    } catch (error) {
      setSwitchProblem(error)
    } finally {
      setSwitching(null)
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/60 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-ink-700 bg-ink-850 text-accent-400">
          <Symbol name="server" className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold wrap-anywhere text-mist-100">{server.name}</h3>
          <p className="text-xs break-all text-mist-500">{server.url}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Badge>{serverKindText(t, server.kind)}</Badge>
        {server.version && <Badge>{server.version}</Badge>}
        {server.enabled ? (
          <Badge tone="ok">{t('settings.servers.state.enabled')}</Badge>
        ) : (
          <Badge>
            <Symbol name="eyeOff" className="h-3.5 w-3.5" />
            {t('settings.servers.state.disabled')}
          </Badge>
        )}
        {failed ? (
          <Badge tone="bad">
            <Symbol name="alert" className="h-3.5 w-3.5" />
            {t('settings.servers.state.failed')}
          </Badge>
        ) : unmatched ? (
          <Badge tone="accent">
            <Symbol name="info" className="h-3.5 w-3.5" />
            {t('settings.servers.state.hint')}
          </Badge>
        ) : (
          <Badge tone="ok">
            <Symbol name="check" className="h-3.5 w-3.5" />
            {t('settings.servers.state.ok')}
          </Badge>
        )}
        {!server.has_token && <Badge>{t('settings.servers.state.noToken')}</Badge>}
      </div>
      <p className="text-sm text-mist-400">{notifyText(t, server.last_notify_at === null ? null : server.last_notify_result, server.last_notify_at === null ? '' : formatDateTime(server.last_notify_at, i18n.language))}</p>
      {failed && server.last_error_code !== null && (
        <p className="flex items-start gap-2 text-sm text-bad-500">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0 wrap-anywhere">{t('settings.servers.lastError.label', { text: serverErrorText(t, server.last_error_code) })}</span>
        </p>
      )}
      {unmatched && <FormMessage tone="info">{t('settings.servers.unmatched')}</FormMessage>}
      <div className="flex flex-col gap-2">
        <h4 className="text-xs font-semibold text-mist-400">{t('settings.servers.libraries.title')}</h4>
        {server.libraries.length === 0 ? (
          <p className="text-sm text-mist-500">{t('settings.servers.libraries.none')}</p>
        ) : (
          <>
            <ul className="flex flex-col gap-2">
              {server.libraries.map((library) => (
                <li key={library.id} className="min-w-0 rounded-lg border border-ink-700 bg-ink-850 p-2.5">
                  <Toggle
                    label={t('settings.servers.libraries.refresh', { name: library.name })}
                    hint={
                      <span className="break-all">
                        {libraryKindText(t, library.kind)}
                        {library.locations.length > 0 && <span className="font-mono"> · {library.locations.join(' · ')}</span>}
                      </span>
                    }
                    checked={library.refresh}
                    disabled={switching !== null}
                    onChange={(next) => void switchLibrary(library, next)}
                  />
                </li>
              ))}
            </ul>
            <p className="text-xs text-mist-500">{t('settings.servers.libraries.hint')}</p>
          </>
        )}
        {switchProblem !== null && <FormMessage>{errorText(t, switchProblem)}</FormMessage>}
      </div>
      {server.path_mappings.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-xs font-semibold text-mist-400">{t('settings.servers.mappings.title')}</h4>
          <ul className="flex flex-col gap-2">
            {server.path_mappings.map((mapping) => (
              <li key={`${mapping.local}\u0000${mapping.remote}`} className="flex min-w-0 flex-col gap-0.5 rounded-lg border border-ink-700 bg-ink-850 p-2.5 text-xs">
                <p className="text-mist-500">
                  {t('settings.servers.mappings.local')} <span className="font-mono break-all text-mist-200">{mapping.local}</span>
                </p>
                <p className="text-mist-500">
                  {t('settings.servers.mappings.remote')} <span className="font-mono break-all text-mist-200">{mapping.remote}</span>
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {checked !== null && (
        <FormMessage tone="ok">
          {t('settings.servers.test.ok', {
            kind: serverKindText(t, checked.kind),
            version: checked.version ?? '',
            count: checked.libraries.length,
            value: formatNumber(checked.libraries.length, i18n.language),
          })}
        </FormMessage>
      )}
      {checkProblem !== null && <FormMessage>{errorText(t, checkProblem)}</FormMessage>}
      <div className="mt-auto flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" loading={checking} onClick={() => void check()} aria-label={t('settings.servers.actions.testLabel', { name: server.name })}>
          {t('common.actions.test')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onEdit} aria-label={t('settings.servers.actions.editLabel', { name: server.name })}>
          {t('common.actions.edit')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onRemove} aria-label={t('settings.servers.actions.removeLabel', { name: server.name })}>
          {t('common.actions.remove')}
        </Button>
      </div>
    </div>
  )
}

/** Entfernen mit Rueckfrage. Der Medienserver selbst wird dabei nicht angesprochen. */
function RemoveServerDialog({ server, onClose, onRemoved }: { server: MediaServer; onClose: () => void; onRemoved: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await mediaServersApi.remove(server.id)
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
      title={t('settings.servers.remove.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('settings.servers.remove.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm wrap-anywhere text-mist-300">{t('settings.servers.remove.text', { name: server.name })}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
