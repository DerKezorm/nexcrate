/**
 * Die Saetze zu den unklaren Dateien einer eigenen Serienfassung (S6, Ue3, Entscheidungen 15 bis 17): was der Name oder
 * Sonarr gesagt hat, warum die Datei keine Folge bekam, und wie der Vorschlag lautet. Der Server schickt Codes und
 * Nummern, nie Saetze. Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import type { NearbyEpisode, UnassignedFile } from '../../api/types'
import { formatCalendarDate } from '../../lib/format'
import { countingName } from '../checker/seriesCheckerText'
import { episodeCode, sourceCode } from './seriesText'

/** Warum eine Datei beim Einlesen keine Folge bekam. Ein unbekannter Code bleibt lesbar. */
export function unclearReasonText(t: TFunction, reason: string | null | undefined): string | null {
  switch (reason) {
    case 'no_numbers':
      return t('series.unassigned.reason.no_numbers')
    case 'unknown_numbers':
      return t('series.unassigned.reason.unknown_numbers')
    case 'ambiguous':
      return t('series.unassigned.reason.ambiguous')
    case 'counted_otherwise':
      return t('series.unassigned.reason.counted_otherwise')
    case 'episode_has_file':
      return t('series.unassigned.reason.episode_has_file')
    case 'duplicate':
      return t('series.unassigned.reason.duplicate')
    default:
      return typeof reason === 'string' && reason !== '' ? t('series.unassigned.reason.unknown', { code: reason }) : null
  }
}

/** Eine Folge als "S03E01 „Das Ende“", ohne Namen nur als Nummer. */
export function episodeText(t: TFunction, episode: NearbyEpisode): string {
  const code = episodeCode(episode.season, episode.episode)
  const name = typeof episode.name === 'string' && episode.name.trim() !== '' ? episode.name.trim() : null
  return name !== null ? t('series.unassigned.episodeNamed', { code, name }) : code
}

/** Eine Folge in der Auswahlliste: Nummer, Name und Sendedatum, soweit es sie gibt. */
export function nearbyOptionText(t: TFunction, episode: NearbyEpisode, language: string): string {
  const text = episodeText(t, episode)
  const date = typeof episode.air_date === 'string' && episode.air_date !== '' ? formatCalendarDate(episode.air_date, language) : null
  return date !== null ? t('series.unassigned.episodeDated', { episode: text, date }) : text
}

/**
 * Was der Name oder Sonarr zu dieser Datei gesagt hat, etwa „S02E13 „Das Ende“ (Sonarr, 04.12.2019)“ oder
 * „S01E99 (aus dem Namen)“. null, wenn weder Sonarr noch der Name etwas hergaben.
 */
export function evidenceText(t: TFunction, file: UnassignedFile, sourceName: string | null, language: string): string | null {
  const source = file.source_episode ?? null
  if (source !== null) {
    const episode = episodeText(t, { id: 0, season: source.season, episode: source.episode, name: source.name, air_date: source.air_date })
    const name = typeof sourceName === 'string' && sourceName !== '' ? sourceName : t('series.unassigned.someSource')
    const date = typeof source.air_date === 'string' && source.air_date !== '' ? formatCalendarDate(source.air_date, language) : null
    return date !== null ? t('series.unassigned.fromSource', { episode, name, date }) : t('series.unassigned.fromSourceNoDate', { episode, name })
  }
  const read = file.read_as ?? null
  const code = read !== null ? sourceCode(read) : null
  if (code === null) return null
  const scheme = typeof read?.numbering === 'string' && read.numbering !== '' ? countingName(t, read.numbering) : null
  return scheme !== null ? t('series.unassigned.fromNameScheme', { code, scheme }) : t('series.unassigned.fromName', { code })
}

/**
 * Der Vorschlag als Satz: „S02E13 „Das Ende“ (Sonarr, 04.12.2019) ist wahrscheinlich S03E01 „Das Ende“.“ Die Folgen
 * stehen in `nearby`; fehlt dort eine, sagt der Satz nur, dass es einen Vorschlag gibt.
 */
export function proposalText(t: TFunction, file: UnassignedFile, sourceName: string | null, language: string): string {
  const ids = file.proposal?.episode_ids ?? []
  const nearby = Array.isArray(file.nearby) ? file.nearby : []
  const found = ids.map((id) => nearby.find((episode) => episode.id === id)).filter((episode): episode is NearbyEpisode => episode !== undefined)
  if (found.length !== ids.length || found.length === 0) return t('series.unassigned.proposalUnknown')
  const to = found.map((episode) => episodeText(t, episode)).join(' · ')
  const from = evidenceText(t, file, sourceName, language)
  return from !== null ? t('series.unassigned.proposal', { from, to }) : t('series.unassigned.proposalPlain', { to })
}

/** Die Stufen, die "Alle Vorschlaege uebernehmen" nimmt: gleicher Titel, mit oder ohne gleiches Sendedatum. */
export const SAFE_PROPOSAL_STEPS = [1, 2]

function takeable(file: UnassignedFile): boolean {
  return file.left_out !== true && typeof file.proposal?.step === 'number' && SAFE_PROPOSAL_STEPS.includes(file.proposal.step)
}

/**
 * Wie viele Vorschlaege der Stufen 1 oder 2 auf dem Folgentitel einer Quelle ruhen (`safe`). Nur die nimmt der Server
 * ungefragt. ⚠️ Am 18.09.2026 fehlte `safe` in der Antwort, die Seite zaehlte deshalb alle 57 Vorschlaege aus
 * Dateinamen mit und meldete "57 Dateien sind zugeordnet", waehrend der Server keinen nahm.
 */
export function safeProposals(files: readonly UnassignedFile[]): number {
  return files.filter((file) => takeable(file) && file.proposal?.safe === true).length
}

/** Vorschlaege der Stufen 1 oder 2, deren Titel nexcrate im Dateinamen fand. Die nimmt der Knopf erst nach Rueckfrage. */
export function nameProposals(files: readonly UnassignedFile[]): number {
  return files.filter((file) => takeable(file) && file.proposal?.safe !== true).length
}

/** Die offenen Dateien einer Fassung: weder zugeordnet noch ausgelassen. */
export function openFiles(files: readonly UnassignedFile[], versionId: number): number {
  return files.filter((file) => file.version_id === versionId && file.left_out !== true).length
}
