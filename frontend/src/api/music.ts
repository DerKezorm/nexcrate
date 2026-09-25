import { api } from './client'
import type {
  AcoustIdState,
  AlbumTagPreview,
  MusicFilesSettings,
  AlbumCreated,
  AlbumHit,
  AlbumIn,
  AlbumSearchKind,
  ArtistDetail,
  ArtistHit,
  ArtistIn,
  ArtistList,
  ArtistPatch,
  ArtistPreview,
  ArtistRemoved,
  ArtistSummary,
  LoadingStatus,
  MusicBrainzIn,
  CountriesOut,
  MusicBrainzState,
  MusicProfile,
  MusicProfilePreview,
  ProfileAnswers,
  QuestionList,
  ReleaseDetail,
  TargetIn,
  TargetOut,
  WatchOut,
} from './types'

/** So viele Kuenstler holt eine Seite der Kuenstlerliste (M1.5). */
export const ARTISTS_PAGE_SIZE = 60

export type ArtistSort = 'name' | 'added'

export const ARTIST_SORTS: readonly ArtistSort[] = ['name', 'added']

/**
 * Musik (M1.4, M1.5): Kuenstler und Alben suchen und hinzufuegen, die Kuenstlerseite, das Laden im Hintergrund, die
 * Zielausgabe einer Fassung, die Schalter fuer MusicBrainz und das Cover Art Archive. Die Albumseite selbst nutzt
 * `libraryApi.detail` wie jeder andere Titel; nur ihre Ausgabe mit Titeln kommt von hier (`release`).
 */
export const musicApi = {
  searchArtists: (q: string, signal?: AbortSignal) => api.get<ArtistHit[]>(`/music/search/artists?q=${encodeURIComponent(q)}`, { signal }),
  previewArtist: (mbid: string) => api.post<ArtistPreview>('/music/artists/preview', { mbid }),
  addArtist: (body: ArtistIn) => api.post<ArtistSummary>('/music/artists', body),
  searchAlbums: (q: string, artist?: string, kind?: AlbumSearchKind, signal?: AbortSignal) => {
    const params = new URLSearchParams({ q })
    if (artist) params.set('artist', artist)
    if (kind) params.set('kind', kind)
    return api.get<AlbumHit[]>(`/music/search/albums?${params.toString()}`, { signal })
  },
  addAlbum: (body: AlbumIn) => api.post<AlbumCreated>('/music/albums', body),
  listArtists: (q: string, sort: ArtistSort, page: number, signal?: AbortSignal, tag?: string | null) => {
    const params = new URLSearchParams({ sort, page: String(page), page_size: String(ARTISTS_PAGE_SIZE) })
    if (q.trim() !== '') params.set('q', q.trim())
    if (tag) params.set('tag', tag)
    return api.get<ArtistList>(`/music/artists?${params.toString()}`, { signal })
  },
  artist: (id: number, signal?: AbortSignal) => api.get<ArtistDetail>(`/music/artists/${id}`, { signal }),
  changeArtist: (id: number, body: ArtistPatch) => api.patch<ArtistSummary>(`/music/artists/${id}`, body),
  refreshArtist: (id: number) => api.post<ArtistSummary>(`/music/artists/${id}/refresh`),
  removeArtist: (id: number) => api.delete<ArtistRemoved>(`/music/artists/${id}`),
  /** Was mehrere Kuenstler mit neuen Alben tun; ohne `artist_ids` alle, die die Suche findet. */
  setMonitorNew: (body: { monitor_new: 'all' | 'none'; artist_ids?: number[] | null; q?: string | null; tag?: string | null }) =>
    api.put<{ changed: number }>('/music/artists/monitor-new', body),
  loading: (signal?: AbortSignal) => api.get<LoadingStatus>('/music/loading', { signal }),
  release: (id: number, signal?: AbortSignal) => api.get<ReleaseDetail>(`/music/releases/${id}`, { signal }),
  loadReleases: (titleId: number) => api.post<{ releases: number }>(`/music/albums/${titleId}/releases`),
  setTarget: (titleId: number, body: TargetIn) => api.patch<TargetOut>(`/music/albums/${titleId}/target`, body),
  setWatch: (titleId: number, monitored: boolean) => api.patch<WatchOut>(`/music/albums/${titleId}/watch`, { monitored }),
  countries: (signal?: AbortSignal) => api.get<CountriesOut>('/music/settings/countries', { signal }),
  changeCountries: (countries: string[] | null) => api.put<CountriesOut>('/music/settings/countries', { countries }),
  settings: () => api.get<MusicBrainzState>('/music/settings/musicbrainz'),
  /** Musik M2: das eine Profil der Musik-Fassung. 404 `not_found`, solange es keines gibt. */
  profile: (signal?: AbortSignal) => api.get<MusicProfile>('/music/profile', { signal }),
  profileQuestions: (signal?: AbortSignal) => api.get<QuestionList>('/music/profile/questions', { signal }),
  /** Baut die Zusammenfassung, ohne zu speichern. 422 `profile_answers_invalid {fields}`. */
  previewProfile: (answers: ProfileAnswers) => api.post<MusicProfilePreview>('/music/profile/preview', { answers }),
  saveProfile: (answers: ProfileAnswers) => api.put<MusicProfile>('/music/profile', { answers }),
  removeProfile: () => api.delete<void>('/music/profile'),
  changeSettings: (body: MusicBrainzIn) => api.put<MusicBrainzState>('/music/settings/musicbrainz', body),
  /** Seit M4: ob Tags und Cover in abgelegte Musik geschrieben werden. */
  filesSettings: (signal?: AbortSignal) => api.get<MusicFilesSettings>('/music/settings/files', { signal }),
  changeFilesSettings: (body: MusicFilesSettings) => api.put<MusicFilesSettings>('/music/settings/files', body),
  /** Seit M4: Fingerprinting mit dem eigenen AcoustID-Schluessel. Der Schluessel kommt nie zurueck. */
  acoustId: (signal?: AbortSignal) => api.get<AcoustIdState>('/music/settings/acoustid', { signal }),
  changeAcoustId: (body: { enabled?: boolean; key?: string }) => api.put<AcoustIdState>('/music/settings/acoustid', body),
  /** Fragt acoustid.org, ob der Schluessel in Gebrauch gilt (der eigene, sonst der mitgelieferte). 409 `acoustid_no_key`. */
  checkAcoustId: () => api.post<AcoustIdState>('/music/settings/acoustid/check'),
  /** Seit M4: die Retag-Vorschau eines Albums; 409 `album_fed_by_source`, `album_without_files`, `album_folder_missing`. */
  tagPreview: (titleId: number, signal?: AbortSignal) => api.get<AlbumTagPreview>(`/music/albums/${titleId}/tags`, { signal }),
  writeTags: (titleId: number) => api.post<AlbumTagPreview>(`/music/albums/${titleId}/tags`),
  /** Musik M6: den Albumordner neu einlesen; 409 `album_fed_by_source`, `album_folder_missing`. */
  readFolder: (titleId: number) => api.post<FolderRead>(`/music/albums/${titleId}/read`),
  /** Musik M6: eine unklare Datei einem Titel geben, oder mit null keinem; 409 `track_has_file`, 422 `track_not_in_album`. */
  settleFile: (titleId: number, fileId: number, trackId: number | null) =>
    api.put<{ unclear_left: number }>(`/music/albums/${titleId}/files/${fileId}`, { track_id: trackId }),
}

/** Musik M6: was "Ordner neu einlesen" fand. */
export type FolderRead = { linked: number; unclear: number; gone: number; known: number }
