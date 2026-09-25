import { useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { errorText } from '../../api/client'
import { ALBUM_NAMES_MAX, RELEASE_NAME_MAX, releasesApi } from '../../api/releases'
import type { AlbumCheck, AlbumCheckRelease, ParsedAlbum } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section } from '../../components/ui'
import { formatList, formatNumber } from '../../lib/format'
import { musicStepText } from '../../lib/musicSteps'
import { Detail } from '../settings/parts'
import type { FitState } from './checkerText'
import { albumNoteText, albumRejectionText } from './musicCheckerText'
import { FitBadge, RejectionList } from './resultParts'
import { namesOf } from './seriesCheckerText'

/** Ausgabe und Art sind Woerter der Szene ("deluxe", "ep"): sie stehen da, wie der Name sie schreibt, nur gross. */
function sceneWord(value: string): string {
  return value.length <= 2 ? value.toUpperCase() : value.charAt(0).toUpperCase() + value.slice(1)
}

/**
 * Der Release-Pruefer fuer Musik im Reiter Fassungen (Musik M2): bis zu 50 Namen. Je Name steht da, was nexcrate
 * liest, welche Stufe das ist und was das eine Musik-Profil dazu sagt. Ohne Profil nur, was gelesen wurde. Gesucht
 * und geladen wird nichts.
 */
