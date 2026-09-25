import type { AlbumFiles, AlbumTrackChoice } from '../../api/types'

/** Die reinen Teile des Dialogs "Von Hand zuordnen" fuer Alben (M4.6). */

/** Was der Besitzer fuer eine Datei waehlt: einen Titel, "trotzdem ablegen" oder nichts. */
export type AlbumChoice = { kind: 'track'; trackId: number } | { kind: 'loose' } | { kind: 'none' }

export const LOOSE = 'loose'
/** Dateien, die der Besitzer noch entscheiden kann: nicht im Albumordner. */
export const EDITABLE = ['open', 'other_album', 'filed', 'not_filed', 'loose']

export function durationText(ms: number | null): string | null {
  if (ms === null || ms <= 0) return null
  const seconds = Math.round(ms / 1000)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

export function trackCode(track: Pick<AlbumTrackChoice, 'medium' | 'position'>, severalMedia: boolean): string {
  return severalMedia ? `${track.medium}-${String(track.position).padStart(2, '0')}` : String(track.position).padStart(2, '0')
}

/** Die Wahl, mit der der Dialog oeffnet: was nexcrate schon entschieden hatte, sonst nichts (Vorschlaege nur auf Knopfdruck). */
export function initialAlbumChoices(data: AlbumFiles): Record<number, AlbumChoice> {
  const tracks = new Set(data.tracks.map((track) => track.id))
  const choices: Record<number, AlbumChoice> = {}
  for (const file of data.files) {
    if (file.placed || !EDITABLE.includes(file.decision)) continue
    choices[file.key] = file.decision === 'filed' && file.track_id !== null && tracks.has(file.track_id) ? { kind: 'track', trackId: file.track_id } : { kind: 'none' }
  }
  return choices
}

/** Titel, die zwei Dateien bekommen sollen, oder die schon eine abgelegte Datei dieses Downloads haben. */
export function doubleTracks(data: AlbumFiles, choices: Record<number, AlbumChoice>): Set<number> {
  const seen = new Map<number, number>()
  for (const file of data.files) {
    if (file.placed && file.track_id !== null) seen.set(file.track_id, (seen.get(file.track_id) ?? 0) + 1)
  }
  for (const choice of Object.values(choices)) {
    if (choice.kind === 'track') seen.set(choice.trackId, (seen.get(choice.trackId) ?? 0) + 1)
  }
  return new Set([...seen.entries()].filter(([, count]) => count > 1).map(([id]) => id))
}
