import { useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { TitleDetail, TitleVersion } from '../../api/types'
import { PosterImage } from '../../components/PosterImage'
import { Symbol } from '../../components/Symbol'
import { Button } from '../../components/ui'
import { VersionChip } from '../../components/VersionChip'
import { groupLabel } from '../music/groupText'
import { INDEXERS_TAB_PATH } from '../settings/tabs'

/**
 * Der Kopf der Albumseite (M1.5.40): Cover, Kuenstler-Credit mit Links, Art, Erstveroeffentlichung. Seit M3 mit
 * "Suchen", solange ein Indexer eingeschaltet ist; ein Album ohne Kuenstler in der Bibliothek sucht nicht.
 */
export function AlbumHeader({
  title,
  versions,
  onChangeVersions,
  onRemove,
  onSearch,
  searchBusy = false,
  noIndexer = false,
  actions,
}: {
  title: TitleDetail
  versions: TitleVersion[]
  onChangeVersions: () => void
  onRemove: () => void
  onSearch?: () => void
  searchBusy?: boolean
  noIndexer?: boolean
  /** Weitere Knoepfe hinter "Fassungen aendern", etwa "Umbenennen". */
  actions?: ReactNode
}) {
  const { t } = useTranslation()
  const noIndexerId = useId()
  const album = title.album
  if (album === null || album === undefined) return null
  const year = album.first_release_date ? album.first_release_date.slice(0, 4) : null
  const meta = [groupLabel(t, album.group), year !== null ? t('title.album.firstRelease', { year }) : null].filter(Boolean).join(' · ')

  return (
    <header className="flex flex-col gap-6 sm:flex-row sm:items-end">
      <PosterImage url={title.poster_url} placeholder="note" square className="w-36 shrink-0 shadow-2xl shadow-black/40 sm:w-48" />
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <p className="text-sm text-mist-500">{meta}</p>
        <h1 className="text-3xl font-bold tracking-tight wrap-anywhere sm:text-5xl">
          {title.title}
          <span className="text-accent-500">.</span>
        </h1>
        {album.credit.length > 0 && (
          <p className="text-sm text-mist-300">
            {t('title.album.by')}{' '}
            {album.credit.map((part, index) => (
              <span key={`${part.mbid}-${index}`}>
                {part.artist_id !== null ? (
                  <Link to={`/kuenstler/${part.artist_id}`} className="font-medium text-accent-400 hover:underline">
                    {part.name}
                  </Link>
                ) : (
                  part.name
                )}
                {part.join}
              </span>
            ))}
          </p>
        )}
        {album.disambiguation && <p className="text-sm text-mist-400">{album.disambiguation}</p>}
        <div className="flex flex-wrap gap-1.5">
          {versions.map((version) => (
            <VersionChip key={version.id} version={version} />
          ))}
        </div>
        <div className="flex flex-wrap gap-2 pt-1">
          {onSearch && (
            <Button onClick={onSearch} loading={searchBusy} disabled={noIndexer} aria-describedby={noIndexer ? noIndexerId : undefined}>
              {!searchBusy && <Symbol name="search" />}
              {searchBusy ? t('search.actionBusy') : t('search.action')}
            </Button>
          )}
          <Button variant="ghost" onClick={onChangeVersions}>
            <Symbol name="layers" />
            {t('title.actions.changeVersions')}
          </Button>
          {actions}
          <Button variant="ghost" onClick={onRemove}>
            <Symbol name="trash" />
            {t('title.actions.remove')}
          </Button>
        </div>
        {onSearch && noIndexer && (
          <p id={noIndexerId} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-500">
            <Symbol name="info" className="h-4 w-4 shrink-0 text-info-500" />
            <span>{t('search.noIndexer')}</span>
            <Link to={INDEXERS_TAB_PATH} className="font-medium text-accent-400 hover:underline">
              {t('search.noIndexerLink')}
            </Link>
          </p>
        )}
        {album.mb_gone_at !== null && (
          <p className="flex items-start gap-2 text-sm text-mist-400">
            <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
            {t('title.album.gone')}
          </p>
        )}
      </div>
    </header>
  )
}
