import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { errorText } from '../../api/client'
import { bazarrApi, type BazarrPart, type BazarrSettings as BazarrState } from '../../api/outside'
import { Badge, FormMessage, Section, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime } from '../../lib/format'
import { Loading } from '../files/PatternFields'
import { useSwitchSetting } from './useSwitchSetting'

/** Der Reiter mit den API-Schluesseln: Bazarr bekommt einen eigenen, nur mit Lesen. */
const KEYS_TAB_PATH = '/einstellungen?reiter=schluessel'
/** So oft fragt die Seite, ob Bazarr verbunden ist, solange sie offen ist. */
const STATUS_MS = 10_000

type PartName = 'radarr' | 'sonarr'

/**
 * System, "Bazarr" (Issue #8, Beta): nexcrate antwortet Bazarr wie Radarr und Sonarr, unter eigenen Basis-URLs je Teil.
 * Die Seite sagt, was man in Bazarr eintraegt, aus der Adresse, unter der sie selbst gerade laeuft, und ob Bazarr schon
 * fragt oder live verbunden ist.
 */
export function BazarrSettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const setting = useSwitchSetting(bazarrApi)
  const [state, setState] = useState<BazarrState | null>(null)

  useEffect(() => {
    if (setting.enabled !== true) return
    let current = true
    const load = () =>
      bazarrApi.get().then(
        (found) => {
          if (current) setState(found)
        },
        () => undefined,
      )
    void load()
    const timer = window.setInterval(() => void load(), STATUS_MS)
    return () => {
      current = false
      window.clearInterval(timer)
    }
  }, [setting.enabled])

  async function change(next: boolean) {
    const saved = await setting.change(next)
    if (saved !== null) notify(saved ? t('system.bazarr.savedOn') : t('system.bazarr.savedOff'))
  }

  return (
    <div className="flex flex-col gap-4">
      <Section title={t('system.bazarr.title')} badge={<Badge tone="info">{t('common.beta')}</Badge>} intro={t('system.bazarr.intro')}>
        {setting.enabled === null ? (
          setting.loadError !== null ? (
            <FormMessage>{errorText(t, setting.loadError)}</FormMessage>
          ) : (
            <Loading />
          )
        ) : (
          <div className="flex flex-col gap-3">
            <Switch
              label={t('system.bazarr.switch')}
              hint={t('system.bazarr.hint')}
              checked={setting.enabled}
              onChange={(next) => void change(next)}
              disabled={setting.busy}
            />
            {setting.problem !== null && <FormMessage>{errorText(t, setting.problem)}</FormMessage>}
            <p className="text-xs text-mist-500">{t('system.bazarr.beta')}</p>
          </div>
        )}
      </Section>
      {setting.enabled === true && (
        <Section title={t('system.bazarr.inBazarr')} intro={t('system.bazarr.inBazarrIntro')}>
          <div className="grid gap-3 lg:grid-cols-2">
            <PartCard part="radarr" urlBase={state?.url_base ?? null} status={state?.radarr ?? null} />
            <PartCard part="sonarr" urlBase={state?.url_base ?? null} status={state?.sonarr ?? null} />
          </div>
          <p className="text-sm text-mist-400">
            {t('system.bazarr.key')}{' '}
            <Link to={KEYS_TAB_PATH} className="font-medium text-accent-400 hover:underline">
              {t('system.bazarr.keyLink')}
            </Link>
          </p>
          <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm text-mist-400">
            <li>{t('system.bazarr.noteVersions')}</li>
            <li>{t('system.bazarr.noteSubtitles')}</li>
            <li>{t('system.bazarr.notePaths')}</li>
          </ul>
        </Section>
      )}
    </div>
  )
}

function PartCard({ part, urlBase, status }: { part: PartName; urlBase: string | null; status: BazarrPart | null }) {
  const { t, i18n } = useTranslation()
  const https = window.location.protocol === 'https:'
  const port = window.location.port || (https ? '443' : '80')
  const rows: [string, string][] = [
    [t('system.bazarr.address'), window.location.hostname],
    [t('system.bazarr.port'), port],
    [t('system.bazarr.baseUrl'), urlBase === null ? '…' : `${urlBase}/bazarr/${part}`],
    [t('system.bazarr.ssl'), https ? t('system.bazarr.yes') : t('system.bazarr.no')],
  ]
  const state =
    status === null
      ? null
      : status.live && status.since
        ? { tone: 'ok' as const, text: t('system.bazarr.live', { at: formatDateTime(status.since, i18n.language) }) }
        : status.last_request
          ? { tone: 'info' as const, text: t('system.bazarr.asked', { at: formatDateTime(status.last_request, i18n.language) }) }
          : { tone: 'neutral' as const, text: t('system.bazarr.notYet') }
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-mist-100">{t(part === 'radarr' ? 'system.bazarr.asRadarr' : 'system.bazarr.asSonarr')}</h3>
        {state && <Badge tone={state.tone}>{state.text}</Badge>}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-mist-500">{label}</dt>
            <dd className="min-w-0 break-all font-mono text-mist-200">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
