import type { TFunction } from 'i18next'

import { ANIME_CATEGORY, DEFAULT_MOVIE_CATEGORIES, DEFAULT_SERIES_CATEGORIES, isMovieCategory, isSeriesCategory } from '../../api/indexers'
import type { IndexerCaps } from '../../api/types'

export type CategoryOption = { id: number; name: string }

/**
 * Die Kategorien aus caps, flach: jede Hauptkategorie, direkt dahinter ihre Unterkategorien.
 * Newznab nennt eine Unterkategorie oft nur "HD"; dann steht die Hauptkategorie davor.
 */
export function flattenCategories(caps: IndexerCaps | null): CategoryOption[] {
  if (!caps) return []
  const seen = new Set<number>()
  const result: CategoryOption[] = []
  const push = (option: CategoryOption) => {
    if (seen.has(option.id)) return
    seen.add(option.id)
    result.push(option)
  }
  for (const category of caps.categories) {
    push({ id: category.id, name: category.name })
    for (const sub of category.subcats ?? []) {
      const named = sub.name.toLocaleLowerCase().includes(category.name.toLocaleLowerCase())
      push({ id: sub.id, name: named ? sub.name : `${category.name} ${sub.name}` })
    }
  }
  return result
}

/** Vorauswahl nach dem ersten Test: die Filmkategorien des Indexers, sonst die ueblichen. */
export function defaultCategories(caps: IndexerCaps | null): number[] {
  const movies = flattenCategories(caps)
    .map((option) => option.id)
    .filter(isMovieCategory)
  return movies.length > 0 ? movies : [...DEFAULT_MOVIE_CATEGORIES]
}

/** Namen fuer die ueblichen Filmkategorien, wenn der Indexer selbst keine nennt. */
function standardName(t: TFunction, id: number): string {
  switch (id) {
    case 2000:
      return t('indexers.categoryName.movies')
    case 2010:
      return t('indexers.categoryName.foreign')
    case 2020:
      return t('indexers.categoryName.other')
    case 2030:
      return t('indexers.categoryName.sd')
    case 2040:
      return t('indexers.categoryName.hd')
    case 2045:
      return t('indexers.categoryName.uhd')
    case 2050:
      return t('indexers.categoryName.bluray')
    case 2060:
      return t('indexers.categoryName.threeD')
    default:
      return t('indexers.categoryName.unnamed', { id })
  }
}

/**
 * Was als Kaestchen dasteht: die Kategorien aus caps, ohne caps die ueblichen fuer Filme.
 * Gewaehlte Nummern, die der Indexer nicht nennt, stehen trotzdem da, damit man sie abwaehlen
 * kann. Ohne `all` nur Filmkategorien und was schon gewaehlt ist.
 */
export function categoryOptions(t: TFunction, caps: IndexerCaps | null, selected: readonly number[], all: boolean): CategoryOption[] {
  const fromCaps = flattenCategories(caps)
  const base = fromCaps.length > 0 ? fromCaps : DEFAULT_MOVIE_CATEGORIES.map((id) => ({ id, name: standardName(t, id) }))
  const known = new Set(base.map((option) => option.id))
  const extra = selected.filter((id) => !known.has(id)).map((id) => ({ id, name: standardName(t, id) }))
  const options = [...base, ...extra]
  return all ? options : options.filter((option) => isMovieCategory(option.id) || selected.includes(option.id))
}

/** Nennt der Indexer ausser Filmkategorien noch andere, die man aufklappen kann? */
export function hasOtherCategories(caps: IndexerCaps | null): boolean {
  return flattenCategories(caps).some((option) => !isMovieCategory(option.id))
}

/** Seit S3: eine Serienkategorie mit dem Hinweis, ob sie Anime ist. Anime hat seit A3 eine eigene Liste. */
export type SeriesCategoryOption = CategoryOption & { anime: boolean }

