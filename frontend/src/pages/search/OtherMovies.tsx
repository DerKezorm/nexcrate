import type { TFunction } from 'i18next'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { SearchRelease } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button } from '../../components/ui'
import { formatNumber } from '../../lib/format'

function parsedText(t: TFunction, release: SearchRelease, series: boolean): string {
  if (series) {
    const name = release.not_this_series?.parsed_title ?? release.parsed_series?.series_title ?? null
    return name ? t('search.series.other.parsed', { title: name }) : t('search.series.other.parsedNone')
  }
  const title = release.not_this_movie?.parsed_title ?? release.parsed?.title ?? null
  const year = release.not_this_movie?.parsed_year ?? release.parsed?.year ?? null
  if (!title) return t('search.other.parsedNone')
  return year ? t('search.other.parsed', { title, year }) : t('search.other.parsedNoYear', { title })
}

/**
 * Releases, die ein Indexer geliefert hat, die aber nach Titel oder Jahr zu einem anderen Film
 * gehoeren. Zugeklappt, damit man sieht, warum sie fehlen, ohne dass sie die Liste fuellen. Seit S3 mit `series`
 * dasselbe fuer Releases anderer Serien.
 */
export function OtherMovies({ releases, series = false }: { releases: readonly SearchRelease[]; series?: boolean }) {
  const { t, i18n } = useTranslation()
  const [open, setOpen] = useState(false)

  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button variant="ghost" size="sm" aria-expanded={open} onClick={() => setOpen((current) => !current)}>
          <Symbol name={open ? 'chevronDown' : 'chevron'} className="h-3.5 w-3.5" />
          {series
            ? open
              ? t('search.series.other.hide')
              : t('search.series.other.show', { count: releases.length, value: formatNumber(releases.length, i18n.language) })
            : open
              ? t('search.other.hide')
              : t('search.other.show', { count: releases.length, value: formatNumber(releases.length, i18n.language) })}
        </Button>
      </div>
      {open && (
        <>
          <p className="text-xs text-mist-500">{series ? t('search.series.other.intro') : t('search.other.intro')}</p>
          <ul aria-label={series ? t('search.series.other.listLabel') : t('search.other.listLabel')} className="flex flex-col divide-y divide-ink-700/60 rounded-xl border border-ink-700">
            {releases.map((release) => (
              <li key={release.release_key} className="flex min-w-0 flex-col gap-0.5 px-3 py-2">
                <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{release.title}</p>
                <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-mist-500">
                  <span className="wrap-anywhere">{parsedText(t, release, series)}</span>
                  <span className="wrap-anywhere">{release.indexer}</span>
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
