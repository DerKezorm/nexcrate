import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { tagsApi, type TagEntry } from '../../api/tags'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'

/**
 * Einstellungen, "Tags", wie Einstellungen, Tags in Radarr, Sonarr und Lidarr: jeder Tag mit
 * dem, was ihn traegt, Umbenennen (ein Name, den es schon gibt, legt beide zusammen) und Loeschen nach Rueckfrage.
 */
export function TagSettings() {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [items, setItems] = useState<TagEntry[] | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [editing, setEditing] = useState<{ id: number; label: string } | null>(null)
  const [removing, setRemoving] = useState<TagEntry | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    tagsApi.list().then(
      (found) => setItems(Array.isArray(found?.items) ? found.items : []),
      (error: unknown) => setProblem(error),
    )
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function act(what: () => Promise<unknown>, done: string) {
    setBusy(true)
    setProblem(null)
    try {
      await what()
      notify(done)
      setEditing(null)
      setRemoving(null)
      load()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  const number = (value: number) => formatNumber(value, i18n.language)
  return (
    <Section title={t('tags.settings.title')} intro={t('tags.settings.intro')}>
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {items === null ? (
        problem === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )
      ) : items.length === 0 ? (
        <p className="text-sm text-mist-500">{t('tags.settings.empty')}</p>
      ) : (
        <ul className="flex flex-col divide-y divide-ink-700 rounded-xl border border-ink-700" aria-label={t('tags.settings.title')}>
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
              <Symbol name="tag" className="h-4 w-4 text-mist-500" />
              {editing?.id === item.id ? (
                <form
                  className="flex flex-1 items-center gap-2"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void act(() => tagsApi.rename(item.id, editing.label), t('tags.settings.renamed'))
                  }}
                >
                  <input
                    autoFocus
                    value={editing.label}
                    maxLength={64}
                    onChange={(event) => setEditing({ id: item.id, label: event.target.value })}
                    aria-label={t('tags.settings.newName', { tag: item.label })}
                    className="min-w-0 flex-1 rounded-full border border-ink-700 bg-ink-900 px-3 py-1 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                  />
                  <Button type="submit" size="sm" loading={busy} disabled={editing.label.trim() === ''}>
                    {t('common.actions.save')}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setEditing(null)} disabled={busy}>
                    {t('common.actions.cancel')}
                  </Button>
                </form>
              ) : (
                <>
                  <span className="min-w-0 flex-1 font-medium text-mist-100 wrap-anywhere">{item.label}</span>
                  <span className="text-xs text-mist-500 tabular-nums">
                    {t('tags.settings.carried', { movies: number(item.movie), series: number(item.series), artists: number(item.artist) })}
                  </span>
                  <Button variant="ghost" size="sm" onClick={() => setEditing({ id: item.id, label: item.label })} aria-label={t('tags.settings.rename', { tag: item.label })}>
                    {t('tags.settings.renameShort')}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setRemoving(item)} aria-label={t('tags.settings.remove', { tag: item.label })}>
                    <Symbol name="trash" />
                  </Button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {removing !== null && (
        <Dialog
          open
          title={t('tags.settings.removeTitle', { tag: removing.label })}
          onClose={() => !busy && setRemoving(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setRemoving(null)} disabled={busy}>
                {t('common.actions.cancel')}
              </Button>
              <Button variant="danger" onClick={() => void act(() => tagsApi.remove(removing.id), t('tags.settings.removed'))} loading={busy}>
                {t('tags.settings.removeConfirm')}
              </Button>
            </>
          }
        >
          <p className="text-sm text-mist-300">{t('tags.settings.removeText')}</p>
        </Dialog>
      )}
    </Section>
  )
}
