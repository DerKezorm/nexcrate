import { useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { errorText } from '../../api/client'
import type { FolderMount, Version } from '../../api/types'
import { versionsApi } from '../../api/versions'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { Tile, TileHeader } from '../settings/parts'
import { MUSIC_VERSIONS_TAB_PATH, SERIES_VERSIONS_TAB_PATH, VERSIONS_TAB_PATH } from '../settings/tabs'
import type { KindSection } from '../settings/useKindLabel'
import { useVersions } from '../versions/useVersions'
import { FolderPicker } from './FolderPicker'
import { FolderRules } from './FolderRules'
import { mountOf } from './folderText'
import { looksLikeSeason } from './seasonFolder'
import { SpaceLine } from './SpaceLine'
import { useMounts } from './useMounts'

function Loading() {
  const { t } = useTranslation()
  return (
    <p className="flex items-center gap-2 py-2 text-sm text-mist-500" role="status">
      <Spinner />
      {t('common.loading')}
    </p>
  )
}

/**
 * Unterreiter "Ordner". Oben eine Karte mit dem, was nexcrate eingebunden sieht, mit freiem Platz; sieht es nichts,
 * ein Satz mit dem Hinweis fuer die Compose-Datei. Darunter die Standardordner je Medienart (`kindSwitch` waehlt sie):
 * je Fassung ihr Standardordner und die Auswahl ueber einen Dialog, der blaettert. Einen Pfad tippt man nie ein. Seit
 * Befund 13 kommen nur neue Titel dorthin; vorhandene bleiben in ihrem Ordner. Das sagt der Hinweis an der Fassung und
 * in der Auswahl, fuer Filme und fuer Serien je eigen. Musik sagt in einem Satz, wann sie kommt.
 */
export function FolderSection({ kind, kindSwitch }: { kind: KindSection; kindSwitch: ReactNode }) {
  const { t } = useTranslation()
  const { mounts, error: mountsError } = useMounts()

  return (
    <>
      <Section title={t('settings.files.folders.mounts')} intro={t('settings.files.folders.mountsIntro')}>
        {mountsError !== null && <FormMessage>{errorText(t, mountsError)}</FormMessage>}
        {mounts === null ? mountsError === null && <Loading /> : mounts.length === 0 ? <NoMounts /> : <MountList mounts={mounts} />}
      </Section>

      <Section title={t('settings.files.folders.title')} intro={t('settings.files.folders.intro')}>
        {kindSwitch}
        {/* Je Art neu aufgebaut: So stehen beim Umschalten nie kurz die Fassungen der anderen Art da. */}
        <VersionFolders key={kind} kind={kind === 'music' ? 'album' : kind} mounts={mounts} />
      </Section>
    </>
  )
}

/** Die Standardordner der Fassungen einer Art, mit der Auswahl eines Ordners. */
function VersionFolders({ kind, mounts }: { kind: 'movie' | 'series' | 'album'; mounts: FolderMount[] | null }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const { versions, error, reload } = useVersions(kind)
  const [picking, setPicking] = useState<Version | null>(null)
  const series = kind === 'series'
  // Seit M4 hat auch die Musik-Fassung einen Standardordner: darunter legt nexcrate Kuenstler- und Albumordner an.
  const hint = series ? t('series.folders.hint') : kind === 'album' ? t('settings.files.folders.hintMusic') : t('settings.files.folders.hint')

  return (
    <>
      {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
      {versions === null ? (
        error === null && <Loading />
      ) : versions.length === 0 ? (
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-500">
          <span>{series ? t('series.folders.noVersions') : kind === 'album' ? t('settings.files.folders.noVersionsMusic') : t('settings.files.folders.noVersions')}</span>
          <Link to={series ? SERIES_VERSIONS_TAB_PATH : kind === 'album' ? MUSIC_VERSIONS_TAB_PATH : VERSIONS_TAB_PATH} className="font-medium text-accent-400 hover:underline">
            {t('settings.files.folders.noVersionsLink')}
          </Link>
        </p>
      ) : (
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          {versions.map((version) => (
            <li key={version.id} className="min-w-0">
              <VersionFolder
                version={version}
                kind={kind}
                hint={hint}
                series={series}
                mounts={mounts}
                onPick={() => setPicking(version)}
                onCleared={() => {
                  notify(t('settings.files.folders.cleared', { label: version.label }))
                  reload()
                }}
              />
            </li>
          ))}
        </ul>
      )}

      {picking && (
        <FolderPicker
          title={t('settings.files.picker.title', { label: picking.label })}
          hint={hint}
          warning={series ? (path) => (looksLikeSeason(path) ? t('series.folders.seasonLike') : null) : undefined}
          start={picking.folder ?? null}
          onClose={() => setPicking(null)}
          onTake={async (path) => {
            const saved = await versionsApi.setFolder(picking.id, path)
            setPicking(null)
            const values = { label: saved.label, folder: saved.folder ?? '' }
            notify(series ? t('series.folders.saved', values) : t('settings.files.folders.saved', values))
            reload()
          }}
        />
      )}
    </>
  )
}

function SeasonLikeWarning() {
  const { t } = useTranslation()
  return (
    <p role="note" className="flex items-start gap-2 text-xs text-bad-400">
      <Symbol name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{t('series.folders.seasonLike')}</span>
    </p>
  )
}

function NoMounts() {
  const { t } = useTranslation()
  return (
    <div role="note" className="flex flex-col gap-2 rounded-xl border border-info-500/30 bg-info-500/5 p-4 text-sm">
      <p className="flex items-start gap-2 font-medium text-mist-100">
        <Symbol name="folder" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
        <span>{t('settings.files.folders.noMounts')}</span>
      </p>
      <p className="text-mist-400">{t('settings.files.folders.composeHint')}</p>
      <pre className="overflow-x-auto rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 font-mono text-xs text-mist-300">
        {t('settings.files.folders.composeExample')}
      </pre>
    </div>
  )
}

function MountList({ mounts }: { mounts: FolderMount[] }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-2">
      <ul aria-label={t('settings.files.folders.mounts')} className="grid grid-cols-[minmax(0,1fr)] gap-2 sm:grid-cols-[repeat(2,minmax(0,1fr))]">
        {mounts.map((mount) => (
          <li key={mount.path} className="flex min-w-0 flex-col gap-2 rounded-lg border border-ink-700 bg-ink-900/60 p-3">
            <p className="flex items-start gap-2 font-mono text-sm break-all text-mist-200">
              <Symbol name="folder" className="mt-0.5 h-4 w-4 shrink-0 text-mist-500" />
              <span className="min-w-0">{mount.path}</span>
            </p>
            <SpaceLine path={mount.path} freeBytes={mount.free_bytes} totalBytes={mount.total_bytes} />
          </li>
        ))}
      </ul>
    </div>
  )
}

function VersionFolder({
  version,
  kind,
  hint,
  series = false,
  mounts,
  onPick,
  onCleared,
}: {
  version: Version
  kind: 'movie' | 'series' | 'album'
  hint: string
  /** Serienfassung: warnt vor einem Ordner, der wie eine Staffel heisst (S4, Entscheidung 55). */
  series?: boolean
  mounts: FolderMount[] | null
  onPick: () => void
  onCleared: () => void
}) {
  const { t } = useTranslation()
  const reasonId = useId()
  const [clearing, setClearing] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const folder = version.folder ?? null
  const mount = folder !== null && mounts !== null ? mountOf(folder, mounts) : null
  const noMounts = mounts !== null && mounts.length === 0

  async function clear() {
    setClearing(true)
    setProblem(null)
    try {
      await versionsApi.setFolder(version.id, null)
      onCleared()
    } catch (error) {
      setProblem(error)
    } finally {
      setClearing(false)
    }
  }

  return (
    <Tile className="h-full">
      <TileHeader title={version.label} />
      {folder !== null ? (
        <p className="flex items-start gap-2 font-mono text-sm break-all text-mist-200">
          <Symbol name="folder" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
          <span className="min-w-0">{folder}</span>
        </p>
      ) : (
        <p className="text-sm text-mist-500">{t('settings.files.folders.none')}</p>
      )}
      {mount && <SpaceLine path={mount.path} freeBytes={mount.free_bytes} totalBytes={mount.total_bytes} />}
      {/* Nur mit Ordner: Ohne einen gibt es kein "hier". */}
      {folder !== null && <p className="text-xs text-mist-500">{hint}</p>}
      {series && looksLikeSeason(folder) && <SeasonLikeWarning />}
      {/* Regeln fuer Mediatheken: nur mit Standardordner, nur fuer Filme und Serien. */}
      {folder !== null && kind !== 'album' && <FolderRules version={version} kind={kind} />}
      <div className="mt-auto flex flex-col gap-1.5">
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={folder === null ? 'primary' : 'ghost'}
            onClick={onPick}
            disabled={mounts === null || noMounts}
            aria-describedby={noMounts ? reasonId : undefined}
            aria-label={folder === null ? t('settings.files.folders.chooseLabel', { label: version.label }) : t('settings.files.folders.changeLabel', { label: version.label })}
          >
            <Symbol name="folder" />
            {folder === null ? t('settings.files.folders.choose') : t('settings.files.folders.change')}
          </Button>
          {folder !== null && (
            <Button variant="ghost" size="sm" loading={clearing} onClick={() => void clear()} aria-label={t('settings.files.folders.clearLabel', { label: version.label })}>
              {t('settings.files.folders.clear')}
            </Button>
          )}
        </div>
        {noMounts && (
          <p id={reasonId} className="text-xs text-mist-500">
            {t('settings.files.folders.chooseBlocked')}
          </p>
        )}
      </div>
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </Tile>
  )
}
