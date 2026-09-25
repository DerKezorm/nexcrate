import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { subtitlesApi } from '../../api/subtitles'
import { FormMessage, Section, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { useSwitchSetting } from '../settings/useSwitchSetting'
import { Loading } from './PatternFields'

/** Untertitel (Schritt 3c, C9): ob nexcrate die Untertitel-Dateien aus dem Download neben den Film legt. Ab Werk an, speichert sofort. */
export function SubtitlesSection() {
  const { t } = useTranslation()
  const notify = useNotice()
  const setting = useSwitchSetting(subtitlesApi)

  async function change(next: boolean) {
    const saved = await setting.change(next)
    if (saved !== null) notify(saved ? t('settings.files.subtitles.savedOn') : t('settings.files.subtitles.savedOff'))
  }

  return (
    <Section title={t('settings.files.subtitles.title')}>
      {setting.enabled === null ? (
        setting.loadError !== null ? (
          <FormMessage>{errorText(t, setting.loadError)}</FormMessage>
        ) : (
          <Loading />
        )
      ) : (
        <div className="flex flex-col gap-3">
          <Switch
            label={t('settings.files.subtitles.switch')}
            hint={t('settings.files.subtitles.hint')}
            checked={setting.enabled}
            onChange={(next) => void change(next)}
            disabled={setting.busy}
          />
          {setting.problem !== null && <FormMessage>{errorText(t, setting.problem)}</FormMessage>}
        </div>
      )}
    </Section>
  )
}
