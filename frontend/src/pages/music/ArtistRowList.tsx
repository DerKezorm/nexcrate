import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { ArtistSummary } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { STATE_LOOK } from '../../lib/states'
import { ArtistCover } from './ArtistCover'
import { badgeState, countParts, stateLabel } from './artistCounts'
import { loadErrorText, tileLoadingText } from './loadingText'

/**
 * Die Musik-Bibliothek als Liste: Kuenstler mit Cover, Name, Alias und der Zeile darunter, was er hat, was fehlt,
 * was besser gehen koennte und wie viel Platz das belegt. Rechts das Abzeichen des einen Zustands, der zaehlt
 * (Rueckmeldung 20.09.2026). Ein Kuenstler, der noch laedt, zeigt statt alledem seinen Fortschritt.
 */
export function ArtistRowList({
  artists,
  selection = null,
}: {
  artists: ArtistSummary[]
  /** Rueckmeldung 20.09.2026: im Auswahlmodus ein Kaestchen je Zeile. */
  selection?: { ids: ReadonlySet<number>; whole: boolean; toggle: (id: number) => void } | null
}) {
  const { t, i18n } = useTranslation()
  return (
    <div className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-850/60">
      <ul>
        {artists.map((artist) => {
          const notReady = artist.load_state !== 'ready'
          const failed = artist.load_state === 'failed'
          const sub = notReady ? tileLoadingText(t, artist.load_state, artist.load_done, artist.load_total) : countParts(t, artist, i18n.language).join(' · ')
          const state = notReady ? null : badgeState(artist)
          return (
            <li key={artist.id} className="flex min-w-0 items-center border-b border-ink-700/60 last:border-b-0">
              {selection !== null && (
                <input
                  type="checkbox"
                  checked={selection.whole || selection.ids.has(artist.id)}
                  onChange={() => selection.toggle(artist.id)}
                  aria-label={t('library.select.markLabel', { title: artist.name })}
                  className="ml-4 h-4 w-4 shrink-0 accent-accent-500"
                />
              )}
              <Link to={`/kuenstler/${artist.id}`} className="group flex min-w-0 flex-1 items-center gap-3 px-4 py-3 hover:bg-ink-850">
                <ArtistCover name={artist.name} url={artist.cover_url} className="w-11 shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold text-mist-100 group-hover:text-accent-400">{artist.name}</p>
                  {artist.alias_display && <p className="truncate text-xs text-mist-500">{artist.alias_display}</p>}
                  <p className={'truncate text-xs ' + (failed ? 'text-bad-500' : notReady ? 'text-accent-400' : 'text-mist-500')} title={failed ? loadErrorText(t, artist.load_error) : undefined}>
                    {sub}
                  </p>
                </div>
                {state !== null && (
                  <span className={'inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ' + STATE_LOOK[state].chip}>
                    <Symbol name={STATE_LOOK[state].symbol} className="h-3.5 w-3.5" />
                    {stateLabel(t, state)}
                  </span>
                )}
              </Link>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
