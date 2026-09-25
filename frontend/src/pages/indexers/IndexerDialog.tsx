import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { DAILY_LIMIT_MAX, DAILY_LIMIT_MIN, INDEXER_KINDS, indexersApi, isMovieCategory } from '../../api/indexers'
import type { Indexer, IndexerCaps, IndexerKind, IndexerSearchSettings, IndexerTest, IndexerTestResult, IndexerUpdate } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PasswordField } from '../../components/PasswordField'
import { Segmented } from '../../components/Segmented'
import { Button, Field, FormMessage, Toggle } from '../../components/ui'
import {
  animeCategoryOptions,
  categoryOptions,
  cleanAnimeCategories,
  cleanMusicCategories,
  cleanSeriesCategories,
  defaultAnimeCategories,
  defaultCategories,
  defaultMusicCategories,
  defaultSeriesCategories,
  hasOtherCategories,
  musicCategoryOptions,
  seriesCategoryOptions,
} from './categories'
import { kindText } from './indexerText'
import {
  hasSearchSettings,
  initialSearchSettings,
  languageOptions,
  MINIMUM_SEEDERS_MAX,
  PRIORITY_MAX,
  PRIORITY_MIN,
  readDailyLimit,
  readSearchSettings,
} from './searchSettings'

/** Die Kategorien als ein Text, unabhaengig von der Reihenfolge der Klicks. */
function categoriesKey(ids: readonly number[]): string {
  return [...ids].sort((a, b) => a - b).join(',')
}

/**
 * Ein Test und die Kategorien, fuer die er galt. Waehlt man danach andere, gilt sein Feed nicht mehr. `series` gilt
 * genauso fuer die Serienkategorien (S3); null, wenn der Test keine Serien geprueft hat.
 */
type Tested = { result: IndexerTestResult; categories: string; series: string | null }

/**
 * Der Plan nennt die Einstellungen der Suche und das Tageslimit nur bei PATCH. Hat der Server sie beim Anlegen nicht
 * uebernommen, gehen sie gleich hinterher. Scheitert das, ist der Indexer trotzdem angelegt; ein zweiter Klick auf
 * Speichern legte sonst einen zweiten an. Die Liste zeigt dann, was gilt.
 */
async function settleAfterCreate(saved: Indexer, settings: IndexerSearchSettings, dailyLimit: number | null): Promise<Indexer> {
  const change: IndexerUpdate = {}
  if (!hasSearchSettings(saved, settings)) Object.assign(change, settings)
  if (dailyLimit !== null && saved.daily_limit !== dailyLimit) change.daily_limit = dailyLimit
  if (Object.keys(change).length === 0) return saved
  try {
    return await indexersApi.update(saved.id, change)
  } catch {
    return saved
  }
}

/**
 * Einen Indexer eintragen (`indexer` null) oder aendern: Name, Adresse, Schluessel,
 * Kategorien und ob er aktiv ist. Die Art (Newznab oder Torznab) ist nur beim Eintragen waehlbar. Beim Aendern
 * steht sie als Text da, und der Server lehnt einen Wechsel ab. Ein neuer Indexer wird erst geprueft; danach stehen seine
 * Kategorien als Kaestchen da, die Filmkategorien vorausgewaehlt.
 *
 * Wie Radarr prueft nexcrate, ob der Feed in den gewaehlten Kategorien leer ist. Dann sagt der
 * Test es deutlich, und Speichern geht nur ueber "Trotzdem speichern" (`confirm_empty`). Der
 * Knopf steht unten neben "Speichern", damit er auch am Telefon ohne Scrollen zu sehen ist.
 *
 * ⚠️ Der API-Schluessel ist nie vorbelegt, auch nicht beim Bearbeiten: Der Server gibt ihn
 * nie heraus. Bleibt das Feld beim Bearbeiten leer, geht gar kein Schluessel hinaus und der
 * gespeicherte bleibt. Nach dem Speichern wird das Feld sofort geleert.
 *
 * Seit Schritt 2c darunter, was die Suche veraendert: Prioritaet, Mindestzahl an Seedern (nur
 * Torznab), die Sprachen hinter MULTi und die Titelsuche ohne Jahr. Diese Werte gehen bei jedem
 * Speichern vollstaendig mit.
 *
 * Seit Schritt 3c das Tageslimit: 1 bis 100000 oder leer. Es geht nur hinaus, wenn es sich geaendert hat; ein Server
 * von davor kennt es nicht und bekommt es so nie zu sehen.
 *
 * Seit S3 eine zweite Liste "Kategorien für Serien" wie bei Sonarr (Entscheidung 5). Nach dem ersten Test steht die
 * Vorgabe des Servers da, Anime gesperrt. Der Test prueft beide Listen und warnt, wenn die Serienkategorien leer sind,
 * sperrt aber nicht. Hinaus geht die Liste beim Anlegen nur, wenn sie von der Vorgabe abweicht, beim Aendern nur, wenn
 * sie sich geaendert hat; sonst bleibt alles wie bei einem Server von davor.
 */
