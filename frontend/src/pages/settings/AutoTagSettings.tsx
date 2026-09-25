import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { autoTagsApi, type AutoTag, type AutoTagKind, type AutoTagOptions } from '../../api/autoTags'
import { errorText } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { Segmented } from '../../components/Segmented'
import { Symbol } from '../../components/Symbol'
import { TagChips } from '../../components/TagEditor'
import { Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { sourcesApi } from '../../api/sources'
import type { SourceApp } from '../../api/types'
import { AutoTagDialog } from './AutoTagDialog'
import { AutoTagImportDialog } from './AutoTagImportDialog'
import { APP_NAME, APP_OF_KIND, conditionLine } from './autoTagText'

const KINDS: readonly AutoTagKind[] = ['movie', 'series', 'album']

/**
 * Einstellungen, Tags, "Automatisch vergeben": die Regeln je Art, wie Auto Tagging in Radarr,
 * Sonarr und Lidarr. Speichern und Loeschen lassen die Regeln der Art sofort ueber die Bibliothek laufen.
 */
export function AutoTagSettings({ onChanged }: { onChanged?: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [kind, setKind] = useState<AutoTagKind>('movie')
  const [rules, setRules] = useState<AutoTag[] | null>(null)
  const [options, setOptions] = useState<AutoTagOptions | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [editing, setEditing] = useState<AutoTag | 'new' | null>(null)
  const [removing, setRemoving] = useState<AutoTag | null>(null)
  const [busy, setBusy] = useState(false)
  const [importing, setImporting] = useState(false)
  // Welche Apps eine Verbindung mit Schluessel haben: nur dann gibt es "Aus ... holen".
  const [apps, setApps] = useState<Set<SourceApp>>(new Set())

  useEffect(() => {
    let current = true
    sourcesApi.list().then(
      (found) => {
        if (current && Array.isArray(found)) setApps(new Set(found.filter((source) => source.has_api_key).map((source) => source.app)))
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [])

  const load = useCallback((which: AutoTagKind) => {
    setRules(null)
    setProblem(null)
    Promise.all([autoTagsApi.list(which), autoTagsApi.options(which)]).then(
      ([found, offered]) => {
        setRules(Array.isArray(found) ? found : [])
        setOptions(offered)
      },
      (error: unknown) => setProblem(error),
    )
  }, [])

  useEffect(() => {
    load(kind)
  }, [kind, load])

  const profiles = useMemo(() => new Map((options?.profiles ?? []).map((profile) => [profile.id, profile.name])), [options])

  function kindName(value: AutoTagKind): string {
    if (value === 'movie') return t('tags.auto.kind.movie')
    if (value === 'series') return t('tags.auto.kind.series')
    return t('tags.auto.kind.album')
  }

  function imported(taken: number, changed: number, close: boolean) {
    notify(t('tags.auto.import.done', { count: taken, changed }))
    if (close) setImporting(false)
    load(kind)
    onChanged?.()
  }

  function done(changed: number) {
    notify(t('tags.auto.applied', { count: changed }))
    setEditing(null)
    setRemoving(null)
    load(kind)
    onChanged?.()
  }

  async function remove(rule: AutoTag) {
    setBusy(true)
    try {
      done((await autoTagsApi.remove(rule.id)).changed)
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={t('tags.auto.title')} intro={t('tags.auto.intro')}>
      <Segmented value={kind} options={KINDS} onChange={setKind} label={kindName} ariaLabel={t('tags.auto.kindLabel')} />
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {rules === null ? (
        problem === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )
      ) : rules.length === 0 ? (
        <p className="text-sm text-mist-500">{t('tags.auto.empty')}</p>
      ) : (
        <ul className="flex flex-col divide-y divide-ink-700 rounded-xl border border-ink-700" aria-label={t('tags.auto.title')}>
          {rules.map((rule) => (
            <li key={rule.id} className="flex flex-col gap-2 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 font-medium wrap-anywhere text-mist-100">{rule.name}</span>
                {rule.remove_automatically && <span className="text-xs text-mist-500">{t('tags.auto.removesMark')}</span>}
                <Button variant="ghost" size="sm" onClick={() => setEditing(rule)} aria-label={t('tags.auto.edit', { name: rule.name })}>
                  {t('common.actions.edit')}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setRemoving(rule)} aria-label={t('tags.auto.delete', { name: rule.name })}>
                  <Symbol name="trash" />
                </Button>
              </div>
              <TagChips tags={rule.tags} />
              <ul className="flex flex-col gap-0.5 text-xs text-mist-400">
                {rule.conditions.map((condition, index) => (
                  <li key={index} className="wrap-anywhere">
                    {conditionLine(t, condition, profiles)}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
      {options !== null && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => setEditing('new')}>
            <Symbol name="plus" />
            {t('tags.auto.add')}
          </Button>
          {apps.has(APP_OF_KIND[kind]) && (
            <Button variant="ghost" size="sm" onClick={() => setImporting(true)}>
              {t('tags.auto.import.button', { app: APP_NAME[APP_OF_KIND[kind]] })}
            </Button>
          )}
        </div>
      )}
      {importing && <AutoTagImportDialog kind={kind} profiles={profiles} onClose={() => setImporting(false)} onDone={imported} />}
      {editing !== null && options !== null && (
        <AutoTagDialog kind={kind} rule={editing === 'new' ? null : editing} options={options} onClose={() => setEditing(null)} onSaved={done} />
      )}
      {removing !== null && (
        <Dialog
          open
          title={t('tags.auto.deleteTitle', { name: removing.name })}
          onClose={() => !busy && setRemoving(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setRemoving(null)} disabled={busy}>
                {t('common.actions.cancel')}
              </Button>
              <Button variant="danger" onClick={() => void remove(removing)} loading={busy}>
                {t('tags.auto.deleteConfirm')}
              </Button>
            </>
          }
        >
          <p className="text-sm text-mist-300">{t('tags.auto.deleteText')}</p>
        </Dialog>
      )}
    </Section>
  )
}
