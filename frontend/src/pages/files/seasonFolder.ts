/**
 * Ob ein Ordnerpfad einen Teil hat, der wie eine Staffel aussieht (S4, Entscheidung 55): `s1`, `S04`, `season 2`,
 * `staffel 3`, `Season_02`. Jellyfin liest so einen Ordner als Staffel und ordnet Serien darin falsch zu; gemessen in
 * B1 an `s4b1`: Jellyfin nimmt schon den Anfang (Wort fuer Staffel, dann Ziffern) und laesst den Rest stehen, "series"
 * zaehlt dort auch als Staffelwort. Die Warnung sperrt nichts.
 */
const SEASON_PART = /^(?:s|season|staffel|saison|series|temporada|stagione)[ ._-]*\d/i

export function looksLikeSeason(path: string | null | undefined): boolean {
  if (!path) return false
  return path
    .split(/[\\/]+/)
    .map((part) => part.trim())
    .some((part) => part !== '' && SEASON_PART.test(part))
}
