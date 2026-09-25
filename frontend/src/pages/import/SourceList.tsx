import { useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { sourcesApi } from '../../api/sources'
import type { ImportRun, Source, SourceApp, SourceTestResult, Version } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDate, formatDateTime } from '../../lib/format'
import { RadarrClientsFlow } from '../clients/RadarrClientsFlow'
import { RadarrNamingDialog } from '../files/RadarrNamingDialog'
import { RadarrIndexerDialog } from './RadarrIndexers'
import { QualitySetupDialog } from './QualitySetupDialog'
import { checkText, runErrorText, runLines, runProgress } from './runText'
import { SonarrNamingDialog } from './SonarrNaming'
import { SourceDialog } from './SourceDialog'
import { TakeoverDialog } from './TakeoverDialog'
import { TakeoverUndoDialog } from './TakeoverUndoDialog'
import { useImportRun } from './useImportRun'

type Loaded<T> = { items: T[] | null; error: unknown }

/** Was sich neben einer Verbindung oeffnen laesst, die nicht uebernommen ist. */
type SourceAction = 'indexers' | 'clients' | 'naming' | 'quality' | 'takeover'

/**
 * Die Verbindungen zu einer App, Radarr oder Sonarr. Eine eintragen und pruefen, ihre Fassung waehlen oder gleich
 * anlegen, dann importieren. Neben jeder Verbindung lassen sich Indexer, Download-Programme und die Benennung holen,
 * und "Übernehmen …" macht ihre Fassungen zu nexcrates eigenen; seit S6 gilt das auch fuer Sonarr. Eine uebernommene
 * Verbindung bleibt als Eintrag: "Rückgängig machen" verbindet sie wieder und startet einen Import, dem die Karte
 * folgt; "Eintrag entfernen" behaelt Titel, Fassungen und Dateien. In der App aendert sich bei alledem nichts.
 */
