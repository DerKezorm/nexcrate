import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { PROFILE_KINDS } from '../../api/profiles'
import type { MediaKind, Version } from '../../api/types'
import { versionsApi } from '../../api/versions'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { ProfileRow, type ProfileActions } from '../profiles/ProfileRow'
import { Tile, TileHeader } from '../settings/parts'
import { DelayRow } from './DelayRow'
import { VersionDialog } from './VersionDialog'

/**
 * Die Fassungen einer Medienart: Name und wie viele Titel sie haben. Anlegen, umbenennen,
 * entfernen. Seit Schritt 2b steht bei Arten mit Profilen (zunaechst Filme) das Profil darunter,
 * sofern die Seite `profileActions` mitgibt. Den Satz ohne Fassung gibt die Seite je Art mit.
 */
export function VersionList({
  kind,
  versions,
  error,
  onChanged,
  profileActions,
  emptyText,
}: {
  kind: MediaKind
  versions: Version[] | null
  error: unknown
  onChanged: () => void
  profileActions?: ProfileActions
  /** Was ohne Fassung dasteht. Ohne Angabe der Satz fuer Filme. */
  emptyText?: string
}) {
  const { t, i18n } = useTranslation()
  // undefined: kein Dialog. null: neue Fassung.
  const [editing, setEditing] = useState<Version | null | undefined>(undefined)
  const [removing, setRemoving] = useState<Version | null>(null)

  if (versions === null) {
    return error !== null ? (
      <FormMessage>{errorText(t, error)}</FormMessage>
    ) : (
      <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
        <Spinner />
        {t('common.loading')}
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
      {versions.length === 0 && <p className="text-sm text-mist-500">{emptyText ?? t('settings.versions.empty')}</p>}
      <ul className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-[repeat(2,minmax(0,1fr))] xl:grid-cols-[repeat(3,minmax(0,1fr))]">
        {versions.map((version) => (
          <li key={version.id} className="min-w-0">
            <Tile className="h-full">
              <TileHeader
                title={version.label}
                sub={t('settings.versions.titles', { count: version.title_count, value: formatNumber(version.title_count, i18n.language) })}
              >
                <Button variant="ghost" size="sm" onClick={() => setEditing(version)} aria-label={t('settings.versions.renameLabel', { label: version.label })}>
                  {t('settings.versions.rename')}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setRemoving(version)} aria-label={t('settings.versions.removeLabel', { label: version.label })}>
                  {t('common.actions.remove')}
                </Button>
              </TileHeader>
              {profileActions && PROFILE_KINDS.includes(kind) && <ProfileRow version={version} actions={profileActions} />}
              <DelayRow version={version} onChanged={onChanged} />
            </Tile>
          </li>
        ))}
        <li className="min-w-0">
          <button
            type="button"
            onClick={() => setEditing(null)}
            className="flex h-full min-h-20 w-full items-center justify-center gap-2 rounded-xl border border-dashed border-ink-600 p-4 text-center text-mist-500 transition-colors hover:border-accent-500/60 hover:text-accent-400"
          >
            <Symbol name="plus" className="h-5 w-5" />
            <span className="text-sm font-semibold">{t('settings.versions.add')}</span>
          </button>
        </li>
      </ul>

      {editing !== undefined && (
        <VersionDialog
          kind={kind}
          version={editing}
          onClose={() => setEditing(undefined)}
          onSaved={() => {
            setEditing(undefined)
            onChanged()
          }}
        />
      )}
      {removing && (
        <RemoveVersionDialog
          kind={kind}
          version={removing}
          onClose={() => setRemoving(null)}
          onRemoved={() => {
            setRemoving(null)
            onChanged()
          }}
        />
      )}
    </div>
  )
}

/**
 * Entfernen mit Rueckfrage. Nutzt eine Verbindung oder ein Titel die Fassung, sagt der Server 409 `version_in_use`.
 * Der Satz nennt die App, die eine Fassung dieser Art fuellen kann.
 */
function RemoveVersionDialog({ kind, version, onClose, onRemoved }: { kind: MediaKind; version: Version; onClose: () => void; onRemoved: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await versionsApi.remove(version.id)
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
      title={t('settings.versions.remove.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('settings.versions.remove.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">
          {kind === 'series' ? t('series.versions.removeText', { label: version.label }) : t('settings.versions.remove.text', { label: version.label })}
        </p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
