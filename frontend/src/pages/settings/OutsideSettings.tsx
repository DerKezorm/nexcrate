import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { systemSettingsApi, type SystemSettings, type UpdateInfo } from '../../api/outside'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, Spinner, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime } from '../../lib/format'

/**
 * Unter "Ueber nexcrate" (V4): die Adresse nach aussen, aus der andere Programme "In nexcrate
 * oeffnen" bauen, der Unterpfad, mit dem nexcrate gestartet ist, und ob es eine neuere Fassung gibt.
 */
export function OutsideSettings() {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<SystemSettings | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [hidden, setHidden] = useState(false)

  useEffect(() => {
    let current = true
    systemSettingsApi.read().then(
      // Nur eine Antwort in der erwarteten Form zaehlt; sonst bleibt der Teil weg, "Ueber nexcrate" steht trotzdem.
      (found) => current && (found && typeof found.update === 'object' ? setSettings(found) : setHidden(true)),
      (error: unknown) => current && setProblem(error),
    )
    return () => {
      current = false
    }
  }, [])

  if (hidden) return null
  if (settings === null) {
    return problem !== null ? (
      <FormMessage>{errorText(t, problem)}</FormMessage>
    ) : (
      <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
        <Spinner />
        {t('common.loading')}
      </p>
    )
  }
  return (
    <>
      <WebAddress settings={settings} onSaved={setSettings} />
      <UpdateCheck settings={settings} onChanged={setSettings} />
    </>
  )
}

function WebAddress({ settings, onSaved }: { settings: SystemSettings; onSaved: (next: SystemSettings) => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [value, setValue] = useState(settings.web_url ?? '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      const saved = await systemSettingsApi.change({ web_url: value })
      onSaved(saved)
      setValue(saved.web_url ?? '')
      notify(t('system.outside.saved'))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(event) => void save(event)} className="flex flex-col gap-3 border-t border-ink-700 pt-4">
      <h3 className="text-sm font-semibold text-mist-300">{t('system.outside.title')}</h3>
      <Field
        label={t('system.outside.address')}
        hint={t('system.outside.addressHint')}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="https://example.com/nexcrate"
        inputMode="url"
        autoComplete="off"
      />
      <p className="text-xs text-mist-500">
        {settings.url_base === '' ? t('system.outside.noBase') : t('system.outside.base', { base: settings.url_base })}
      </p>
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      <Button type="submit" loading={busy} className="self-start">
        {t('system.outside.save')}
      </Button>
    </form>
  )
}

function UpdateCheck({ settings, onChanged }: { settings: SystemSettings; onChanged: (next: SystemSettings) => void }) {
  const { t, i18n } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const update = settings.update

  async function toggle(enabled: boolean) {
    setProblem(null)
    try {
      onChanged(await systemSettingsApi.change({ update_check: enabled }))
    } catch (error) {
      setProblem(error)
    }
  }

  async function checkNow() {
    setBusy(true)
    setProblem(null)
    try {
      const found: UpdateInfo = await systemSettingsApi.checkNow()
      onChanged({ ...settings, update: found })
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
      <h3 className="text-sm font-semibold text-mist-300">{t('system.update.title')}</h3>
      <Switch label={t('system.update.switch')} hint={t('system.update.switchHint')} checked={update.enabled} onChange={(value) => void toggle(value)} />
      <p className="text-sm text-mist-400" role="status">
        {!update.enabled
          ? t('system.update.off')
          : update.available === true
            ? t('system.update.available', { latest: update.latest ?? '', current: update.current })
            : update.available === false
              ? t('system.update.current', { current: update.current })
              : update.problem === 'not_found'
                ? t('system.update.notFound')
                : t('system.update.unknown')}
        {update.checked_at !== null && update.enabled && (
          <span className="block text-xs text-mist-500">{t('system.update.checked', { at: formatDateTime(update.checked_at, i18n.language) })}</span>
        )}
      </p>
      {update.available === true && (
        <a href={update.url} target="_blank" rel="noopener noreferrer" className="inline-flex w-fit items-center gap-1 text-sm font-medium text-accent-400 hover:underline">
          {t('system.update.link')}
          <Symbol name="link" className="h-3.5 w-3.5" />
        </a>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {update.enabled && (
        <Button variant="ghost" size="sm" onClick={() => void checkNow()} loading={busy} className="self-start">
          {t('system.update.checkNow')}
        </Button>
      )}
    </div>
  )
}
