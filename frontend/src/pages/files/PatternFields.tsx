import { useId, useLayoutEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { Symbol } from '../../components/Symbol'
import { Spinner } from '../../components/ui'
import type { PatternPreview } from './usePatternPreview'

export function Loading() {
  const { t } = useTranslation()
  return (
    <p className="flex items-center gap-2 py-2 text-sm text-mist-500" role="status">
      <Spinner />
      {t('common.loading')}
    </p>
  )
}

/**
 * Ein Muster in einem Feld, das umbricht und mit dem Inhalt waechst: Das Dateimuster der TRaSH Guides hat gut 200
 * Zeichen und waere in einer Zeile am Telefon nur zu einem kleinen Teil zu sehen. Ein Muster hat keine
 * Zeilenumbrueche, deshalb setzt Enter keinen, und eingefuegte fallen weg. `onCursor` meldet, wo der Cursor steht.
 */
export function PatternInput({
  label,
  value,
  onChange,
  onCursor,
}: {
  label: string
  value: string
  onChange: (value: string, cursor: number | null) => void
  onCursor: (cursor: number | null) => void
}) {
  const id = useId()
  const area = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => {
    const element = area.current
    if (!element) return
    function fit() {
      element!.style.height = 'auto'
      // jsdom misst nichts und liefert 0. Dann bleibt die Hoehe, die eine Zeile braucht.
      if (element!.scrollHeight > 0) element!.style.height = `${element!.scrollHeight}px`
    }
    fit()
    // Schmaler oder breiter bricht der Text anders um.
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [value])

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-mist-300">
        {label}
      </label>
      <textarea
        ref={area}
        id={id}
        rows={1}
        value={value}
        onChange={(event) => onChange(event.target.value.replace(/[\r\n]+/g, ''), event.target.selectionStart)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.preventDefault()
        }}
        onSelect={(event) => onCursor(event.currentTarget.selectionStart)}
        onFocus={(event) => onCursor(event.currentTarget.selectionStart)}
        autoComplete="off"
        spellCheck={false}
        maxLength={1000}
        className="w-full min-w-0 resize-none rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 font-mono text-sm leading-6 wrap-anywhere text-mist-100 transition-colors focus:border-accent-500 focus:outline-none"
      />
    </div>
  )
}

/** Die Platzhalter des Servers als Knoepfe. Ein Klick setzt einen dort ein, wo man zuletzt in einem Muster war. */
export function TokenList({ tokens, onInsert }: { tokens: readonly string[]; onInsert: (token: string) => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium text-mist-300">{t('settings.files.naming.tokens')}</h3>
      <p className="text-xs text-mist-500">{t('settings.files.naming.tokensHint')}</p>
      {tokens.length > 0 && (
        <ul aria-label={t('settings.files.naming.tokens')} className="flex flex-wrap gap-1.5">
          {tokens.map((token) => (
            <li key={token} className="min-w-0">
              <button
                type="button"
                onClick={() => onInsert(token)}
                aria-label={t('settings.files.naming.insertLabel', { token })}
                className="max-w-full rounded-lg border border-ink-700 bg-ink-900 px-2 py-1 text-left font-mono text-xs break-all text-mist-200 transition-colors hover:border-accent-500/60 hover:text-accent-400"
              >
                {token}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Ordner und Datei eines erfundenen Films nach den Mustern, oder warum das so nicht geht. */
export function PreviewBox({ preview, empty, emptyId }: { preview: PatternPreview; empty: boolean; emptyId: string }) {
  const { t } = useTranslation()
  return (
    <div aria-live="polite" className="flex flex-col gap-1.5 rounded-xl border border-ink-700 bg-ink-950/60 px-3.5 py-3">
      <h3 className="text-xs font-medium text-mist-500">{t('settings.files.naming.preview')}</h3>
      {empty ? (
        <p id={emptyId} className="text-sm text-bad-500">
          {t('settings.files.naming.previewEmpty')}
        </p>
      ) : preview.error !== null ? (
        <p className="text-sm wrap-anywhere text-bad-500">{errorText(t, preview.error)}</p>
      ) : preview.result !== null ? (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
          <dt className="text-mist-500">{t('settings.files.naming.previewFolder')}</dt>
          <dd className="font-mono break-all text-accent-400">{preview.result.folder}</dd>
          <dt className="text-mist-500">{t('settings.files.naming.previewFile')}</dt>
          <dd className="font-mono break-all text-accent-400">{preview.result.file}</dd>
        </dl>
      ) : (
        <Loading />
      )}
    </div>
  )
}

/**
 * Zwei Muster zum Lesen, lange brechen um. Unter einem Muster steht, was nexcrate dagegen hat, wenn etwas, und blau,
 * was dazu gut zu wissen ist.
 */
export function PatternRows({
  folder,
  file,
  folderProblem = null,
  fileProblem = null,
  folderNotes = [],
  fileNotes = [],
}: {
  folder: string
  file: string
  folderProblem?: string | null
  fileProblem?: string | null
  folderNotes?: readonly string[]
  fileNotes?: readonly string[]
}) {
  const { t } = useTranslation()
  const rows = [
    { key: 'folder', label: t('settings.files.naming.movieFolder'), value: folder, problem: folderProblem, notes: folderNotes },
    { key: 'file', label: t('settings.files.naming.movieFile'), value: file, problem: fileProblem, notes: fileNotes },
  ]
  return (
    <dl className="flex flex-col gap-2.5">
      {rows.map((row) => (
        <div key={row.key} className="flex min-w-0 flex-col gap-1">
          <dt className="text-xs text-mist-500">{row.label}</dt>
          <dd className="rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 font-mono text-sm break-all text-mist-100">
            {row.value.trim() === '' ? <span className="font-sans text-mist-500">{t('settings.files.radarrNaming.empty')}</span> : row.value}
          </dd>
          {row.problem !== null && <dd className="text-sm wrap-anywhere text-bad-500">{row.problem}</dd>}
          {row.notes.map((note) => (
            <dd key={note} className="flex items-start gap-1.5 text-xs wrap-anywhere text-mist-400">
              <Symbol name="info" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info-500" />
              <span className="min-w-0">{note}</span>
            </dd>
          ))}
        </div>
      ))}
    </dl>
  )
}

/** Hinweise als ruhige blaue Zeilen, keine Warnungen. */
export function NoteLines({ notes, label }: { notes: readonly string[]; label?: string }) {
  if (notes.length === 0) return null
  return (
    <ul aria-label={label} className="flex flex-col gap-1.5">
      {notes.map((note) => (
        <li key={note} className="flex items-start gap-2 text-sm wrap-anywhere text-mist-300">
          <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
          <span className="min-w-0">{note}</span>
        </li>
      ))}
    </ul>
  )
}
