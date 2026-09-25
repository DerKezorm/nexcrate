import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { sourcesApi } from '../../api/sources'
import { DEFAULT_DELAY, type DelayRule, type MediaKind, type Protocol, type Source, type SourceApp, type Version } from '../../api/types'
import { versionsApi } from '../../api/versions'
import { Dialog } from '../../components/Dialog'
import { Segmented } from '../../components/Segmented'
import { Button, Field, FormMessage, SelectField, Toggle } from '../../components/ui'
import { protocolName } from './delayText'
import { draftOf, ruleOnly, tagsOf, type TaggedDraft } from './taggedDelay'
import { TaggedDelayRules } from './TaggedDelayRules'

const PROTOCOLS: readonly Protocol[] = ['usenet', 'torrent']
/** Welche App die Regeln fuer welche Art haelt. */
const APP_OF_KIND: Record<MediaKind, SourceApp> = { movie: 'radarr', series: 'sonarr', album: 'lidarr' }
/** Wie im Server: 30 Tage. */
const MAX_MINUTES = 43200

/** Eine Zahl aus einem Textfeld: leer ist 0, alles andere muss eine ganze Zahl im Bereich sein. */
function wholeNumber(text: string, low: number, high: number): number | null {
  const trimmed = text.trim()
  if (trimmed === '') return 0
  if (!/^-?\d+$/.test(trimmed)) return null
  const value = Number(trimmed)
  return value >= low && value <= high ? value : null
}

/**
 * Die Verzoegerungsregel einer Fassung: das Delay-Profil der Arr-Programme. Erklaert wird jede Zeile, weil das Wort
 * allein nichts sagt: bevorzugtes Protokoll, ein Protokoll ganz aus, Wartezeit je Protokoll und die zwei Ausnahmen.
 * Nur die Automatik wartet; wer selbst auf "Laden" drueckt, wartet nie.
 */
