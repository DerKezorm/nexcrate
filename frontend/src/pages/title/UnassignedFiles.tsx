import { type ReactNode, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { EpisodeChoice, NearbyEpisode, TitleDetail, TitleVersion, UnassignedFile } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import type { DeleteTarget } from './DeleteFilesDialog'
import { definitionIdOf, isFed, sourceCode } from './seriesText'
import { evidenceText, nameProposals, nearbyOptionText, openFiles, proposalText, safeProposals, unclearReasonText } from './unclearText'

/** Die Adresse des Abschnitts, zu der der Hinweis oben auf der Serienseite springt. */
export const UNASSIGNED_ANCHOR = 'nicht-zugeordnet'

type Props = {
  titleId: number
  files: readonly UnassignedFile[]
  versions: readonly TitleVersion[]
  /** Nach jeder Aenderung die Antwort des Servers, die Folgen enthaelt. */
  onChanged: (detail: TitleDetail) => void
  /** "In den Papierkorb" fuer eine Datei ohne Folge, unklar oder ausgelassen (entschieden am 25.09.2026). */
  onDeleteFiles?: (target: DeleteTarget) => void
}

/** Eine Fassung mit ihren Dateien ohne Folge. `version` fehlt, wenn die Fassung nicht mehr am Titel steht. */
type Group = { version: TitleVersion | null; files: UnassignedFile[] }

function groupsOf(files: readonly UnassignedFile[], versions: readonly TitleVersion[]): Group[] {
  const groups: Group[] = versions
    .map((version) => ({ version, files: files.filter((file) => definitionIdOf(version) === file.version_id) }))
    .filter((group) => group.files.length > 0)
  const known = new Set(groups.flatMap((group) => group.files.map((file) => file.id)))
  const rest = files.filter((file) => !known.has(file.id))
  return rest.length > 0 ? [...groups, { version: null, files: rest }] : groups
}

/**
 * Die Dateien einer Serie, die zu keiner Folge gehoeren (S6, Ue3, Entscheidungen 15 bis 17), ein Kasten je Fassung.
 *
 * Bei einer eigenen Fassung ist jede Datei eine Aufgabe: Pfad, Groesse, Qualitaet, was Sonarr oder der Name sagte, und
 * der Vorschlag als Satz mit "So zuordnen". Wer den Vorschlag nicht will, waehlt "Andere Folge" aus den Folgen ohne
 * Datei um diese Stelle (mehrere fuer eine Doppelfolge) oder sagt "Keine Folge". Ausgelassene Dateien stehen
 * zusammengeklappt darunter und lassen sich doch noch zuordnen.
 *
 * Bei einer Fassung, die eine Sonarr-Verbindung speist, bleibt es eine reine Liste: dort entscheidet Sonarr.
 */
export function UnassignedFiles({ titleId, files, versions, onChanged, onDeleteFiles }: Props) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  // Die Zeile, an der gerade gearbeitet wird, und der Fehler dazu. Der Schluessel ist `file-<id>` oder `version-<id>`.
  const [busy, setBusy] = useState<string | null>(null)
  const [problem, setProblem] = useState<{ key: string; error: unknown } | null>(null)
  // "Andere Folge": die offene Datei und die gewaehlten Folgen, null als noch nicht gewaehlt.
  const [picking, setPicking] = useState<number | null>(null)
  const [chosen, setChosen] = useState<(number | null)[]>([null])
  // "Alle Vorschlaege uebernehmen" mit Vorschlaegen aus Dateinamen: die Fassung, die nachfragt.
  const [confirming, setConfirming] = useState<number | null>(null)
  const groups = groupsOf(files, versions)

  if (groups.length === 0) return null

  async function run(key: string, call: () => Promise<TitleDetail>, message: string | ((detail: TitleDetail) => string)) {
    if (busy !== null) return
    setBusy(key)
    setProblem(null)
    try {
      const detail = await call()
      onChanged(detail)
      setPicking(null)
      notify(typeof message === 'string' ? message : message(detail))
    } catch (error) {
      setProblem({ key, error })
    } finally {
      setBusy(null)
    }
  }

  /** Nimmt alle Vorschlaege einer Fassung und meldet, wie viele der Server wirklich zugeordnet hat. */
  function takeAll(versionId: number, fromNames: boolean) {
    setConfirming(null)
    const before = openFiles(files, versionId)
    void run(`version-${versionId}`, () => libraryApi.takeFileProposals(titleId, versionId, fromNames), (detail) => {
      const taken = Math.max(0, before - openFiles(detail.series?.unassigned_files ?? [], versionId))
      return taken > 0
        ? t('series.unassigned.proposalsDone', { count: taken, value: formatNumber(taken, language) })
        : t('series.unassigned.proposalsNone')
    })
  }

  function openPicker(file: UnassignedFile) {
    setProblem(null)
    setPicking(file.id)
    setChosen([file.proposal?.episode_ids[0] ?? null])
  }

  return (
    <Section title={t('series.unassigned.title')}>
      <div className="flex flex-col gap-6">
        {groups.map((group) => {
          const version = group.version
          const label = version?.label ?? t('series.unassigned.someVersion')
          const name = version?.source_name || label
          const fed = version === null || isFed(version)
          const open = group.files.filter((file) => file.left_out !== true)
          const leftOut = group.files.filter((file) => file.left_out === true)
          const safe = safeProposals(group.files)
          const named = nameProposals(group.files)
          const proposals = safe + named
          const versionId = group.files[0].version_id
          return (
            <div key={version?.id ?? 'rest'} className="flex min-w-0 flex-col gap-3 border-t border-ink-700/60 pt-5 first:border-t-0 first:pt-0">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 flex-col gap-1">
                  <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold wrap-anywhere text-mist-100">
                    <Symbol name="layers" className="h-4 w-4 shrink-0 text-accent-400" />
                    {label}
                  </h3>
                  <p className="max-w-3xl text-sm text-mist-500">{fed ? t('series.unassigned.intro', { name }) : t('series.unassigned.ownIntro')}</p>
                </div>
                {!fed && proposals > 0 && (
                  <Button
                    size="sm"
                    aria-label={t('series.unassigned.proposalsLabel', { label })}
                    loading={busy === `version-${versionId}`}
                    disabled={busy !== null}
                    onClick={() => (named > 0 ? setConfirming(versionId) : takeAll(versionId, false))}
                  >
                    <Symbol name="check" />
                    {t('series.unassigned.proposals')}
                  </Button>
                )}
              </div>

              <ul aria-label={t('series.unassigned.filesLabel', { label })} className="flex flex-col">
                {open.map((file) => (
                  <li key={file.id} className="flex min-w-0 flex-col gap-2 border-t border-ink-700/60 py-3 first:border-t-0 first:pt-0">
                    <FileFacts file={file} fed={fed} label={version?.label ?? null} name={name} sourceName={version?.source_name ?? null} />
                    {!fed && (
                      <>
                        {file.proposal && <p className="text-sm wrap-anywhere text-mist-100">{proposalText(t, file, version?.source_name ?? null, language)}</p>}
                        {(file.occupied ?? []).length > 0 && (
                          <p className="text-sm wrap-anywhere text-mist-100">
                            {t('series.unassigned.occupied', {
                              episodes: (file.occupied ?? []).map((episode) => nearbyOptionText(t, episode, language)).join(' · '),
                              path: (file.occupied ?? []).map((episode) => episode.file.relative_path).join(' · '),
                            })}
                          </p>
                        )}
                        <div className="flex flex-wrap items-center gap-2">
                          {(file.occupied ?? []).length > 0 && (
                            <Button
                              size="sm"
                              aria-label={t('series.unassigned.insteadLabel', { path: file.relative_path })}
                              loading={busy === `file-${file.id}` && picking === null}
                              disabled={busy !== null}
                              onClick={() =>
                                void run(
                                  `file-${file.id}`,
                                  () => libraryApi.assignFile(titleId, file.version_id, file.id, (file.occupied ?? []).map((episode) => episode.id), true),
                                  t('series.unassigned.replaced'),
                                )
                              }
                            >
                              <Symbol name="swap" />
                              {t('series.unassigned.instead')}
                            </Button>
                          )}
                          {file.proposal && (
                            <Button
                              size="sm"
                              aria-label={t('series.unassigned.assignLabel', { path: file.relative_path })}
                              loading={busy === `file-${file.id}` && picking === null}
                              disabled={busy !== null}
                              onClick={() =>
                                void run(
                                  `file-${file.id}`,
                                  () => libraryApi.assignFile(titleId, file.version_id, file.id, file.proposal?.episode_ids ?? []),
                                  t('series.unassigned.assigned'),
                                )
                              }
                            >
                              {t('series.unassigned.assign')}
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={t('series.unassigned.otherLabel', { path: file.relative_path })}
                            disabled={busy !== null}
                            onClick={() => (picking === file.id ? setPicking(null) : openPicker(file))}
                          >
                            <Symbol name="swap" />
                            {t('series.unassigned.other')}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={t('series.unassigned.noneLabel', { path: file.relative_path })}
                            disabled={busy !== null}
                            onClick={() => void run(`file-${file.id}`, () => libraryApi.leaveOutFile(titleId, file.version_id, file.id, true), t('series.unassigned.leftOut'))}
                          >
                            <Symbol name="close" />
                            {t('series.unassigned.none')}
                          </Button>
                          {onDeleteFiles && (
                            <ToBin file={file} label={label} disabled={busy !== null} onDelete={onDeleteFiles} />
                          )}
                        </div>
                        {picking === file.id && (
                          <Picker
                            file={file}
                            chosen={chosen}
                            busy={busy !== null}
                            loadAll={() => libraryApi.episodeChoices(titleId, file.version_id)}
                            onChange={setChosen}
                            onCancel={() => setPicking(null)}
                            onSave={(ids, replace) =>
                              void run(
                                `file-${file.id}`,
                                () => libraryApi.assignFile(titleId, file.version_id, file.id, ids, replace),
                                replace ? t('series.unassigned.replaced') : t('series.unassigned.assigned'),
                              )
                            }
                          />
                        )}
                      </>
                    )}
                    {problem?.key === `file-${file.id}` && <FormMessage>{errorText(t, problem.error)}</FormMessage>}
                  </li>
                ))}
              </ul>

              {!fed && leftOut.length > 0 && (
                <LeftOutFiles
                  files={leftOut}
                  busy={busy}
                  problem={problem}
                  onBack={(file) => void run(`file-${file.id}`, () => libraryApi.leaveOutFile(titleId, file.version_id, file.id, false), t('series.unassigned.backIn'))}
                  toBin={onDeleteFiles ? (file) => <ToBin file={file} label={label} disabled={busy !== null} onDelete={onDeleteFiles} /> : undefined}
                />
              )}
              {problem?.key === `version-${versionId}` && <FormMessage>{errorText(t, problem.error)}</FormMessage>}
              <Dialog
                open={confirming === versionId}
                title={t('series.unassigned.confirmTitle')}
                onClose={() => setConfirming(null)}
                footer={
                  <>
                    <Button variant="ghost" onClick={() => setConfirming(null)}>
                      {t('common.actions.cancel')}
                    </Button>
                    <Button onClick={() => takeAll(versionId, true)}>
                      {t('series.unassigned.confirmTake', { count: proposals, value: formatNumber(proposals, language) })}
                    </Button>
                  </>
                }
              >
                <div className="flex flex-col gap-3 text-sm text-mist-300">
                  <p>{t('series.unassigned.confirmNames', { count: named, value: formatNumber(named, language) })}</p>
                  {safe > 0 && <p>{t('series.unassigned.confirmSafe', { count: safe, value: formatNumber(safe, language) })}</p>}
                </div>
              </Dialog>
            </div>
          )
        })}
      </div>
    </Section>
  )
}

/** Pfad, Groesse, Qualitaet und was Sonarr oder der Name sagte. Bei einer Fassung aus Sonarr auch deren Nummern. */
function FileFacts({ file, fed, label, name, sourceName }: { file: UnassignedFile; fed: boolean; label: string | null; name: string; sourceName: string | null }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const code = sourceCode(file.source_numbers)
  const facts = fed
    ? [label, code !== null ? t('series.unassigned.numbers', { name, code }) : t('series.unassigned.noNumbers', { name }), file.quality, file.size_bytes ? sizeText(t, file.size_bytes, language) : null]
    : [file.quality, file.size_bytes ? sizeText(t, file.size_bytes, language) : null]
  // Eine uebernommene Fassung hat keinen Namen der Verbindung mehr; dann heisst es schlicht Sonarr.
  const evidence = fed ? null : evidenceText(t, file, sourceName, language)
  const reason = fed ? null : unclearReasonText(t, file.read_as?.reason)
  const shown = facts.filter((part): part is string => typeof part === 'string' && part !== '')

  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{file.relative_path}</span>
      {shown.length > 0 && <span className="text-xs wrap-anywhere text-mist-500">{shown.join(' · ')}</span>}
      {evidence !== null && <span className="text-sm wrap-anywhere text-mist-300">{evidence}</span>}
      {reason !== null && <span className="text-sm wrap-anywhere text-mist-400">{reason}</span>}
    </div>
  )
}

/**
 * "Andere Folge": eine Auswahl je Folge aus den Folgen ohne Datei um diese Stelle, mehrere fuer eine Doppelfolge.
 * Seit 18.09.2026 auf Wunsch jede Folge der Serie, auch eine mit Datei: die wird dann ersetzt und steht danach unter
 * "Nicht zugeordnet". Vorher bot die Auswahl bei einer Serie, deren Folgen alle eine (falsche) Datei hatten, nichts an.
 */
function Picker({
  file,
  chosen,
  busy,
  loadAll,
  onChange,
  onCancel,
  onSave,
}: {
  file: UnassignedFile
  chosen: (number | null)[]
  busy: boolean
  loadAll: () => Promise<EpisodeChoice[]>
  onChange: (chosen: (number | null)[]) => void
  onCancel: () => void
  onSave: (episodeIds: number[], replace: boolean) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const nearby: NearbyEpisode[] = Array.isArray(file.nearby) ? file.nearby : []
  const [all, setAll] = useState<EpisodeChoice[] | null>(null)
  const [wantAll, setWantAll] = useState(nearby.length === 0)
  const [loadProblem, setLoadProblem] = useState<unknown>(null)
  const ids = chosen.filter((id): id is number => id !== null)
  const twice = ids.length !== new Set(ids).size

  // Einmal je geoeffneter Auswahl: `loadAll` entsteht bei jedem Zeichnen neu und darf keine zweite Anfrage ausloesen.
  const asked = useRef(false)
  useEffect(() => {
    if (!wantAll || asked.current) return
    asked.current = true
    loadAll().then(setAll, setLoadProblem)
  }, [wantAll, loadAll])

  const options: EpisodeChoice[] = wantAll ? (all ?? []) : nearby.map((episode) => ({ ...episode, file: null }))
  // Folgen, die schon eine andere Datei haben: Speichern ersetzt sie.
  const replacing = options.filter((episode) => ids.includes(episode.id) && episode.file !== null && episode.file.id !== file.id)
  const seasons = [...new Set(options.map((episode) => episode.season))]

  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
      {!wantAll && nearby.length === 0 && <p className="text-sm text-mist-400">{t('series.unassigned.noNearby')}</p>}
      {wantAll && all === null && loadProblem === null && <p className="text-sm text-mist-400">{t('common.loading')}</p>}
      {loadProblem !== null && <FormMessage>{errorText(t, loadProblem)}</FormMessage>}
      {options.length > 0 && chosen.map((selected, index) => (
        <div key={index} className="flex min-w-0 items-center gap-2">
          <select
            aria-label={t('series.unassigned.pickLabel', { path: file.relative_path })}
            value={selected === null ? '' : String(selected)}
            onChange={(event) => {
              const next = [...chosen]
              next[index] = event.target.value === '' ? null : Number(event.target.value)
              onChange(next)
            }}
            className="min-w-0 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
          >
            <option value="">{t('series.unassigned.pickNone')}</option>
            {seasons.map((season) => (
              <optgroup key={season} label={season === 0 ? t('series.unassigned.specialsGroup') : t('series.unassigned.seasonGroup', { season })}>
                {options
                  .filter((episode) => episode.season === season)
                  .map((episode) => (
                    <option key={episode.id} value={episode.id}>
                      {episode.file !== null && episode.file.id !== file.id
                        ? t('series.unassigned.optionTaken', { episode: nearbyOptionText(t, episode, language) })
                        : nearbyOptionText(t, episode, language)}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
          {chosen.length > 1 && (
            <Button
              size="sm"
              variant="ghost"
              aria-label={t('series.unassigned.removeLabel', { path: file.relative_path })}
              onClick={() => onChange(chosen.filter((_value, position) => position !== index))}
            >
              <Symbol name="close" />
            </Button>
          )}
        </div>
      ))}
      {replacing.length > 0 && (
        <p className="text-sm wrap-anywhere text-accent-400">
          {t('series.unassigned.willReplace', { path: replacing.map((episode) => episode.file?.relative_path ?? '').join(' · ') })}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" loading={busy} disabled={busy || ids.length === 0 || twice} onClick={() => onSave(ids, replacing.length > 0)}>
          {t('series.unassigned.save')}
        </Button>
        {!wantAll && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setWantAll(true)}>
            {t('series.unassigned.showAll')}
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => onChange([...chosen, null])} aria-label={t('series.unassigned.moreLabel', { path: file.relative_path })}>
          <Symbol name="plus" />
          {t('series.unassigned.more')}
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
          {t('common.actions.cancel')}
        </Button>
      </div>
      {twice && <FormMessage>{t('series.unassigned.twice')}</FormMessage>}
    </div>
  )
}

/** "In den Papierkorb" fuer eine Datei ohne Folge; fragt ueber denselben Dialog wie das Loeschen an einer Folge. */
function ToBin({ file, label, disabled, onDelete }: { file: UnassignedFile; label: string; disabled: boolean; onDelete: (target: DeleteTarget) => void }) {
  const { t } = useTranslation()
  return (
    <Button
      size="sm"
      variant="ghost"
      aria-label={t('series.unassigned.toBinLabel', { path: file.relative_path })}
      disabled={disabled}
      onClick={() => onDelete({ kind: 'unclear', versionId: file.version_id, label, fileId: file.id, fileName: file.relative_path })}
    >
      <Symbol name="trash" />
      {t('series.unassigned.toBin')}
    </Button>
  )
}

/** Die ausgelassenen Dateien, zugeklappt. Sie zaehlen nicht als unklar, lassen sich aber doch noch zuordnen. */
function LeftOutFiles({
  files,
  busy,
  problem,
  onBack,
  toBin,
}: {
  files: readonly UnassignedFile[]
  busy: string | null
  problem: { key: string; error: unknown } | null
  onBack: (file: UnassignedFile) => void
  toBin?: (file: UnassignedFile) => ReactNode
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [open, setOpen] = useState(false)

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {/* ⚠️ Eine eigene Klasse ersetzt die Groesse des Zeichens: ohne h-4 w-4 fuellte der Pfeil die ganze Breite (18.09.2026). */}
      <Button size="sm" variant="ghost" className="self-start" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Symbol name="chevronDown" className={`h-4 w-4 ${open ? 'rotate-180' : ''}`} />
        {open ? t('series.unassigned.leftOutHide') : t('series.unassigned.leftOutShow', { count: files.length, value: formatNumber(files.length, language) })}
      </Button>
      {open && (
        <ul aria-label={t('series.unassigned.leftOutTitle')} className="flex flex-col">
          {files.map((file) => (
            <li key={file.id} className="flex min-w-0 flex-col gap-1.5 border-t border-ink-700/60 py-3 first:border-t-0 first:pt-0">
              <span className="font-mono text-xs leading-5 wrap-anywhere text-mist-500">{file.relative_path}</span>
              <div className="flex flex-wrap items-center gap-2">
                <Badge>{t('series.unassigned.leftOutTag')}</Badge>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={t('series.unassigned.assignAgainLabel', { path: file.relative_path })}
                  loading={busy === `file-${file.id}`}
                  disabled={busy !== null}
                  onClick={() => onBack(file)}
                >
                  {t('series.unassigned.assignAgain')}
                </Button>
                {toBin?.(file)}
              </div>
              {problem?.key === `file-${file.id}` && <FormMessage>{errorText(t, problem.error)}</FormMessage>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * Der Hinweis oben auf der Serienseite: Wie viele Dateien zu keiner Folge gehoeren, mit einem Sprung zum Abschnitt
 * "Nicht zugeordnet". Der steht ganz unten unter allen Staffeln, und am 18.09.2026 hat ihn der Besitzer dort nicht gefunden.
 */
export function UnassignedJump({ files, versions }: { files: readonly UnassignedFile[]; versions: readonly TitleVersion[] }) {
  const { t, i18n } = useTranslation()
  const own = new Set(versions.filter((version) => !isFed(version)).map((version) => definitionIdOf(version)))
  const count = files.filter((file) => file.left_out !== true && own.has(file.version_id)).length
  if (count === 0) return null
  return (
    <div className="flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-xl border border-accent-500/40 bg-accent-500/5 p-4">
      <p className="flex min-w-0 items-start gap-2 text-sm text-mist-200">
        <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
        <span className="min-w-0 wrap-anywhere">{t('series.unassigned.jump', { count, value: formatNumber(count, i18n.language) })}</span>
      </p>
      <Button
        size="sm"
        onClick={() => document.getElementById(UNASSIGNED_ANCHOR)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
      >
        <Symbol name="chevronDown" />
        {t('series.unassigned.jumpButton')}
      </Button>
    </div>
  )
}
