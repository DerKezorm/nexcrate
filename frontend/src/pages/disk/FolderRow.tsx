import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { DiskFolder, DiskRootKind, Proposal } from '../../api/types'
import { PosterImage } from '../../components/PosterImage'
import { Symbol } from '../../components/Symbol'
import { Badge, Button } from '../../components/ui'
import { formatList, formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { albumFromText, fromText, largestVideo, proposalPoster, shownSeasons, stateLabel, stateText, stateTone } from './diskText'

export type RowActions = {
  onAssign: (folder: DiskFolder, proposal: Proposal | null) => void
  onRestore: (folder: DiskFolder) => void
  onTakePath: (folder: DiskFolder) => void
  onIgnore: (folder: DiskFolder) => void
  onUnignore: (folder: DiskFolder) => void
}

/** The states a row can be assigned from. A disc folder or a loose file cannot, in this block. */
const ASSIGNABLE = new Set(['proposal', 'unknown', 'conflict', 'restorable'])

/** The states that offer "Ignorieren". What nexcrate owns or Radarr feeds needs no mark. */
const IGNORABLE = new Set(['proposal', 'unknown', 'conflict', 'restorable', 'moved', 'file', 'disc', 'no_video', 'unreadable'])

/** Was ein Albumordner sagt, und woher: Tags, release.nex oder der Ordnername. */
function AlbumDetails({ folder }: { folder: DiskFolder }) {
  const { t, i18n } = useTranslation()
  const album = folder.album
  if (!album) return null
  const source = album.from === 'companion' ? t('disk.album.row.sourceCompanion') : album.from === 'tags' ? t('disk.album.row.sourceTags') : t('disk.album.row.sourceFolder')
  return (
    <div className="flex min-w-0 flex-col gap-0.5 pl-6 text-xs text-mist-500">
      <p className="tabular-nums">{t('disk.album.row.audio', { count: album.audio, value: formatNumber(album.audio, i18n.language) })}</p>
      <p className="wrap-anywhere">
        {album.artist ? t('disk.album.row.says', { source, artist: album.artist, album: album.album }) : t('disk.album.row.saysNoArtist', { source, album: album.album })}
      </p>
    </div>
  )
}

/** Die Vorschlaege eines Albumordners: Kuenstler, Album und Jahr, ob eindeutig, ob schon in der Bibliothek, und woher. */
function AlbumProposals({ folder }: { folder: DiskFolder }) {
  const { t } = useTranslation()
  const proposals = folder.album?.proposals ?? []
  if (proposals.length === 0) return null
  return (
    <ul aria-label={t('disk.row.proposalsLabel', { name: folder.relative_path })} className="flex flex-col gap-1.5 pl-6">
      {proposals.map((proposal, index) => (
        <li key={`${proposal.title_id ?? proposal.mbid}-${index}`} className="flex min-w-0 flex-col">
          <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <span className="font-medium wrap-anywhere text-mist-100">
              {[proposal.artist, proposal.year ? `${proposal.title} (${proposal.year})` : proposal.title].filter(Boolean).join(', ')}
            </span>
            {proposal.unambiguous && <Badge tone="accent">{t('disk.row.unambiguous')}</Badge>}
            {proposal.title_id !== null ? <Badge tone="ok">{t('disk.row.inLibrary')}</Badge> : <Badge>{t('disk.album.row.notInLibrary')}</Badge>}
          </p>
          <p className="text-xs text-mist-500">
            {t('disk.row.proposal')}: {albumFromText(t, proposal.from)}
          </p>
        </li>
      ))}
    </ul>
  )
}

/**
 * One scanned folder: its name, the largest video with size and the quality its name gives, the state in one sentence,
 * what its `release.nex` says, its proposals with poster, title, year and how they were found, and the actions its
 * state allows.
 *
 * Ein Serienordner (S6) zeigt statt der Datei, wie viele Videos das Einlesen ansehen wuerde, und seine Staffelordner.
 */
export function FolderRow({ folder, actions, busy, kind = 'movie' }: { folder: DiskFolder; actions: RowActions; busy: boolean; kind?: DiskRootKind }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const series = kind === 'series'
  const album = kind === 'album'
  const name = folder.relative_path
  const video = largestVideo(folder)
  const more = Math.max(0, (Array.isArray(folder.videos) ? folder.videos.length : 0) - 1)
  const companion = folder.companion
  const entries = Array.isArray(companion?.entries) ? companion.entries.filter((entry) => typeof entry?.version === 'string' && entry.version !== '') : []
  const proposals = Array.isArray(folder.proposals) ? folder.proposals : []
  const ignored = folder.state === 'ignored' || folder.ignored === true
  const videos = folder.series?.videos ?? 0
  const seasons = shownSeasons(folder)

  return (
    <article className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-4 md:grid-cols-[minmax(0,1fr)_auto]">
      <div className="flex min-w-0 flex-col gap-2">
        <h3 className="flex min-w-0 items-start gap-2 font-semibold wrap-anywhere text-mist-100">
          <Symbol name={series ? 'tv' : album ? 'note' : 'folder'} className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
          <span className="min-w-0">{name}</span>
          {(series || album) && <Badge tone={stateTone(folder.state)}>{stateLabel(t, folder.state)}</Badge>}
        </h3>
        {album ? (
          <AlbumDetails folder={folder} />
        ) : series ? (
          <div className="flex min-w-0 flex-col gap-0.5 pl-6 text-xs text-mist-500">
            {videos > 0 && <p className="tabular-nums">{t('disk.series.row.videos', { count: videos, value: formatNumber(videos, language) })}</p>}
            {seasons.names.length > 0 ? (
              <p className="wrap-anywhere">
                {t('disk.series.row.seasons', { names: formatList(seasons.names, language) })}
                {seasons.more > 0 && ` ${t('disk.series.row.moreSeasons', { count: seasons.more })}`}
              </p>
            ) : (
              videos > 0 && <p>{t('disk.series.row.noSeasons')}</p>
            )}
          </div>
        ) : video !== null ? (
          <div className="flex min-w-0 flex-col gap-0.5 pl-6 text-xs">
            <p className="font-mono leading-5 wrap-anywhere text-mist-300">{video.name}</p>
            <p className="flex flex-wrap gap-x-2 text-mist-500">
              <span className="tabular-nums">{sizeText(t, video.size_bytes, language)}</span>
              <span>{video.name_quality ? t('disk.row.nameQuality', { quality: video.name_quality }) : t('disk.row.noNameQuality')}</span>
              {more > 0 && <span>{t('disk.row.moreVideos', { count: more })}</span>}
            </p>
          </div>
        ) : (
          folder.kind !== 'file' && <p className="pl-6 text-xs text-mist-500">{t('disk.row.noVideo')}</p>
        )}
        <p className="pl-6 text-sm text-mist-300">{stateText(t, folder, kind)}</p>
        {album && <AlbumProposals folder={folder} />}
        {!series && !album && companion !== null && companion.title && (
          <p className="pl-6 text-sm text-mist-200">
            {t('disk.row.companionMovie', { title: companion.year ? `${companion.title} (${companion.year})` : companion.title })}
            {entries.length > 0 && (
              <span className="text-mist-500">
                {' · '}
                {t('disk.row.companionVersion', { count: entries.length, labels: formatList(entries.map((entry) => entry.version), language) })}
              </span>
            )}
          </p>
        )}
        {proposals.length > 0 && (
          <ul aria-label={t('disk.row.proposalsLabel', { name })} className="flex flex-col gap-1.5 pl-6">
            {proposals.map((proposal, index) => (
              <li key={`${proposal.tmdb_id}-${index}`} className="flex min-w-0 items-center gap-3">
                <PosterImage url={proposalPoster(proposal)} className="w-8 shrink-0" />
                <div className="flex min-w-0 flex-1 flex-col">
                  <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                    <span className="font-medium wrap-anywhere text-mist-100">{proposal.year ? `${proposal.title} (${proposal.year})` : proposal.title}</span>
                    {proposal.unambiguous && <Badge tone="accent">{t('disk.row.unambiguous')}</Badge>}
                    {proposal.title_id !== null && <Badge tone="ok">{t('disk.row.inLibrary')}</Badge>}
                  </p>
                  <p className="text-xs text-mist-500">
                    {t('disk.row.proposal')}: {fromText(t, proposal.from)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
        {(folder.state === 'library' || folder.state === 'moved' || folder.state === 'conflict') && folder.title_id !== null && (
          <Link to={`/titel/${folder.title_id}`} className="w-fit pl-6 text-sm font-medium text-accent-400 hover:underline">
            {album ? t('disk.album.row.openTitle') : series ? t('disk.series.row.openTitle') : t('disk.row.openTitle')}
          </Link>
        )}
      </div>

      <div className="flex flex-wrap items-start gap-2 md:flex-col md:items-end">
        {folder.state === 'restorable' && (
          <Button size="sm" onClick={() => actions.onRestore(folder)} disabled={busy} aria-label={t('disk.row.restoreLabel', { name })}>
            {t('disk.row.restore')}
          </Button>
        )}
        {!series && !album && folder.state === 'moved' && (
          <Button size="sm" onClick={() => actions.onTakePath(folder)} disabled={busy} aria-label={t('disk.row.takePathLabel', { name })}>
            {t('disk.row.takePath')}
          </Button>
        )}
        {!ignored && ASSIGNABLE.has(folder.state) && folder.kind !== 'file' && folder.kind !== 'disc' && (
          <Button
            size="sm"
            variant={folder.state === 'restorable' ? 'ghost' : 'primary'}
            onClick={() => actions.onAssign(folder, proposals[0] ?? null)}
            disabled={busy}
            aria-label={t('disk.row.assignLabel', { name })}
          >
            {t('disk.row.assign')}
          </Button>
        )}
        {ignored ? (
          <Button size="sm" variant="ghost" onClick={() => actions.onUnignore(folder)} disabled={busy} aria-label={t('disk.row.unignoreLabel', { name })}>
            {t('disk.row.unignore')}
          </Button>
        ) : (
          IGNORABLE.has(folder.state) && (
            <Button size="sm" variant="ghost" onClick={() => actions.onIgnore(folder)} disabled={busy} aria-label={t('disk.row.ignoreLabel', { name })}>
              <Symbol name="eyeOff" />
              {t('disk.row.ignore')}
            </Button>
          )
        )}
      </div>
    </article>
  )
}
