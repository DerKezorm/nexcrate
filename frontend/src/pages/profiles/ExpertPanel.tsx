import type { TFunction } from 'i18next'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { errorText } from '../../api/client'
import { expertApi } from '../../api/expert'
import type { AnimeTrashFamily, ExpertProfile, ExpertProfileBody, LanguageEntry } from '../../api/types'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { languageNameOf } from '../../lib/media'
import { FORMATS_TAB_PATH, QUALITY_TAB_PATH } from '../settings/tabs'
import {
  SEVERAL_LANGUAGES,
  bodyToSave,
  entryItems,
  entryLabel,
  fixedCutoff,
  merged,
  missingQualities,
  moved,
  placed,
  requiredLanguage,
  split,
  withRequiredLanguage,
  type QualityEntry,
} from './expertProfile'
import { languageName } from './profileText'

/** TRaSHs Anime-Profile unter ihren eigenen Namen; das sind Eigennamen der Leitfaeden, keine Texte zum Uebersetzen. */
const TRASH_ANIME: Record<AnimeTrashFamily, { file: string; name: string }> = {
  standard: { file: 'anime-remux-1080p', name: '[Anime] Remux-1080p' },
  german: { file: 'german-anime-hd-bluray-web', name: '[German] Anime HD Bluray + WEB' },
}

const INPUT = 'rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none'

/** Die Namen, die der Assistent nennt, wo er sie kennt; sonst der des Browsers fuer den Code. */
function languageLabelOf(t: TFunction, code: string, language: string): string {
  const known = languageName(t, code)
  return known === code.toUpperCase() ? languageNameOf(code, language) || known : known
}

/**
 * Ein Profil von Hand, wie Radarrs Qualitaetsprofil: die Qualitaeten in ihrer Reihenfolge mit Haken und
 * Gruppen, der Cutoff, die Sprache, die Punkte je Custom Format, Mindestpunkte und Upgrade-bis-Punkte. Steht als Teil im
 * Profil-Fenster (`ProfileDialog`), nicht mehr als eigenes Fenster an einer Fassung.
 *
 * Die Groessen je Qualitaet und die Formate selbst stehen unter Einstellungen → Qualitaet; hier stehen nur die
 * Verweise darauf mit ihren Punkten. So aendert ein Format sich an einer Stelle und gilt in jedem Profil.
 *
 * ⚠️ Der Assistent bleibt daneben. Speichert man dort, ist das Profil wieder seines und alles hier ist ueberschrieben.
 */
