import type { SearchRelease, SearchReleaseVersion } from '../../api/types'

/** Ein Release mit seinem Ergebnis fuer eine Fassung. */
export type VersionRow = { release: SearchRelease; entry: SearchReleaseVersion }

/** Passt zum Profil. Bei Serien (S3) steht das Ergebnis unter `series_result`. */
export function fits(entry: SearchReleaseVersion): boolean {
  return entry.result?.accepted === true || entry.series_result?.accepted === true
}

/**
 * Die Releases dieses Films fuer eine Fassung, in ihrer Reihenfolge: erst die mit Platz, aufsteigend,
 * dann die uebrigen so, wie der Server sie schickt. Releases anderer Filme stehen nie darin.
 * `onlyFitting` laesst alles weg, was nicht passt.
 */
export function rowsForVersion(releases: readonly SearchRelease[], versionId: number, onlyFitting = false): VersionRow[] {
  const rows: VersionRow[] = []
  for (const release of releases) {
    if (!release.belongs) continue
    const entry = release.versions.find((item) => item.version_id === versionId)
    if (entry) rows.push({ release, entry })
  }
  const ranked = rows.filter((row) => row.entry.rank !== null).sort((left, right) => (left.entry.rank ?? 0) - (right.entry.rank ?? 0))
  const rest = rows.filter((row) => row.entry.rank === null)
  const ordered = [...ranked, ...rest]
  return onlyFitting ? ordered.filter((row) => fits(row.entry)) : ordered
}

/** Das beste passende Release einer Fassung, oder undefined. */
export function bestFitting(rows: readonly VersionRow[]): VersionRow | undefined {
  return rows.find((row) => row.entry.rank !== null && fits(row.entry))
}

/** Was ein Indexer geliefert hat, das aber zu einem anderen Film oder einer anderen Serie gehoert. */
export function otherMovies(releases: readonly SearchRelease[]): SearchRelease[] {
  return releases.filter((release) => !release.belongs)
}
