import { useTranslation } from 'react-i18next'

import { PageTitle } from '../components/ui'
import { AboutSettings } from './settings/AboutSettings'

/** "Ueber nexcrate" als eigene Seite, erreichbar aus der Fusszeile, wie in Nexview und nexbeat. */
export function AboutPage() {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-6">
      <PageTitle>{t('system.about.title')}</PageTitle>
      <AboutSettings />
    </div>
  )
}
