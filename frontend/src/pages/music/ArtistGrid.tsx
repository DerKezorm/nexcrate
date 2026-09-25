import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { ArtistSummary } from '../../api/types'
import { formatNumber } from '../../lib/format'
import { ArtistCover } from './ArtistCover'
import { loadErrorText, tileLoadingText } from './loadingText'

/** Die Kachelzeile einer Kuenstlerkachel: der Ladezustand, solange er nicht `ready` ist, sonst die Zahlen (Entscheidung 38). */
function ArtistCountLine({ artist }: { artist: ArtistSummary }) {
  const { t, i18n } = useTranslation()
  if (artist.load_state !== 'ready') {
    const loading = tileLoadingText(t, artist.load_state, artist.load_done, artist.load_total)
    const failed = artist.load_state === 'failed'
    return (
      <p className={'truncate text-xs ' + (failed ? 'text-bad-500' : 'text-accent-400')} title={failed ? loadErrorText(t, artist.load_error) : undefined}>
        {loading}
      </p>
    )
  }
  return (
    <p className="truncate text-xs text-mist-500">
      {t('music.artist.counts', { count: artist.albums, value: formatNumber(artist.albums, i18n.language), complete: formatNumber(artist.complete, i18n.language) })}
    </p>
  )
}

/** Die Musik-Bibliothek als Kacheln: Kuenstler mit Cover, Name, Alias und Zahl der Alben (Entscheidung 38). */
export function ArtistGrid({
  artists,
  selection = null,
}: {
  artists: ArtistSummary[]
  /** Rueckmeldung 20.09.2026: im Auswahlmodus ein Kaestchen in der Ecke. */
  selection?: { ids: ReadonlySet<number>; whole: boolean; toggle: (id: number) => void } | null
}) {
  const { t } = useTranslation()
  return (
    <ul className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
      {artists.map((artist) => (
        <li key={artist.id} className="relative min-w-0">
          {selection !== null && (
            <input
              type="checkbox"
              checked={selection.whole || selection.ids.has(artist.id)}
              onChange={() => selection.toggle(artist.id)}
              aria-label={t('library.select.markLabel', { title: artist.name })}
              className="absolute top-2 left-2 z-10 h-5 w-5 rounded border border-ink-700 bg-ink-900/90 accent-accent-500"
            />
          )}
          <Link to={`/kuenstler/${artist.id}`} className="group flex flex-col gap-2.5 rounded-xl">
            <div className="transition-transform duration-200 group-hover:-translate-y-1">
              <ArtistCover name={artist.name} url={artist.cover_url} className="shadow-lg shadow-black/30" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-mist-100 group-hover:text-accent-400" title={artist.name}>
                {artist.name}
              </p>
              {artist.alias_display && <p className="truncate text-xs text-mist-500">{artist.alias_display}</p>}
              <ArtistCountLine artist={artist} />
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}
