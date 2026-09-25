import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { TitleSummary } from '../../api/types'
import { PosterImage } from '../../components/PosterImage'
import { VersionChip } from '../../components/VersionChip'
import { formatNumber } from '../../lib/format'

/** Die Bibliothek als Poster. Filme und Serien mit denselben Karten, Serien zeigen im Chip ihre Folgen. */
export function TitleGrid({
  titles,
  selection = null,
}: {
  titles: TitleSummary[]
  /** Rueckmeldung 20.09.2026: im Auswahlmodus ein Kaestchen in der Ecke jeder Karte. */
  selection?: { ids: ReadonlySet<number>; whole: boolean; toggle: (id: number) => void } | null
}) {
  return (
    <ul className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
      {titles.map((title) => (
        <li key={title.id} className="relative min-w-0">
          {selection !== null && (
            <CardMark title={title} selection={selection} />
          )}
          <TitleCard title={title} />
        </li>
      ))}
    </ul>
  )
}

function CardMark({
  title,
  selection,
}: {
  title: TitleSummary
  selection: { ids: ReadonlySet<number>; whole: boolean; toggle: (id: number) => void }
}) {
  const { t } = useTranslation()
  const marked = selection.whole || selection.ids.has(title.id)
  return (
    <input
      type="checkbox"
      checked={marked}
      onChange={() => selection.toggle(title.id)}
      aria-label={t('library.select.markLabel', { title: title.title })}
      className="absolute top-2 left-2 z-10 h-5 w-5 rounded border border-ink-700 bg-ink-900/90 accent-accent-500"
    />
  )
}

function TitleCard({ title }: { title: TitleSummary }) {
  const { t, i18n } = useTranslation()
  const unclear = title.versions.reduce((sum, version) => sum + (version.unclear_files ?? 0), 0)
  const isAlbum = title.kind === 'album'
  const kind = title.kind === 'series' ? t('series.kind') : t('common.kind.movie')
  const meta = isAlbum
    ? [title.artist ?? null, title.year !== null && title.year > 0 ? String(title.year) : null].filter(Boolean).join(' · ')
    : [kind, title.year !== null && title.year > 0 ? String(title.year) : null].filter(Boolean).join(' · ')
  return (
    <Link to={`/titel/${title.id}`} className="group flex flex-col gap-2.5 rounded-xl">
      <div className="transition-transform duration-200 group-hover:-translate-y-1">
        <PosterImage url={title.poster_url} placeholder={isAlbum ? 'note' : 'film'} square={isAlbum} className="shadow-lg shadow-black/30" />
      </div>
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-mist-100 group-hover:text-accent-400" title={title.title}>
          {title.title}
        </p>
        <p className="truncate text-xs text-mist-500">{meta}</p>
        {unclear > 0 && (
          <p className="truncate text-xs text-accent-400 tabular-nums">
            {t('library.list.unclear', { count: unclear, value: formatNumber(unclear, i18n.language) })}
          </p>
        )}
      </div>
      <div className="flex flex-wrap gap-1">
        {title.versions.map((version) => (
          <VersionChip key={version.id} version={version} compact />
        ))}
      </div>
    </Link>
  )
}
