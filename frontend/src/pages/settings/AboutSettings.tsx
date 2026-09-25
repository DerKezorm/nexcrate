import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { systemApi } from '../../api/system'
import type { Health } from '../../api/types'
import { Logo } from '../../components/Logo'
import { Symbol } from '../../components/Symbol'
import { Button, Section, Spinner } from '../../components/ui'
import { WhatsNewDialog } from '../../components/WhatsNewDialog'
import { withBase } from '../../lib/base'
import { entryFor, latestVersion } from '../../lib/whatsnew'
import { OutsideSettings } from './OutsideSettings'
import { TrashNotice } from './TrashNotice'

/**
 * Das offizielle Logo von TMDB, unveraendert aus https://www.themoviedb.org/about/logos-attribution
 * ("Alt short, blue"). Der Dateiname dort traegt die Pruefsumme des Inhalts; die Kopie in
 * `public/` hat dieselbe.
 */
export const TMDB_LOGO_SRC = '/tmdb-logo.svg'

export const TMDB_URL = 'https://www.themoviedb.org'
export const WEBSITE_URL = 'https://nexcrate.nexapps.dev'
export const REPO_URL = 'https://github.com/DerKezorm/nexcrate'
export const XEM_URL = 'https://thexem.info'
export const MUSICBRAINZ_URL = 'https://musicbrainz.org'
export const ACOUSTID_URL = 'https://acoustid.org'

function Outside({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex w-fit items-center gap-1 text-sm font-medium text-accent-400 hover:underline">
      {children}
      <Symbol name="link" className="h-3.5 w-3.5" />
    </a>
  )
}

/**
 * "Ueber nexcrate": Version, Quellcode und Lizenz, der Hinweis auf TMDB, den die Nutzungsbedingungen verlangen,
 * TheXEM fuer die Szene-Nummern, MusicBrainz und AcoustID fuer Musik, und die TRaSH Guides (MIT) samt ihrem Stand.
 * Das TMDB-Logo steht kleiner und ruhiger da als das von nexcrate, der Hinweis in TMDBs
 * eigenem Wortlaut.
 */
export function AboutSettings() {
  const { t, i18n } = useTranslation()
  const [health, setHealth] = useState<Health | null>(null)
  const [failed, setFailed] = useState(false)
  const [showNews, setShowNews] = useState(false)
  // Das Fenster "Was ist neu" noch einmal oeffnen, fuer die laufende Version oder die letzte davor mit einem Text.
  const newsVersion = health ? latestVersion(health.version) : null
  const newsEntry = newsVersion ? entryFor(newsVersion, i18n.language) : null

  useEffect(() => {
    let current = true
    systemApi.health().then(
      (result) => current && setHealth(result),
      () => current && setFailed(true),
    )
    return () => {
      current = false
    }
  }, [])

  return (
    <Section title="nexcrate">
      <div className="flex items-center gap-4">
        <Logo className="h-14 w-14 shrink-0" />
        <div className="min-w-0">
          <p className="text-xl font-bold tracking-tight">
            NEX<span className="text-accent-500">CRATE</span>
          </p>
          <p className="text-sm text-mist-500" role="status">
            {health ? (
              t('system.about.version', { version: health.version })
            ) : failed ? (
              t('system.about.versionUnknown')
            ) : (
              <span className="inline-flex items-center gap-2">
                <Spinner className="h-3.5 w-3.5" />
                {t('common.loading')}
              </span>
            )}
          </p>
        </div>
      </div>
      <p className="text-sm text-mist-400">{t('system.about.intro')}</p>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
        <dt className="text-mist-500">{t('system.about.website')}</dt>
        <dd className="min-w-0">
          <Outside href={WEBSITE_URL}>{WEBSITE_URL.replace('https://', '')}</Outside>
        </dd>
        <dt className="text-mist-500">{t('system.about.source')}</dt>
        <dd className="min-w-0">
          <Outside href={REPO_URL}>{REPO_URL.replace('https://', '')}</Outside>
        </dd>
        <dt className="text-mist-500">{t('system.about.license')}</dt>
        <dd className="text-mist-200">GNU AGPL v3.0</dd>
      </dl>
      {newsVersion && newsEntry && (
        <Button variant="ghost" size="sm" className="self-start" onClick={() => setShowNews(true)}>
          <Symbol name="sparkle" />
          {t('whatsnew.showAgain', { version: newsVersion })}
        </Button>
      )}
      {showNews && newsVersion && newsEntry && <WhatsNewDialog version={newsVersion} entry={newsEntry} onClose={() => setShowNews(false)} />}

      <OutsideSettings />

      <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
        <h3 className="text-sm font-semibold text-mist-300">{t('system.about.tmdbTitle')}</h3>
        <img src={withBase(TMDB_LOGO_SRC)} alt={t('system.about.tmdbLogo')} width={108} height={14} className="h-3.5 w-auto self-start" />
        <p className="text-sm text-mist-400">{t('system.about.tmdbText')}</p>
        <p lang="en" className="text-sm text-mist-300">
          {t('system.about.tmdbNotice')}
        </p>
        <Outside href={TMDB_URL}>{t('system.about.tmdbLink')}</Outside>
      </div>

      <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
        <h3 className="text-sm font-semibold text-mist-300">{t('system.about.xemTitle')}</h3>
        <p className="text-sm text-mist-400">{t('system.about.xemText')}</p>
        <Outside href={XEM_URL}>thexem.info</Outside>
      </div>

      <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
        <h3 className="text-sm font-semibold text-mist-300">{t('system.about.musicTitle')}</h3>
        <p className="text-sm text-mist-400">{t('system.about.musicText')}</p>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <Outside href={MUSICBRAINZ_URL}>musicbrainz.org</Outside>
          <Outside href={ACOUSTID_URL}>acoustid.org</Outside>
        </div>
      </div>

      <TrashNotice />
    </Section>
  )
}
