import { useTranslation } from 'react-i18next'

import { ProgressBar } from '../../components/ui'
import { sizeText } from '../../lib/size'
import { isLow, usedShare } from './folderText'

/** Wie voll das Dateisystem unter einem Ordner ist, als Balken und in Worten. Knapp wird rosa. */
export function SpaceLine({ path, freeBytes, totalBytes }: { path: string; freeBytes: number; totalBytes: number }) {
  const { t, i18n } = useTranslation()
  const low = isLow(freeBytes, totalBytes)
  return (
    <div className="flex flex-col gap-1.5">
      <ProgressBar value={usedShare(freeBytes, totalBytes)} tone={low ? 'bad' : 'info'} label={t('settings.files.folders.usage', { path })} />
      <p className={'text-xs ' + (low ? 'text-bad-500' : 'text-mist-500')}>
        {t('settings.files.folders.space', { free: sizeText(t, freeBytes, i18n.language), total: sizeText(t, totalBytes, i18n.language) })}
      </p>
    </div>
  )
}
