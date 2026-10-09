import type { DownloadEpisodeChoice, DownloadFiles, DownloadVideo } from '../../api/types'

/** Entscheidungen, deren Dateien der Besitzer noch zuordnen kann. Wie `OPEN_DECISIONS` im Server. */
export const OPEN_DECISIONS = ['open', 'not_needed', 'duplicate', 'sample', 'not_filed', 'extra', 'skipped']

/**
 * `part`: die Haelfte einer Doppelfolge, 1 oder 2; fehlt oder null fuer eine ganze Folge. `byName`: vorgewaehlt nach dem
 * Folgennamen in der Datei (seit 09.10.2026).
 */
export type Choice = { episodes: (number | null)[]; proposal: boolean; part?: 1 | 2 | null; byName?: boolean }

/** Die Folgen, die der Server fuer eine Datei vorschlaegt, und wie; null ohne Vorschlag. */
export function proposalOf(file: DownloadVideo): { ids: number[]; by: 'name' | 'shift' } | null {
  const proposal = file.proposal
  if (!proposal || proposal.episodes.length === 0) return null
  return { ids: proposal.episodes.map((episode) => episode.id), by: proposal.by }
}

/** Ob eine wartende Datei einen Vorschlag hat: dann nimmt der Knopf ihn statt der rohen Nummern. */
export function hasProposals(data: DownloadFiles, by?: 'name' | 'shift'): boolean {
  return data.files.some((file) => OPEN_DECISIONS.includes(file.decision) && proposalOf(file) !== null && (by === undefined || proposalOf(file)?.by === by))
}

/**
 * Wie die Wahl einer Datei von dem abweicht, was die Datei sagt: `name`, wenn sie den Namen einer Folge traegt, die nicht
 * gewaehlt ist; sonst `number`, wenn die gelesenen Folgen andere sind als die gewaehlten. null ohne Wahl oder ohne Abweichung.
 */
export function deviation(file: DownloadVideo, choice: Choice | undefined): 'name' | 'number' | null {
  const chosen = (choice?.episodes ?? []).filter((id): id is number => id !== null)
  if (chosen.length === 0) return null
  const named = (file.named ?? []).map((episode) => episode.id)
  if (named.length > 0) return named.every((id) => chosen.includes(id)) ? null : 'name'
  const read = file.episodes.map((episode) => episode.id)
  if (read.length === 0) return null
  return read.length === chosen.length && read.every((id) => chosen.includes(id)) ? null : 'number'
}

/** Die Haelfte, die nexcrate aus einer Datei gelesen hat. */
export function readPart(file: DownloadVideo): 1 | 2 | null {
  const part = file.reading?.part
  return part === 1 || part === 2 ? part : null
}

/** Was nexcrate aus einer Datei gelesen hat, als Folgen-Code; null ohne Lesung. */
export function readingCode(file: DownloadVideo): string | null {
  if (file.episodes.length > 0) return file.episodes.map((episode) => episode.code).join(', ')
  const reading = file.reading
  if (!reading || reading.season === null || reading.numbers.length === 0) return null
  return reading.numbers.map((number) => `S${String(reading.season).padStart(2, '0')}E${String(number).padStart(2, '0')}`).join(', ')
}

/**
 * Ob der Dialog mit dem Gelesenen vorbelegt: nicht beim Verdacht auf eine andere Serie und nicht bei einem Paket, das anders
 * zaehlt als TMDB. Dort waere ein Klick zu schnell eine falsche Folge; der Knopf "Gelesene Nummern uebernehmen" hilft.
 */
export function preselects(problemCode: string | null | undefined, unknown: string): boolean {
  return problemCode !== 'other_series_suspected' && unknown === ''
}

/**
 * Vorbelegung: das Gelesene, wenn es nur Folgen dieses Downloads sind; eine Datei, die eine andere Folge liest, bleibt leer.
 * Ohne `preselect` bleibt alles leer. Dateien ohne lesbaren Namen bleiben immer leer: ihre Reihenfolge sagt nichts sicher
 * ueber die Folge, Vorschlaege gibt es nur auf Knopfdruck (`withProposals`, Antwort des Besitzers vom 17.09.2026).
 *
 * Seit 09.10.2026 zuerst, auch ohne `preselect`: traegt eine offene Datei den Namen einer Folge, ist diese Folge
 * vorgewaehlt, markiert "nach Folgenname", auch wenn sie nicht zu diesem Download gehoert (The Office S04: die Datei
 * S04E14 ist TMDBs S04E10). Nicht, wenn diese Folge schon eine Datei dieses Downloads hat. Ein Vorschlag aus der
 * Verschiebung kommt nur per Knopf.
 */
export function initialChoices(data: DownloadFiles, preselect = true): Record<number, Choice> {
  const choices: Record<number, Choice> = {}
  const taken = new Set<number>()
  const inDownload = new Set(data.episodes.filter((episode) => episode.in_download).map((episode) => episode.id))
  const choosable = new Set(data.episodes.filter((episode) => episode.state !== 'filed').map((episode) => episode.id))
  const editable = data.files.filter((file) => OPEN_DECISIONS.includes(file.decision))
  for (const file of editable) {
    const proposal = file.decision === 'open' ? proposalOf(file) : null
    if (proposal?.by !== 'name' || !proposal.ids.every((id) => choosable.has(id) && !taken.has(id))) continue
    choices[file.key] = { episodes: proposal.ids, proposal: false, byName: true }
    proposal.ids.forEach((id) => taken.add(id))
  }
  for (const file of editable) {
    if (choices[file.key]) continue
    const read = preselect && file.decision === 'open' ? file.episodes.map((episode) => episode.id) : []
    const usable = read.length > 0 && read.every((id) => inDownload.has(id) && !taken.has(id))
    const part = usable ? readPart(file) : null
    choices[file.key] = part !== null ? { episodes: read, proposal: false, part } : { episodes: usable ? read : [null], proposal: false }
    // Zwei Haelften derselben Folge nehmen sie nicht einander weg.
    if (usable && readPart(file) === null) read.forEach((id) => taken.add(id))
  }
  return choices
}

