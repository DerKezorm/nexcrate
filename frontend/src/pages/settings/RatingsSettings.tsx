import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { ratingsApi, type RatingsSettings as Settings } from '../../api/outside'
import { Button, Field, FormMessage, Section, Spinner, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime, formatNumber } from '../../lib/format'

/**
 * Online-Dienste, "Wertungen" (V4): IMDbs taegliche Datei (ab Werk an, Antwort des Besitzers
 * vom 21.09.2026), und mit einem eigenen OMDb-Schluessel Rotten Tomatoes und Metacritic. Die Namensnennung, die IMDb
 * verlangt, steht hier im Wortlaut.
 */
export function RatingsSettings() {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [key, setKey] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let current = true
    ratingsApi.settings().then(
      (found) => current && found && typeof found.imdb === 'object' && setSettings(found),
      (error: unknown) => current && setProblem(error),
    )
    return () => {
      current = false
    }
  }, [])

  async function run(action: () => Promise<Settings>, done?: string) {
    setProblem(null)
    try {
      setSettings(await action())
      if (done) notify(done)
    } catch (error) {
      setProblem(error)
    }
  }

  async function refresh() {
    setLoading(true)
    await run(() => ratingsApi.refresh())
    setLoading(false)
  }

  async function storeKey(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    await run(() => ratingsApi.storeKey(key.trim()), t('settings.ratings.omdbStored'))
    setKey('')
    setSaving(false)
  }

  return (
    <Section title={t('settings.ratings.title')} intro={t('settings.ratings.intro')}>
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {settings === null ? (
        problem === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )
      ) : (
        <>
          <Switch
            label={t('settings.ratings.imdbSwitch')}
            hint={t('settings.ratings.imdbHint')}
            checked={settings.imdb_enabled}
            onChange={(value) => void run(() => ratingsApi.switchImdb(value))}
          />
          <p className="text-sm text-mist-400" role="status">
            {settings.imdb.loaded_at !== null && settings.imdb.rows !== null
              ? t('settings.ratings.loaded', {
                  count: settings.imdb.rows,
                  value: formatNumber(settings.imdb.rows, i18n.language),
                  at: formatDateTime(settings.imdb.loaded_at, i18n.language),
                })
              : t('settings.ratings.notLoaded')}
            {settings.imdb.problem !== null && <span className="block text-bad-500">{t('settings.ratings.problem')}</span>}
          </p>
          {settings.imdb_enabled && (
            <Button variant="ghost" size="sm" onClick={() => void refresh()} loading={loading} className="self-start">
              {t('settings.ratings.refresh')}
            </Button>
          )}
          <p lang="en" className="text-xs text-mist-500">
            {settings.attribution}
          </p>

          <form onSubmit={(event) => void storeKey(event)} className="flex flex-col gap-3 border-t border-ink-700 pt-4">
            <h3 className="text-sm font-semibold text-mist-300">{t('settings.ratings.omdbTitle')}</h3>
            <p className="text-sm text-mist-400">{t('settings.ratings.omdbIntro')}</p>
            {settings.omdb.has_key ? (
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-sm text-mist-300">{t('settings.ratings.omdbHasKey', { today: settings.omdb.today, limit: settings.omdb.limit })}</p>
                <Button variant="ghost" size="sm" onClick={() => void run(() => ratingsApi.removeKey(), t('settings.ratings.omdbRemoved'))}>
                  {t('settings.ratings.omdbRemove')}
                </Button>
              </div>
            ) : (
              <>
                <Field label={t('settings.ratings.omdbKey')} value={key} onChange={(event) => setKey(event.target.value)} autoComplete="off" spellCheck={false} />
                <Button type="submit" loading={saving} disabled={key.trim() === ''} className="self-start">
                  {t('settings.ratings.omdbSave')}
                </Button>
              </>
            )}
            <p lang="en" className="text-xs text-mist-500">
              {settings.omdb_credit}
            </p>
          </form>
        </>
      )}
    </Section>
  )
}
