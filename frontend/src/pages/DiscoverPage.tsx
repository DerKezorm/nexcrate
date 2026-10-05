import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { errorText } from '../api/client'
import { DISCOVER_LISTS, discoverApi, NO_FILTERS, type DiscoverFilters, type DiscoverKind } from '../api/discover'
import type { DiscoverState } from '../api/types'
import { TabRow } from '../components/TabRow'
import { Button, FormMessage, PageLoading, PageTitle } from '../components/ui'
import { useNotice } from '../components/useNotice'
import { formatNumber } from '../lib/format'
import { AlbumLists } from './discover/AlbumLists'
import { TitleLists } from './discover/TitleLists'

/** Die Art in der Adresse, deutsch wie die anderen Adressen. */
const KIND_PARAM: Record<DiscoverKind, string> = { movie: 'filme', series: 'serien', album: 'musik' }

function kindFrom(value: string | null): DiscoverKind {
  const found = (Object.entries(KIND_PARAM) as [DiscoverKind, string][]).find(([, param]) => param === value)
  return found ? found[0] : 'movie'
}

function listFrom(kind: DiscoverKind, value: string | null): string {
  const lists = DISCOVER_LISTS[kind] as readonly string[]
  return value !== null && lists.includes(value) ? value : lists[0]
}

/**
 * Entdecken (05.10.2026): fuer alle ohne nexview davor. Je Art ein paar Listen mit hoechstens 20 Titeln, die noch
 * nicht in der Bibliothek stehen, Filme nur, wenn es sie digital oder auf Disc schon gibt. Ein Klick fuegt hinzu,
 * "Nicht interessiert" blendet fuer immer aus.
 *
 * ⚠️ Was die Seite zeigt, steht im Zustand und von dort in der Adresse, nicht umgekehrt (wie die Bibliothek).
 */
export function DiscoverPage() {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [params, setParams] = useSearchParams()
  const [kind, setKind] = useState<DiscoverKind>(() => kindFrom(params.get('art')))
  const [lists, setLists] = useState<Record<DiscoverKind, string>>(() => ({
    movie: listFrom('movie', kindFrom(params.get('art')) === 'movie' ? params.get('liste') : null),
    series: listFrom('series', kindFrom(params.get('art')) === 'series' ? params.get('liste') : null),
    album: listFrom('album', kindFrom(params.get('art')) === 'album' ? params.get('liste') : null),
  }))
  const [filters, setFilters] = useState<Record<'movie' | 'series', DiscoverFilters>>({ movie: NO_FILTERS, series: NO_FILTERS })
  const [state, setState] = useState<DiscoverState | null>(null)
  const [stateError, setStateError] = useState<unknown>(null)
  // Nach "Alle wieder zeigen" bauen sich die Listen neu auf.
  const [shown, setShown] = useState(0)

  useEffect(() => {
    let current = true
    discoverApi.state().then(
      (result) => current && setState(result),
      (error: unknown) => current && setStateError(error),
    )
    return () => {
      current = false
    }
  }, [])

  const list = lists[kind]
  useEffect(() => {
    const next: Record<string, string> = {}
    if (kind !== 'movie') next.art = KIND_PARAM[kind]
    if (list !== DISCOVER_LISTS[kind][0]) next.liste = list
    setParams(next, { replace: true })
  }, [kind, list, setParams])

  function refreshState() {
    discoverApi.state().then(setState, () => undefined)
  }

  async function switchListenBrainz(enabled: boolean) {
    setState(await discoverApi.switchListenBrainz(enabled))
  }

  async function showAll() {
    try {
      const result = await discoverApi.resetHidden()
      notify(t('discover.hidden.reset', { count: result.removed, value: formatNumber(result.removed, i18n.language) }))
      setShown((value) => value + 1)
      refreshState()
    } catch (error) {
      notify(errorText(t, error))
    }
  }

  const tabs = [
    { value: 'movie' as const, label: t('discover.kinds.movie'), symbol: 'film' as const },
    { value: 'series' as const, label: t('discover.kinds.series'), symbol: 'tv' as const },
    { value: 'album' as const, label: t('discover.kinds.album'), symbol: 'note' as const },
  ]
  const hiddenCount = state ? state.hidden.movie + state.hidden.series + state.hidden.album : 0

  return (
    <div className="flex flex-col gap-6">
      <PageTitle sub={t('discover.sub')}>{t('discover.title')}</PageTitle>
      <TabRow tabs={tabs} active={kind} onChange={setKind} label={t('discover.kindLabel')} />

      {state === null ? (
        stateError !== null ? <FormMessage>{errorText(t, stateError)}</FormMessage> : <PageLoading />
      ) : kind === 'album' ? (
        <AlbumLists
          key={`album-${shown}`}
          list={list}
          enabled={state.listenbrainz_enabled}
          onList={(next) => setLists((current) => ({ ...current, album: next }))}
          onSwitch={switchListenBrainz}
          onHidden={refreshState}
        />
      ) : (
        <TitleLists
          key={`${kind}-${shown}`}
          kind={kind}
          list={list}
          filters={filters[kind]}
          tmdbConfigured={state.tmdb_configured}
          preferredRegion={state.region}
          onList={(next) => setLists((current) => ({ ...current, [kind]: next }))}
          onFilters={(next) => setFilters((current) => ({ ...current, [kind]: next }))}
          onTmdbSaved={() => refreshState()}
          onHidden={refreshState}
        />
      )}

      {hiddenCount > 0 && (
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-ink-700/60 pt-4 text-sm text-mist-500">
          {t('discover.hidden.count', { count: hiddenCount, value: formatNumber(hiddenCount, i18n.language) })}
          <Button variant="ghost" size="sm" onClick={() => void showAll()}>
            {t('discover.hidden.showAll')}
          </Button>
        </p>
      )}
    </div>
  )
}
