import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { calendarApi, type CalendarSettings } from '../../api/calendar'
import { errorText } from '../../api/client'
import { Button, Card, FormMessage, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { withBase } from '../../lib/base'

/**
 * Das Abo: eine Adresse, die eine Kalender-App ohne Anmeldung liest. Ein Weg nach draussen, also mit
 * Riegel: ab Werk aus, ein eigener Schluessel, der sonst nichts oeffnet, jederzeit austauschbar.
 */
export function Subscription({ settings, onChange }: { settings: CalendarSettings; onChange: (next: CalendarSettings) => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [copied, setCopied] = useState(false)

  const address =
    settings.feed_key === '' ? '' : `${window.location.origin}${withBase(settings.feed_path)}?key=${encodeURIComponent(settings.feed_key)}`

  async function run(work: () => Promise<CalendarSettings>, done: string) {
    setBusy(true)
    setError(null)
    try {
      onChange(await work())
      notify(done)
    } catch (problem: unknown) {
      setError(problem)
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(address)
      setCopied(true)
    } catch {
      // Ohne Zwischenablage bleibt die Adresse zum Markieren stehen.
      setCopied(false)
    }
  }

  return (
    <Card>
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold">{t('calendar.feed.title')}</h2>
          <p className="max-w-2xl text-sm text-mist-500">{t('calendar.feed.intro')}</p>
        </div>
        <Switch
          label={t('calendar.feed.switch')}
          hint={t('calendar.feed.hint')}
          checked={settings.feed_enabled}
          disabled={busy}
          onChange={(checked) =>
            run(
              () => calendarApi.saveSettings({ feed_enabled: checked }),
              checked ? t('calendar.feed.turnedOn') : t('calendar.feed.turnedOff'),
            )
          }
        />
        {settings.feed_enabled && address !== '' && (
          <div className="flex flex-col gap-2">
            <p className="break-all rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-xs text-mist-300">{address}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" size="sm" onClick={copy}>
                {t('calendar.feed.copy')}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                loading={busy}
                onClick={() => {
                  setCopied(false)
                  return run(() => calendarApi.newKey(), t('calendar.feed.newKeyDone'))
                }}
              >
                {t('calendar.feed.newKey')}
              </Button>
              {copied && <span className="text-xs text-ok-500">{t('calendar.feed.copied')}</span>}
            </div>
            <FormMessage tone="info">{t('calendar.feed.warning')}</FormMessage>
          </div>
        )}
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
      </div>
    </Card>
  )
}