/** Namen fuer die ueblichen Serienkategorien, wenn der Indexer selbst keine nennt. */
function seriesStandardName(t: TFunction, id: number): string {
  switch (id) {
    case 5000:
      return t('indexers.categoryName.tv')
    case 5010:
      return t('indexers.categoryName.tvWeb')
    case 5020:
      return t('indexers.categoryName.tvForeign')
    case 5030:
      return t('indexers.categoryName.tvSd')
    case 5040:
      return t('indexers.categoryName.tvHd')
    case 5045:
      return t('indexers.categoryName.tvUhd')
    case 5050:
      return t('indexers.categoryName.tvOther')
    case 5060:
      return t('indexers.categoryName.tvSport')
    case 5070:
      return t('indexers.categoryName.tvAnime')
    case 5080:
      return t('indexers.categoryName.tvDocumentary')
    default:
      return t('indexers.categoryName.unnamed', { id })
  }
}

/** Die Nummern der Anime-Kategorie und ihrer Unterkategorien aus caps. */
function animeIds(caps: IndexerCaps | null): Set<number> {
  const ids = new Set<number>([ANIME_CATEGORY])
  for (const category of caps?.categories ?? []) {
    if (category.id !== ANIME_CATEGORY) continue
    for (const sub of category.subcats ?? []) ids.add(sub.id)
  }
  return ids
}

/** Vorauswahl fuer Serien: alle Serienkategorien des Indexers ohne Anime, sonst die ueblichen. */
export function defaultSeriesCategories(caps: IndexerCaps | null): number[] {
  const anime = animeIds(caps)
  const series = flattenCategories(caps)
    .map((option) => option.id)
    .filter((id) => isSeriesCategory(id) && !anime.has(id))
  return series.length > 0 ? series : [...DEFAULT_SERIES_CATEGORIES]
}

/**
 * Die Kaestchen fuer Serien: 5000 bis 5999 aus caps, ohne caps die ueblichen samt Anime. Gewaehlte Nummern, die der
 * Indexer nicht nennt, stehen trotzdem da. Anime steht immer da, aber gesperrt.
 */
export function seriesCategoryOptions(t: TFunction, caps: IndexerCaps | null, selected: readonly number[]): SeriesCategoryOption[] {
  const anime = animeIds(caps)
  const fromCaps = flattenCategories(caps).filter((option) => isSeriesCategory(option.id))
  const base = fromCaps.length > 0 ? fromCaps : [...DEFAULT_SERIES_CATEGORIES, ANIME_CATEGORY].map((id) => ({ id, name: seriesStandardName(t, id) }))
  const known = new Set(base.map((option) => option.id))
  const extra = selected.filter((id) => !known.has(id)).map((id) => ({ id, name: seriesStandardName(t, id) }))
  return [...base, ...extra].map((option) => ({ ...option, anime: anime.has(option.id) }))
}

/** Die Serienkategorien, die hinausgehen: sortiert, ohne Anime. */
export function cleanSeriesCategories(caps: IndexerCaps | null, ids: readonly number[]): number[] {
  const anime = animeIds(caps)
  return [...new Set(ids)].filter((id) => !anime.has(id)).sort((a, b) => a - b)
}

/**
 * Seit Anime A3: die Vorgabe fuer Anime wie `indexers.default_anime_categories`. 5070 samt Unterkategorien aus den caps,
 * ohne caps 5070, und keine, wenn die caps Kategorien nennen, aber nicht 5070.
 */
export function defaultAnimeCategories(caps: IndexerCaps | null): number[] {
  const listed = caps?.categories ?? []
  if (listed.length === 0) return [ANIME_CATEGORY]
  if (!listed.some((category) => category.id === ANIME_CATEGORY)) return []
  return [...animeIds(caps)].sort((a, b) => a - b)
}

