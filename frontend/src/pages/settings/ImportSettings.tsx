import { useCallback, useEffect, useMemo, useState } from 'react'

import { tmdbApi } from '../../api/tmdb'
import type { SourceApp } from '../../api/types'
import { ImportHistory } from '../import/ImportHistory'
import { SourceList } from '../import/SourceList'
import { useSources } from '../import/useSources'
import { useVersions } from '../versions/useVersions'

/**
 * Import aus Radarr oder Sonarr, ein Helfer fuer alle, die eine der beiden schon nutzen, kein Hauptweg. Je App ein
 * eigener Unterreiter. Der Ablauf: Verbindung eintragen und pruefen, Fassung waehlen oder gleich anlegen, importieren.
 * Danach liest nexcrate alle 15 Minuten von selbst neu ein. Bei Radarr lassen sich neben jeder Verbindung Indexer,
 * Download-Programme und die Benennung holen und die Verbindung uebernehmen. Sonarr braucht den TMDB-Token; fehlt er,
 * sagt es ein Hinweis.
 *
 * Die letzten Importe zeigen alle Laeufe, auch die der anderen App. Die Liste kennt dafuer alle Verbindungen.
 */
export function ImportSettings({ app }: { app: SourceApp }) {
  const { sources, error: sourcesError, reload: reloadSources } = useSources()
  const { versions, error: versionsError, reload: reloadVersions } = useVersions(app === 'sonarr' ? 'series' : 'movie')
  const [historyToken, setHistoryToken] = useState(0)
  const [tmdbMissing, setTmdbMissing] = useState(false)
  const own = useMemo(() => (sources === null ? null : sources.filter((source) => source.app === app)), [sources, app])

  useEffect(() => {
    if (app !== 'sonarr') return
    let current = true
    // Nur ein Hinweis: Scheitert die Frage, bleibt er weg. Den Fehler zeigt der Import selbst, falls er daran scheitert.
    tmdbApi.state().then(
      (state) => {
        if (current) setTmdbMissing(!state.configured)
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [app])

  const finished = useCallback(() => {
    reloadSources()
    reloadVersions()
    setHistoryToken((count) => count + 1)
  }, [reloadSources, reloadVersions])

  return (
    <div className="flex flex-col gap-4">
      <SourceList
        app={app}
        tmdbMissing={tmdbMissing}
        sources={{ items: own, error: sourcesError }}
        versions={{ items: versions, error: versionsError }}
        onSourcesChanged={reloadSources}
        onVersionsChanged={reloadVersions}
        onImportFinished={finished}
      />
      {sources !== null && own !== null && own.length > 0 && <ImportHistory sources={sources} refreshToken={historyToken} />}
    </div>
  )
}
