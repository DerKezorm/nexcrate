/**
 * Die Saetze zur Suche nach einer Serie (S3): der Umfang im Kopf, Folgennummern kurz gefasst und die Zeilen der
 * Zusammenstellung "würde nehmen". Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import type { ParsedSeriesRelease, Search, SeriesTake } from '../../api/types'
import { formatList, formatNumber } from '../../lib/format'

/** Eine Suche nach einer Serie. Ein Server von vor S3 schickt `kind` nicht und sucht nur Filme. */
export function isSeriesSearch(search: Search): boolean {
  return search.kind === 'series'
}

/** Der Kopf einer Suche nach einer Serie: ganze Serie, eine Staffel oder eine Folge. null ohne Umfang. */
export function scopeText(t: TFunction, search: Search): string | null {
  const scope = search.scope
  if (!scope) return null
  if (scope.kind === 'episode') return t('search.series.scope.episode', { code: scope.code ?? '' })
  if (scope.kind === 'season') {
    if (scope.season === 0) return t('search.series.scope.specials')
    return t('search.series.scope.season', { season: scope.season ?? '' })
  }
  return t('search.series.scope.series')
}

type Code = { season: number; episode: number }

function parseCode(code: string): Code | null {
  const match = /^S(\d+)E(\d+)$/.exec(code)
  return match ? { season: Number(match[1]), episode: Number(match[2]) } : null
}

/**
 * Folgennummern kurz: Folgen am Stueck werden zu "S02E01 bis S02E04", eine Staffel ohne Folge (`S02`) zu "Staffel 2".
 * Was sich nicht lesen laesst, steht da, wie es kommt. Die Reihenfolge bleibt die des Servers.
 */
export function episodeCodesText(t: TFunction, codes: readonly string[], language: string): string {
  const parts: string[] = []
  let run: { first: string; last: string; at: Code } | null = null
  const close = () => {
    if (run === null) return
    parts.push(run.first === run.last ? run.first : t('search.series.codes.range', { first: run.first, last: run.last }))
    run = null
  }
  for (const code of codes) {
    const parsed = parseCode(code)
    if (parsed === null) {
      close()
      const season = /^S(\d+)$/.exec(code)
      parts.push(season ? t('search.series.codes.season', { season: Number(season[1]) }) : code)
      continue
    }
    if (run !== null && run.at.season === parsed.season && run.at.episode + 1 === parsed.episode) {
      run = { first: run.first, last: code, at: parsed }
      continue
    }
    close()
    run = { first: code, last: code, at: parsed }
  }
  close()
  return formatList(parts, language)
}

/** Die Folgen eines Releases in der Liste: ein Staffelpaket als "Staffel 2", sonst die Nummern kurz. */
export function matchedEpisodesText(t: TFunction, parsed: ParsedSeriesRelease | null | undefined, codes: readonly string[], language: string): string | null {
  if (parsed && parsed.release_type === 'season_pack' && parsed.season !== null) return t('search.series.codes.season', { season: parsed.season })
  if (codes.length === 0) return null
  return episodeCodesText(t, codes, language)
}

/**
 * Eine Zeile der Zusammenstellung: "Staffelpaket, füllt 9 Folgen und ersetzt 2 Folgen", "S02E05 einzeln" oder die
 * Nummern mit dem, was sie tun.
 */
export function takeText(t: TFunction, take: SeriesTake, parsed: ParsedSeriesRelease | null | undefined, language: string): string {
  const all = [...take.fills, ...take.replaces]
  const fills = take.fills.length > 0 ? t('search.series.outcome.fills', { count: take.fills.length, value: formatNumber(take.fills.length, language) }) : null
  const replaces =
    take.replaces.length > 0 ? t('search.series.outcome.replaces', { count: take.replaces.length, value: formatNumber(take.replaces.length, language) }) : null
  const parts = fills !== null && replaces !== null ? t('search.series.outcome.both', { fills, replaces }) : (fills ?? replaces ?? '')
  if (parsed && parsed.release_type === 'season_pack') return t('search.series.outcome.line', { head: t('search.series.outcome.pack'), parts })
  if (all.length === 1 && take.replaces.length === 0) return t('search.series.outcome.single', { code: all[0] })
  const sorted = [...all].sort()
  return t('search.series.outcome.line', { head: t('search.series.outcome.several', { codes: episodeCodesText(t, sorted, language) }), parts })
}
