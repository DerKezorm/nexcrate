import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { automaticApi } from '../../api/automatic'
import type { AutomaticState } from '../../api/types'
import { FormMessage } from '../../components/ui'

/** Der Schalter einer Art, null solange er nicht gelesen ist oder nicht gelesen werden konnte. */
function useAutomatic(field: 'enabled' | 'series_enabled'): boolean | null {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  useEffect(() => {
    let current = true
    automaticApi.get().then(
      (state: AutomaticState) => {
        if (current) setEnabled(state[field] === true)
      },
      () => {
        // Ohne den Schalter steht kein Satz da; der Dialog selbst geht weiter.
      },
    )
    return () => {
      current = false
    }
  }, [field])
  return enabled
}

/**
 * Ein Satz beim Hinzufuegen einer Serie, beim Aendern der Regel und bei einer neuen Fassung (S5, Entscheidung 23): Ist
 * die Automatik fuer Serien an, sucht sie gelaufene ueberwachte Folgen gleich; ist sie aus, sucht man auf der Seite der
 * Serie. Einen Haken wie in Sonarr gibt es nicht: Wer alte Folgen nicht will, waehlt die Regel "Kuenftige".
 */
export function SeriesAutomaticNote() {
  const { t } = useTranslation()
  const enabled = useAutomatic('series_enabled')
  if (enabled === null) return null
  return <FormMessage tone="info">{enabled ? t('series.automatic.on') : t('series.automatic.off')}</FormMessage>
}

/**
 * Derselbe Satz beim Hinzufuegen eines Films, nach dem Schalter fuer Filme: Ist er an, sucht die Automatik die neue
 * Fassung ab dem Erscheinen des Films; ist er aus, sucht man auf der Seite des Films.
 */
export function MovieAutomaticNote() {
  const { t } = useTranslation()
  const enabled = useAutomatic('enabled')
  if (enabled === null) return null
  return <FormMessage tone="info">{enabled ? t('library.add.automaticOn') : t('library.add.automaticOff')}</FormMessage>
}
