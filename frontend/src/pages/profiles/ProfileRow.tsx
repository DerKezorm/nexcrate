import { useTranslation } from 'react-i18next'

import type { Version } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button } from '../../components/ui'
import { profileLineText } from './profileText'

export type ProfileActions = {
  /** Das Profil selbst oeffnen: Name, Assistent und Von Hand stehen dort (der eine Ort, 20.09.2026). */
  onOpen: (version: Version) => void
  /** Ein anderes Profil fuer diese Fassung waehlen. */
  onChoose: (version: Version) => void
}

/**
 * Das Profil einer Fassung in einer Zeile: wie es heisst, was es tut, und zwei Wege — oeffnen oder ein anderes
 * waehlen. Alles andere (Fragen, Handarbeit, Export, Import) steckt im Profil-Fenster, damit es einen Ort gibt.
 */
export function ProfileRow({ version, actions }: { version: Version; actions: ProfileActions }) {
  const { t, i18n } = useTranslation()
  const line = version.profile_line ?? null
  const hasProfile = version.has_profile === true || version.profile != null

  return (
    <div className="mt-auto flex flex-col gap-2 border-t border-ink-700 pt-3">
      <div className="flex flex-wrap items-start gap-2">
        <Symbol name="shield" className={'mt-0.5 h-4 w-4 shrink-0 ' + (hasProfile ? 'text-accent-400' : 'text-mist-600')} />
        <div className="min-w-0 flex-1">
          {version.profile ? (
            <p className="text-sm font-medium wrap-anywhere text-mist-100">{version.profile}</p>
          ) : (
            <p className="text-sm text-mist-500">{t('profiles.line.none')}</p>
          )}
          {line && <p className="min-w-0 text-sm wrap-anywhere text-mist-300">{profileLineText(t, line, i18n.language)}</p>}
        </div>
        {line?.outdated && (
          <Badge tone="bad">
            <Symbol name="refresh" className="h-3.5 w-3.5" />
            {t('profiles.line.outdated')}
          </Badge>
        )}
      </div>
      {line?.outdated && <p className="text-xs text-mist-500">{t('profiles.line.outdatedHint')}</p>}
      <div className="flex flex-wrap gap-1.5">
        {hasProfile && (
          <Button size="sm" variant="ghost" onClick={() => actions.onOpen(version)} aria-label={t('profiles.tile.openLabel', { label: version.label })}>
            <Symbol name="settings" className="h-3.5 w-3.5" />
            {t('profiles.tile.open')}
          </Button>
        )}
        <Button
          size="sm"
          variant={hasProfile ? 'ghost' : 'primary'}
          onClick={() => actions.onChoose(version)}
          aria-label={t('profiles.tile.chooseLabel', { label: version.label })}
        >
          <Symbol name="swap" className="h-3.5 w-3.5" />
          {hasProfile ? t('profiles.tile.choose') : t('profiles.tile.chooseFirst')}
        </Button>
      </div>
    </div>
  )
}
