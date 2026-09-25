import { useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { namingApi } from '../../api/naming'
import type { Umlauts, VersionNaming, VersionNamingPatterns } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { Tile, TileHeader } from '../settings/parts'
import { insertToken, type PatternCursor, type PatternField } from './patternDraft'
import { PatternInput, PatternRows, PreviewBox, TokenList } from './PatternFields'
import { RadarrNamingDialog } from './RadarrNamingDialog'
import { usePatternPreview } from './usePatternPreview'

/**
 * Die Benennung je Fassung fuer Filme: Jede nutzt die Standard-Benennung oder zwei eigene Muster. "Eigene Benennung"
 * beginnt mit den Mustern der Standard-Benennung, "Zurück zur Standard-Benennung" loescht die eigenen. Fuellt ein
 * Radarr die Fassung, laesst sich seine Benennung uebernehmen. Die Regel fuer Umlaute gilt fuer alle gleich.
 */
export function VersionNamingSection({
  versions,
  umlauts,
  tokens,
  onChange,
}: {
  versions: VersionNaming[]
  umlauts: Umlauts
  tokens: readonly string[]
  onChange: (entry: VersionNaming) => void
}) {
  const { t } = useTranslation()
  const notify = useNotice()
  // Hoechstens eine Fassung ist offen zum Bearbeiten.
  const [editing, setEditing] = useState<number | null>(null)
  const [taking, setTaking] = useState<VersionNaming | null>(null)

  return (
    <Section title={t('settings.files.versionNaming.title')} intro={t('settings.files.versionNaming.intro')}>
      {versions.length === 0 ? (
        <p className="text-sm text-mist-500">{t('settings.files.folders.noVersions')}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {versions.map((entry) => (
            <li key={entry.version_id} className="min-w-0">
              <VersionNamingTile
                entry={entry}
                umlauts={umlauts}
                tokens={tokens}
                editing={editing === entry.version_id}
                onEdit={() => setEditing(entry.version_id)}
                onCancel={() => setEditing(null)}
                onSaved={(saved) => {
                  setEditing(null)
                  onChange(saved)
                  notify(t('settings.files.versionNaming.saved', { label: saved.label }))
                }}
                onReset={(saved) => {
                  onChange(saved)
                  notify(t('settings.files.versionNaming.resetDone', { label: saved.label }))
                }}
                onTake={() => setTaking(entry)}
              />
            </li>
          ))}
        </ul>
      )}
      {taking !== null && taking.source_id !== null && (
        <RadarrNamingDialog
          sourceId={taking.source_id}
          versionId={taking.version_id}
          label={taking.label}
          onClose={() => setTaking(null)}
          onTaken={(saved) => {
            setTaking(null)
            setEditing((current) => (current === saved.version_id ? null : current))
            onChange(saved)
            notify(t('settings.files.radarrNaming.taken', { label: saved.label }))
          }}
        />
      )}
    </Section>
  )
}

function VersionNamingTile({
  entry,
  umlauts,
  tokens,
  editing,
  onEdit,
  onCancel,
  onSaved,
  onReset,
  onTake,
}: {
  entry: VersionNaming
  umlauts: Umlauts
  tokens: readonly string[]
  editing: boolean
  onEdit: () => void
  onCancel: () => void
  onSaved: (saved: VersionNaming) => void
  onReset: (saved: VersionNaming) => void
  onTake: () => void
}) {
  const { t } = useTranslation()
  const [resetting, setResetting] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const label = entry.label

  async function reset() {
    setResetting(true)
    setProblem(null)
    try {
      onReset(await namingApi.resetVersion(entry.version_id))
    } catch (error) {
      setProblem(error)
    } finally {
      setResetting(false)
    }
  }

  return (
    <Tile>
      <TileHeader title={label}>
        {entry.own ? <Badge tone="accent">{t('settings.files.versionNaming.ownBadge')}</Badge> : <Badge>{t('settings.files.versionNaming.defaultBadge')}</Badge>}
      </TileHeader>
      {editing ? (
        <VersionNamingEditor entry={entry} umlauts={umlauts} tokens={tokens} onCancel={onCancel} onSaved={onSaved} />
      ) : (
        <>
          {entry.own ? <PatternRows folder={entry.movie_folder} file={entry.movie_file} /> : <p className="text-sm text-mist-400">{t('settings.files.versionNaming.usesDefault')}</p>}
          <div className="flex flex-wrap gap-2">
            {entry.own ? (
              <>
                <Button variant="ghost" size="sm" onClick={onEdit} aria-label={t('settings.files.versionNaming.editLabel', { label })}>
                  {t('common.actions.edit')}
                </Button>
                <Button variant="ghost" size="sm" loading={resetting} onClick={() => void reset()} aria-label={t('settings.files.versionNaming.resetLabel', { label })}>
                  {t('settings.files.versionNaming.reset')}
                </Button>
              </>
            ) : (
              <Button variant="ghost" size="sm" onClick={onEdit} aria-label={t('settings.files.versionNaming.ownLabel', { label })}>
                {t('settings.files.versionNaming.own')}
              </Button>
            )}
            {entry.source_id !== null && (
              <Button variant="ghost" size="sm" onClick={onTake} aria-label={t('settings.files.versionNaming.fromRadarrLabel', { label })}>
                <Symbol name="import" />
                {t('settings.files.versionNaming.fromRadarr')}
              </Button>
            )}
          </div>
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        </>
      )}
    </Tile>
  )
}

/** Die zwei Muster einer Fassung zum Bearbeiten, mit Platzhaltern und Vorschau wie bei der Standard-Benennung. */
function VersionNamingEditor({
  entry,
  umlauts,
  tokens,
  onCancel,
  onSaved,
}: {
  entry: VersionNaming
  umlauts: Umlauts
  tokens: readonly string[]
  onCancel: () => void
  onSaved: (saved: VersionNaming) => void
}) {
  const { t } = useTranslation()
  const emptyId = useId()
  const [draft, setDraft] = useState<VersionNamingPatterns>({ movie_folder: entry.movie_folder, movie_file: entry.movie_file })
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const cursor = useRef<PatternCursor>({ field: 'movie_file', position: null })
  const empty = draft.movie_folder.trim() === '' || draft.movie_file.trim() === ''
  const forPreview = useMemo(() => ({ ...draft, umlauts }), [draft, umlauts])
  const preview = usePatternPreview(forPreview, empty)
  // Wer von der Standard-Benennung aus beginnt, darf ihre Muster unveraendert als eigene speichern.
  const changed = !entry.own || draft.movie_folder !== entry.movie_folder || draft.movie_file !== entry.movie_file
  const label = entry.label

  function change(field: PatternField, value: string, position: number | null) {
    cursor.current = { field, position }
    setDraft((current) => ({ ...current, [field]: value }))
  }

  function insert(token: string) {
    const { field, position } = cursor.current
    const next = insertToken(draft[field], position, token)
    cursor.current = { field, position: next.position }
    setDraft({ ...draft, [field]: next.value })
  }

  async function save() {
    if (busy || empty) return
    setBusy(true)
    setProblem(null)
    try {
      onSaved(await namingApi.saveVersion(entry.version_id, { movie_folder: draft.movie_folder, movie_file: draft.movie_file }))
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <PatternInput
        label={t('settings.files.naming.movieFolder')}
        value={draft.movie_folder}
        onChange={(value, position) => change('movie_folder', value, position)}
        onCursor={(position) => {
          cursor.current = { field: 'movie_folder', position }
        }}
      />
      <PatternInput
        label={t('settings.files.naming.movieFile')}
        value={draft.movie_file}
        onChange={(value, position) => change('movie_file', value, position)}
        onCursor={(position) => {
          cursor.current = { field: 'movie_file', position }
        }}
      />
      <TokenList tokens={tokens} onInsert={insert} />
      <PreviewBox preview={preview} empty={empty} emptyId={emptyId} />
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          onClick={() => void save()}
          loading={busy}
          disabled={!changed || empty}
          aria-describedby={empty ? emptyId : undefined}
          aria-label={t('settings.files.versionNaming.saveLabel', { label })}
        >
          {t('common.actions.save')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy} aria-label={t('settings.files.versionNaming.cancelLabel', { label })}>
          {t('common.actions.cancel')}
        </Button>
      </div>
    </div>
  )
}
