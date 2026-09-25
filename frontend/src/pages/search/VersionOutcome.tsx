import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { ReasonCount, ReleaseResult, SearchVersion } from '../../api/types'
import { buttonClasses } from '../../components/buttonClasses'
import { Symbol } from '../../components/Symbol'
import { Badge, Button } from '../../components/ui'
import { formatAgeHours, formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { fitState, upgradeText } from '../checker/checkerText'
import { ForNowHint, MatchedRules } from '../checker/resultParts'
import { Detail, Tile, TileHeader } from '../settings/parts'
import { VERSIONS_TAB_PATH } from '../settings/tabs'
import { LoadButton } from './LoadButton'
import { bestFitting, type VersionRow } from './searchOrder'
import { peersText, reasonCountText } from './searchText'

/**
 * Das Ergebnis der Suche fuer eine Fassung in einer Karte: "würde nehmen" mit Release, Punkten und
 * Grund, "behält die vorhandene Datei" oder "nichts passt" mit den haeufigsten Gruenden. Liegt das
 * Release, das nexcrate nehmen wuerde, unter dem Ziel des Profils, heisst es "würde vorerst nehmen".
 * Eine Fassung ohne Profil sagt das und fuehrt dorthin, wo man eines einrichtet.
 */
export function VersionOutcome({ version, rows }: { version: SearchVersion; rows: readonly VersionRow[] }) {
  if (!version.has_profile) return <NoProfile label={version.label} />
  const taken = version.would_take !== null ? rows.find((row) => row.release.release_key === version.would_take) : undefined
  if (taken && taken.entry.result) return <WouldTake label={version.label} row={taken} result={taken.entry.result} />
  if (version.keeps_current) return <KeepsCurrent version={version} best={bestFitting(rows)} />
  return <NothingFits version={version} found={rows.length > 0} />
}

export function NoProfile({ label, path = VERSIONS_TAB_PATH }: { label: string; path?: string }) {
  const { t } = useTranslation()
  return (
    <Tile>
      <TileHeader title={label}>
        <Badge>
          <Symbol name="shield" className="h-3.5 w-3.5" />
          {t('search.outcome.noProfile')}
        </Badge>
      </TileHeader>
      <p className="text-sm text-mist-400">{t('search.outcome.noProfileText', { label })}</p>
      <div>
        <Link to={path} className={buttonClasses('primary', 'sm')} aria-label={t('search.outcome.setUpLabel', { label })}>
          {t('search.outcome.setUp')}
        </Link>
      </div>
    </Tile>
  )
}

function WouldTake({ label, row, result }: { label: string; row: VersionRow; result: ReleaseResult }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [rules, setRules] = useState(false)
  const hintId = useId()
  const { release, entry } = row
  const upgrade = result.upgrade
  const forNow = fitState(result) === 'forNow'

  const place = entry.rank === null || entry.rank <= 1 ? t('search.outcome.firstPlace') : t('search.outcome.place', { rank: formatNumber(entry.rank, language) })
  const file = upgrade === null ? t('search.outcome.noFile') : upgrade.better ? t('search.outcome.better') : null
  const facts = [
    { label: t('search.outcome.indexer'), value: release.indexer, mono: false },
    { label: t('search.outcome.quality'), value: release.parsed?.quality ?? null, mono: true },
    { label: t('search.outcome.size'), value: release.size_bytes !== null ? sizeText(t, release.size_bytes, language) : null, mono: false },
    { label: t('search.outcome.age'), value: release.age_hours !== null ? formatAgeHours(release.age_hours, language) : null, mono: false },
    { label: t('search.outcome.peers'), value: peersText(t, release, language), mono: false },
  ].filter((fact) => fact.value !== null && fact.value !== '')

  return (
    <Tile>
      <TileHeader title={label} sub={t('checker.score', { count: result.score, value: formatNumber(result.score, language) })}>
        {forNow ? (
          <Badge tone="accent" describedBy={hintId}>
            <Symbol name="clock" className="h-3.5 w-3.5" />
            {t('search.outcome.wouldTakeForNow')}
          </Badge>
        ) : (
          <Badge tone="ok">
            <Symbol name="check" className="h-3.5 w-3.5" />
            {t('search.outcome.wouldTake')}
          </Badge>
        )}
      </TileHeader>
      <p className="font-mono text-sm leading-5 wrap-anywhere text-mist-100">{release.title}</p>
      {facts.length > 0 && (
        <dl className="grid grid-cols-[repeat(2,minmax(0,1fr))] gap-3 sm:grid-cols-[repeat(3,minmax(0,1fr))] lg:grid-cols-[repeat(5,minmax(0,1fr))]">
          {facts.map((fact) => (
            <Detail key={fact.label} label={fact.label} mono={fact.mono}>
              {fact.value}
            </Detail>
          ))}
        </dl>
      )}
      <p className="text-sm text-mist-200">
        {place}
        {file && <> {file}</>}
      </p>
      {forNow && <ForNowHint id={hintId} />}
      {upgrade && <p className="text-xs text-mist-500">{upgradeText(t, upgrade, language).current}</p>}
      <LoadButton release={release} entry={entry} prominent />
      <div>
        <Button variant="ghost" size="sm" aria-expanded={rules} onClick={() => setRules((current) => !current)}>
          <Symbol name={rules ? 'chevronDown' : 'chevron'} className="h-3.5 w-3.5" />
          {rules ? t('search.outcome.rulesHide') : t('search.outcome.rulesShow')}
        </Button>
      </div>
      {rules && <MatchedRules matched={result.matched} />}
    </Tile>
  )
}

function KeepsCurrent({ version, best }: { version: SearchVersion; best: VersionRow | undefined }) {
  const { t, i18n } = useTranslation()
  const upgrade = best?.entry.result?.upgrade ?? null
  const text = upgrade ? upgradeText(t, upgrade, i18n.language) : null
  return (
    <Tile>
      <TileHeader title={version.label}>
        <Badge tone="info">
          <Symbol name="shield" className="h-3.5 w-3.5" />
          {t('search.outcome.keepsCurrent')}
        </Badge>
      </TileHeader>
      <p className="text-sm text-mist-200">{best ? t('search.outcome.keepsText') : t('search.outcome.keepsNoneFits')}</p>
      {text?.reason && <p className="text-sm text-mist-400">{t('search.outcome.keepsBest', { reason: text.reason })}</p>}
      {text && <p className="text-xs text-mist-500">{text.current}</p>}
      <ReasonList reasons={version.nothing_fits} />
    </Tile>
  )
}

function NothingFits({ version, found }: { version: SearchVersion; found: boolean }) {
  const { t } = useTranslation()
  return (
    <Tile>
      <TileHeader title={version.label}>
        <Badge tone="bad">
          <Symbol name="alert" className="h-3.5 w-3.5" />
          {t('search.outcome.nothingFits')}
        </Badge>
      </TileHeader>
      <p className="text-sm text-mist-200">{found ? t('search.outcome.noneFits') : t('search.outcome.noneFound')}</p>
      <ReasonList reasons={version.nothing_fits} />
    </Tile>
  )
}

/** Die haeufigsten Gruende in Worten, der haeufigste zuerst. */
export function ReasonList({ reasons }: { reasons: readonly ReasonCount[] }) {
  const { t, i18n } = useTranslation()
  if (reasons.length === 0) return null
  const sorted = [...reasons].sort((left, right) => right.count - left.count)
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-xs font-semibold text-mist-400">{t('search.outcome.reasonsTitle')}</h4>
      <ul className="flex flex-col gap-1">
        {sorted.map((reason) => (
          <li key={reason.code} className="flex items-start gap-2 text-sm text-mist-200">
            <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-bad-500" />
            <span className="min-w-0 wrap-anywhere">{reasonCountText(t, reason, i18n.language)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
