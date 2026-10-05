import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { DISCOVER_LISTS, discoverApi } from '../../api/discover'
import type { DiscoverAlbum, DiscoverAlbums } from '../../api/types'
import { Segmented } from '../../components/Segmented'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import { AddAlbumDialog } from '../music/AddAlbumDialog'
import { DISCOVER_GRID, DiscoverCard } from './DiscoverCard'
import { useListLabels } from './labels'

/**
 * Alben in Entdecken, von ListenBrainz. Ohne den Schalter fragt nexcrate dort nichts; die Seite erklaert, was dann
 * hinausgeht, und schaltet ihn auf Wunsch ein (derselbe Schalter steht unter Einstellungen, Online-Dienste).
 */
export function AlbumLists({
  list,
  enabled,
  onList,
  onSwitch,
  onHidden,
}: {
  list: string
  enabled: boolean
  onList: (list: string) => void
  onSwitch: (enabled: boolean) => Promise<void>
  onHidden: () => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const labels = useListLabels()

  const [found, setFound] = useState<DiscoverAlbums | null>(null)
  const [loading, setLoading] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [opened, setOpened] = useState<DiscoverAlbum | null>(null)
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set())
  const [round, setRound] = useState(0)
  const [switching, setSwitching] = useState(false)
  const generation = useRef(0)

  useEffect(() => {
    if (!enabled) return
    const run = ++generation.current
    const abort = new AbortController()
    setLoading(true)
    setProblem(null)
    discoverApi.albums(list, abort.signal).then(
      (result) => {
        if (run !== generation.current) return
        setFound(result)
        setGone(new Set())
        setLoading(false)
      },
      (error: unknown) => {
        if (run !== generation.current || abort.signal.aborted) return
        setLoading(false)
        setProblem(error)
      },
    )
    return () => abort.abort()
  }, [list, enabled, round])

  async function switchOn() {
    setSwitching(true)
    try {
      await onSwitch(true)
    } catch (error) {
      notify(errorText(t, error))
    } finally {
      setSwitching(false)
    }
  }

  if (!enabled) {
    return (
      <div className="flex max-w-xl flex-col items-start gap-4 rounded-2xl border border-ink-700 bg-ink-900/60 p-5">
        <p className="font-semibold text-mist-100">{t('discover.music.offTitle')}</p>
        <p className="text-sm text-mist-400">{t('discover.music.offText')}</p>
        <Button onClick={() => void switchOn()} loading={switching}>
          {t('discover.music.switchOn')}
        </Button>
      </div>
    )
  }

  async function hide(item: DiscoverAlbum) {
    setGone((current) => new Set(current).add(item.mbid))
    try {
      await discoverApi.hide('album', item.mbid)
      onHidden()
      setRound((value) => value + 1)
    } catch (error) {
      setGone((current) => {
        const next = new Set(current)
        next.delete(item.mbid)
        return next
      })
      notify(errorText(t, error))
    }
  }

  const items = (found?.items ?? []).filter((item) => !gone.has(item.mbid))
  const listensOf = (item: DiscoverAlbum) => {
    if (item.listens === null) return null
    const value = formatNumber(item.listens, language)
    return list === 'classics' ? t('discover.music.listensEver', { count: item.listens, value }) : t('discover.music.listensWeek', { count: item.listens, value })
  }
  const metaOf = (item: DiscoverAlbum) =>
    [item.artist, item.primary_type === 'EP' ? 'EP' : null, item.first_release_date?.slice(0, 4) ?? null].filter(Boolean).join(' · ')

  return (
    <div className="flex flex-col gap-5">
      <div className="overflow-x-auto">
        <Segmented value={list} options={DISCOVER_LISTS.album} onChange={onList} label={(value) => labels.album[value] ?? value} ariaLabel={t('discover.listLabel')} />
      </div>
      <p className="-mt-2 text-sm text-mist-500">{labels.albumHint[list] ?? ''}</p>

      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {found === null ? (
        problem === null && (
          <p className="flex items-center justify-center gap-2 py-16 text-sm text-mist-500" role="status">
            <Spinner />
            {t('discover.loading')}
          </p>
        )
      ) : items.length === 0 && !loading ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-12 text-center text-sm text-mist-500" role="status">
          {t('discover.empty')}
        </p>
      ) : (
        <ul className={DISCOVER_GRID + ' transition-opacity ' + (loading ? 'opacity-60' : '')} aria-busy={loading}>
          {items.map((item) => (
            <DiscoverCard
              key={item.mbid}
              album
              title={item.title}
              meta={metaOf(item)}
              extra={listensOf(item)}
              posterUrl={item.cover_url}
              onOpen={() => setOpened(item)}
              onHide={() => void hide(item)}
            />
          ))}
        </ul>
      )}
      <p className="text-xs text-mist-600">{t('discover.music.source')}</p>

      {opened !== null && (
        <AddAlbumDialog
          initial={opened}
          onClose={() => setOpened(null)}
          onAdded={() => {
            notify(t('discover.added', { title: opened.title }))
            setGone((current) => new Set(current).add(opened.mbid))
            setRound((value) => value + 1)
          }}
        />
      )}
    </div>
  )
}
