import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import { musicApi } from '../../api/music'
import type { MediaKind } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import type { StateFilter } from './address'
import type { Selection } from './selection'
import { RemoveManyDialog } from './RemoveManyDialog'
import { SeriesTypeDialog } from './SeriesTypeDialog'
import { TagsDialog } from './TagsDialog'

/** Die Regeln, die eine Serienfassung kennt; dieselben wie im Dialog einer einzelnen Serie. */
const RULES = ['all', 'future', 'missing', 'none'] as const

/**
 * Die Leiste ueber der Liste, solange der Auswahlmodus an ist (Rueckmeldung 20.09.2026): wie viele markiert sind,
 * zwei Abkuerzungen zum Markieren und die Aktionen. Was die Aktionen sind, haengt an der Art: Filme und Alben
 * beobachten oder in Ruhe lassen, Serien bekommen eine Regel, Kuenstler entscheiden ueber neue Alben.
 */
export function SelectionBar({
  kind,
  artists = false,
  selection,
  state,
  q,
  tag = '',
  shown,
  total,
  onDone,
}: {
  kind: MediaKind
  /** Die Ansicht Kuenstler der Musik: dort geht es um neue Alben, nicht um Fassungen. */
  artists?: boolean
  selection: Selection
  state: StateFilter
  q: string
  /** Seit T1: der Tag-Filter der Ansicht, fuer "Alle im Filter". */
  tag?: string
  /** Die Nummern, die gerade geladen sind, fuer "Alle auf dieser Seite". */
  shown: number[]
  total: number
  onDone: () => void
}) {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [busy, setBusy] = useState(false)
  const [tagging, setTagging] = useState(false)
  const [typing, setTyping] = useState(false)
  const [removing, setRemoving] = useState(false)
  const count = selection.count(total)
  const nothing = count === 0

  async function run(what: () => Promise<string>) {
    if (busy || nothing) return
    setBusy(true)
    try {
      notify(await what())
      onDone()
      selection.stop()
    } catch (problem) {
      notify(errorText(t, problem))
    } finally {
      setBusy(false)
    }
  }

  const number = (value: number) => formatNumber(value, i18n.language)
  const ids = () => selection.idsForServer()

  async function watch(monitored: boolean) {
    if (artists) {
      const result = await musicApi.setMonitorNew({ monitor_new: monitored ? 'all' : 'none', artist_ids: ids(), q: q || null, ...(tag ? { tag } : {}) })
      return t(monitored ? 'library.select.artistsWatched' : 'library.select.artistsLeft', { count: result.changed, value: number(result.changed) })
    }
    const result = await libraryApi.setMonitoredAll({ monitored, kind, state: state === 'all' ? null : state, q: q || null, ...(tag ? { tag } : {}), title_ids: ids() })
    return t(monitored ? 'library.watchAll.watched' : 'library.watchAll.leftAlone', { count: result.changed, value: number(result.changed) })
  }

  async function rule(chosen: (typeof RULES)[number]) {
    const result = await libraryApi.setWatchRule({ rule: chosen, state: state === 'all' ? null : state, q: q || null, ...(tag ? { tag } : {}), title_ids: ids() })
    return t('library.select.ruleDone', { count: result.versions, value: number(result.versions) })
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-accent-500/40 bg-accent-500/10 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-sm font-semibold text-accent-400">{t('library.select.count', { count, value: number(count) })}</span>
        <Button variant="ghost" size="sm" onClick={() => selection.page(shown)} disabled={busy}>
          {t('library.select.page', { count: shown.length, value: number(shown.length) })}
        </Button>
        <Button variant="ghost" size="sm" onClick={selection.all} disabled={busy}>
          {t('library.select.whole', { count: total, value: number(total) })}
        </Button>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {kind === 'series' && !artists ? (
            <>
              {RULES.map((chosen) => (
                <Button key={chosen} variant="ghost" size="sm" disabled={busy || nothing} onClick={() => void run(() => rule(chosen))}>
                  {t(`library.select.rule.${chosen}` as 'library.select.rule.all')}
                </Button>
              ))}
              {/* B1: die Art der Serie, wie Sonarrs Massenbearbeitung. */}
              <Button variant="ghost" size="sm" disabled={busy || nothing} onClick={() => setTyping(true)}>
                <Symbol name="swap" />
                {t('library.select.seriesType.open')}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" disabled={busy || nothing} onClick={() => void run(() => watch(false))}>
                <Symbol name="eyeOff" />
                {t('library.watchAll.leaveAlone')}
              </Button>
              <Button variant="ghost" size="sm" disabled={busy || nothing} onClick={() => void run(() => watch(true))}>
                <Symbol name="eye" />
                {t('library.watchAll.watch')}
              </Button>
            </>
          )}
          {/* Tags gehoeren an Filme, Serien und Kuenstler; ein Album zeigt die seines Kuenstlers. */}
          {(kind !== 'album' || artists) && (
            <Button variant="ghost" size="sm" disabled={busy || nothing} onClick={() => setTagging(true)}>
              <Symbol name="tag" />
              {t('tags.dialog.open')}
            </Button>
          )}
          {/* 24.09.2026: mehrere Titel auf einmal entfernen, wahlweise samt Dateien in den Papierkorb. */}
          {!artists && (
            <Button variant="ghost" size="sm" disabled={busy || nothing} onClick={() => setRemoving(true)}>
              <Symbol name="trash" />
              {t('library.select.remove.open')}
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={selection.stop} disabled={busy}>
            {t('library.select.done')}
          </Button>
        </div>
      </div>
      {kind === 'series' && !artists && <p className="text-xs text-mist-400">{t('library.select.ruleHint')}</p>}
      {typing && (
        <SeriesTypeDialog
          count={count}
          scope={{ ids: ids(), state: state === 'all' ? null : state, q: q || null, tag: tag || null }}
          onClose={() => setTyping(false)}
          onDone={(result) => {
            setTyping(false)
            notify(
              t('library.select.seriesType.done', { count: result.changed, value: number(result.changed) }) +
                (result.fed > 0 ? ' ' + t('library.select.seriesType.fed', { count: result.fed, value: number(result.fed) }) : ''),
            )
            onDone()
            selection.stop()
          }}
        />
      )}
      {removing && (
        <RemoveManyDialog
          count={count}
          scope={{ kind, ids: ids(), state: state === 'all' ? null : state, q: q || null, tag: tag || null }}
          onClose={() => setRemoving(false)}
          onDone={(result) => {
            setRemoving(false)
            const parts = [t('library.select.remove.done', { count: result.removed, value: number(result.removed) })]
            if (result.files > 0) parts.push(t('library.select.remove.doneFiles', { count: result.files, value: number(result.files) }))
            if (result.kept_fed > 0) parts.push(t('library.select.remove.doneFed', { count: result.kept_fed, value: number(result.kept_fed) }))
            if (result.failed > 0) parts.push(t('library.select.remove.doneFailed', { count: result.failed, value: number(result.failed) }))
            notify(parts.join(' '))
            onDone()
            selection.stop()
          }}
        />
      )}
      {tagging && (
        <TagsDialog
          count={count}
          scope={{
            kind: artists ? 'artist' : kind === 'series' ? 'series' : 'movie',
            ids: ids(),
            state: artists || state === 'all' ? null : state,
            q: q || null,
            tag: tag || null,
          }}
          onClose={() => setTagging(false)}
          onDone={(changed) => {
            setTagging(false)
            notify(t('tags.dialog.done', { count: changed, value: number(changed) }))
            onDone()
            selection.stop()
          }}
        />
      )}
    </div>
  )
}
