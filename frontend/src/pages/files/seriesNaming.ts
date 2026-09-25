export const SERIES_PATTERNS = ['series_folder', 'season_folder', 'specials_folder', 'episode_file', 'daily_file', 'anime_file'] as const
export type SeriesPattern = (typeof SERIES_PATTERNS)[number]

/** Der Serienordner mit genau einem Zusatz fuer einen Server: vorhandene Zusaetze fallen weg (S4, Entscheidung 9). */
export function withAddition(pattern: string, addition: string, additions: readonly string[]): string {
  let base = pattern
  for (const known of additions) base = base.split(known).join('')
  return `${base.trim()} ${addition}`.trim()
}
