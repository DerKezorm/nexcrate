import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { indexersApi } from '../../api/indexers'
import { sourcesApi } from '../../api/sources'
import type { RadarrIndexer, Source } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PasswordField } from '../../components/PasswordField'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { kindText } from '../indexers/indexerText'
import { Tile } from '../settings/parts'

/**
 * "Indexer holen" neben einer Verbindung im Reiter Import. Radarr gibt alles heraus ausser dem Schluessel,
 * der kommt dort als `********`. Den traegt man hier ein; nexcrate prueft und legt an.
 *
 * Erst die Liste aus Radarr, dann fuer den gewaehlten Indexer der Schluessel.
 *
 * ⚠️ Das Feld ist immer leer und Pflicht: Radarr schickt jeden Schluessel nur als `********`.
 * Nach dem Hinzufuegen wird es sofort geleert. Liefert der Feed in Radarrs Kategorien nichts,
 * geht Hinzufuegen nur ueber "Trotzdem speichern", unten neben "Hinzufügen".
 *
 * Bei einer Verbindung zu Sonarr (S6, Entscheidung 14) sind die Kategorien Serienkategorien, und ein Indexer, den
 * nexcrate schon hat, bekommt sie nur ergaenzt: dafuer braucht es keinen Schluessel.
 *
 * Bei Lidarr (Musik-Abschluss) ebenso mit Musik-Kategorien. Eine uebernommene Verbindung gibt ihre Einstellungen
 * weiter heraus, nur ihre Bibliothek liest nexcrate nicht mehr.
 */