export function MusicChecker({ profileVersion = 0, onSetUpProfile }: { profileVersion?: number; onSetUpProfile: () => void }) {
  const { t, i18n } = useTranslation()
  const namesId = useId()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [shown, setShown] = useState<{ check: AlbumCheck; profileVersion: number } | null>(null)
  // Nur die neueste Pruefung zaehlt. Eine langsame alte ueberschreibt keine neuere.
  const generation = useRef(0)
  const names = namesOf(text)

  async function run() {
    if (names.length === 0) return setProblem(t('checker.music.missingNames'))
    if (names.length > ALBUM_NAMES_MAX) return setProblem(t('checker.music.tooMany'))
    if (names.some((name) => name.length > RELEASE_NAME_MAX)) return setProblem(t('checker.music.tooLong'))
    const current = ++generation.current
    setBusy(true)
    setProblem(null)
    try {
      const check = await releasesApi.checkAlbum(names)
      if (current !== generation.current) return
      setShown({ check, profileVersion })
    } catch (error) {
      if (current !== generation.current) return
      setShown(null)
      setProblem(errorText(t, error))
    } finally {
      if (current === generation.current) setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void run()
  }

  // Ein Ergebnis von vor einer Aenderung des Profils sagt nichts mehr: weg damit, statt Altes zu zeigen.
  const check = shown !== null && shown.profileVersion === profileVersion ? shown.check : null

  return (
    <Section title={t('checker.music.title')} intro={t('checker.music.intro')}>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={namesId} className="text-sm font-medium text-mist-300">
            {t('checker.music.names')}
          </label>
          <textarea
            id={namesId}
            value={text}
            rows={5}
            spellCheck={false}
            aria-describedby={`${namesId}-hint`}
            onChange={(event) => setText(event.target.value)}
            className="w-full min-w-0 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 font-mono text-xs text-mist-100 focus:border-accent-500 focus:outline-none"
          />
          <p id={`${namesId}-hint`} className="text-xs text-mist-500">
            {t('checker.music.namesHint')}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" loading={busy}>
            {!busy && <Symbol name="search" />}
            {t('checker.music.submit')}
          </Button>
          {names.length > 0 && (
            <span className={'text-xs tabular-nums ' + (names.length > ALBUM_NAMES_MAX ? 'text-bad-500' : 'text-mist-500')}>
              {t('checker.music.count', { value: formatNumber(names.length, i18n.language) })}
            </span>
          )}
        </div>
      </form>
      {problem && <FormMessage>{problem}</FormMessage>}
      {check && (
        <div className="flex flex-col gap-4 border-t border-ink-700 pt-4">
          {!check.has_profile && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3">
              <p className="min-w-0 text-sm text-mist-400">{t('checker.music.noProfile')}</p>
              <Button size="sm" onClick={onSetUpProfile}>
                {t('checker.setUp')}
              </Button>
            </div>
          )}
          <ul className="flex flex-col gap-3">
            {check.releases.map((release, index) => (
              <li key={`${release.name}-${index}`} className="min-w-0">
                <ReleaseRow release={release} open={index === 0} ranked={check.releases.length > 1} language={i18n.language} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  )
}

function fitOf(release: AlbumCheckRelease): FitState | null {
  if (release.verdict === null) return null
  return !release.verdict.accepted ? 'fitsNot' : release.verdict.for_now ? 'forNow' : 'fits'
}

function ReleaseRow({ release, open, ranked, language }: { release: AlbumCheckRelease; open: boolean; ranked: boolean; language: string }) {
  const { t } = useTranslation()
  const verdict = release.verdict
  const fit = fitOf(release)
  return (
    <details open={open} className="min-w-0 rounded-xl border border-ink-700 bg-ink-900/40">
      <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 px-4 py-3">
        <span className="min-w-0 font-mono text-xs wrap-anywhere text-mist-200">{release.name}</span>
        <span className="flex shrink-0 flex-wrap items-center gap-1.5">
          <Badge tone={release.step === 'unknown' ? 'bad' : 'info'}>{musicStepText(t, release.step)}</Badge>
          {ranked && verdict !== null && verdict.place !== null && <Badge>{t('checker.music.place', { place: formatNumber(verdict.place, language) })}</Badge>}
          {fit !== null && <FitBadge state={fit} />}
        </span>
      </summary>
      <div className="flex flex-col gap-4 border-t border-ink-700 px-4 py-4">
        <ReadFacts parsed={release.parsed} language={language} />
        {verdict !== null && <RejectionList rejections={verdict.rejections} toText={(rejection) => albumRejectionText(t, rejection)} />}
        {verdict !== null && verdict.notes.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <h4 className="text-xs font-semibold text-mist-400">{t('checker.music.notesTitle')}</h4>
            {verdict.notes.map((note, index) => (
              <p key={`${note.code}-${index}`} className="flex items-start gap-2 text-sm text-mist-300">
                <Symbol name={note.code === 'below_target' || note.code === 'above_target' ? 'clock' : 'info'} className={'mt-0.5 h-4 w-4 shrink-0 ' + (note.code === 'below_target' || note.code === 'above_target' ? 'text-accent-400' : 'text-mist-500')} />
                <span className="min-w-0">{albumNoteText(t, note)}</span>
              </p>
            ))}
          </div>
        )}
      </div>
    </details>
  )
}

function qualityLine(t: TFunction, parsed: ParsedAlbum, language: string): string | null {
  const parts: string[] = []
  if (parsed.format !== null) parts.push(parsed.bitrate !== null ? `${parsed.format} ${parsed.bitrate}` : parsed.format)
  if (parsed.bit_depth !== null) parts.push(t('checker.music.read.bits', { bits: formatNumber(parsed.bit_depth, language) }))
  if (parsed.sample_rate_khz !== null) parts.push(t('checker.music.read.khz', { khz: formatNumber(parsed.sample_rate_khz, language) }))
  return parts.length > 0 ? parts.join(', ') : null
}

function ReadFacts({ parsed, language }: { parsed: ParsedAlbum; language: string }) {
  const { t } = useTranslation()
  const unknown = t('checker.parsed.unknown')
  const quality = qualityLine(t, parsed, language)
  const media = parsed.media_count !== null ? formatNumber(parsed.media_count, language) : null
  const part = parsed.disc_part !== null ? t('checker.music.read.discPart', { part: formatNumber(parsed.disc_part, language) }) : null
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-mist-300">{t('checker.music.readTitle')}</h3>
      <dl className="grid grid-cols-[minmax(0,1fr)] gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3 sm:grid-cols-[repeat(2,minmax(0,1fr))] xl:grid-cols-[repeat(4,minmax(0,1fr))]">
        <Detail label={t('checker.music.read.artist')}>
          {parsed.various_artists ? t('checker.music.read.variousArtists') : parsed.artist || unknown}
          {parsed.unsure && (
            <span className="block text-xs text-accent-400" title={t('checker.music.read.unsureHint')}>
              {t('checker.music.read.unsure')}
            </span>
          )}
        </Detail>
        <Detail label={t('checker.music.read.album')}>{parsed.album || unknown}</Detail>
        <Detail label={t('checker.music.read.year')}>
          {parsed.year ?? unknown}
          {parsed.edition_year !== null && <span className="block text-xs text-mist-500">{t('checker.music.read.editionYear', { year: parsed.edition_year })}</span>}
        </Detail>
        <Detail label={t('checker.music.read.format')} mono>
          {quality ?? unknown}
        </Detail>
        {parsed.source !== null && (
          <Detail label={t('checker.music.read.source')} mono>
            {parsed.source}
          </Detail>
        )}
        {(media !== null || part !== null) && <Detail label={t('checker.music.read.media')}>{[media, part].filter(Boolean).join(', ')}</Detail>}
        {parsed.editions.length > 0 && <Detail label={t('checker.music.read.edition')}>{formatList(parsed.editions.map(sceneWord), language)}</Detail>}
        {parsed.kinds.length > 0 && <Detail label={t('checker.music.read.kind')}>{formatList(parsed.kinds.map(sceneWord), language)}</Detail>}
        {parsed.country !== null && (
          <Detail label={t('checker.music.read.country')} mono>
            {parsed.country}
          </Detail>
        )}
        {parsed.catalogue_number !== null && (
          <Detail label={t('checker.music.read.catalogue')} mono>
            {parsed.catalogue_number}
          </Detail>
        )}
        {parsed.revision.version > 1 && <Detail label={t('checker.music.read.repeat')}>{t('checker.music.read.repack')}</Detail>}
        <Detail label={t('checker.music.read.group')} mono>
          {parsed.group ?? unknown}
        </Detail>
        {parsed.suffix !== null && (
          <Detail label={t('checker.music.read.suffix')} mono>
            {parsed.suffix}
          </Detail>
        )}
      </dl>
      {parsed.unsure && <p className="text-xs text-mist-500">{t('checker.music.read.unsureHint')}</p>}
    </div>
  )
}
