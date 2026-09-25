import type { Episode, EpisodeNumber, NumberingCorrectionIn } from '../../api/types'

/** Wie im Server: Nummern von 0 bis 9999, eine Doppelfolge hoechstens 10 Folgen weit. */
export const CORRECTION_NUMBER_MAX = 9999
export const CORRECTION_RANGE_MAX = 10

/**
 * Die lokale Korrektur einer Folge, wie der Server sie in den Nummern der Folge schickt. Seit B6 kann sie nur die
 * Durchzaehlnummer einer Anime-Serie tragen, ohne Staffel und Folge.
 */
export function ownerNumber(episode: Episode): EpisodeNumber | null {
  return (
    episode.numbers.find(
      (number) => number.scheme === 'owner' && ((typeof number.season === 'number' && typeof number.episode === 'number') || typeof number.absolute === 'number'),
    ) ?? null
  )
}

/** Eine Nummer aus einem Feld, 0 bis 9999. null, wenn es keine ist. */
export function readCorrectionNumber(text: string): number | null {
  const clean = text.trim()
  if (!/^\d{1,4}$/.test(clean)) return null
  const value = Number(clean)
  return value <= CORRECTION_NUMBER_MAX ? value : null
}

/**
 * Die Korrekturen, die eine Eingabe ergibt: die Folge selbst und mit `following` jede spaetere Folge derselben
 * TMDB-Staffel, fortgezaehlt ab dem Ende der korrigierten. Rein, damit Vorschau und Speichern dasselbe rechnen.
 * Seit B6: `season` und `episode` sind null, wenn nur die Durchzaehlnummer (`absolute`) korrigiert wird; sie zaehlt
 * genauso fort.
 */
export function correctionsFor(
  episode: Episode,
  seasonEpisodes: readonly Episode[],
  input: { season: number | null; episode: number | null; episodeEnd: number | null; absolute?: number | null },
  following: boolean,
): NumberingCorrectionIn[] {
  const counted = input.absolute ?? null
  const own: NumberingCorrectionIn = { episode_id: episode.id, season: input.season, episode: input.episode, episode_end: input.episodeEnd }
  if (counted !== null) own.absolute = counted
  if (!following) return [own]
  const last = input.episode === null ? null : (input.episodeEnd ?? input.episode)
  const later = seasonEpisodes
    .filter((item) => item.id !== episode.id && item.season_number === episode.season_number && item.number > episode.number)
    .sort((left, right) => left.number - right.number)
  const followers: NumberingCorrectionIn[] = []
  for (const item of later) {
    const step = item.number - episode.number
    const number = last === null ? null : last + step
    const through = counted === null ? null : counted + step
    if ((number !== null && number > CORRECTION_NUMBER_MAX) || (through !== null && through > CORRECTION_NUMBER_MAX)) continue
    const entry: NumberingCorrectionIn = { episode_id: item.id, season: number === null ? null : input.season, episode: number, episode_end: null }
    if (through !== null) entry.absolute = through
    followers.push(entry)
  }
  return [own, ...followers]
}

/** Eine Durchzaehlnummer aus einem Feld, 1 bis 9999. null, wenn es keine ist. */
export function readCountedNumber(text: string): number | null {
  const value = readCorrectionNumber(text)
  return value !== null && value >= 1 ? value : null
}