export function ExpertPanel({ profileId, onDone }: { profileId: number; onDone: () => void }) {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [profile, setProfile] = useState<ExpertProfile | null>(null)
  const [entries, setEntries] = useState<QualityEntry[]>([])
  const [cutoff, setCutoff] = useState<string | null>(null)
  const [scores, setScores] = useState<Record<number, number>>({})
  const [numbers, setNumbers] = useState({ min_score: 0, upgrade_until: 0, min_upgrade_step: 1, upgrades_allowed: true })
  const [languages, setLanguages] = useState<LanguageEntry[]>([])
  const [filter, setFilter] = useState('')
  // B2: Serienprofile haben einen zweiten Regelsatz fuer Anime. Bearbeitet wird immer einer; der andere liegt
  // solange hier und kommt beim Speichern wieder dazu.
  const [branch, setBranch] = useState<'main' | 'anime'>('main')
  const [stashed, setStashed] = useState<ExpertProfileBody | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  // B2b: welches Anime-Profil von TRaSH der Knopf nimmt; leer heisst passend zu den Sprachen, wie der Server es waehlt.
  const [trashFamily, setTrashFamily] = useState<AnimeTrashFamily | ''>('')

  /** Einen Regelsatz in die Felder legen. */
  const show = useCallback((body: ExpertProfileBody) => {
    setEntries((body.qualities ?? []) as QualityEntry[])
    setCutoff(typeof body.cutoff === 'string' ? body.cutoff : null)
    setScores(Object.fromEntries((body.formats ?? []).map((entry) => [entry.format_id, entry.score])))
    setLanguages(body.languages ?? [])
    setNumbers({
      min_score: body.min_score ?? 0,
      upgrade_until: body.upgrade_until ?? 0,
      min_upgrade_step: body.min_upgrade_step ?? 1,
      upgrades_allowed: body.upgrades_allowed !== false,
    })
  }, [])

  useEffect(() => {
    let dropped = false
    expertApi
      .ofProfile(profileId)
      .then((read) => {
        if (dropped) return
        const body = read.expert
        setProfile(read)
        setStashed(body.anime ?? null)
        setBranch('main')
        show(body)
      })
      .catch((error: unknown) => {
        if (!dropped) setLoadError(error)
      })
    return () => {
      dropped = true
    }
  }, [profileId, show])

  const missing = useMemo(() => (profile === null ? [] : missingQualities(entries, profile.qualities_available)), [entries, profile])
  const shown = useMemo(() => {
    const text = filter.trim().toLowerCase()
    const rows = profile?.formats_available ?? []
    return text === '' ? rows : rows.filter((row) => row.name.toLowerCase().includes(text))
  }, [filter, profile])
  const scored = Object.values(scores).filter((score) => score !== 0).length

  // Die Originalsprache zuerst, wie der Server sie schickt; der Rest nach dem Namen, unter dem man ihn sucht.
  const languageOptions = useMemo(() => {
    const codes = profile?.languages_available ?? []
    const name = (code: string) => languageLabelOf(t, code, i18n.language)
    const rest = codes.filter((code) => code !== 'original').sort((left, right) => name(left).localeCompare(name(right), i18n.language))
    return codes.includes('original') ? ['original', ...rest] : rest
  }, [profile, i18n.language, t])
  const languageLabel = (code: string) => languageLabelOf(t, code, i18n.language)

  // Ziehen: was gerade gezogen wird und worueber es haengt, als Stelle in `entries`. Die Pfeile bleiben der Weg
  // fuer die Tastatur und fuer Geraete, die kein Ziehen kennen.
  const [dragging, setDragging] = useState<number | null>(null)
  const [over, setOver] = useState<number | null>(null)

  function drop(target: number) {
    if (dragging !== null) change(placed(entries, dragging, target))
    setDragging(null)
    setOver(null)
  }

  function change(next: QualityEntry[]) {
    setEntries(next)
    setCutoff((before) => fixedCutoff(next, before))
  }

  function toggle(index: number) {
    change(entries.map((entry, position) => (position === index ? { ...entry, allowed: !entry.allowed } : entry)))
  }

  /** Was gerade in den Feldern steht, als Regelsatz. */
  function edited(): ExpertProfileBody {
    const base = (branch === 'anime' ? (profile?.expert.anime ?? profile?.expert) : profile?.expert) as ExpertProfileBody
    return bodyToSave({ ...base, ...numbers, languages }, entries, cutoff, scores)
  }

  /** Zum anderen Regelsatz wechseln: der bearbeitete wird beiseitegelegt, der andere kommt in die Felder. */
  function switchTo(wanted: 'main' | 'anime') {
    if (profile === null || wanted === branch) return
    const keep = edited()
    const next = stashed
    setStashed(keep)
    setBranch(wanted)
    show(next ?? keep)
  }

  /** Anime-Regeln anlegen (als Kopie des Profils) oder wieder entfernen. */
  function toggleAnime() {
    if (profile === null) return
    if (branch === 'anime') {
      // Der Zweig geht weg; das Profil selbst kommt zurueck in die Felder.
      const main = stashed ?? profile.expert
      setStashed(null)
      setBranch('main')
      show(main)
      return
    }
    if (stashed !== null) {
      switchTo('anime')
      return
    }
    const copy = edited()
    setStashed(copy)
    setBranch('anime')
    show(copy)
  }

  /**
   * B2b: den Anime-Zweig aus TRaSHs Anime-Profil fuellen. Der Server legt fehlende Formate an, speichert das Profil
   * aber nicht; der gefuellte Zweig kommt in die Felder und wird erst mit Speichern uebernommen.
   */
  async function fillFromTrash() {
    if (profile === null || busy) return
    setBusy(true)
    setProblem(null)
    try {
      const filled = await expertApi.animeFromTrash(profileId, trashFamily === '' ? null : trashFamily)
      // Der gefuellte Zweig wird die Grundlage der Anime-Felder, damit Ziel-Aufloesung und Sprachregel mitkommen.
      setProfile({ ...profile, formats_available: filled.formats_available, expert: { ...profile.expert, anime: filled.anime } })
      if (branch === 'main') {
        setStashed(edited())
        setBranch('anime')
      }
      show(filled.anime)
      const name = Object.values(TRASH_ANIME).find((entry) => entry.file === filled.trash_profile)?.name ?? filled.trash_profile
      notify(
        filled.formats_created > 0
          ? t('profiles.expert.anime.filledCreated', { profile: name, count: filled.formats_created })
          : t('profiles.expert.anime.filled', { profile: name }),
      )
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    if (profile === null || busy) return
    setBusy(true)
    setProblem(null)
    const here = edited()
    const other = stashed
    // Ohne Anime-Zweig geht genau das hinaus, was hereinkam: das Feld bleibt weg, statt als null mitzureisen.
    const main = branch === 'anime' ? (other ?? here) : here
    const anime = branch === 'anime' ? here : other
    const body: ExpertProfileBody = anime === null ? main : { ...main, anime }
    try {
      await expertApi.saveByProfile(profileId, body)
      notify(t('profiles.expert.saved'))
      onDone()
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {profile === null ? (
        loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : (
          <p className="flex items-center gap-2 text-sm text-mist-500">
            <Spinner /> {t('profiles.expert.loading')}
          </p>
        )
      ) : (
        <div className="flex flex-col gap-5">
          {profile.mode === 'wizard' && <FormMessage tone="info">{t('profiles.expert.fromWizard')}</FormMessage>}
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}

          {/* B2: Serien haben einen zweiten Regelsatz fuer Anime. Ohne ihn zaehlt fuer Anime das Profil selbst. */}
          {profile.kind === 'series' && (
            <section className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-mist-200">{t('profiles.expert.anime.title')}</span>
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  {stashed !== null || branch === 'anime' ? (
                    <>
                      <Button size="sm" variant={branch === 'main' ? 'primary' : 'ghost'} onClick={() => switchTo('main')} disabled={busy}>
                        {t('profiles.expert.anime.forSeries')}
                      </Button>
                      <Button size="sm" variant={branch === 'anime' ? 'primary' : 'ghost'} onClick={() => switchTo('anime')} disabled={busy}>
                        {t('profiles.expert.anime.forAnime')}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={toggleAnime} disabled={busy || branch !== 'anime'}>
                        {t('profiles.expert.anime.remove')}
                      </Button>
                    </>
                  ) : (
                    <Button size="sm" variant="ghost" onClick={toggleAnime} disabled={busy}>
                      {t('profiles.expert.anime.add')}
                    </Button>
                  )}
                </div>
              </div>
              <p className="text-xs text-mist-500">
                {branch === 'anime' ? t('profiles.expert.anime.editing') : stashed !== null ? t('profiles.expert.anime.exists') : t('profiles.expert.anime.none')}
              </p>
              {/* B2b: aus TRaSHs Anime-Profil fuellen, samt fehlender Formate. */}
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 text-xs text-mist-400">
                  {t('profiles.expert.anime.trashChoice')}
                  <select value={trashFamily} onChange={(event) => setTrashFamily(event.target.value as AnimeTrashFamily | '')} className={INPUT} disabled={busy}>
                    <option value="">{t('profiles.expert.anime.trashAuto')}</option>
                    <option value="standard">{TRASH_ANIME.standard.name}</option>
                    <option value="german">{TRASH_ANIME.german.name}</option>
                  </select>
                </label>
                <Button size="sm" variant="ghost" onClick={() => void fillFromTrash()} disabled={busy}>
                  {t('profiles.expert.anime.fromTrash')}
                </Button>
              </div>
              <p className="text-xs text-mist-500">{t('profiles.expert.anime.fromTrashHint')}</p>
            </section>
          )}

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-mist-200">{t('profiles.expert.qualitiesTitle')}</h3>
            <p className="text-xs text-mist-500">{t('profiles.expert.qualitiesHint')}</p>
            <ul className="flex flex-col gap-1.5">
              {/* Die beste Qualitaet steht oben, wie in Radarr; im Profil selbst steht die schwaechste zuerst. */}
              {entries
                .map((entry, index) => ({ entry, index }))
                .reverse()
                .map(({ entry, index }) => (
                  <li
                    key={`${entryLabel(entry)}-${index}`}
                    draggable
                    onDragStart={(event) => {
                      // Firefox zieht nur, was Daten mitbringt.
                      event.dataTransfer?.setData('text/plain', entryLabel(entry))
                      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
                      setDragging(index)
                    }}
                    onDragOver={(event) => {
                      if (dragging === null) return
                      event.preventDefault()
                      if (over !== index) setOver(index)
                    }}
                    onDrop={(event) => {
                      event.preventDefault()
                      drop(index)
                    }}
                    onDragEnd={() => {
                      setDragging(null)
                      setOver(null)
                    }}
                    data-dragging={dragging === index || undefined}
                    data-over={(over === index && dragging !== index) || undefined}
                    className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2 data-[dragging]:opacity-50 data-[over]:border-accent-500"
                  >
                    <span aria-hidden="true" title={t('profiles.expert.drag')} className="cursor-grab text-mist-500 select-none">
                      {'⠿'}
                    </span>
                    <label className="flex min-w-0 flex-1 items-center gap-2">
                      <input type="checkbox" checked={entry.allowed} onChange={() => toggle(index)} className="accent-accent-500" aria-label={t('profiles.expert.allowed', { quality: entryLabel(entry) })} />
                      <span className="min-w-0 font-mono text-xs wrap-anywhere text-mist-200">{entryItems(entry).join(', ')}</span>
                      {entry.group !== undefined && <Badge tone="info">{t('profiles.expert.sameWorth')}</Badge>}
                    </label>
                    <span className="flex items-center gap-1">
                      <Button size="sm" variant="ghost" onClick={() => change(moved(entries, index, 1))} disabled={index === entries.length - 1} aria-label={t('profiles.expert.up', { quality: entryLabel(entry) })}>
                        {'↑'}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => change(moved(entries, index, -1))} disabled={index === 0} aria-label={t('profiles.expert.down', { quality: entryLabel(entry) })}>
                        {'↓'}
                      </Button>
                      {entry.group === undefined ? (
                        <Button size="sm" variant="ghost" onClick={() => change(merged(entries, index - 1))} disabled={index === 0} aria-label={t('profiles.expert.merge', { quality: entryLabel(entry) })}>
                          {t('profiles.expert.mergeShort')}
                        </Button>
                      ) : (
                        <Button size="sm" variant="ghost" onClick={() => change(split(entries, index))} aria-label={t('profiles.expert.split', { quality: entryLabel(entry) })}>
                          {t('profiles.expert.splitShort')}
                        </Button>
                      )}
                    </span>
                  </li>
                ))}
            </ul>
            {missing.length > 0 && (
              <p className="text-xs text-mist-500">
                {t('profiles.expert.missing', { qualities: missing.join(', ') })}{' '}
                <Button size="sm" variant="ghost" onClick={() => change([...missing.map((name) => ({ name, allowed: false })), ...entries])}>
                  {t('profiles.expert.addMissing')}
                </Button>
              </p>
            )}
          </section>

          <section className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-[repeat(2,minmax(0,1fr))]">
            <div className="flex flex-col gap-1.5">
              {/* Der Hinweis steht neben dem Feld, nicht darin: Sonst hiesse das Feld fuer Vorleseprogramme Feld plus Hinweis. */}
              <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
                {t('profiles.expert.cutoff')}
                <select value={cutoff ?? ''} onChange={(event) => setCutoff(event.target.value === '' ? null : event.target.value)} className={INPUT}>
                  {entries
                    .filter((entry) => entry.allowed)
                    .map((entry) => (
                      <option key={entryLabel(entry)} value={entryLabel(entry)}>
                        {entryItems(entry).join(', ')}
                      </option>
                    ))}
                </select>
              </label>
              <p className="text-xs text-mist-500">{t('profiles.expert.cutoffHint')}</p>
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
                {t('profiles.expert.language')}
                <select value={requiredLanguage(languages)} onChange={(event) => setLanguages(withRequiredLanguage(languages, event.target.value))} className={INPUT}>
                  <option value="">{t('profiles.expert.languageAny')}</option>
                  {requiredLanguage(languages) === SEVERAL_LANGUAGES && (
                    <option value={SEVERAL_LANGUAGES}>
                      {t('profiles.expert.languageSeveral', {
                        languages: languages
                          .filter((entry) => entry.role === 'required')
                          .map((entry) => languageLabel(entry.code))
                          .join(', '),
                      })}
                    </option>
                  )}
                  {languageOptions.map((code) => (
                    <option key={code} value={code}>
                      {languageLabel(code)}
                    </option>
                  ))}
                </select>
              </label>
              <p className="text-xs text-mist-500">{t('profiles.expert.languageHint')}</p>
            </div>
            <NumberField
              label={t('profiles.expert.minScore')}
              hint={t('profiles.expert.minScoreHint')}
              value={numbers.min_score}
              onChange={(value) => setNumbers({ ...numbers, min_score: value })}
            />
            <NumberField
              label={t('profiles.expert.upgradeUntil')}
              hint={t('profiles.expert.upgradeUntilHint')}
              value={numbers.upgrade_until}
              onChange={(value) => setNumbers({ ...numbers, upgrade_until: value })}
            />
            <NumberField
              label={t('profiles.expert.step')}
              hint={t('profiles.expert.stepHint')}
              value={numbers.min_upgrade_step}
              least={0}
              onChange={(value) => setNumbers({ ...numbers, min_upgrade_step: value })}
            />
            <label className="flex items-center gap-2 text-sm text-mist-300">
              <input type="checkbox" checked={numbers.upgrades_allowed} onChange={(event) => setNumbers({ ...numbers, upgrades_allowed: event.target.checked })} className="accent-accent-500" />
              {t('profiles.expert.upgradesAllowed')}
            </label>
          </section>

          <section className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-mist-200">{t('profiles.expert.formatsTitle', { count: scored })}</h3>
              <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={t('profiles.expert.filter')} aria-label={t('profiles.expert.filter')} className={INPUT + ' w-48'} />
            </div>
            <p className="text-xs text-mist-500">
              {t('profiles.expert.formatsHint')}{' '}
              <Link to={FORMATS_TAB_PATH} className="text-accent-400 hover:underline">
                {t('profiles.expert.toFormats')}
              </Link>
            </p>
            <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto rounded-xl border border-ink-700 p-2">
              {shown.map((format) => (
                <li key={format.id} className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 text-sm wrap-anywhere text-mist-300">{format.name}</span>
                  {format.origin === 'own' && <Badge tone="accent">{t('profiles.expert.ownFormat')}</Badge>}
                  <input
                    type="number"
                    value={scores[format.id] ?? 0}
                    onChange={(event) => setScores({ ...scores, [format.id]: Number(event.target.value) })}
                    aria-label={t('profiles.expert.scoreOf', { name: format.name })}
                    className={INPUT + ' w-24'}
                  />
                </li>
              ))}
            </ul>
            <p className="text-xs text-mist-500">
              {t('profiles.expert.sizesHint')}{' '}
              <Link to={QUALITY_TAB_PATH} className="text-accent-400 hover:underline">
                {t('profiles.expert.toSizes')}
              </Link>
            </p>
          </section>
          <div className="flex flex-wrap items-center gap-3 border-t border-ink-700 pt-4">
            <Button onClick={() => void save()} disabled={cutoff === null || busy} loading={busy}>
              {t('profiles.expert.save')}
            </Button>
            {cutoff === null && <span className="text-xs text-mist-500">{t('profiles.expert.needsQuality')}</span>}
          </div>
        </div>
      )}
    </div>
  )
}

/** Eine Zahl mit Beschriftung und Hinweis daneben. */
function NumberField({ label, hint, value, onChange, least }: { label: string; hint: string; value: number; onChange: (value: number) => void; least?: number }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
        {label}
        <input type="number" min={least} value={value} onChange={(event) => onChange(Number(event.target.value))} className={INPUT} />
      </label>
      <p className="text-xs text-mist-500">{hint}</p>
    </div>
  )
}
