import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { ReleaseHead } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { countryText } from '../../lib/country'
import { formatNumber } from '../../lib/format'
import { formatsText } from './albumText'

const GROUP_THRESHOLD = 10

/** Das erste Format einer Ausgabe, fuer die Gruppierung; ohne Angabe "Sonstige". */
function primaryFormat(t: (key: string) => string, formats: string[]): string {
  return formats[0]?.trim() || t('title.album.releases.otherFormat')
}

function ReleaseRow({ release, language }: { release: ReleaseHead; language: string }) {
  const { t } = useTranslation()
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-ink-700/50 px-4 py-2 text-sm last:border-b-0">
      <span className="min-w-0 flex-1 wrap-anywhere text-mist-200">
        {release.name}
        {release.gone && <span className="ml-2 text-xs text-mist-500">{t('title.album.releases.gone')}</span>}
      </span>
      <span className="shrink-0 text-xs text-mist-500 tabular-nums">
        {[formatsText(release.formats, release.media_count), countryText(release.country, language), release.date].filter(Boolean).join(' · ')}
      </span>
    </li>
  )
}

/**
 * Die Ausgaben zugeklappt (Entscheidung 40), ab zehn Ausgaben nach Format gruppiert (Entscheidung 41 nennt die
 * Tabelle im Dialog; diese Liste hier ist die knappe Uebersicht auf der Seite selbst).
 */
export function ReleaseList({ releases }: { releases: ReleaseHead[] }) {
  const { t, i18n } = useTranslation()
  const [open, setOpen] = useState(false)
  if (releases.length === 0) return null
  const grouped = releases.length >= GROUP_THRESHOLD
  const byFormat = new Map<string, ReleaseHead[]>()
  if (grouped) {
    for (const release of releases) {
      const key = primaryFormat(t, release.formats)
      const list = byFormat.get(key) ?? []
      list.push(release)
      byFormat.set(key, list)
    }
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-850/50">
      <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open} className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-ink-850">
        <h3 className="text-sm font-semibold text-mist-100">{t('title.album.releases.title')}</h3>
        <span className="flex items-center gap-2.5">
          <span className="text-sm text-mist-500 tabular-nums">{formatNumber(releases.length, i18n.language)}</span>
          <Symbol name="chevronDown" className={'h-4 w-4 text-mist-500 transition-transform ' + (open ? '' : '-rotate-90')} />
        </span>
      </button>
      {open &&
        (grouped ? (
          <div className="border-t border-ink-700">
            {[...byFormat.entries()].map(([format, items]) => (
              <div key={format}>
                <p className="px-4 pt-2 text-xs font-semibold tracking-wide text-mist-600 uppercase">{format}</p>
                <ul>
                  {items.map((release) => (
                    <ReleaseRow key={release.id} release={release} language={i18n.language} />
                  ))}
                </ul>
              </div>
            ))}
          </div>
        ) : (
          <ul className="border-t border-ink-700">
            {releases.map((release) => (
              <ReleaseRow key={release.id} release={release} language={i18n.language} />
            ))}
          </ul>
        ))}
    </div>
  )
}