export function IndexerDialog({ indexer, onClose, onSaved }: { indexer: Indexer | null; onClose: () => void; onSaved: (saved: Indexer) => void }) {
  const { t, i18n } = useTranslation()
  const [kind, setKind] = useState<IndexerKind>(indexer?.kind ?? 'newznab')
  const [name, setName] = useState(indexer?.name ?? '')
  const [url, setUrl] = useState(indexer?.url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [enabled, setEnabled] = useState(indexer?.enabled ?? true)
  const [categories, setCategories] = useState<number[]>(indexer?.categories ?? [])
  const [seriesCategories, setSeriesCategories] = useState<number[]>(indexer?.series_categories ?? [])
  // Ein gespeicherter Indexer eines Servers mit S3 hat seine Serienliste schon, ein neuer bekommt sie nach dem Test.
  const [seriesKnown, setSeriesKnown] = useState(indexer !== null && Array.isArray(indexer.series_categories))
  // Die Vorgabe, die der Server beim Anlegen ohne eigene Wahl nimmt. Nur eine Abweichung geht hinaus.
  const [seriesDefault, setSeriesDefault] = useState<string | null>(null)
  const [caps, setCaps] = useState<IndexerCaps | null>(indexer?.caps ?? null)
  // Seit M3: die Musikkategorien. Ohne eigene Wahl folgen sie der Vorgabe aus den caps; nur eine Wahl geht hinaus.
  const [musicCategories, setMusicCategories] = useState<number[]>(indexer?.music_categories ?? [])
  const [musicChosen, setMusicChosen] = useState(indexer?.music_categories_chosen ?? false)
  const [musicKnown, setMusicKnown] = useState(indexer !== null && Array.isArray(indexer.music_categories))
  // Seit Anime A3: die Kategorien, die eine Anime-Serie zusaetzlich fragt, gebaut wie die Musikkategorien.
  const [animeCategories, setAnimeCategories] = useState<number[]>(indexer?.anime_categories ?? [])
  const [animeChosen, setAnimeChosen] = useState(indexer?.anime_categories_chosen ?? false)
  // B3: nexcrate sucht eine Anime-Serie immer ueber die Durchzaehlung; das Standardformat kommt dazu, solange das hier an ist.
  const [animeStandard, setAnimeStandard] = useState(indexer?.anime_standard_format_search ?? true)
  const [animeKnown, setAnimeKnown] = useState(indexer !== null && Array.isArray(indexer.anime_categories))
  // Ein neuer Indexer nennt seine Kategorien erst nach dem Test. Ein gespeicherter hat sie schon.
  const [known, setKnown] = useState(indexer !== null)
  const [showAll, setShowAll] = useState(() => (indexer?.categories ?? []).some((id) => !isMovieCategory(id)))
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<Tested | null>(null)
  const [testProblem, setTestProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Welcher der beiden Knoepfe gerade speichert, damit sich nur einer dreht.
  const [confirming, setConfirming] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  // Der Server hat 409 `indexer_categories_empty` (Filmkategorien) oder `indexer_anime_categories_empty` (Anime B4)
  // gesagt: Jetzt gibt es "Trotzdem speichern", und zwar genau fuer das, was er bemaengelt hat.
  const [emptyRefused, setEmptyRefused] = useState<'movie' | 'anime' | null>(null)
  // Die leeren Filmkategorien sind schon bestaetigt; danach kann noch die Anime-Frage kommen.
  const [movieConfirmed, setMovieConfirmed] = useState(false)
  // Die Adresse hatte keinen Pfad, der Server hat /api angehaengt. Das Feld zeigt dann die gueltige.
  const [completedUrl, setCompletedUrl] = useState<string | null>(null)
  const [initial] = useState(() => initialSearchSettings(indexer))
  const [priority, setPriority] = useState(initial.priority)
  const [minimumSeeders, setMinimumSeeders] = useState(initial.minimumSeeders)
  const [multiLanguages, setMultiLanguages] = useState<string[]>(initial.multiLanguages)
  const [removeYear, setRemoveYear] = useState(initial.removeYear)
  const [dailyLimit, setDailyLimit] = useState(() => (typeof indexer?.daily_limit === 'number' ? String(indexer.daily_limit) : ''))

  /** Art, Adresse oder Schluessel geaendert: Der alte Test gilt nicht mehr. */
  function resetTest() {
    setTested(null)
    setTestProblem(null)
    setEmptyRefused(null)
    setMovieConfirmed(false)
    setCompletedUrl(null)
  }

  async function test() {
    resetTest()
    const cleanUrl = url.trim()
    const key = apiKey.trim()
    if (cleanUrl === '') return setTestProblem(t('indexers.form.testNeedsUrl'))
    // Beim ersten Test eines neuen Indexers gibt es noch keine Wahl. Dann prueft der Server die ueblichen.
    const chosen = known && categories.length > 0 ? [...categories].sort((a, b) => a - b) : undefined
    const chosenSeries = seriesKnown ? cleanSeriesCategories(caps, seriesCategories) : undefined
    const withCategories = {
      ...(chosen ? { categories: chosen } : {}),
      // Eine leere Serienliste prueft nichts: Serien ueberspringt nexcrate dann ohnehin.
      ...(chosenSeries && chosenSeries.length > 0 ? { series_categories: chosenSeries } : {}),
    }
    let body: IndexerTest
    if (indexer === null) body = { kind, url: cleanUrl, api_key: key, ...withCategories }
    // Beim Bearbeiten gehen die gespeicherte Art und die Kategorien aus dem Formular mit. Umstellen laesst sich die Art dort nicht.
    else body = key === '' ? { indexer_id: indexer.id, kind, url: cleanUrl, ...withCategories } : { indexer_id: indexer.id, kind, url: cleanUrl, api_key: key, ...withCategories }
    setTesting(true)
    try {
      const result = await indexersApi.test(body)
      // Wie in Radarr eingetragen, also ohne /api: Der Server hat es angehaengt, und so wird auch gespeichert.
      if (result.url && result.url !== cleanUrl) {
        setUrl(result.url)
        setCompletedUrl(result.url)
      }
      // Ohne eigene Wahl hat der Server den Feed mit seinen Standardkategorien geprueft. Genau die stehen dann als Haken da.
      const serverDefaults = result.default_categories && result.default_categories.length > 0 ? result.default_categories : null
      const next = chosen ?? serverDefaults ?? defaultCategories(result.caps)
      const seriesFromServer = Array.isArray(result.default_series_categories) ? result.default_series_categories : null
      const seriesDefaults = cleanSeriesCategories(result.caps, seriesFromServer ?? defaultSeriesCategories(result.caps))
      const nextSeries = chosenSeries ?? seriesDefaults
      setCaps(result.caps)
      if (!chosen) setCategories(next)
      // Ein Server von vor S3 schickt keine Vorgabe fuer Serien; dann bleibt die Serienliste weg.
      const seriesServer = seriesFromServer !== null || chosenSeries !== undefined
      if (!chosenSeries && seriesServer) setSeriesCategories(nextSeries)
      if (seriesServer) setSeriesDefault(categoriesKey(seriesDefaults))
      setKnown(true)
      setSeriesKnown(seriesServer)
      if (!musicChosen) setMusicCategories(result.default_music_categories ?? defaultMusicCategories(result.caps))
      setMusicKnown(true)
      if (!animeChosen) setAnimeCategories(result.default_anime_categories ?? defaultAnimeCategories(result.caps))
      setAnimeKnown(true)
      const probedSeries = typeof result.series_feed_items === 'number' ? categoriesKey(nextSeries) : null
      setTested({ result, categories: categoriesKey(next), series: probedSeries })
    } catch (error) {
      setTestProblem(errorText(t, error))
    } finally {
      setTesting(false)
    }
  }

  async function save(confirmMovie = false, confirmAnime = false) {
    if (busy) return
    const confirmEmpty = confirmMovie || movieConfirmed
    if (confirmMovie) setMovieConfirmed(true)
    const cleanName = name.trim()
    const cleanUrl = url.trim()
    const key = apiKey.trim()
    if (cleanName === '') return setProblem(t('indexers.form.missingName'))
    if (cleanUrl === '') return setProblem(t('indexers.form.missingUrl'))
    if (indexer === null && tested === null) return setProblem(t('indexers.form.testFirst'))
    if (categories.length === 0) return setProblem(t('indexers.form.missingCategories'))
    const settings = readSearchSettings(kind, { priority, minimumSeeders, multiLanguages, removeYear })
    if (settings === 'priority') return setProblem(t('indexers.form.priorityInvalid'))
    if (settings === 'minimumSeeders') return setProblem(t('indexers.form.minimumSeedersInvalid'))
    const limit = readDailyLimit(dailyLimit)
    if (limit === 'invalid') return setProblem(t('indexers.form.dailyLimitInvalid'))
    const sorted = [...categories].sort((a, b) => a - b)
    const cleanSeries = cleanSeriesCategories(caps, seriesCategories)

    setBusy(true)
    setConfirming(confirmMovie || confirmAnime)
    setProblem(null)
    try {
      let saved: Indexer
      if (indexer === null) {
        const created = await indexersApi.create({
          name: cleanName,
          kind,
          url: cleanUrl,
          api_key: key,
          categories: sorted,
          ...(seriesKnown && categoriesKey(cleanSeries) !== seriesDefault ? { series_categories: cleanSeries } : {}),
          ...(musicKnown && musicChosen ? { music_categories: cleanMusicCategories(musicCategories) } : {}),
          ...(animeKnown && animeChosen ? { anime_categories: cleanAnimeCategories(animeCategories) } : {}),
          ...(animeStandard ? {} : { anime_standard_format_search: false }),
          enabled,
          ...settings,
          ...(limit !== null ? { daily_limit: limit } : {}),
          ...(confirmEmpty ? { confirm_empty: true } : {}),
          ...(confirmAnime ? { confirm_anime_empty: true } : {}),
        })
        saved = await settleAfterCreate(created, settings, limit)
      } else {
        const change: IndexerUpdate = { name: cleanName, categories: sorted, enabled, ...settings }
        if (cleanUrl !== indexer.url) change.url = cleanUrl
        if (key !== '') change.api_key = key
        if (limit !== (indexer.daily_limit ?? null)) change.daily_limit = limit
        if (seriesKnown && categoriesKey(cleanSeries) !== categoriesKey(indexer.series_categories ?? [])) change.series_categories = cleanSeries
        if (musicKnown && musicChosen) {
          const cleanMusic = cleanMusicCategories(musicCategories)
          if (!indexer.music_categories_chosen || categoriesKey(cleanMusic) !== categoriesKey(indexer.music_categories ?? [])) change.music_categories = cleanMusic
        } else if (!musicChosen && indexer.music_categories_chosen) {
          change.music_categories_default = true
        }
        // Eine Antwort ohne das Feld gilt als an, sonst reist es bei jedem Speichern unnoetig mit.
        if (animeStandard !== (indexer.anime_standard_format_search ?? true)) {
          change.anime_standard_format_search = animeStandard
        }
        if (animeKnown && animeChosen) {
          const cleanAnime = cleanAnimeCategories(animeCategories)
          if (!indexer.anime_categories_chosen || categoriesKey(cleanAnime) !== categoriesKey(indexer.anime_categories ?? [])) change.anime_categories = cleanAnime
        } else if (!animeChosen && indexer.anime_categories_chosen) {
          change.anime_categories_default = true
        }
        if (confirmEmpty) change.confirm_empty = true
        if (confirmAnime) change.confirm_anime_empty = true
        saved = await indexersApi.update(indexer.id, change)
      }
      setApiKey('')
      onSaved(saved)
    } catch (error) {
      // indexer_url_invalid, indexer_no_categories, indexer_categories_empty und die Indexer-Codes kommen mit Text aus errors.json.
      setProblem(errorText(t, error))
      const code = error instanceof ApiError ? error.code : null
      setEmptyRefused(code === 'indexer_categories_empty' ? 'movie' : code === 'indexer_anime_categories_empty' ? 'anime' : null)
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void save()
  }

  function close() {
    if (busy) return
    setApiKey('')
    onClose()
  }

  function toggleCategory(id: number, on: boolean) {
    setEmptyRefused(null)
    setMovieConfirmed(false)
    setCategories((current) => (on ? [...current.filter((entry) => entry !== id), id] : current.filter((entry) => entry !== id)))
  }

  function toggleMusicCategory(id: number, on: boolean) {
    setMusicChosen(true)
    setMusicCategories((current) => (on ? [...current.filter((entry) => entry !== id), id] : current.filter((entry) => entry !== id)))
  }

  function musicToDefault() {
    setMusicChosen(false)
    setMusicCategories(defaultMusicCategories(caps))
  }

  function toggleAnimeCategory(id: number, on: boolean) {
    setAnimeChosen(true)
    setAnimeCategories((current) => (on ? [...current.filter((entry) => entry !== id), id] : current.filter((entry) => entry !== id)))
  }

  function animeToDefault() {
    setAnimeChosen(false)
    setAnimeCategories(defaultAnimeCategories(caps))
  }

  function toggleSeriesCategory(id: number, on: boolean) {
    setSeriesCategories((current) => (on ? [...current.filter((entry) => entry !== id), id] : current.filter((entry) => entry !== id)))
  }

  function toggleLanguage(code: string, on: boolean) {
    setMultiLanguages((current) => (on ? [...current.filter((entry) => entry !== code), code] : current.filter((entry) => entry !== code)))
  }

  const keyHint = indexer === null ? t('indexers.form.apiKeyHint') : indexer.has_api_key ? t('indexers.form.keySaved') : t('indexers.form.keyNone')
  const options = categoryOptions(t, caps, categories, showAll)
  // Auch Sprachen, die der Indexer schon hatte (etwa aus Radarr), bleiben zum Abwaehlen stehen.
  const languages = languageOptions(t, [...initial.multiLanguages, ...multiLanguages], i18n.language)
  // Was der letzte Test ueber den Feed sagt, solange die Kategorien dieselben sind.
  const feed = tested !== null && tested.categories === categoriesKey(categories) ? tested.result.feed_items : null
  const offerConfirm = emptyRefused !== null || feed === 0
  const seriesOptions = seriesCategoryOptions(t, caps, seriesCategories)
  const musicOptions = musicCategoryOptions(t, caps, musicCategories)
  const animeOptions = animeCategoryOptions(t, caps, animeCategories)
  const seriesFeed =
    tested !== null && tested.series !== null && tested.series === categoriesKey(cleanSeriesCategories(caps, seriesCategories)) ? (tested.result.series_feed_items ?? null) : null

  return (
    <Dialog
      open
      wide
      title={indexer ? t('indexers.form.dialogEdit', { name: indexer.name }) : t('indexers.form.dialogAdd')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          {offerConfirm && (
            <Button variant="danger" onClick={() => void (emptyRefused === 'anime' ? save(false, true) : save(true))} loading={busy && confirming} disabled={busy}>
              {t('indexers.form.confirmEmpty')}
            </Button>
          )}
          <Button onClick={() => void save()} loading={busy && !confirming} disabled={busy}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <p className="text-sm font-medium text-mist-300">{t('indexers.kind.label')}</p>
          {indexer === null ? (
            <Segmented
              value={kind}
              options={INDEXER_KINDS}
              onChange={(next) => {
                setKind(next)
                resetTest()
              }}
              label={(value) => kindText(t, value)}
              ariaLabel={t('indexers.kind.label')}
            />
          ) : (
            <p className="text-sm text-mist-100">{kindText(t, kind)}</p>
          )}
          <p className="text-xs text-mist-500">{kind === 'torznab' ? t('indexers.kind.torznabHint') : t('indexers.kind.newznabHint')}</p>
        </div>
        <Field
          label={t('indexers.form.name')}
          hint={t('indexers.form.nameHint')}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={100}
          autoComplete="off"
          autoFocus
        />
        <Field
          label={t('indexers.form.url')}
          hint={completedUrl !== null ? t('indexers.form.urlCompleted', { url: completedUrl }) : t('indexers.form.urlHint')}
          value={url}
          onChange={(event) => {
            setUrl(event.target.value)
            resetTest()
          }}
          inputMode="url"
          maxLength={2048}
          autoComplete="off"
          spellCheck={false}
          className="w-full min-w-0"
        />
        <PasswordField
          label={t('indexers.form.apiKey')}
          hint={keyHint}
          value={apiKey}
          onChange={(event) => {
            setApiKey(event.target.value)
            resetTest()
          }}
          autoComplete="new-password"
          maxLength={200}
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => void test()} loading={testing}>
            {t('common.actions.test')}
          </Button>
          {testing && (
            <span className="text-xs text-mist-500" role="status">
              {t('indexers.form.checking')}
            </span>
          )}
        </div>
        {feed !== null && feed > 0 && <FormMessage tone="ok">{t('indexers.form.feedOk')}</FormMessage>}
        {feed === 0 && <FormMessage>{t('indexers.form.feedEmpty')}</FormMessage>}
        {seriesFeed !== null && seriesFeed > 0 && <FormMessage tone="ok">{t('indexers.form.seriesFeedOk')}</FormMessage>}
        {seriesFeed === 0 && <FormMessage>{t('indexers.form.seriesFeedEmpty')}</FormMessage>}
        {tested && (
          <p className="text-sm text-mist-400">
            {tested.result.caps === null
              ? t('indexers.state.noCaps')
              : tested.result.caps.movie_search
                ? t('indexers.form.movieSearch')
                : t('indexers.form.noMovieSearch')}
            {tested.result.caps !== null && typeof tested.result.caps.tv_search === 'boolean' && (
              <> {tested.result.caps.tv_search ? t('indexers.form.seriesSearch') : t('indexers.form.noSeriesSearch')}</>
            )}
          </p>
        )}
        {testProblem && <FormMessage>{testProblem}</FormMessage>}

        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-1.5 text-sm font-medium text-mist-300">{t('indexers.form.categoriesMovies')}</legend>
          {!known ? (
            <p className="text-sm text-mist-500">{t('indexers.form.categoriesAfterTest')}</p>
          ) : (
            <>
              <p className="text-xs text-mist-500">{caps === null ? t('indexers.form.categoriesDefault') : t('indexers.form.categoriesHint')}</p>
              <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5 sm:grid-cols-[repeat(2,minmax(0,1fr))]">
                {options.map((option) => {
                  const checked = categories.includes(option.id)
                  return (
                    <li key={option.id} className="min-w-0">
                      <label
                        className={
                          'flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2 text-sm ' +
                          (checked ? 'border-accent-500/50 bg-accent-500/5' : 'border-ink-700 bg-ink-900/60')
                        }
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                          checked={checked}
                          onChange={(event) => toggleCategory(option.id, event.target.checked)}
                        />
                        <span className="min-w-0 flex-1 wrap-anywhere">
                          <span className="text-mist-100">{option.name}</span> <span className="text-xs text-mist-500 tabular-nums">{option.id}</span>
                        </span>
                      </label>
                    </li>
                  )
                })}
              </ul>
              {hasOtherCategories(caps) && (
                <div>
                  <Button variant="ghost" size="sm" onClick={() => setShowAll((current) => !current)}>
                    {showAll ? t('indexers.form.showMovies') : t('indexers.form.showAll')}
                  </Button>
                </div>
              )}
            </>
          )}
        </fieldset>

        {(seriesKnown || tested === null) && (
          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="mb-1.5 text-sm font-medium text-mist-300">{t('indexers.form.seriesCategories')}</legend>
            {!seriesKnown ? (
              <p className="text-sm text-mist-500">{t('indexers.form.seriesCategoriesAfterTest')}</p>
            ) : (
              <>
                <p className="text-xs text-mist-500">
                  {caps === null || !seriesOptions.some((option) => !option.anime) ? t('indexers.form.seriesCategoriesDefault') : t('indexers.form.seriesCategoriesHint')}
                </p>
                <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5 sm:grid-cols-[repeat(2,minmax(0,1fr))]">
                  {seriesOptions.map((option) => {
                    const checked = !option.anime && seriesCategories.includes(option.id)
                    return (
                      <li key={option.id} className="min-w-0">
                        <label
                          className={
                            'flex items-start gap-2.5 rounded-lg border px-3 py-2 text-sm ' +
                            (option.anime ? 'cursor-not-allowed border-ink-700 bg-ink-900/30 opacity-60 ' : 'cursor-pointer ') +
                            (checked ? 'border-accent-500/50 bg-accent-500/5' : option.anime ? '' : 'border-ink-700 bg-ink-900/60')
                          }
                        >
                          <input
                            type="checkbox"
                            className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                            checked={checked}
                            disabled={option.anime}
                            onChange={(event) => toggleSeriesCategory(option.id, event.target.checked)}
                          />
                          <span className="min-w-0 flex-1 wrap-anywhere">
                            <span className="text-mist-100">{option.name}</span> <span className="text-xs text-mist-500 tabular-nums">{option.id}</span>
                            {option.anime && <span className="block text-xs text-mist-500">{t('indexers.form.animeSeparately')}</span>}
                          </span>
                        </label>
                      </li>
                    )
                  })}
                </ul>
              </>
            )}
          </fieldset>
        )}

        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-1.5 text-sm font-medium text-mist-300">{t('indexers.form.animeCategories')}</legend>
          {!animeKnown ? (
            <p className="text-sm text-mist-500">{t('indexers.form.animeCategoriesAfterTest')}</p>
          ) : (
            <>
              <p className="text-xs text-mist-500">
                {animeChosen
                  ? t('indexers.form.animeCategoriesChosen')
                  : animeCategories.length === 0
                    ? t('indexers.form.animeCategoriesNone')
                    : t('indexers.form.animeCategoriesDefault')}
              </p>
              <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5 sm:grid-cols-[repeat(2,minmax(0,1fr))]">
                {animeOptions.map((option) => {
                  const checked = animeCategories.includes(option.id)
                  return (
                    <li key={option.id} className="min-w-0">
                      <label
                        className={
                          'flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2 text-sm ' +
                          (checked ? 'border-accent-500/50 bg-accent-500/5' : 'border-ink-700 bg-ink-900/60')
                        }
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                          checked={checked}
                          onChange={(event) => toggleAnimeCategory(option.id, event.target.checked)}
                        />
                        <span className="min-w-0 flex-1 wrap-anywhere">
                          <span className="text-mist-100">{option.name}</span> <span className="text-xs text-mist-500 tabular-nums">{option.id}</span>
                        </span>
                      </label>
                    </li>
                  )
                })}
              </ul>
              {animeChosen && (
                <div>
                  <Button variant="ghost" size="sm" onClick={animeToDefault}>
                    {t('indexers.form.animeCategoriesToDefault')}
                  </Button>
                </div>
              )}
            </>
          )}
          {/* B3: wie Sonarrs Anime Standard Format Search, nur ab Werk an, weil nexcrate bisher immer beides fragte. */}
          <label className="mt-1 flex cursor-pointer items-start gap-2.5 rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
              checked={animeStandard}
              onChange={(event) => setAnimeStandard(event.target.checked)}
            />
            <span className="min-w-0 flex-1">
              <span className="text-mist-100">{t('indexers.form.animeStandardFormat')}</span>
              <span className="mt-0.5 block text-xs text-mist-500">{t('indexers.form.animeStandardFormatHint')}</span>
            </span>
          </label>
        </fieldset>

        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-1.5 text-sm font-medium text-mist-300">{t('indexers.form.musicCategories')}</legend>
          {!musicKnown ? (
            <p className="text-sm text-mist-500">{t('indexers.form.musicCategoriesAfterTest')}</p>
          ) : (
            <>
              <p className="text-xs text-mist-500">{musicChosen ? t('indexers.form.musicCategoriesChosen') : t('indexers.form.musicCategoriesDefault')}</p>
              <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5 sm:grid-cols-[repeat(2,minmax(0,1fr))]">
                {musicOptions.map((option) => {
                  const checked = !option.leftOut && musicCategories.includes(option.id)
                  return (
                    <li key={option.id} className="min-w-0">
                      <label
                        className={
                          'flex items-start gap-2.5 rounded-lg border px-3 py-2 text-sm ' +
                          (option.leftOut ? 'cursor-not-allowed border-ink-700 bg-ink-900/30 opacity-60 ' : 'cursor-pointer ') +
                          (checked ? 'border-accent-500/50 bg-accent-500/5' : option.leftOut ? '' : 'border-ink-700 bg-ink-900/60')
                        }
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                          checked={checked}
                          disabled={option.leftOut}
                          onChange={(event) => toggleMusicCategory(option.id, event.target.checked)}
                        />
                        <span className="min-w-0 flex-1 wrap-anywhere">
                          <span className="text-mist-100">{option.name}</span> <span className="text-xs text-mist-500 tabular-nums">{option.id}</span>
                          {option.leftOut && <span className="block text-xs text-mist-500">{t('indexers.form.musicLeftOut')}</span>}
                        </span>
                      </label>
                    </li>
                  )
                })}
              </ul>
              {musicChosen && (
                <div>
                  <Button variant="ghost" size="sm" onClick={musicToDefault}>
                    {t('indexers.form.musicCategoriesToDefault')}
                  </Button>
                </div>
              )}
            </>
          )}
        </fieldset>

        <div className="flex flex-col gap-4 border-t border-ink-700 pt-4">
          <h3 className="text-sm font-semibold text-mist-200">{t('indexers.form.searchTitle')}</h3>
          <Field
            label={t('indexers.form.priority')}
            hint={t('indexers.form.priorityHint')}
            type="number"
            inputMode="numeric"
            min={PRIORITY_MIN}
            max={PRIORITY_MAX}
            step={1}
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
            className="w-28 tabular-nums"
          />
          <Field
            label={t('indexers.form.dailyLimit')}
            hint={t('indexers.form.dailyLimitHint')}
            type="number"
            inputMode="numeric"
            min={DAILY_LIMIT_MIN}
            max={DAILY_LIMIT_MAX}
            step={1}
            value={dailyLimit}
            onChange={(event) => setDailyLimit(event.target.value)}
            className="w-36 tabular-nums"
          />
          {kind === 'torznab' && (
            <Field
              label={t('indexers.form.minimumSeeders')}
              hint={t('indexers.form.minimumSeedersHint')}
              type="number"
              inputMode="numeric"
              min={0}
              max={MINIMUM_SEEDERS_MAX}
              step={1}
              value={minimumSeeders}
              onChange={(event) => setMinimumSeeders(event.target.value)}
              className="w-28 tabular-nums"
            />
          )}
          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="mb-1.5 text-sm font-medium text-mist-300">{t('indexers.form.multiLanguages')}</legend>
            <p className="text-xs text-mist-500">{t('indexers.form.multiLanguagesHint')}</p>
            <ul className="grid grid-cols-[repeat(2,minmax(0,1fr))] gap-1.5 sm:grid-cols-[repeat(3,minmax(0,1fr))]">
              {languages.map((option) => {
                const checked = multiLanguages.includes(option.code)
                return (
                  <li key={option.code} className="min-w-0">
                    <label
                      className={
                        'flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 text-sm ' +
                        (checked ? 'border-accent-500/50 bg-accent-500/5' : 'border-ink-700 bg-ink-900/60')
                      }
                    >
                      <input
                        type="checkbox"
                        className="h-4 w-4 shrink-0 accent-accent-500"
                        checked={checked}
                        onChange={(event) => toggleLanguage(option.code, event.target.checked)}
                      />
                      <span className="min-w-0 flex-1 wrap-anywhere text-mist-100">{option.name}</span>
                    </label>
                  </li>
                )
              })}
            </ul>
          </fieldset>
          <Toggle label={t('indexers.form.removeYear')} hint={t('indexers.form.removeYearHint')} checked={removeYear} onChange={setRemoveYear} />
        </div>

        <Toggle label={t('indexers.form.enabled')} hint={t('indexers.form.enabledHint')} checked={enabled} onChange={setEnabled} />
        {problem && <FormMessage>{problem}</FormMessage>}
      </form>
    </Dialog>
  )
}
