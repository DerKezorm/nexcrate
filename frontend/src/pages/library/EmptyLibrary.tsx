import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { diskApi } from '../../api/disk'
import { Symbol, type SymbolName } from '../../components/Symbol'
import { Button } from '../../components/ui'
import { IMPORT_TAB_PATH, SONARR_IMPORT_TAB_PATH } from '../settings/tabs'
import { rootsOfKind, waitingCount } from '../disk/diskText'
import { DISK_PATH, SERIES_DISK_PATH } from './AddMenu'

const PILL_CLASS = 'inline-flex w-fit items-center gap-2 rounded-full border border-ink-700 bg-ink-850 px-4 py-2 text-sm font-semibold text-mist-200 transition-colors hover:bg-ink-800 hover:text-mist-100'
const LINK_CLASS = 'mt-auto ' + PILL_CLASS

function Way({ symbol, title, text, children }: { symbol: SymbolName; title: string; text: string; children: React.ReactNode }) {
  return (
    <article className="flex flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-850/60 p-5">
      <Symbol name={symbol} className="h-7 w-7 text-accent-400" />
      <h3 className="font-semibold text-mist-100">{title}</h3>
      <p className="text-sm text-mist-400">{text}</p>
      {children}
    </article>
  )
}

/**
 * Eine leere Bibliothek zeigt drei gleichwertige Wege. Neu anfangen ist der Normalfall und
 * oeffnet die Suche bei TMDB; nach Downloads sucht nexcrate erst spaeter, das steht dabei.
 * Filme, die schon auf der Platte liegen, liest die Seite "Ordner einlesen" ein (library from
 * disk). Der Import aus Radarr ist ein Angebot daneben, kein Muss.
 *
 * Fuer Serien sind es seit S6 drei Wege: Sonarr verbinden, eine Serie bei TMDB suchen, oder die
 * Serienordner einlesen, die schon auf der Platte liegen.
 */
export function EmptyLibrary({ onStart, kind = 'movie' }: { onStart: () => void; kind?: 'movie' | 'series' }) {
  const { t } = useTranslation()
  const waiting = useWaitingFolders(kind)
  if (kind === 'series') {
    return (
      <section className="flex flex-col gap-4" aria-labelledby="empty-library-title">
        <h2 id="empty-library-title" className="text-lg font-semibold">
          {t('series.empty.title')}
        </h2>
        <WaitingNote kind={kind} count={waiting} />
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-[repeat(3,minmax(0,1fr))]">
          <Way symbol="import" title={t('series.empty.sonarrTitle')} text={t('series.empty.sonarrText')}>
            <Link to={SONARR_IMPORT_TAB_PATH} className={LINK_CLASS}>
              {t('series.empty.sonarrAction')}
              <Symbol name="arrow" />
            </Link>
          </Way>
          <Way symbol="folder" title={t('series.empty.diskTitle')} text={t('series.empty.diskText')}>
            <Link to={SERIES_DISK_PATH} className={LINK_CLASS}>
              {t('series.empty.diskAction')}
              <Symbol name="arrow" />
            </Link>
          </Way>
          <Way symbol="search" title={t('series.empty.searchTitle')} text={t('series.empty.searchText')}>
            <Button onClick={onStart} className="mt-auto w-fit">
              <Symbol name="search" />
              {t('series.empty.searchAction')}
            </Button>
          </Way>
        </div>
      </section>
    )
  }
  return (
    <section className="flex flex-col gap-4" aria-labelledby="empty-library-title">
      <h2 id="empty-library-title" className="text-lg font-semibold">
        {t('library.emptyLibrary.title')}
      </h2>
      <WaitingNote kind={kind} count={waiting} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-[repeat(3,minmax(0,1fr))]">
        <Way symbol="sparkle" title={t('library.emptyLibrary.freshTitle')} text={t('library.emptyLibrary.freshText')}>
          <Button onClick={onStart} className="mt-auto w-fit">
            <Symbol name="search" />
            {t('library.emptyLibrary.freshAction')}
          </Button>
        </Way>
        <Way symbol="folder" title={t('library.emptyLibrary.diskTitle')} text={t('library.emptyLibrary.diskText')}>
          <Link to={DISK_PATH} className={LINK_CLASS}>
            {t('library.emptyLibrary.diskAction')}
            <Symbol name="arrow" />
          </Link>
        </Way>
        <Way symbol="import" title={t('library.emptyLibrary.importTitle')} text={t('library.emptyLibrary.importText')}>
          <Link to={IMPORT_TAB_PATH} className={LINK_CLASS}>
            {t('library.emptyLibrary.importAction')}
            <Symbol name="arrow" />
          </Link>
        </Way>
      </div>
    </section>
  )
}

/**
 * Eingelesene Ordner, die noch zugeordnet werden muessen. Wer einen Ordner einliest und dann zur Bibliothek geht, sah
 * sonst nur "Noch keine Filme" und hielt das Einlesen fuer gescheitert (Befund vom 15.10.2026). Faellt die Anfrage
 * aus, bleibt es bei 0: Die drei Wege stehen ohnehin da.
 */
function useWaitingFolders(kind: 'movie' | 'series'): number {
  const [count, setCount] = useState(0)
  useEffect(() => {
    let current = true
    const abort = new AbortController()
    diskApi.overview(abort.signal).then(
      (overview) => {
        if (current && Array.isArray(overview?.roots)) setCount(waitingCount(rootsOfKind(overview.roots, kind)))
      },
      () => undefined,
    )
    return () => {
      current = false
      abort.abort()
    }
  }, [kind])
  return count
}

function WaitingNote({ kind, count }: { kind: 'movie' | 'series'; count: number }) {
  const { t } = useTranslation()
  if (count === 0) return null
  return (
    <div role="status" className="flex flex-col items-start gap-3 rounded-2xl border border-accent-500/40 bg-accent-500/5 p-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-mist-200">{kind === 'series' ? t('library.emptyLibrary.waitingSeries', { count }) : t('library.emptyLibrary.waiting', { count })}</p>
      <Link to={kind === 'series' ? SERIES_DISK_PATH : DISK_PATH} className={'shrink-0 ' + PILL_CLASS}>
        {t('library.emptyLibrary.waitingAction')}
        <Symbol name="arrow" />
      </Link>
    </div>
  )
}
