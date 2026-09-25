import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { downloadClientsApi } from '../../api/downloadClients'
import type { RadarrDownloadClient, Source } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { Tile } from '../settings/parts'
import type { ClientTemplate } from './ClientDialog'
import { clientKindText } from './clientText'

type Listed = { items: RadarrDownloadClient[] | null; error: unknown }

/**
 * "Aus Radarr holen": die SABnzbd- und qBittorrent-Eintraege jeder Verbindung zu Radarr. Wer einen
 * waehlt, landet im Dialog zum Eintragen, mit Adresse und Einstellungen aus Radarr und leerem Geheimnis.
 */
export function RadarrClientsDialog({ sources, onPick, onClose }: { sources: Source[]; onPick: (template: ClientTemplate) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [lists, setLists] = useState<Record<number, Listed>>({})

  useEffect(() => {
    let current = true
    for (const source of sources) {
      downloadClientsApi.fromRadarr(source.id).then(
        (items) => {
          if (current) setLists((previous) => ({ ...previous, [source.id]: { items, error: null } }))
        },
        (error: unknown) => {
          if (current) setLists((previous) => ({ ...previous, [source.id]: { items: null, error } }))
        },
      )
    }
    return () => {
      current = false
    }
  }, [sources])

  return (
    <Dialog open wide title={t('settings.clients.radarr.title')} onClose={onClose}>
      <div className="flex flex-col gap-5">
        <p className="text-sm text-mist-400">{t('settings.clients.radarr.intro')}</p>
        {sources.map((source) => {
          const listed = lists[source.id]
          return (
            <section key={source.id} aria-label={source.name} className="flex flex-col gap-2">
              {sources.length > 1 && <h3 className="text-sm font-semibold wrap-anywhere text-mist-300">{source.name}</h3>}
              {listed === undefined ? (
                <p className="flex items-center gap-2 py-2 text-sm text-mist-500" role="status">
                  <Spinner />
                  {t('common.loading')}
                </p>
              ) : listed.items === null ? (
                <FormMessage>{errorText(t, listed.error)}</FormMessage>
              ) : listed.items.length === 0 ? (
                <p className="text-sm text-mist-500">{t('settings.clients.radarr.empty')}</p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {listed.items.map((item) => (
                    <li key={item.radarr_id} className="min-w-0">
                      <Tile>
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <h4 className="font-semibold wrap-anywhere text-mist-100">{item.name}</h4>
                            <p className="text-xs break-all text-mist-500">{item.url}</p>
                          </div>
                          {item.already_added ? (
                            <Badge tone="ok">
                              <Symbol name="check" className="h-3.5 w-3.5" />
                              {t('settings.clients.radarr.alreadyAdded')}
                            </Badge>
                          ) : (
                            <Button size="sm" onClick={() => onPick({ source: { id: source.id, name: source.name }, item })} aria-label={t('settings.clients.radarr.pickLabel', { name: item.name })}>
                              {t('settings.clients.radarr.pick')}
                            </Button>
                          )}
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge>{clientKindText(t, item.kind)}</Badge>
                          {!item.enabled && <Badge>{t('settings.clients.radarr.unusedInRadarr')}</Badge>}
                          {item.radarr_category !== null && item.radarr_category !== '' && (
                            <span className="text-xs wrap-anywhere text-mist-500">{t('settings.clients.radarr.category', { category: item.radarr_category })}</span>
                          )}
                        </div>
                      </Tile>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )
        })}
      </div>
    </Dialog>
  )
}
