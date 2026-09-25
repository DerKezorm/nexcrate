import { useTranslation } from 'react-i18next'

import { Section } from '../../components/ui'
import { AcoustIdNotice } from './AcoustIdNotice'
import { MusicBrainzNotice } from './MusicBrainzNotice'
import { RatingsSettings } from './RatingsSettings'
import { TmdbSettings } from './TmdbSettings'
import { XemNotice } from './XemNotice'

/**
 * Reiter "Online-Dienste" (19.09.2026, Wunsch des Besitzers): alles, was nexcrate im Internet fragt, an einer Stelle,
 * je Dienst wofuer, was ohne ihn fehlt und was nach draussen geht. TMDB zuerst, weil ohne Token bei Filmen und Serien
 * nichts geht. Die Hinweise auf TMDB und die TRaSH Guides, die Lizenzen verlangen, bleiben unter "Ueber nexcrate".
 */
export function ServicesSettings() {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-6">
      <TmdbSettings />
      <RatingsSettings />
      <Section title={t('settings.services.title')} intro={t('settings.services.intro')}>
        <AcoustIdNotice />
        <MusicBrainzNotice />
        <XemNotice />
      </Section>
    </div>
  )
}