export function SourceList({
  app,
  tmdbMissing = false,
  sources,
  versions,
  onSourcesChanged,
  onVersionsChanged,
  onImportFinished,
}: {
  app: SourceApp
  /** Nur Sonarr: Ohne TMDB-Token liest nexcrate keine Serien. */
  tmdbMissing?: boolean
  /** Nur die Verbindungen dieser App. */
  sources: Loaded<Source>
  /** Die Fassungen der Medienart dieser App. */
  versions: Loaded<Version>
  onSourcesChanged: () => void
  onVersionsChanged: () => void
  onImportFinished: () => void
}) {
  const { t } = useTranslation()
  const notify = useNotice()
  const series = app === 'sonarr'
  // undefined: kein Dialog. null: neue Verbindung.
  const [editing, setEditing] = useState<Source | null | undefined>(undefined)
  const [removing, setRemoving] = useState<Source | null>(null)
  const [undoing, setUndoing] = useState<Source | null>(null)
  // Der Import, den "Rückgängig machen" gestartet hat, je Verbindung. Die Karte beginnt mit ihm, auch wenn die neu
  // geladene Liste ihn noch nicht nennt.
  const [undoRuns, setUndoRuns] = useState<Readonly<Record<number, ImportRun>>>({})
  const [action, setAction] = useState<{ kind: SourceAction; source: Source } | null>(null)
  // Dieselbe Liste, solange das Fenster offen ist: Die Liste der Download-Programme liest bei jeder neuen neu.
  const actionSources = useMemo(() => (action === null ? [] : [action.source]), [action])

  const sourceList = sources.items
  const versionList = versions.items
  const loaded = sourceList !== null && versionList !== null
  const versionOf = (source: Source) => versionList?.find((version) => version.id === source.version_id)
  const texts = series
    ? { title: t('series.import.title'), intro: t('series.import.intro'), readOnly: t('series.import.readOnly'), add: t('series.import.add'), empty: t('series.import.empty') }
    : { title: t('import.title'), intro: t('import.intro'), readOnly: t('import.readOnly'), add: t('import.sources.add'), empty: t('import.sources.empty') }

  return (
    <Section
      title={texts.title}
      intro={texts.intro}
      actions={
        loaded ? (
          <Button onClick={() => setEditing(null)}>
            <Symbol name="plus" />
            {texts.add}
          </Button>
        ) : undefined
      }
    >
      <p className="flex items-start gap-2 rounded-xl border border-ok-500/30 bg-ok-500/5 px-4 py-3 text-sm text-mist-300">
        <Symbol name="shield" className="mt-0.5 h-4 w-4 shrink-0 text-ok-500" />
        {texts.readOnly}
      </p>
      {series && tmdbMissing && (
        <FormMessage tone="info" role="note">
          {t('series.import.tmdbNeeded')}
        </FormMessage>
      )}

      {sources.error !== null && <FormMessage>{errorText(t, sources.error)}</FormMessage>}
      {versions.error !== null && <FormMessage>{errorText(t, versions.error)}</FormMessage>}

      {!loaded ? (
        sources.error === null &&
        versions.error === null && (
          <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )
      ) : (
        <>
          {sourceList.length === 0 ? (
            <p className="text-sm text-mist-500">{texts.empty}</p>
          ) : (
            <ul className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
              {sourceList.map((source) => (
                <li key={source.id} className="min-w-0">
                  {source.taken_over_at ? (
                    <TakenOverCard
                      source={source}
                      series={series}
                      version={versionOf(source)}
                      onUndo={() => setUndoing(source)}
                      onRemove={() => setRemoving(source)}
                      onAction={(kind) => setAction({ kind, source })}
                    />
                  ) : (
                    <SourceCard
                      app={app}
                      source={source}
                      initialRun={newerRun(source.last_import, undoRuns[source.id])}
                      version={versionOf(source)}
                      onEdit={() => setEditing(source)}
                      onRemove={() => setRemoving(source)}
                      onFinished={onImportFinished}
                      onAction={(kind) => setAction({ kind, source })}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
          <div className="space-y-2 text-sm text-mist-500">
            {series ? (
              <>
                <p className="flex items-start gap-2">
                  <Symbol name="refresh" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
                  <span>{t('series.import.schedule')}</span>
                </p>
                <p className="flex items-start gap-2">
                  <Symbol name="swap" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
                  <span>
                    <strong className="font-semibold text-mist-300">{t('import.takeoverLabel')}</strong> {t('import.sources.series.takeoverNote')}
                  </span>
                </p>
              </>
            ) : (
              <>
                <p className="flex items-start gap-2">
                  <Symbol name="refresh" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
                  <span>
                    <strong className="font-semibold text-mist-300">{t('import.scheduleLabel')}</strong> {t('import.schedule')}
                  </span>
                </p>
                <p className="flex items-start gap-2">
                  <Symbol name="swap" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
                  <span>
                    <strong className="font-semibold text-mist-300">{t('import.takeoverLabel')}</strong> {t('import.takeoverNote')}
                  </span>
                </p>
              </>
            )}
          </div>

          {editing !== undefined && (
            <SourceDialog
              app={app}
              source={editing}
              sources={sourceList}
              versions={versionList}
              onVersionCreated={onVersionsChanged}
              onClose={() => setEditing(undefined)}
              onSaved={() => {
                setEditing(undefined)
                onSourcesChanged()
              }}
            />
          )}
        </>
      )}

      {removing && (
        <RemoveSourceDialog
          source={removing}
          onClose={() => setRemoving(null)}
          onRemoved={() => {
            setRemoving(null)
            onSourcesChanged()
            onVersionsChanged()
          }}
        />
      )}

      {undoing && (
        <TakeoverUndoDialog
          source={undoing}
          label={versionOf(undoing)?.label ?? null}
          onClose={() => setUndoing(null)}
          onUndone={(run) => {
            setUndoRuns((current) => ({ ...current, [undoing.id]: run }))
            setUndoing(null)
            onSourcesChanged()
          }}
        />
      )}

      {action?.kind === 'indexers' && <RadarrIndexerDialog source={action.source} onClose={() => setAction(null)} />}
      {action?.kind === 'quality' && <QualitySetupDialog source={action.source} onClose={() => setAction(null)} />}
      {action?.kind === 'clients' && <RadarrClientsFlow sources={actionSources} onClose={() => setAction(null)} />}
      {action?.kind === 'naming' &&
        (series ? (
          <SonarrNamingDialog
            sourceId={action.source.id}
            versionId={action.source.version_id}
            label={versionOf(action.source)?.label ?? ''}
            onClose={() => setAction(null)}
            onTaken={(saved) => {
              setAction(null)
              notify(t('import.sonarrNaming.taken', { label: saved.label }))
            }}
          />
        ) : (
          <RadarrNamingDialog
            sourceId={action.source.id}
            versionId={action.source.version_id}
            label={versionOf(action.source)?.label ?? ''}
            onClose={() => setAction(null)}
            onTaken={(saved) => {
              setAction(null)
              notify(t('settings.files.radarrNaming.taken', { label: saved.label }))
            }}
          />
        ))}
      {action?.kind === 'takeover' && (
        <TakeoverDialog
          source={action.source}
          onClose={() => {
            setAction(null)
            onSourcesChanged()
          }}
          onTaken={onImportFinished}
        />
      )}
    </Section>
  )
}

/**
 * Der Lauf, mit dem eine Karte beginnt: der aus der Liste, oder der aus der Antwort auf "Rückgängig machen", solange die
 * Liste keinen gleich neuen kennt. Laufnummern steigen.
 */
function newerRun(listed: ImportRun | null, started: ImportRun | undefined): ImportRun | null {
  if (started === undefined || (listed !== null && listed.id >= started.id)) return listed
  return started
}

function SourceHead({ source }: { source: Source }) {
  return (
    <div className="flex items-start gap-3">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-ink-700 bg-ink-850 text-accent-400">
        <Symbol name={source.app === 'sonarr' ? 'tv' : 'film'} className="h-5 w-5" />
      </span>
      <div className="min-w-0 flex-1">
        <h3 className="font-semibold wrap-anywhere text-mist-100">{source.name}</h3>
        <p className="text-xs break-all text-mist-500">{source.url}</p>
      </div>
    </div>
  )
}

function SourceCard({
  app,
  source,
  initialRun,
  version,
  onEdit,
  onRemove,
  onFinished,
  onAction,
}: {
  app: SourceApp
  source: Source
  /** Womit die Karte beginnt, meist `source.last_import`. Laeuft dieser Lauf, fragt sie ihn nach. */
  initialRun: ImportRun | null
  version: Version | undefined
  onEdit: () => void
  onRemove: () => void
  onFinished: () => void
  onAction: (kind: SourceAction) => void
}) {
  const { t, i18n } = useTranslation()
  const takeoverHintId = useId()
  const series = app === 'sonarr'
  const { run, error: startProblem, starting, start } = useImportRun(source.id, initialRun, onFinished)
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<SourceTestResult | null>(null)
  const [testProblem, setTestProblem] = useState<unknown>(null)
  const running = run?.status === 'running'

  async function test() {
    setTesting(true)
    setTested(null)
    setTestProblem(null)
    try {
      setTested(await sourcesApi.test({ source_id: source.id }))
    } catch (error) {
      setTestProblem(error)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/60 p-4 sm:p-5">
      <SourceHead source={source} />
      <div className="flex flex-wrap gap-1.5">
        {source.has_api_key ? (
          <Badge tone="ok">
            <Symbol name="check" className="h-3.5 w-3.5" />
            {t('import.sources.badgeKey')}
          </Badge>
        ) : (
          <Badge tone="bad">
            <Symbol name="alert" className="h-3.5 w-3.5" />
            {t('import.sources.badgeNoKey')}
          </Badge>
        )}
        {version && (
          <Badge tone="accent">
            <Symbol name="layers" className="h-3.5 w-3.5" />
            {t('import.sources.versionOf', { label: version.label })}
          </Badge>
        )}
      </div>
      <div className="flex flex-col gap-2" aria-live="polite">
        <RunState run={run} source={source} />
        {tested && (
          <div>
            <Badge tone="ok">
              <Symbol name="check" className="h-3.5 w-3.5" />
              {checkText(t, app, tested, i18n.language)}
            </Badge>
          </div>
        )}
        {testProblem !== null && <FormMessage>{errorText(t, testProblem)}</FormMessage>}
        {startProblem !== null && <FormMessage>{errorText(t, startProblem)}</FormMessage>}
      </div>
      <div className="mt-auto flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => void start()} loading={starting} disabled={running} aria-label={t('import.run.startLabel', { name: source.name })}>
            {!starting && <Symbol name="import" />}
            {t('import.run.start')}
          </Button>
          <Button variant="ghost" size="sm" loading={testing} onClick={() => void test()} aria-label={t('import.sources.testLabel', { name: source.name })}>
            {t('common.actions.test')}
          </Button>
          <Button variant="ghost" size="sm" onClick={onEdit} aria-label={t('import.sources.editLabel', { name: source.name })}>
            {t('common.actions.edit')}
          </Button>
          <Button variant="ghost" size="sm" onClick={onRemove} disabled={running} aria-label={t('import.sources.removeLabel', { name: source.name })}>
            {t('common.actions.remove')}
          </Button>
        </div>
        <div className="flex flex-col gap-2 border-t border-ink-700 pt-3">
          <p className="text-xs font-semibold text-mist-400">{series ? t('import.sources.series.fetchTitle') : t('import.sources.fetchTitle')}</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" onClick={() => onAction('indexers')} aria-label={t('import.indexers.openLabel', { name: source.name })}>
              <Symbol name="search" />
              {t('import.indexers.open')}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onAction('clients')} aria-label={t('import.clients.openLabel', { name: source.name })}>
              <Symbol name="download" />
              {t('import.clients.open')}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onAction('naming')} disabled={version === undefined} aria-label={t('import.naming.openLabel', { name: source.name })}>
              <Symbol name="folder" />
              {t('import.naming.open')}
            </Button>
            {/* Qualitaetsprofile, Custom Formats und Groessen holen: steht hier, wie die drei daneben. */}
            <Button variant="ghost" size="sm" onClick={() => onAction('quality')} aria-label={t('import.quality.openLabel', { name: source.name })}>
              <Symbol name="shield" />
              {t('import.quality.open')}
            </Button>
          </div>
        </div>
        <div className="flex flex-col items-start gap-1.5 border-t border-ink-700 pt-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onAction('takeover')}
            disabled={running}
            aria-label={t('import.takeover.openLabel', { name: source.name })}
            aria-describedby={takeoverHintId}
          >
            <Symbol name="swap" />
            {t('import.takeover.open')}
          </Button>
          <p id={takeoverHintId} className="text-xs text-mist-500">
            {series ? t('import.takeover.series.openHint') : t('import.takeover.openHint')}
          </p>
        </div>
      </div>
    </div>
  )
}

/**
 * Eine uebernommene Verbindung: wann, dass nexcrate diese App nicht mehr liest, und wofuer der Eintrag noch da ist.
 * Pruefen, importieren, aendern und holen gehen nicht mehr (der Server sagt 409 `source_taken_over`). Es bleiben
 * "Rückgängig machen" und "Eintrag entfernen". Bei Sonarr sagen die Saetze Serien statt Filme.
 */
function TakenOverCard({
  source,
  series,
  version,
  onUndo,
  onRemove,
  onAction,
}: {
  source: Source
  series: boolean
  version: Version | undefined
  onUndo: () => void
  onRemove: () => void
  /** Indexer und Download-Programme gibt auch eine uebernommene Verbindung heraus. */
  onAction: (kind: 'indexers' | 'clients') => void
}) {
  const { t, i18n } = useTranslation()
  const hintId = useId()
  return (
    <div className="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/60 p-4 sm:p-5">
      <SourceHead source={source} />
      <div className="flex flex-wrap gap-1.5">
        <Badge tone="ok">
          <Symbol name="check" className="h-3.5 w-3.5" />
          {t('import.sources.takenOverBadge')}
        </Badge>
        {version && (
          <Badge tone="accent">
            <Symbol name="layers" className="h-3.5 w-3.5" />
            {t('import.sources.versionOf', { label: version.label })}
          </Badge>
        )}
      </div>
      <p className="text-sm text-mist-300">
        {series
          ? t('import.sources.series.takenOver', { date: formatDate(source.taken_over_at ?? '', i18n.language) })
          : t('import.sources.takenOver', { date: formatDate(source.taken_over_at ?? '', i18n.language) })}
      </p>
      <FormMessage tone="info" role="note">{series ? t('import.sources.series.takenOverInfo') : t('import.sources.takenOverInfo')}</FormMessage>
      <div className="mt-auto flex flex-col items-start gap-1.5">
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" size="sm" onClick={onUndo} aria-label={t('import.takeover.undo.openLabel', { name: source.name })}>
            <Symbol name="back" />
            {t('import.takeover.undo.open')}
          </Button>
          <Button variant="ghost" size="sm" onClick={onRemove} aria-label={t('import.sources.removeRecordLabel', { name: source.name })} aria-describedby={hintId}>
            {t('import.sources.removeRecord')}
          </Button>
        </div>
        <p id={hintId} className="text-xs text-mist-500">
          {series ? t('import.sources.series.removeRecordHint') : t('import.sources.removeRecordHint')}
        </p>
      </div>
      <div className="flex flex-col gap-2 border-t border-ink-700 pt-3">
        <p className="text-xs font-semibold text-mist-400">{series ? t('import.sources.series.fetchTitle') : t('import.sources.fetchTitle')}</p>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" size="sm" onClick={() => onAction('indexers')} aria-label={t('import.indexers.openLabel', { name: source.name })}>
            <Symbol name="search" />
            {t('import.indexers.open')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => onAction('clients')} aria-label={t('import.clients.openLabel', { name: source.name })}>
            <Symbol name="download" />
            {t('import.clients.open')}
          </Button>
        </div>
      </div>
    </div>
  )
}

/**
 * Was mit dem letzten Import dieser Verbindung ist. Ein laufender wird nachgefragt, bis er fertig ist; bei Sonarr steht
 * dabei, wie weit er ist. Ein fertiger Lauf aus Sonarr sagt dazu, was sich zuordnen liess und was nicht.
 */
function RunState({ run, source }: { run: ImportRun | null; source: Source }) {
  const { t, i18n } = useTranslation()
  if (run === null) return <p className="text-sm text-mist-500">{t('import.run.never')}</p>
  if (run.status === 'running') {
    return (
      <p className="flex items-center gap-2 text-sm text-info-500">
        <Spinner />
        {runProgress(t, run, i18n.language) ?? t('import.run.running')}
      </p>
    )
  }
  if (run.status === 'failed') return <FormMessage>{runErrorText(t, run, source)}</FormMessage>
  return (
    <div className="flex items-start gap-2 text-sm">
      <Symbol name="check" className="mt-0.5 h-4 w-4 shrink-0 text-ok-500" />
      <div className="min-w-0">
        <p className="text-ok-500">{run.finished_at ? t('import.run.doneAt', { time: formatDateTime(run.finished_at, i18n.language) }) : t('import.history.done')}</p>
        {runLines(t, run, i18n.language).map((line) => (
          <p key={line} className="wrap-anywhere text-mist-400">
            {line}
          </p>
        ))}
      </div>
    </div>
  )
}

/**
 * Entfernen mit Rueckfrage. Laeuft gerade ein Import, sagt der Server 409 `import_running`. Bei einer uebernommenen
 * Verbindung verschwindet nur der Eintrag; das sagt der Dialog dann auch.
 */
function RemoveSourceDialog({ source, onClose, onRemoved }: { source: Source; onClose: () => void; onRemoved: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const takenOver = Boolean(source.taken_over_at)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await sourcesApi.remove(source.id)
      onRemoved()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  let text: string
  if (takenOver && source.app === 'sonarr') text = t('import.sources.series.removeRecordText', { name: source.name })
  else if (takenOver) text = t('import.sources.removeRecordText', { name: source.name })
  else if (source.app === 'sonarr') text = t('series.import.removeText', { name: source.name })
  else text = t('import.sources.remove.text', { name: source.name })

  return (
    <Dialog
      open
      title={takenOver ? t('import.sources.removeRecordTitle') : t('import.sources.remove.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('import.sources.remove.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm wrap-anywhere text-mist-300">{text}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