export function DelayDialog({ version, onClose, onSaved }: { version: Version; onClose: () => void; onSaved: (saved: Version) => void }) {
  const { t } = useTranslation()
  // Die Regel der Fassung ohne die Regeln fuer Tags; die stehen in `tagged` darunter.
  const start: DelayRule = ruleOnly(version.delay ?? DEFAULT_DELAY)
  const [rule, setRule] = useState<DelayRule>(start)
  const [usenetMinutes, setUsenetMinutes] = useState(String(start.usenet_minutes))
  const [torrentMinutes, setTorrentMinutes] = useState(String(start.torrent_minutes))
  const [minimumScore, setMinimumScore] = useState(String(start.minimum_score))
  const [tagged, setTagged] = useState<TaggedDraft[]>(() => (version.delay?.tagged ?? []).map(draftOf))
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  // Einlesen aus Radarr, Sonarr oder Lidarr: die Verbindungen der passenden App, auch uebernommene.
  const [sources, setSources] = useState<Source[]>([])
  const [sourceId, setSourceId] = useState<number | null>(null)
  const [reading, setReading] = useState(false)
  const [readNote, setReadNote] = useState<string | null>(null)
  const app = APP_OF_KIND[version.kind]

  useEffect(() => {
    let current = true
    sourcesApi.list().then(
      (found) => {
        if (!current) return
        const fitting = Array.isArray(found) ? found.filter((source) => source.app === app && source.has_api_key) : []
        setSources(fitting)
        setSourceId(fitting.length > 0 ? fitting[0].id : null)
      },
      // Ohne Liste gibt es den Knopf nicht; der Dialog selbst geht auch so.
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [app])

  async function readFromSource() {
    if (sourceId === null || reading) return
    setReading(true)
    setProblem(null)
    setReadNote(null)
    try {
      const read = await sourcesApi.delay(sourceId)
      const notes: string[] = []
      if (read.rule !== null) {
        setRule(ruleOnly(read.rule))
        setUsenetMinutes(String(read.rule.usenet_minutes))
        setTorrentMinutes(String(read.rule.torrent_minutes))
        setMinimumScore(String(read.rule.minimum_score))
        notes.push(t('settings.delay.read.done'))
      } else {
        notes.push(t('settings.delay.read.nothing'))
      }
      const rules = read.tagged_rules ?? []
      if (rules.length > 0) {
        setTagged(rules.map(draftOf))
        notes.push(t('settings.delay.read.taggedCame', { count: rules.length }))
      }
      if (read.tagged > rules.length) notes.push(t('settings.delay.read.tagged', { count: read.tagged - rules.length }))
      if (read.shortened) notes.push(t('settings.delay.read.shortened'))
      setReadNote(notes.join(' '))
    } catch (error) {
      setProblem(errorText(t, error))
    } finally {
      setReading(false)
    }
  }

  const change = (values: Partial<DelayRule>) => setRule((current) => ({ ...current, ...values }))

  function submit(event: FormEvent) {
    event.preventDefault()
    void save()
  }

  async function save() {
    if (busy) return
    const usenet = wholeNumber(usenetMinutes, 0, MAX_MINUTES)
    const torrent = wholeNumber(torrentMinutes, 0, MAX_MINUTES)
    const score = wholeNumber(minimumScore, -1000000, 1000000)
    if (usenet === null || torrent === null) {
      setProblem(t('settings.delay.problem.minutes'))
      return
    }
    if (score === null) {
      setProblem(t('settings.delay.problem.score'))
      return
    }
    if (!rule.enable_usenet && !rule.enable_torrent) {
      setProblem(t('settings.delay.problem.bothOff'))
      return
    }
    const rules = []
    for (const draft of tagged) {
      const names = tagsOf(draft.tags)
      const usenetTagged = wholeNumber(draft.usenet, 0, MAX_MINUTES)
      const torrentTagged = wholeNumber(draft.torrent, 0, MAX_MINUTES)
      if (names.length === 0) {
        setProblem(t('settings.delay.problem.tags'))
        return
      }
      if (usenetTagged === null || torrentTagged === null) {
        setProblem(t('settings.delay.problem.minutes'))
        return
      }
      rules.push({ ...draft.rule, tags: names, usenet_minutes: usenetTagged, torrent_minutes: torrentTagged })
    }
    setBusy(true)
    setProblem(null)
    try {
      onSaved(
        await versionsApi.setDelay(version.id, {
          ...rule,
          usenet_minutes: usenet,
          torrent_minutes: torrent,
          minimum_score: score,
          tagged: rules,
        }),
      )
    } catch (error) {
      setProblem(errorText(t, error))
      setBusy(false)
    }
  }

  function reset() {
    setRule(DEFAULT_DELAY)
    setUsenetMinutes('0')
    setTorrentMinutes('0')
    setMinimumScore('0')
    setTagged([])
    setProblem(null)
  }

  function close() {
    if (!busy) onClose()
  }

  const waits = (wholeNumber(usenetMinutes, 0, MAX_MINUTES) ?? 0) > 0 || (wholeNumber(torrentMinutes, 0, MAX_MINUTES) ?? 0) > 0

  return (
    <Dialog
      open
      title={t('settings.delay.dialogTitle', { label: version.label })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={reset} disabled={busy}>
            {t('settings.delay.reset')}
          </Button>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void save()} loading={busy}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-5">
        <p className="text-sm text-mist-400">{t('settings.delay.intro')}</p>

        {sources.length > 0 && (
          <div className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-850 p-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <div className="min-w-0 flex-1">
                <SelectField label={t('settings.delay.read.label')} value={sourceId ?? ''} onChange={(event) => setSourceId(Number(event.target.value))}>
                  {sources.map((source) => (
                    <option key={source.id} value={source.id}>
                      {source.name}
                    </option>
                  ))}
                </SelectField>
              </div>
              <Button variant="ghost" onClick={() => void readFromSource()} loading={reading} disabled={busy}>
                {t('settings.delay.read.button')}
              </Button>
            </div>
            <p className="text-xs text-mist-500">{t('settings.delay.read.hint')}</p>
            {readNote !== null && <FormMessage tone="info">{readNote}</FormMessage>}
          </div>
        )}

        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium text-mist-300">{t('settings.delay.preferred')}</legend>
          <Segmented
            value={rule.preferred_protocol}
            options={PROTOCOLS}
            onChange={(next) => change({ preferred_protocol: next })}
            label={(option) => protocolName(t, option)}
            ariaLabel={t('settings.delay.preferred')}
          />
          <p className="text-xs text-mist-500">{t('settings.delay.preferredHint')}</p>
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-850 p-3">
            <Toggle label={t('settings.delay.useUsenet')} checked={rule.enable_usenet} onChange={(checked) => change({ enable_usenet: checked })} />
            <Field
              label={t('settings.delay.waitUsenet')}
              hint={t('settings.delay.waitHint')}
              value={usenetMinutes}
              onChange={(event) => setUsenetMinutes(event.target.value)}
              inputMode="numeric"
              autoComplete="off"
              disabled={!rule.enable_usenet}
            />
          </div>
          <div className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-850 p-3">
            <Toggle label={t('settings.delay.useTorrent')} checked={rule.enable_torrent} onChange={(checked) => change({ enable_torrent: checked })} />
            <Field
              label={t('settings.delay.waitTorrent')}
              hint={t('settings.delay.waitHint')}
              value={torrentMinutes}
              onChange={(event) => setTorrentMinutes(event.target.value)}
              inputMode="numeric"
              autoComplete="off"
              disabled={!rule.enable_torrent}
            />
          </div>
        </div>
        <p className="text-xs text-mist-500">{t('settings.delay.offHint')}</p>

        <fieldset className="flex flex-col gap-3" disabled={!waits}>
          <legend className="mb-2 text-sm font-medium text-mist-300">{t('settings.delay.exceptions')}</legend>
          {!waits && <p className="text-xs text-mist-500">{t('settings.delay.exceptionsIdle')}</p>}
          <Toggle
            label={t('settings.delay.bypassQuality')}
            hint={t('settings.delay.bypassQualityHint', { protocol: protocolName(t, rule.preferred_protocol) })}
            checked={rule.bypass_highest_quality}
            onChange={(checked) => change({ bypass_highest_quality: checked })}
            disabled={!waits}
          />
          {version.kind !== 'album' && (
          <Toggle
            label={t('settings.delay.bypassScore')}
            hint={t('settings.delay.bypassScoreHint', { protocol: protocolName(t, rule.preferred_protocol) })}
            checked={rule.bypass_score}
            onChange={(checked) => change({ bypass_score: checked })}
            disabled={!waits}
          />
          )}
          {version.kind !== 'album' && rule.bypass_score && (
            <Field
              label={t('settings.delay.minimumScore')}
              value={minimumScore}
              onChange={(event) => setMinimumScore(event.target.value)}
              inputMode="numeric"
              autoComplete="off"
              disabled={!waits}
            />
          )}
        </fieldset>

        <TaggedDelayRules drafts={tagged} onChange={setTagged} base={rule} disabled={busy} />

        <p className="text-xs text-mist-500">{t('settings.delay.manualNote')}</p>
        {problem && <FormMessage>{problem}</FormMessage>}
      </form>
    </Dialog>
  )
}
