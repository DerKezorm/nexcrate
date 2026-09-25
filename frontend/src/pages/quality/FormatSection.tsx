import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { expertApi } from '../../api/expert'
import type { CustomFormat, FormatCondition, MediaKind } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { conditionSummary } from './conditions'
import { FormatDialog } from './FormatDialog'

/**
 * Alle Custom Formats einer Art, die aus den Leitfaeden und die eigenen, wie Radarrs Seite "Custom Formats".
 * Ein Format steht fuer sich; die Punkte bekommt es im Profil einer Fassung. Entfernen geht nur, solange kein
 * Profil darauf zeigt, und was darauf zeigt, steht in der Zeile.
 */
export function FormatSection({ kind }: { kind: MediaKind }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [formats, setFormats] = useState<CustomFormat[] | null>(null)
  const [conditions, setConditions] = useState<FormatCondition[]>([])
  const [loadError, setLoadError] = useState<unknown>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [editing, setEditing] = useState<CustomFormat | null>(null)
  const [adding, setAdding] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoadError(null)
    return Promise.all([expertApi.formats(kind), expertApi.conditions(kind)])
      .then(([rows, list]) => {
        setFormats(rows)
        setConditions(list.items)
      })
      .catch((error: unknown) => setLoadError(error))
  }, [kind])

  useEffect(() => {
    setFormats(null)
    void load()
  }, [load])

  async function remove(format: CustomFormat) {
    if (busyId !== null) return
    setBusyId(format.id)
    setProblem(null)
    try {
      await expertApi.removeFormat(format.id)
      notify(t('quality.formats.removed', { name: format.name }))
      await load()
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusyId(null)
    }
  }

  function done() {
    setAdding(false)
    setEditing(null)
    void load()
  }

  return (
    <Section
      title={t('quality.formats.title')}
      intro={t('quality.formats.intro')}
      actions={
        <Button onClick={() => setAdding(true)} disabled={formats === null}>
          <Symbol name="plus" className="h-4 w-4" />
          {t('quality.formats.add')}
        </Button>
      }
    >
      {formats === null ? (
        loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : (
          <p className="flex items-center gap-2 text-sm text-mist-500">
            <Spinner /> {t('quality.formats.loading')}
          </p>
        )
      ) : (
        <div className="flex flex-col gap-3">
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
          {formats.length === 0 && <p className="text-sm text-mist-500">{t('quality.formats.empty')}</p>}
          <ul className="flex flex-col gap-2">
            {formats.map((format) => (
              <li key={format.id} className="flex flex-wrap items-start gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium wrap-anywhere text-mist-100">{format.name}</span>
                    <Badge tone={format.origin === 'trash' ? 'info' : 'accent'}>{format.origin === 'trash' ? t('quality.formats.fromGuides') : t('quality.formats.ownOne')}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-mist-500">
                    {format.specifications.length === 0 ? t('quality.formats.matchesEverything') : conditionSummary(t, format.specifications)}
                  </p>
                  {format.used_by.length > 0 && <p className="mt-0.5 text-xs text-mist-500">{t('quality.formats.usedBy', { versions: format.used_by.join(', ') })}</p>}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm" variant="ghost" onClick={() => setEditing(format)} aria-label={t('quality.formats.changeLabel', { name: format.name })}>
                    {t('quality.formats.change')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void remove(format)}
                    disabled={format.used_by.length > 0 || busyId !== null}
                    aria-label={t('quality.formats.removeLabel', { name: format.name })}
                  >
                    <Symbol name="trash" className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(adding || editing !== null) && <FormatDialog kind={kind} format={editing} conditions={conditions} onClose={done} onDone={done} />}
    </Section>
  )
}