/** Die Kaestchen fuer Anime: 5070 und seine Unterkategorien aus den caps, sonst 5070. Gewaehltes steht immer da. */
export function animeCategoryOptions(t: TFunction, caps: IndexerCaps | null, selected: readonly number[]): CategoryOption[] {
  const anime = animeIds(caps)
  const fromCaps = flattenCategories(caps).filter((option) => anime.has(option.id))
  const base = fromCaps.length > 0 ? fromCaps : [{ id: ANIME_CATEGORY, name: seriesStandardName(t, ANIME_CATEGORY) }]
  const known = new Set(base.map((option) => option.id))
  const extra = selected.filter((id) => !known.has(id)).map((id) => ({ id, name: seriesStandardName(t, id) }))
  return [...base, ...extra]
}

/** Die Anime-Kategorien, die hinausgehen: sortiert, jede einmal. */
export function cleanAnimeCategories(ids: readonly number[]): number[] {
  return [...new Set(ids)].sort((a, b) => a - b)
}

/** Seit M3: die ueblichen Musikkategorien, wenn der Indexer keine nennt. Wie `album.DEFAULT_MUSIC_CATEGORIES`. */
export const DEFAULT_MUSIC_CATEGORIES = [3000, 3010, 3040] as const
/** Video und Hoerbuecher fragt eine Albensuche nie. */
export const LEFT_OUT_MUSIC_CATEGORIES: readonly number[] = [3020, 3030]

export function isMusicCategory(id: number): boolean {
  return id >= 3000 && id <= 3999
}

/** Namen fuer die ueblichen Musikkategorien, wenn der Indexer selbst keine nennt. */
function musicStandardName(t: TFunction, id: number): string {
  switch (id) {
    case 3000:
      return t('indexers.categoryName.audio')
    case 3010:
      return t('indexers.categoryName.audioMp3')
    case 3020:
      return t('indexers.categoryName.audioVideo')
    case 3030:
      return t('indexers.categoryName.audioBook')
    case 3040:
      return t('indexers.categoryName.audioLossless')
    case 3050:
      return t('indexers.categoryName.audioOther')
    case 3060:
      return t('indexers.categoryName.audioForeign')
    default:
      return t('indexers.categoryName.unnamed', { id })
  }
}

/** Vorauswahl fuer Musik: die Musikkategorien des Indexers ohne Video und Hoerbuecher, sonst die ueblichen. */
export function defaultMusicCategories(caps: IndexerCaps | null): number[] {
  const music = flattenCategories(caps)
    .map((option) => option.id)
    .filter((id) => isMusicCategory(id) && !LEFT_OUT_MUSIC_CATEGORIES.includes(id))
  return music.length > 0 ? music.sort((a, b) => a - b) : [...DEFAULT_MUSIC_CATEGORIES]
}

/** Seit M3: eine Musikkategorie mit dem Hinweis, ob die Suche sie nie fragt (Video, Hoerbuch). */
export type MusicCategoryOption = CategoryOption & { leftOut: boolean }

/** Die Kaestchen fuer Musik: 3000 bis 3999 aus caps, ohne caps die ueblichen. Gewaehltes steht immer da. */
export function musicCategoryOptions(t: TFunction, caps: IndexerCaps | null, selected: readonly number[]): MusicCategoryOption[] {
  const fromCaps = flattenCategories(caps).filter((option) => isMusicCategory(option.id))
  const base = fromCaps.length > 0 ? fromCaps : DEFAULT_MUSIC_CATEGORIES.map((id) => ({ id, name: musicStandardName(t, id) }))
  const known = new Set(base.map((option) => option.id))
  const extra = selected.filter((id) => !known.has(id)).map((id) => ({ id, name: musicStandardName(t, id) }))
  return [...base, ...extra].map((option) => ({ ...option, leftOut: LEFT_OUT_MUSIC_CATEGORIES.includes(option.id) }))
}

/** Die Musikkategorien, die hinausgehen: sortiert, ohne Video und Hoerbuecher. */
export function cleanMusicCategories(ids: readonly number[]): number[] {
  return [...new Set(ids)].filter((id) => !LEFT_OUT_MUSIC_CATEGORIES.includes(id)).sort((a, b) => a - b)
}
