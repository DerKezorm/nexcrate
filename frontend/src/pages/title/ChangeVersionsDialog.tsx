import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { TitleDetail } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { formatList } from '../../lib/format'
import { useVersions } from '../versions/useVersions'
import { definitionOf, isFromSource, namedLabels, pendingDownloads } from './versionDefinitions'
import { SeriesAutomaticNote } from './SeriesAutomaticNote'

/**
 * "Fassungen ändern": alle Fassungen fuer Filme als Kaestchen. Dazunehmen geht immer, abwaehlen
 * nur eigene. Eine Fassung aus Radarr bleibt abgehakt und sagt, warum. Seit Schritt 3: Hat eine
 * abgewaehlte Fassung Downloads im Download-Programm (`pending_downloads`), sagt der Dialog, dass sie
 * mitgehen, und schickt `remove_downloads` nur dann.
 */
export function ChangeVersionsDialog({
  title,
  onClose,
  onChanged,
  onStale,
}: {
  title: TitleDetail
  onClose: () => void
  onChanged: (detail: TitleDetail) => void
  /** Der Server kennt einen neueren Stand, etwa weil ein Import dazwischenkam. Die Seite laedt nach. */
  onStale: () => void
}) {
  const { t, i18n } = useTranslation()
  // Seit S1: die Fassungen der Art des Titels, fuer eine Serie also die Fassungen fuer Serien.
  const { versions, error: versionsError } = useVersions(title.kind)
  // null: noch nichts geaendert, es gilt, was der Titel hat.
  const [wanted, setWanted] = useState<number[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  // Fassungen, die der Server in seinem 409 genannt hat (siehe namedLabels).
  const [named, setNamed] = useState<readonly string[]>([])

  const definitions = versions ?? []
  const rows = definitions.map((definition) => {
    const current = title.versions.find((version) => definitionOf(version, definitions) === definition.id) ?? null
    return { definition, current, locked: current !== null && isFromSource(current) }
  })
  const present = rows.filter((row) => row.current !== null).map((row) => row.definition.id)
  const checked = wanted ?? present
  const add = checked.filter((id) => !present.includes(id))
  const remove = rows.filter((row) => row.current !== null && !row.locked && !checked.includes(row.definition.id)).map((row) => row.definition.id)
  // Nur Fassungen, die gehen sollen. Die Downloads einer Fassung, die bleibt, gehen nicht mit.
  const removingLabels = rows.filter((row) => remove.includes(row.definition.id)).map((row) => row.definition.label)
  const removingLoading = rows
    .filter((row) => remove.includes(row.definition.id))
    .map((row) => ({
      label: row.definition.label,
      downloads: Math.max(row.current === null ? 0 : pendingDownloads(row.current), named.includes(row.definition.label) ? 1 : 0),
    }))
    .filter((entry) => entry.downloads > 0)
  const removingDownloads = removingLoading.reduce((sum, entry) => sum + entry.downloads, 0)
  const empty = versions !== null && versions.length > 0 && checked.length === 0
  const unchanged = add.length === 0 && remove.length === 0

  function toggle(id: number, on: boolean) {
    setProblem(null)
    setWanted((current) => {
      const base = current ?? present
      return on ? [...base.filter((entry) => entry !== id), id] : base.filter((entry) => entry !== id)
    })
  }

  async function save() {
    if (busy || empty || unchanged) return
    setBusy(true)
    setProblem(null)
    try {
      onChanged(await libraryApi.changeVersions(title.id, { add, remove, ...(removingLoading.length > 0 ? { remove_downloads: true } : {}) }))
    } catch (error) {
      setProblem(error)
      setBusy(false)
      if (error instanceof ApiError && error.code === 'version_owned_by_source') {
        setWanted(null)
        onStale()
      }
      // Seit dem Oeffnen hat ein Download begonnen. Die Auswahl bleibt; nach dem Nachladen sagt der Dialog, was mitgeht.
      if (error instanceof ApiError && error.code === 'version_download_active') {
        const labels = namedLabels(error.values.label, removingLabels)
        setNamed((current) => [...new Set([...current, ...labels])])
        onStale()
      }
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog
      open
      title={t('title.change.title', { title: title.title })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void save()} loading={busy} disabled={empty || unchanged}>
            {t('title.change.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-400">{t('title.change.intro')}</p>
        {versionsError !== null && <FormMessage>{errorText(t, versionsError)}</FormMessage>}
        {versions === null ? (
          versionsError === null && (
            <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : versions.length === 0 ? (
          <p className="text-sm text-mist-500">{title.kind === 'series' ? t('series.add.noVersions') : t('title.change.noVersions')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map(({ definition, current, locked }) => {
              const on = locked || checked.includes(definition.id)
              const change = current === null && on ? 'adds' : current !== null && !on ? 'removes' : null
              return (
                <li key={definition.id}>
                  <label
                    className={
                      'flex items-start gap-3 rounded-xl border p-3 ' +
                      (locked
                        ? 'cursor-default border-ink-700 bg-ink-900/40'
                        : on
                          ? 'cursor-pointer border-accent-500/50 bg-accent-500/5'
                          : 'cursor-pointer border-ink-700 bg-ink-900/60')
                    }
                  >
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
                      checked={on}
                      disabled={locked || busy}
                      onChange={(event) => toggle(definition.id, event.target.checked)}
                    />
                    <span className="flex min-w-0 flex-1 flex-col gap-1">
                      <span className="flex flex-wrap items-center gap-2 font-semibold text-mist-100">
                        <span className="wrap-anywhere">{definition.label}</span>
                        {change === 'adds' && <Badge tone="accent">{t('title.change.adds')}</Badge>}
                        {change === 'removes' && <Badge tone="bad">{t('title.change.removes')}</Badge>}
                      </span>
                      {current !== null &&
                        (locked ? (
                          <span className="text-sm text-mist-400">{t('title.change.fromSource', { name: current.source_name ?? '' })}</span>
                        ) : (
                          <span className="text-sm text-mist-500">{t('title.change.own')}</span>
                        ))}
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
        )}
        {empty && <FormMessage tone="info">{t('title.change.keepOne')}</FormMessage>}
        {title.kind === 'series' && add.length > 0 && <SeriesAutomaticNote />}
        {/* Library from disk: a removed version loses only its release.nex, the movie stays. */}
        {remove.length > 0 && <p className="text-sm text-mist-400">{t('title.remove.companion')}</p>}
        {removingLoading.length > 0 && (
          <FormMessage tone="info">
            {t('title.runningDownload.versions', { count: removingDownloads, names: formatList(removingLoading.map((entry) => entry.label), i18n.language) })}
          </FormMessage>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
