import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { errorText } from '../../api/client'
import { recycleApi } from '../../api/recycle'
import type { RecycleBin, RecycleBinEntry } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime, formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { Loading } from './PatternFields'

type Asked = { kind: 'purge'; entry: RecycleBinEntry } | { kind: 'empty' } | null

/**
 * Was im Papierkorb liegt (Antwort 2): geloescht vom Besitzer oder von einem Programm mit
 * Schluessel. Zurueckholen legt die Datei an ihren alten Platz; endgueltig loeschen und leeren fragen vorher nach.
 * Ein Eintrag, dessen Titel weg ist, laesst sich nur noch endgueltig loeschen.
 */
export function RecycleBinList() {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const [bin, setBin] = useState<RecycleBin | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState<number | 'empty' | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [asked, setAsked] = useState<Asked>(null)

  const load = useCallback((signal?: AbortSignal) => {
    recycleApi.bin(signal).then(
      (result) => {
        if (!signal?.aborted) {
          setBin(result)
          setLoadError(null)
        }
      },
      (error: unknown) => {
        if (!signal?.aborted) setLoadError(error)
      },
    )
  }, [])

  useEffect(() => {
    const abort = new AbortController()
    load(abort.signal)
    return () => abort.abort()
  }, [load])

  async function act(key: number | 'empty', work: () => Promise<string>) {
    setBusy(key)
    setProblem(null)
    try {
      notify(await work())
      setAsked(null)
      load()
    } catch (error) {
      setProblem(error)
      setAsked(null)
      load()
    } finally {
      setBusy(null)
    }
  }

  const restore = (entry: RecycleBinEntry) =>
    act(entry.id, async () => {
      await recycleApi.restore(entry.id)
      return t('settings.files.bin.restored', { file: entry.file_name })
    })
  const purge = (entry: RecycleBinEntry) =>
    act(entry.id, async () => {
      await recycleApi.purge(entry.id)
      return t('settings.files.bin.purged', { file: entry.file_name })
    })
  const empty = () =>
    act('empty', async () => {
      const result = await recycleApi.empty()
      return t('settings.files.bin.emptied', { count: result.deleted, value: formatNumber(result.deleted, language) })
    })

  const items = bin?.items ?? []
  return (
    <Section
      title={t('settings.files.bin.title')}
      intro={t('settings.files.bin.intro')}
      actions={
        items.length > 0 ? (
          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => setAsked({ kind: 'empty' })}>
            <Symbol name="trash" />
            {t('settings.files.bin.emptyAll')}
          </Button>
        ) : undefined
      }
    >
      {bin === null ? (
        loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : (
          <Loading />
        )
      ) : items.length === 0 ? (
        <p className="text-sm text-mist-500">{t('settings.files.bin.empty')}</p>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-mist-400 tabular-nums">
            {t('settings.files.bin.total', { count: items.length, value: formatNumber(items.length, language), size: sizeText(t, bin.size_bytes, language) })}
          </p>
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
          <ul aria-label={t('settings.files.bin.title')} className="flex flex-col gap-2">
            {items.map((entry) => (
              <BinRow
                key={entry.id}
                entry={entry}
                busy={busy === entry.id}
                blocked={busy !== null}
                onRestore={() => void restore(entry)}
                onPurge={() => setAsked({ kind: 'purge', entry })}
              />
            ))}
          </ul>
        </div>
      )}
      {asked !== null && (
        <Dialog
          open
          title={asked.kind === 'empty' ? t('settings.files.bin.emptyTitle') : t('settings.files.bin.purgeLabel', { file: asked.entry.file_name })}
          onClose={() => {
            if (busy === null) setAsked(null)
          }}
          footer={
            <>
              <Button variant="ghost" onClick={() => setAsked(null)} disabled={busy !== null}>
                {t('common.actions.cancel')}
              </Button>
              <Button
                variant="danger"
                loading={busy !== null}
                onClick={() => void (asked.kind === 'empty' ? empty() : purge(asked.entry))}
              >
                {asked.kind === 'empty' ? t('settings.files.bin.emptyAll') : t('settings.files.bin.purge')}
              </Button>
            </>
          }
        >
          <p className="text-sm text-mist-300">{asked.kind === 'empty' ? t('settings.files.bin.emptyText') : t('settings.files.bin.purgeText')}</p>
        </Dialog>
      )}
    </Section>
  )
}

function BinRow({ entry, busy, blocked, onRestore, onPurge }: { entry: RecycleBinEntry; busy: boolean; blocked: boolean; onRestore: () => void; onPurge: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const name = entry.year ? `${entry.title} (${entry.year})` : entry.title
  const date = formatDateTime(entry.deleted_at, language)
  const restorable = entry.present && entry.in_library
  return (
    <li className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 p-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2 font-semibold text-mist-100 wrap-anywhere">
          {entry.in_library && entry.title_id !== null ? (
            <Link to={`/titel/${entry.title_id}`} className="hover:text-accent-400 hover:underline">
              {name}
            </Link>
          ) : (
            name
          )}
          <Badge>{entry.version_label}</Badge>
        </span>
        {entry.season !== null && (
          <span className="text-sm text-mist-400">{t('settings.files.bin.episodes', { season: entry.season, episodes: entry.episodes.join(', ') })}</span>
        )}
        <span className="font-mono text-xs leading-5 wrap-anywhere text-mist-400">{entry.file_name}</span>
        <span className="text-xs text-mist-500 tabular-nums">
          {sizeText(t, entry.size_bytes, language)} ·{' '}
          {entry.deleted_by === 'key' && entry.deleted_by_name ? t('settings.files.bin.byProgram', { name: entry.deleted_by_name, date }) : t('settings.files.bin.byOwner', { date })}
        </span>
        {!entry.present && <span className="text-xs text-bad-500">{t('settings.files.bin.gone')}</span>}
        {entry.present && !entry.in_library && <span className="text-xs text-mist-500">{t('settings.files.bin.notInLibrary')}</span>}
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        {restorable && (
          <Button size="sm" variant="ghost" loading={busy} disabled={blocked} aria-label={t('settings.files.bin.restoreLabel', { file: entry.file_name })} onClick={onRestore}>
            <Symbol name="back" />
            {t('settings.files.bin.restore')}
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={blocked} aria-label={t('settings.files.bin.purgeLabel', { file: entry.file_name })} onClick={onPurge}>
          <Symbol name="trash" />
          {t('settings.files.bin.purge')}
        </Button>
      </div>
    </li>
  )
}