/** Offene Dateien ohne lesbaren Namen und ohne Wahl: die, denen `withProposals` eine Folge eintragen wuerde. */
function unreadableWithout(data: DownloadFiles, choices: Record<number, Choice>) {
  return data.files.filter(
    (file) =>
      file.decision === 'open' &&
      file.reading === null &&
      file.episodes.length === 0 &&
      !(choices[file.key]?.episodes ?? []).some((id) => id !== null),
  )
}

/** Folgen dieses Downloads ohne Datei, die noch keine Datei gewaehlt hat, in der Reihenfolge der Serie. */
function freeEpisodes(data: DownloadFiles, choices: Record<number, Choice>) {
  const taken = new Set(
    Object.values(choices)
      .flatMap((choice) => choice.episodes)
      .filter((id): id is number => id !== null),
  )
  return data.episodes.filter((episode) => episode.in_download && episode.state !== 'filed' && !taken.has(episode.id))
}

/** Wie viele unlesbare Dateien der Knopf "Freie Folgen der Reihe nach eintragen" fuellen koennte; 0 blendet ihn aus. */
export function proposable(data: DownloadFiles, choices: Record<number, Choice>): number {
  return Math.min(unreadableWithout(data, choices).length, freeEpisodes(data, choices).length)
}

/** Auf Knopfdruck: jede unlesbare Datei ohne Wahl bekommt der Reihe nach eine freie Folge, deutlich als Vorschlag markiert. */
export function withProposals(data: DownloadFiles, choices: Record<number, Choice>): Record<number, Choice> {
  const next = { ...choices }
  const free = freeEpisodes(data, choices)
  for (const file of unreadableWithout(data, choices)) {
    const episode = free.shift()
    if (!episode) break
    next[file.key] = { episodes: [episode.id], proposal: true }
  }
  return next
}

/** Wie viele offene Dateien keinen lesbaren Namen haben, fuer den Hinweis im Dialog. */
export function unreadableCount(data: DownloadFiles): number {
  return data.files.filter((file) => OPEN_DECISIONS.includes(file.decision) && file.reading === null && file.episodes.length === 0).length
}

/**
 * "Gelesene Nummern uebernehmen": jede Datei ohne Wahl bekommt die Folgen, die sie gelesen hat, soweit sie frei sind. Seit
 * 09.10.2026 nimmt der Knopf den Vorschlag des Servers statt der rohen Nummern, wo es einen gibt: zaehlt das Paket anders,
 * waeren die rohen Nummern ab der Doppelfolge falsch.
 */
export function withReadings(data: DownloadFiles, choices: Record<number, Choice>): Record<number, Choice> {
  const next = { ...choices }
  const taken = new Set(
    Object.values(choices)
      .flatMap((choice) => choice.episodes)
      .filter((id): id is number => id !== null),
  )
  for (const file of data.files) {
    const proposal = proposalOf(file)
    if (!OPEN_DECISIONS.includes(file.decision) || (file.episodes.length === 0 && proposal === null)) continue
    if ((next[file.key]?.episodes ?? []).some((id) => id !== null)) continue
    const wanted = proposal !== null ? proposal.ids : file.episodes.map((episode) => episode.id)
    const read = wanted.filter((id) => !taken.has(id))
    if (read.length === 0) continue
    read.forEach((id) => taken.add(id))
    next[file.key] = { episodes: read, proposal: proposal?.by === 'shift', ...(proposal?.by === 'name' ? { byName: true } : {}) }
  }
  return next
}

/**
 * Folgen, die mehr als einer Datei gewaehlt sind. Zwei Dateien als Teil 1 und Teil 2 derselben Folge sind es nicht
 *; eine ganze Folge neben einem Teil, oder zweimal derselbe Teil, schon.
 */
export function twiceChosen(choices: Record<number, Choice>): Set<number> {
  const seen = new Map<number, Set<number>>()
  const twice = new Set<number>()
  for (const choice of Object.values(choices)) {
    const chosen = choice.episodes.filter((id): id is number => id !== null)
    const half = chosen.length === 1 && (choice.part === 1 || choice.part === 2) ? choice.part : 0
    for (const id of chosen) {
      const taken = seen.get(id) ?? new Set<number>()
      if (taken.size > 0 && (half === 0 || taken.has(0) || taken.has(half))) twice.add(id)
      taken.add(half)
      seen.set(id, taken)
    }
  }
  return twice
}

/** Die Folgen des Downloads, die weder eine Datei haben noch gewaehlt sind, als Codes. */
export function stillMissing(data: DownloadFiles, choices: Record<number, Choice>): string[] {
  const chosen = new Set(Object.values(choices).flatMap((choice) => choice.episodes))
  return data.episodes.filter((episode) => episode.in_download && episode.state !== 'filed' && !chosen.has(episode.id)).map((episode) => episode.code)
}

/** Die Folgen, die die vorhandene Datei einer Folge ausser den gewaehlten noch enthaelt: eine Doppelfolge geht nur ganz. */
export function alsoHeld(episode: DownloadEpisodeChoice, chosenCodes: Set<string>): string[] {
  return (episode.current_file?.episodes ?? []).filter((code) => code !== episode.code && !chosenCodes.has(code))
}
