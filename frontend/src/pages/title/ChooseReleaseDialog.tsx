import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { AlbumBlock } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage } from '../../components/ui'
import { countryText } from '../../lib/country'
import { formatNumber } from '../../lib/format'
import { formatsText } from './albumText'

/** Katalognummer und Label einer Ausgabe in einem Text, wie in der Tabelle gebraucht. */
function labelText(labels: { name?: string; catalog_number?: string }[]): string {
  return labels
    .map((entry) => [entry.name, entry.catalog_number].filter(Boolean).join(' '))
    .filter((text) => text !== '')
    .join(', ')
}

/**
 * "Ausgabe wählen" (Entscheidung 41): jede offizielle Ausgabe mit Format, Land, Datum, Label, Titelzahl und
 * Unterschied zur Zielausgabe. Auch die Box laesst sich waehlen. "Wieder nach Regel" gibt die Wahl an die Zielregel
 * zurueck (Entscheidung 30).
 */
export function ChooseReleaseDialog({
  titleId,
  album,
  onClose,
  onApplied,
}: {
  titleId: number
  album: AlbumBlock
  onClose: () => void
  /** Die Wahl ist gespeichert; die Seite soll den Titel neu holen. */
  onApplied: () => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [selected, setSelected] = useState<number | null>(album.target?.id ?? null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const targetTracks = album.target?.track_count ?? null

  function close() {
    if (!busy) onClose()
  }

  async function apply(releaseId: number | null) {
    setBusy(true)
    setProblem(null)
    try {
      await musicApi.setTarget(titleId, { release_id: releaseId })
      onApplied()
      onClose()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function diffText(release: (typeof album.releases)[number]): string {
    if (targetTracks === null) return ''
    const diff = release.track_count - targetTracks
    if (diff === 0) return t('title.album.chooseRelease.isTarget')
    const value = formatNumber(Math.abs(diff), language)
    return diff > 0 ? t('title.album.chooseRelease.diffMore', { value }) : t('title.album.chooseRelease.diffLess', { value })
  }

  return (
    <Dialog
      open
      table
      title={t('title.album.chooseRelease.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={() => void apply(null)} loading={busy && selected === null} disabled={busy}>
            {t('title.album.chooseRelease.reset')}
          </Button>
          <Button onClick={() => void apply(selected)} loading={busy && selected !== null} disabled={busy || selected === null}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        <div className="overflow-x-auto rounded-xl border border-ink-700">
          <table className="w-full min-w-[52rem] text-sm">
            <thead>
              <tr className="border-b border-ink-700 text-left text-xs tracking-wide text-mist-600 uppercase">
                <th className="px-3 py-2"></th>
                <th className="px-3 py-2">{t('title.album.chooseRelease.columns.name')}</th>
                <th className="px-3 py-2">{t('title.album.chooseRelease.columns.format')}</th>
                <th className="px-3 py-2">{t('title.album.chooseRelease.columns.country')}</th>
                <th className="px-3 py-2">{t('title.album.chooseRelease.columns.date')}</th>
                <th className="px-3 py-2">{t('title.album.chooseRelease.columns.label')}</th>
                <th className="px-3 py-2 text-right">{t('title.album.chooseRelease.columns.tracks')}</th>
                <th className="px-3 py-2 text-right">{t('title.album.chooseRelease.columns.diff')}</th>
              </tr>
            </thead>
            <tbody>
              {album.releases.map((release) => (
                <tr
                  key={release.id}
                  onClick={() => setSelected(release.id)}
                  className={'cursor-pointer border-b border-ink-700/60 last:border-b-0 hover:bg-ink-850 ' + (selected === release.id ? 'bg-accent-500/10' : '')}
                >
                  <td className="px-3 py-2">
                    <input type="radio" name="release" checked={selected === release.id} onChange={() => setSelected(release.id)} aria-label={release.name} className="accent-accent-500" />
                  </td>
                  <td className="max-w-[16rem] min-w-[10rem] px-3 py-2 text-mist-100">{release.name}</td>
                  <td className="px-3 py-2 whitespace-nowrap text-mist-300">{formatsText(release.formats, release.media_count)}</td>
                  <td className="px-3 py-2 whitespace-nowrap text-mist-300">{countryText(release.country, language)}</td>
                  <td className="px-3 py-2 whitespace-nowrap text-mist-300 tabular-nums">{release.date}</td>
                  <td className="max-w-[12rem] min-w-[8rem] px-3 py-2 text-mist-400">{labelText(release.labels)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-mist-300">{formatNumber(release.track_count, language)}</td>
                  <td className={'px-3 py-2 text-right tabular-nums ' + (targetTracks !== null && release.track_count === targetTracks ? 'font-semibold text-accent-400' : 'text-mist-400')}>
                    {diffText(release)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-mist-500">{t('title.album.chooseRelease.scrollHint')}</p>
      </div>
    </Dialog>
  )
}
