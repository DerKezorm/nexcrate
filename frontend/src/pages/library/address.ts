import { LIBRARY_SORTS } from '../../api/library'
import { ARTIST_SORTS, type ArtistSort } from '../../api/music'
import type { LibrarySort, MediaKind, VersionState } from '../../api/types'

/** "unclear" ist kein Zustand einer Fassung: Serien mit Dateien ohne Folge (S6). Nur bei Serien. */
export type StateFilter = 'all' | Exclude<VersionState, 'available'> | 'unclear'

export const KINDS: readonly MediaKind[] = ['movie', 'series', 'album']
export const STATE_FILTERS: readonly StateFilter[] = ['all', 'wanted', 'downloading', 'problem', 'incomplete', 'upgrade', 'unmonitored', 'unclear']

/** Musik M1 (Entscheidung 37): der Umschalter Kuenstler, Alben unter Musik, ab Werk Kuenstler. */
export type MusicView = 'kuenstler' | 'alben'
export const MUSIC_VIEWS: readonly MusicView[] = ['kuenstler', 'alben']

/** Alben in der Reihenfolge, in der sie beim Aufraeumen zaehlen: fehlende Titel und unklare Dateien zuerst. */
const ALBUM_FILTERS: readonly StateFilter[] = ['all', 'incomplete', 'unclear', 'upgrade', 'wanted', 'downloading', 'problem', 'unmonitored']

/** Die Filter, die eine Art anbietet. Unklare Dateien gibt es nicht bei Filmen, fehlende Titel nur bei Alben. */
export function stateFiltersFor(kind: MediaKind): readonly StateFilter[] {
  if (kind === 'album') return ALBUM_FILTERS
  return STATE_FILTERS.filter((value) => (value !== 'unclear' || kind === 'series') && value !== 'incomplete')
}

/** Mehr Seiten stellt die Adresse nie wieder her. Die Bibliothek begrenzt ohnehin auf die letzte vorhandene. */
const MAX_PAGE = 1000

/**
 * Was die Bibliothek gerade zeigt. Es steht in der Adresse, damit ein Titel und
 * "Zurueck" die Liste so wiederbringen, wie sie war, samt der geladenen Seiten.
 */
export type LibraryAddress = {
  kind: MediaKind
  state: StateFilter
  q: string
  sort: LibrarySort
  /** Wie viele Seiten geladen sind, nicht welche gerade zu sehen ist. */
  page: number
  /** Musik M1: Kuenstler oder Alben. Nur bei `kind === 'album'` von Bedeutung. */
  view: MusicView
  /** Musik M1: Sortierung der Kuenstlerliste, getrennt von `sort` (das bleibt fuer die Albenliste). */
  artistSort: ArtistSort
  /** Seit T1: nur was diesen Tag traegt; leer fuer alle. */
  tag: string
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback
}

/** Unbekannte oder kaputte Werte gelten als nicht gesetzt. */
export function readAddress(params: URLSearchParams): LibraryAddress {
  const page = Number(params.get('page'))
  const kind = oneOf(params.get('kind'), KINDS, 'movie')
  return {
    kind,
    state: oneOf(params.get('state'), stateFiltersFor(kind), 'all'),
    q: params.get('q') ?? '',
    sort: oneOf(params.get('sort'), LIBRARY_SORTS, 'title'),
    page: Number.isInteger(page) && page >= 1 ? Math.min(page, MAX_PAGE) : 1,
    view: oneOf(params.get('ansicht'), MUSIC_VIEWS, 'kuenstler'),
    artistSort: oneOf(params.get('ksort'), ARTIST_SORTS, 'name'),
    tag: params.get('tag') ?? '',
  }
}

/** Nur was vom Normalfall abweicht, kommt in die Adresse. So bleibt `/` die Bibliothek, wie sie startet. */
export function writeAddress(address: LibraryAddress): URLSearchParams {
  const params = new URLSearchParams()
  if (address.kind !== 'movie') params.set('kind', address.kind)
  if (address.state !== 'all') params.set('state', address.state)
  if (address.q !== '') params.set('q', address.q)
  if (address.sort !== 'title') params.set('sort', address.sort)
  if (address.page > 1) params.set('page', String(address.page))
  if (address.view !== 'kuenstler') params.set('ansicht', address.view)
  if (address.artistSort !== 'name') params.set('ksort', address.artistSort)
  if (address.tag !== '') params.set('tag', address.tag)
  return params
}

export function sameAddress(a: LibraryAddress, b: LibraryAddress): boolean {
  return (
    a.kind === b.kind &&
    a.state === b.state &&
    a.q === b.q &&
    a.sort === b.sort &&
    a.page === b.page &&
    a.view === b.view &&
    a.artistSort === b.artistSort &&
    a.tag === b.tag
  )
}
