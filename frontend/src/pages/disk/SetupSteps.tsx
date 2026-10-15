import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { DiskRoot, DiskRootKind, Version } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { formatList } from '../../lib/format'
import { TMDB_TAB_PATH } from '../settings/tabs'
import { QuickVersion } from '../versions/QuickVersion'

/**
 * Was fehlt, bevor eingelesene Ordner in die Bibliothek kommen: der TMDB-Schluessel (nicht bei Musik) und eine Fassung
 * der Art. Einlesen geht auch ohne, findet dann aber nur Ordner, und die Bibliothek bleibt leer (Befund vom
 * 15.10.2026). Die Fassung laesst sich hier gleich anlegen; liegt genau ein Ordner ohne Fassung da, wird er auf Wunsch
 * ihr Standardordner. Steht nichts mehr aus, zeigt die Karte nichts.
 */
export function SetupSteps({
  kind,
  tmdbReady,
  versions,
  roots,
  onVersionCreated,
}: {
  kind: DiskRootKind
  tmdbReady: boolean
  /** null, solange die Fassungen laden: dann wartet die Karte. */
  versions: Version[] | null
  roots: readonly DiskRoot[]
  onVersionCreated: (version: Version, folderProblem: unknown) => void
}) {
  const { t, i18n } = useTranslation()
  if (versions === null) return null
  const needsTmdb = kind !== 'album'
  const tmdbMissing = needsTmdb && !tmdbReady
  const versionMissing = versions.length === 0
  if (!tmdbMissing && !versionMissing) return null

  const loose = roots.filter((root) => root.versions.length === 0 && root.error_code === null)
  const folder = loose.length === 1 ? loose[0].path : null
  const title = kind === 'series' ? t('disk.setup.titleSeries') : kind === 'album' ? t('disk.setup.titleMusic') : t('disk.setup.titleMovies')
  const intro = kind === 'series' ? t('disk.setup.introSeries') : kind === 'album' ? t('disk.setup.introMusic') : t('disk.setup.introMovies')

  return (
    <section aria-label={title} className="flex flex-col gap-4 rounded-2xl border border-accent-500/40 bg-accent-500/5 p-5">
      <div className="flex flex-col gap-1">
        <h2 className="text-base font-semibold text-mist-100">{title}</h2>
        <p className="max-w-prose text-sm text-mist-300">{intro}</p>
      </div>
      <ol className="flex flex-col gap-4">
        {needsTmdb && (
          <Step done={!tmdbMissing} number={1}>
            {tmdbMissing ? (
              <>
                <p className="text-sm text-mist-200">{t('disk.setup.tmdbMissing')}</p>
                <Link to={TMDB_TAB_PATH} className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-accent-400 hover:underline">
                  {t('disk.setup.tmdbAction')}
                  <Symbol name="arrow" />
                </Link>
              </>
            ) : (
              <p className="text-sm text-mist-400">{t('disk.setup.tmdbDone')}</p>
            )}
          </Step>
        )}
        <Step done={!versionMissing} number={needsTmdb ? 2 : 1}>
          {versionMissing ? (
            <>
              <p className="text-sm text-mist-200">{kind === 'album' ? t('disk.setup.versionMissingMusic') : t('disk.setup.versionMissing')}</p>
              <QuickVersion kind={kind} folder={folder} onCreated={onVersionCreated} />
            </>
          ) : (
            <p className="text-sm text-mist-400">{t('disk.setup.versionDone', { count: versions.length, labels: formatList(versions.map((version) => version.label), i18n.language) })}</p>
          )}
        </Step>
      </ol>
    </section>
  )
}

function Step({ done, number, children }: { done: boolean; number: number; children: React.ReactNode }) {
  const { t } = useTranslation()
  return (
    <li className="flex items-start gap-3">
      <span
        aria-label={done ? t('disk.setup.stepDone') : t('disk.setup.stepOpen')}
        className={
          'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ' + (done ? 'bg-ok-500/20 text-ok-500' : 'bg-accent-500 text-on-accent')
        }
      >
        {done ? <Symbol name="check" className="h-3.5 w-3.5" /> : number}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">{children}</div>
    </li>
  )
}
