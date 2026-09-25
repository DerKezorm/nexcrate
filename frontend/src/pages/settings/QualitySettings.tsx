import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import type { MediaKind } from '../../api/types'
import { Segmented } from '../../components/Segmented'
import { FormatSection } from '../quality/FormatSection'
import { ProfileListSection } from '../quality/ProfileListSection'
import { QualitySizeSection } from '../quality/QualitySizeSection'
import { addressOfKind, KIND_PARAM, kindFromAddress, qualityTopicFromAddress } from './tabs'
import { useKindLabel, type KindSection } from './useKindLabel'

/** Die beiden Arten, die Custom Formats und Radarrs Qualitaeten kennen. Musik hat eigene Regeln (M2). */
const KINDS: readonly KindSection[] = ['movie', 'series']

/**
 * Reiter "Qualitaet": die Groessen je Qualitaet und die Custom Formats, beide je Medienart. Beides gilt fuer
 * alle Profile einer Art, so wie in Radarr, wo es unter Einstellungen und nicht im Profil steht. Was ein Profil
 * daraus macht, steht bei der Fassung unter "Von Hand".
 */
export function QualitySettings() {
  const { t } = useTranslation()
  const kindLabel = useKindLabel()
  const [params, setParams] = useSearchParams()
  const topic = qualityTopicFromAddress(params.get('unter'))
  const chosen = kindFromAddress(params.get(KIND_PARAM))
  const kind: MediaKind = chosen === 'series' ? 'series' : 'movie'

  function changeKind(next: KindSection) {
    const nextParams = new URLSearchParams(params)
    if (next === 'movie') nextParams.delete(KIND_PARAM)
    else nextParams.set(KIND_PARAM, addressOfKind(next))
    setParams(nextParams, { replace: true })
  }

  return (
    <div className="flex flex-col gap-4">
      <Segmented value={kind} options={KINDS} onChange={changeKind} label={kindLabel} ariaLabel={t('quality.kindLabel')} />
      {topic === 'profiles' && <ProfileListSection key={kind} kind={kind} />}
      {topic === 'sizes' && <QualitySizeSection key={kind} kind={kind} />}
      {topic === 'formats' && <FormatSection key={kind} kind={kind} />}
    </div>
  )
}
