import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { DEFAULT_DELAY, type DelayRule, type Version } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { DOWNLOADS_WAITING_PATH } from '../downloads/address'
import { DelayDialog } from './DelayDialog'
import { delayLineText } from './delayText'

/**
 * Die Verzoegerungsregel einer Fassung in einer Zeile: welches Protokoll gewinnt und wie lange die Automatik auf ein
 * besseres Release wartet. In Radarr, Sonarr und Lidarr heisst das Delay-Profil und haengt an Tags; hier bindet die
 * Fassung. Ein Server von davor schickt keine Regel; dann steht die ab Werk da.
 */
export function DelayRow({ version, onChanged }: { version: Version; onChanged: () => void }) {
  const { t, i18n } = useTranslation()
  const [open, setOpen] = useState(false)
  const rule: DelayRule = version.delay ?? DEFAULT_DELAY
  const waiting = version.waiting ?? 0

  return (
    <div className="flex flex-col gap-2 border-t border-ink-700 pt-3">
      <div className="flex flex-wrap items-start gap-2">
        <Symbol name="clock" className="mt-0.5 h-4 w-4 shrink-0 text-mist-500" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-mist-100">{t('settings.delay.rowTitle')}</p>
          <p className="min-w-0 text-sm wrap-anywhere text-mist-300">{delayLineText(t, rule, i18n.language)}</p>
          {(version.delay?.tagged?.length ?? 0) > 0 && (
            <p className="text-sm text-mist-400">{t('settings.delay.taggedCount', { count: version.delay?.tagged?.length ?? 0 })}</p>
          )}
          {waiting > 0 && (
            <Link to={DOWNLOADS_WAITING_PATH} className="text-sm text-accent-400 hover:underline">
              {t('settings.delay.waiting', { count: waiting, value: formatNumber(waiting, i18n.language) })}
            </Link>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="ghost" onClick={() => setOpen(true)} aria-label={t('settings.delay.changeLabel', { label: version.label })}>
          <Symbol name="settings" className="h-3.5 w-3.5" />
          {t('settings.delay.change')}
        </Button>
      </div>
      {open && (
        <DelayDialog
          version={version}
          onClose={() => setOpen(false)}
          onSaved={() => {
            setOpen(false)
            onChanged()
          }}
        />
      )}
    </div>
  )
}
