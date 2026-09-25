import { useTranslation } from 'react-i18next'

import type { SeasonPlanLine, SeasonResultVersion } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button } from '../../components/ui'
import { formatGb, formatNumber } from '../../lib/format'
import { whenText } from '../../lib/when'
import { loadCodeText, rejectionPhrase, seasonReasonText } from './automaticText'

const GIB = 1024 ** 3

type Props = {
  seasons: SeasonPlanLine[]
  /** Ist die Automatik an? Aus steht der Grund "aus" schon oben, er wiederholt sich nicht. */
  on: boolean
  /** S6: Haelt etwas eine Fassung zurueck, steht der Grund schon oben. "Keine Staffel will etwas" waere dann falsch. */
  held?: boolean
  /** Oeffnet die Suche einer Staffel mit Liste, fuer "Nur als Paket zu haben". */
  onSearchSeason?: (season: number) => void
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0
}

/**
 * Der Kasten "Automatische Suche" einer Serie, darin eine Zeile je Staffel, die etwas will (S5.9, Antwort des Besitzers):
 * was fehlt oder besser werden kann, die naechste Suche mit ihrem Grund, und was die letzte Suche oder RSS fuer die
 * Staffel tat. Laesst die Automatik ein Paket nach der Regel des Besitzers aus, fuehrt "Suchen" zur Suche der Staffel.
 */
export function SeasonPlanLines({ seasons, on, held = false, onSearchSeason }: Props) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  if (seasons.length === 0) return held ? null : <p className="text-sm text-mist-500">{t('title.automatic.series.nothing')}</p>

  return (
    <ul aria-label={t('title.automatic.series.seasonsLabel')} className="flex flex-col gap-3">
      {seasons.map((line) => {
        const name = line.season === 0 ? t('series.seasons.specials') : t('series.seasons.season', { number: line.season })
        const facts = [
          count(line.missing) > 0 ? t('title.automatic.series.missing', { count: line.missing, value: formatNumber(line.missing, language) }) : null,
          count(line.upgrades) > 0 ? t('title.automatic.series.upgrades', { count: line.upgrades, value: formatNumber(line.upgrades, language) }) : null,
          count(line.waiting) > 0 ? t('title.automatic.series.waiting', { count: line.waiting, value: formatNumber(line.waiting, language) }) : null,
          count(line.no_date) > 0 ? t('title.automatic.series.noDate', { count: line.no_date, value: formatNumber(line.no_date, language) }) : null,
        ].filter((part): part is string => part !== null)
        const nextAt = typeof line.next_at === 'string' && line.next_at !== '' ? line.next_at : null
        const reason = typeof line.reason === 'string' && line.reason.length > 0 && !(line.reason === 'off' && !on) ? seasonReasonText(t, line.reason) : null
        const versions = Array.isArray(line.result?.versions) ? line.result.versions : []
        return (
          <li key={line.season} className="flex min-w-0 flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 p-4">
            <div className="flex min-w-0 flex-col gap-0.5">
              <h3 className="text-base font-semibold wrap-anywhere text-mist-100">{[name, ...facts].join(' · ')}</h3>
              <p className="text-sm text-mist-200">
                {nextAt !== null ? t('title.automatic.series.next', { when: whenText(t, nextAt, language) }) : t('title.automatic.series.nextNone')}
              </p>
              {reason !== null && <p className="text-sm wrap-anywhere text-mist-400">{reason}</p>}
            </div>
            {line.result && versions.length > 0 && (
              <div className="flex flex-col gap-2 border-t border-ink-700 pt-2">
                {typeof line.result.at === 'string' && line.result.at !== '' && (
                  <p className="text-xs text-mist-500">{t('title.automatic.series.resultAt', { when: whenText(t, line.result.at, language) })}</p>
                )}
                <ul className="flex flex-col gap-2">
                  {versions.map((version) => (
                    <li key={version.version_id} className="min-w-0">
                      <VersionLine version={version} season={line.season} seasonName={name} onSearchSeason={onSearchSeason} />
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function VersionLine({
  version,
  season,
  seasonName,
  onSearchSeason,
}: {
  version: SeasonResultVersion
  season: number
  seasonName: string
  onSearchSeason?: (season: number) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const number = (value: number) => formatNumber(value, language)
  const loaded = count(version.loaded)
  const notFound = count(version.not_found)
  const noFit = count(version.no_fit)
  const codes = Array.isArray(version.codes) ? version.codes.filter((code) => typeof code === 'string' && code !== '').slice(0, 5) : []
  const pack = version.pack_only
  const loadCode = loaded === 0 && typeof version.load_code === 'string' && version.load_code !== '' ? version.load_code : null
  const lines: string[] = []
  if (loaded > 0) {
    lines.push(
      `${t('title.automatic.series.loaded', { count: loaded, value: number(loaded) })}: ${t('title.automatic.series.loadedEpisodes', {
        filled: number(count(version.filled)),
        replaced: number(count(version.replaced)),
      })}`,
    )
  }
  if (noFit > 0) lines.push(t('title.automatic.series.noFit', { count: noFit, value: number(noFit) }))
  if (notFound > 0) lines.push(t('title.automatic.series.notFound', { count: notFound, value: number(notFound) }))
  if (lines.length === 0 && pack === null) lines.push(t('title.automatic.series.nothingNew'))

  return (
    <div role="group" aria-label={version.label} className="flex min-w-0 flex-col gap-1 text-sm">
      <p className="font-semibold wrap-anywhere text-mist-100">{version.label}</p>
      {lines.map((text) => (
        <p key={text} className="wrap-anywhere text-mist-300">
          {text}
        </p>
      ))}
      {noFit > 0 && codes.length > 0 && <p className="wrap-anywhere text-mist-400">{codes.map((code) => rejectionPhrase(t, code)).join(' · ')}</p>}
      {pack !== null && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <p className="flex min-w-0 items-start gap-2 text-mist-200">
            <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
            <span className="min-w-0 wrap-anywhere">
              {t('title.automatic.series.packOnly', { size: formatGb(count(pack.size) / GIB, language) })}{' '}
              {t('title.automatic.series.packOnlyHint', { count: count(pack.episodes), value: number(count(pack.episodes)) })}
            </span>
          </p>
          {onSearchSeason && (
            <Button variant="ghost" size="sm" onClick={() => onSearchSeason(season)} aria-label={t('title.automatic.series.packSearchLabel', { season: seasonName })}>
              <Symbol name="search" />
              {t('title.automatic.series.packSearch')}
            </Button>
          )}
        </div>
      )}
      {loadCode !== null && <p className="wrap-anywhere text-mist-400">{t('title.automatic.summary.notLoaded', { reason: loadCodeText(t, loadCode, version.label) })}</p>}
    </div>
  )
}