export function RadarrIndexerDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const series = source.app === 'sonarr'
  const music = source.app === 'lidarr'
  const [items, setItems] = useState<RadarrIndexer[] | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [token, setToken] = useState(0)
  const [picked, setPicked] = useState<RadarrIndexer | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [testing, setTesting] = useState(false)
  const [feed, setFeed] = useState<number | null>(null)
  const [testProblem, setTestProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [emptyRefused, setEmptyRefused] = useState(false)
  // Nur bei Sonarr: welcher vorhandene Indexer gerade die Serienkategorien bekommt, und was dabei schiefging.
  const [merging, setMerging] = useState<number | null>(null)
  const [mergeProblem, setMergeProblem] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    sourcesApi.indexers(source.id).then(
      (result) => {
        if (!current) return
        setItems(result)
        setLoadError(null)
      },
      (error: unknown) => current && setLoadError(error),
    )
    return () => {
      current = false
    }
  }, [source.id, token])

  function forgetKey() {
    setApiKey('')
    setFeed(null)
    setTestProblem(null)
    setProblem(null)
    setEmptyRefused(false)
  }

  function pick(item: RadarrIndexer) {
    forgetKey()
    setPicked(item)
  }

  function back() {
    if (busy) return
    forgetKey()
    setPicked(null)
  }

  function close() {
    if (busy) return
    setApiKey('')
    onClose()
  }

  async function test() {
    if (!picked) return
    setFeed(null)
    setTestProblem(null)
    const key = apiKey.trim()
    if (key === '') return setTestProblem(t('import.indexers.missingKey'))
    setTesting(true)
    try {
      // Sonarrs Kategorien sind Serienkategorien, Lidarrs Musik-Kategorien: Dann prueft der Server den Feed dort.
      const listed = picked.categories
      const chosen = listed.length === 0 ? {} : series ? { series_categories: listed } : music ? { music_categories: listed } : { categories: listed }
      const result = await indexersApi.test({ kind: picked.kind, url: picked.url, api_key: key, ...chosen })
      setFeed(series ? (result.series_feed_items ?? result.feed_items) : music ? (result.music_feed_items ?? result.feed_items) : result.feed_items)
    } catch (error) {
      setTestProblem(errorText(t, error))
    } finally {
      setTesting(false)
    }
  }

  /** Nur bei Sonarr und Lidarr: Ein vorhandener Indexer mit derselben Adresse bekommt die Kategorien dazu. */
  async function addCategories(item: RadarrIndexer) {
    if (merging !== null) return
    setMerging(item.radarr_indexer_id)
    setMergeProblem(null)
    try {
      const saved = await indexersApi.fromSource({ source_id: source.id, radarr_indexer_id: item.radarr_indexer_id, api_key: '' })
      notify(music ? t('import.indexers.music.categoriesAdded', { name: saved.name }) : t('import.indexers.series.categoriesAdded', { name: saved.name }))
      setToken((count) => count + 1)
    } catch (error) {
      setMergeProblem(errorText(t, error))
    } finally {
      setMerging(null)
    }
  }

  async function add(confirmEmpty = false) {
    if (!picked || busy) return
    const key = apiKey.trim()
    if (key === '') return setProblem(t('import.indexers.missingKey'))
    setBusy(true)
    setConfirming(confirmEmpty)
    setProblem(null)
    try {
      const saved = await indexersApi.fromSource({
        source_id: source.id,
        radarr_indexer_id: picked.radarr_indexer_id,
        api_key: key,
        ...(confirmEmpty ? { confirm_empty: true } : {}),
      })
      forgetKey()
      setBusy(false)
      setPicked(null)
      notify(t('import.indexers.added', { name: saved.name }))
      setToken((count) => count + 1)
    } catch (error) {
      setProblem(errorText(t, error))
      setEmptyRefused(error instanceof ApiError && error.code === 'indexer_categories_empty')
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void add()
  }

  const categoriesText = (item: RadarrIndexer) => {
    if (item.categories.length === 0) return t('import.indexers.noCategories')
    const list = item.categories.join(', ')
    if (music) return t('import.indexers.music.categories', { list })
    return series ? t('import.indexers.series.categories', { list }) : t('import.indexers.categories', { list })
  }
  const offerConfirm = emptyRefused || feed === 0
  const keyIntro = music ? t('import.indexers.music.keyIntro') : series ? t('import.indexers.series.keyIntro') : t('import.indexers.keyIntro')
  const keyHint = music ? t('import.indexers.music.apiKeyHint') : series ? t('import.indexers.series.apiKeyHint') : t('import.indexers.apiKeyHint')
  const emptyText = music ? t('import.indexers.music.empty') : series ? t('import.indexers.series.empty') : t('import.indexers.empty')
  const unusedText = music ? t('import.indexers.music.unused') : series ? t('import.indexers.series.unused') : t('import.indexers.unusedInRadarr')

  return (
    <Dialog
      open
      wide
      title={picked ? t('import.indexers.keyTitle', { name: picked.name }) : t('import.indexers.dialogTitle', { name: source.name })}
      onClose={close}
      footer={
        picked ? (
          <>
            <Button variant="ghost" onClick={back} disabled={busy}>
              <Symbol name="back" />
              {t('import.indexers.back')}
            </Button>
            {offerConfirm && (
              <Button variant="danger" onClick={() => void add(true)} loading={busy && confirming} disabled={busy}>
                {t('indexers.form.confirmEmpty')}
              </Button>
            )}
            <Button onClick={() => void add()} loading={busy && !confirming} disabled={busy}>
              {t('import.indexers.submit')}
            </Button>
          </>
        ) : undefined
      }
    >
      {picked ? (
        <form onSubmit={submit} noValidate className="flex flex-col gap-4">
          <Tile>
            <div className="min-w-0">
              <p className="font-semibold wrap-anywhere text-mist-100">{picked.name}</p>
              <p className="text-xs break-all text-mist-500">{picked.url}</p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge>{kindText(t, picked.kind)}</Badge>
              <span className="text-xs text-mist-500">{categoriesText(picked)}</span>
            </div>
          </Tile>
          <p className="text-sm text-mist-300">{keyIntro}</p>
          <PasswordField
            label={t('import.indexers.apiKey')}
            hint={keyHint}
            value={apiKey}
            onChange={(event) => {
              setApiKey(event.target.value)
              setFeed(null)
              setTestProblem(null)
              setEmptyRefused(false)
            }}
            autoComplete="new-password"
            maxLength={200}
            autoFocus
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="ghost" size="sm" onClick={() => void test()} loading={testing}>
              {t('common.actions.test')}
            </Button>
            {testing && (
              <span className="text-xs text-mist-500" role="status">
                {t('import.indexers.checking')}
              </span>
            )}
          </div>
          {feed !== null && feed > 0 && <FormMessage tone="ok">{t('indexers.form.feedOk')}</FormMessage>}
          {feed === 0 && <FormMessage>{t('indexers.form.feedEmpty')}</FormMessage>}
          {testProblem && <FormMessage>{testProblem}</FormMessage>}
          {problem && <FormMessage>{problem}</FormMessage>}
        </form>
      ) : loadError !== null ? (
        <FormMessage>{errorText(t, loadError)}</FormMessage>
      ) : items === null ? (
        <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
          <Spinner />
          {t('common.loading')}
        </p>
      ) : items.length === 0 ? (
        <p className="text-sm text-mist-500">{emptyText}</p>
      ) : (
        <div className="flex flex-col gap-2">
          {mergeProblem && <FormMessage>{mergeProblem}</FormMessage>}
          <ul className="flex flex-col gap-2">
            {items.map((item) => {
              // Nur bei Sonarr und Lidarr und nur mit Kategorien: Ohne welche gaebe es nichts zu ergaenzen.
              const canMerge = (series || music) && item.already_added && item.categories.length > 0
              return (
                <li key={item.radarr_indexer_id} className="min-w-0">
                  <Tile>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <h3 className="font-semibold wrap-anywhere text-mist-100">{item.name}</h3>
                        <p className="text-xs break-all text-mist-500">{item.url}</p>
                      </div>
                      {item.already_added ? (
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge tone="ok">
                            <Symbol name="check" className="h-3.5 w-3.5" />
                            {t('import.indexers.alreadyAdded')}
                          </Badge>
                          {canMerge && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => void addCategories(item)}
                              loading={merging === item.radarr_indexer_id}
                              disabled={merging !== null}
                              aria-label={music ? t('import.indexers.music.addCategoriesLabel', { name: item.name }) : t('import.indexers.series.addCategoriesLabel', { name: item.name })}
                            >
                              {music ? t('import.indexers.music.addCategories') : t('import.indexers.series.addCategories')}
                            </Button>
                          )}
                        </div>
                      ) : (
                        <Button size="sm" onClick={() => pick(item)} aria-label={t('import.indexers.pickLabel', { name: item.name })}>
                          {t('import.indexers.pick')}
                        </Button>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge>{kindText(t, item.kind)}</Badge>
                      {!item.enabled && <Badge>{unusedText}</Badge>}
                      <span className="text-xs text-mist-500">{categoriesText(item)}</span>
                    </div>
                    {canMerge && <p className="text-xs text-mist-500">{music ? t('import.indexers.music.addCategoriesHint') : t('import.indexers.series.addCategoriesHint')}</p>}
                  </Tile>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </Dialog>
  )
}
