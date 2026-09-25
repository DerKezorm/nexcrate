import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { TitleDetail } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'
import { formatList } from '../../lib/format'
import { useVersions } from '../versions/useVersions'
import { definitionOf, isFromSource, namedCount, namedLabels, pendingDownloads } from './versionDefinitions'

type Props = {
  title: TitleDetail
  onClose: () => void
  /** Der Titel ist weg. */
  onRemoved: () => void
  /** Nur die eigenen Fassungen sind weg, der Titel bleibt mit denen aus Radarr. */
  onChanged: (detail: TitleDetail) => void
  /** Der Server kennt einen neueren Stand. Die Seite laedt nach, der Dialog passt sich an. */
  onStale: () => void
}

type Shared = Props & { problem: unknown; setProblem: (problem: unknown) => void }

/**
 * "Titel entfernen" mit Rueckfrage. Ohne Fassungen aus Radarr geht der ganze Titel. Liefert
 * Radarr Fassungen, bleiben die; entfernen lassen sich dann nur die eigenen, und der Dialog
 * sagt das vorher. Seit Schritt 3: Hat eine Fassung, die geht, Downloads im Download-Programm
 * (`pending_downloads`), sagt der Dialog, dass nexcrate sie dort mit entfernt, und schickt `remove_downloads` nur dann.
 */
export function RemoveTitleDialog(props: Props) {
  // Die Meldung liegt hier: Kommt nach einem 409 der neue Stand, wechselt der Dialog seine Form und behaelt sie.
  const [problem, setProblem] = useState<unknown>(null)
  const fromSource = props.title.versions.filter(isFromSource)
  return fromSource.length === 0 ? (
    <RemoveWhole {...props} problem={problem} setProblem={setProblem} />
  ) : (
    <SourceRemains {...props} problem={problem} setProblem={setProblem} />
  )
}

function RemoveWhole({ title, onClose, onRemoved, onStale, problem, setProblem }: Shared) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  // Wie viele Downloads der Server in seinem 409 genannt hat (siehe namedCount).
  const [named, setNamed] = useState(0)
  const pending = title.versions.reduce((sum, version) => sum + pendingDownloads(version), 0)
  const loading = Math.max(pending, named)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await libraryApi.remove(title.id, loading > 0)
      onRemoved()
    } catch (error) {
      setProblem(error)
      setBusy(false)
      // Ein Import kam dazwischen und hat Fassungen aus Radarr gebracht.
      if (error instanceof ApiError && error.code === 'title_has_source_versions') onStale()
      // Seit dem Oeffnen hat ein Download begonnen.
      if (error instanceof ApiError && error.code === 'title_download_active') {
        setNamed(namedCount(error.values.count))
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
      title={t('title.remove.title', { title: title.title })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('title.remove.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">{t('title.remove.text')}</p>
        {/* Library from disk: removing takes the release.nex nexcrate wrote, never the movie. */}
        <p className="text-sm text-mist-400">{t('title.remove.companion')}</p>
        {loading > 0 && <FormMessage tone="info">{t('title.runningDownload.movie', { count: loading })}</FormMessage>}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

function SourceRemains({ title, onClose, onChanged, onStale, problem, setProblem }: Shared) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const { versions } = useVersions(title.kind)
  const [busy, setBusy] = useState(false)
  // Die eigenen Fassungen, die der Server in seinem 409 genannt hat (siehe namedLabels).
  const [named, setNamed] = useState<readonly string[]>([])
  const fromSource = title.versions.filter(isFromSource)
  const own = title.versions.filter((version) => !isFromSource(version))
  const ownIds = own.map((version) => definitionOf(version, versions ?? [])).filter((id): id is number => id !== null)
  // Je eigene Fassung, wie viele Downloads mit ihr gehen; eine aus dem 409 zaehlt mindestens einmal.
  const ownLoading = own
    .map((version) => ({ label: version.label, downloads: Math.max(pendingDownloads(version), named.includes(version.label) ? 1 : 0) }))
    .filter((entry) => entry.downloads > 0)
  const ownDownloads = ownLoading.reduce((sum, entry) => sum + entry.downloads, 0)

  async function removeOwn() {
    if (busy || ownIds.length === 0) return
    setBusy(true)
    setProblem(null)
    try {
      onChanged(await libraryApi.changeVersions(title.id, { add: [], remove: ownIds, ...(ownLoading.length > 0 ? { remove_downloads: true } : {}) }))
    } catch (error) {
      setProblem(error)
      setBusy(false)
      if (error instanceof ApiError && error.code === 'version_owned_by_source') onStale()
      if (error instanceof ApiError && error.code === 'version_download_active') {
        const labels = namedLabels(error.values.label, own.map((version) => version.label))
        setNamed((current) => [...new Set([...current, ...labels])])
        onStale()
      }
    }
  }

  function close() {
    if (!busy) onClose()
  }

  const sourceNames = formatList(
    fromSource.map((version) => t('title.remove.sourceVersion', { label: version.label, name: version.source_name ?? '' })),
    language,
  )

  return (
    <Dialog
      open
      title={t('title.remove.title', { title: title.title })}
      onClose={close}
      footer={
        own.length > 0 ? (
          <>
            <Button variant="ghost" onClick={close} disabled={busy}>
              {t('common.actions.cancel')}
            </Button>
            <Button variant="danger" onClick={() => void removeOwn()} loading={busy} disabled={ownIds.length < own.length}>
              {t('title.remove.removeOwn', { count: own.length })}
            </Button>
          </>
        ) : (
          <Button variant="ghost" onClick={close}>
            {t('common.actions.close')}
          </Button>
        )
      }
    >
      <div className="flex flex-col gap-3">
        <p className="flex items-start gap-2 text-sm text-mist-300">
          <Symbol name="shield" className="mt-0.5 h-4 w-4 shrink-0 text-ok-500" />
          <span className="min-w-0">{t('title.remove.sourceText', { count: fromSource.length, names: sourceNames })}</span>
        </p>
        {own.length > 0 && (
          <>
            <p className="text-sm text-mist-300">
              {t('title.remove.ownText', { count: own.length, names: formatList(own.map((version) => version.label), language) })}
            </p>
            <p className="text-sm text-mist-400">{t('title.remove.companion')}</p>
          </>
        )}
        {ownLoading.length > 0 && (
          <FormMessage tone="info">
            {t('title.runningDownload.versions', { count: ownDownloads, names: formatList(ownLoading.map((entry) => entry.label), language) })}
          </FormMessage>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
