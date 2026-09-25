import type { TFunction } from 'i18next'

import type { ReleaseHead, TargetReason } from '../../api/types'
import { countryText } from '../../lib/country'

/** Formate einer Ausgabe zusammengefasst, etwa "2×CD" oder "Digital". */
export function formatsText(formats: string[], mediaCount: number): string {
  if (formats.length === 0) return ''
  const first = formats[0]
  const same = formats.every((format) => format === first)
  if (same && mediaCount > 1) return `${mediaCount}×${first}`
  return formats.join('+')
}

/** Ausgabe mit Format, Land und Datum in Klammern, fuer die Fassungskarte und die Titelliste der Ausgaben. */
export function releaseLine(release: ReleaseHead, language: string): string {
  const formats = formatsText(release.formats, release.media_count)
  const rest = [formats, countryText(release.country, language), release.date].filter(Boolean).join(', ')
  return rest === '' ? release.name : `${release.name} (${rest})`
}

/** Findet die Ausgabe eines Grundes (etwa die ausgelassene Box von `media_brake`) in der Liste der Ausgaben. */
function releaseOf(releases: readonly ReleaseHead[], id: unknown): ReleaseHead | null {
  return releases.find((release) => release.id === id) ?? null
}

/**
 * Der Grund der Zielregel als ein Satz (Entscheidung 27, M1.5.40), etwa "Meiste Titel ohne Box; 6-CD-Ausgabe
 * ausgelassen." Baut aus den Kennungen in `target_reason.codes`; ein Code, den diese Oberflaeche nicht kennt, faellt
 * einfach weg, statt den Satz abzubrechen.
 */
export function targetReasonSentence(t: TFunction, reason: TargetReason | null, releases: readonly ReleaseHead[], language: string): string | null {
  if (reason === null || reason.codes.length === 0) return null
  const byCode = new Map(reason.codes.map((entry) => [String(entry.code), entry]))
  if (byCode.has('owner')) return t('title.album.target.reason.owner')
  // Aus der Quelle (Lidarrs ueberwachte Ausgabe, Entscheidung 48) oder weil die Dateien zu dieser Ausgabe gehoeren (29).
  if (byCode.has('source')) return t('title.album.target.reason.source')
  if (byCode.has('files')) return t('title.album.target.reason.files')

  const brake = byCode.get('media_brake')
  const most = byCode.get('most_tracks')
  const only = byCode.get('only_candidate')

  let base: string
  if (most) base = brake ? t('title.album.target.reason.mostTracksNoBox') : t('title.album.target.reason.mostTracks')
  else if (only) base = t('title.album.target.reason.onlyCandidate')
  else base = t('title.album.target.reason.rule')

  const parts = [base]
  if (brake) {
    const excluded = releaseOf(releases, brake.largest_id)
    const spec = excluded ? formatsText(excluded.formats, excluded.media_count) : t('title.album.target.reason.mediaCount', { count: Number(brake.largest_media) })
    parts.push(t('title.album.target.reason.mediaBrakeClause', { spec }))
  }
  const filler = byCode.get('filler_ignored')
  if (filler) parts.push(t('title.album.target.reason.fillerClause', { tracks: Number(filler.tracks), filler: Number(filler.filler) }))
  const format = byCode.get('format')
  if (format) parts.push(t('title.album.target.reason.formatClause'))
  const country = byCode.get('country')
  if (country) parts.push(t('title.album.target.reason.countryClause', { country: countryText(String(country.country ?? ''), language) ?? '' }))
  const earliest = byCode.get('earliest')
  if (earliest) parts.push(t('title.album.target.reason.earliestClause'))

  return parts.join('; ')
}
