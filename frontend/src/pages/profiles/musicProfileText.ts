import type { TFunction } from 'i18next'

import type { MusicProfileSummary, MusicStepRole } from '../../api/types'

export function musicRoleText(t: TFunction, role: MusicStepRole): string {
  switch (role) {
    case 'target':
      return t('music.profile.role.target')
    case 'for_now':
      return t('music.profile.role.for_now')
    case 'waits':
      return t('music.profile.role.waits')
    default:
      return t('music.profile.role.never')
  }
}

/** Das Profil in einem Absatz, aus den Antworten: Ziel, Vorerst, 24 Bit, Quelle, und was nie genommen wird. */
export function musicProfileSentence(t: TFunction, summary: MusicProfileSummary): string {
  const parts = [
    summary.quality === 'lossless' ? t('music.profile.sentence.lossless') : summary.quality === 'either' ? t('music.profile.sentence.either') : t('music.profile.sentence.lossy'),
  ]
  if (!summary.take_now) {
    // Bei "beides recht" gibt es nichts ausser dem Ziel, das genommen wuerde: der Satz waere leer.
    if (summary.quality !== 'either') parts.push(t('music.profile.sentence.waits'))
  } else if (summary.quality === 'lossless') parts.push(t('music.profile.sentence.takeNowLossless'))
  else if (summary.quality === 'lossy') parts.push(t('music.profile.sentence.takeNowLossy'))
  if (summary.hires === 'prefer') parts.push(t('music.profile.sentence.hiresPrefer'))
  if (summary.hires === 'avoid') parts.push(t('music.profile.sentence.hiresAvoid'))
  if (summary.source === 'avoid_vinyl') parts.push(t('music.profile.sentence.avoidVinyl'))
  if (summary.source === 'prefer_cd') parts.push(t('music.profile.sentence.preferCd'))
  parts.push(t('music.profile.sentence.never'))
  return parts.join(' ')
}
