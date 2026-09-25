import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { automaticApi } from '../../api/automatic'
import { FormMessage } from '../../components/ui'

/** Der Schalter fuer Serien, null solange er nicht gelesen ist oder nicht gelesen werden konnte. */
function useSeriesAutomatic(): boolean | null {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  useEffect(() => {
    let current = true
    automaticApi.get().then(
      (state) => {
        if (current) setEnabled(state.series_enabled === true)
      },
      () => {
        // Ohne den Schalter steht kein Satz da; der Dialog selbst geht weiter.
      },
    )
    return () => {
      current = false
    }
  }, [])
  return enabled
}

/**
 * Ein Satz beim Hinzufuegen einer Serie, beim Aendern der Regel und bei einer neuen Fassung (S5, Entscheidung 23): Ist
 * die Automatik fuer Serien an, sucht sie gelaufene ueberwachte Folgen gleich; ist sie aus, sucht man auf der Seite der
 * Serie. Einen Haken wie in Sonarr gibt es nicht: Wer alte Folgen nicht will, waehlt die Regel "Kuenftige".
 */
export function SeriesAutomaticNote() {
  const { t } = useTranslation()
  const enabled = useSeriesAutomatic()
  if (enabled === null) return null
  return <FormMessage tone="info">{enabled ? t('series.automatic.on') : t('series.automatic.off')}</FormMessage>
}
