import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useParams } from 'react-router-dom'

import { openApi } from '../api/outside'
import { PageLoading } from '../components/ui'
import { addressOfView } from './downloads/address'
import { addressOfFileTopic, FILES_TAB_PATH, MUSIC_VERSIONS_TAB_PATH, SERIES_VERSIONS_TAB_PATH, VERSIONS_TAB_PATH } from './settings/tabs'

/**
 * Die festen Spruenge von aussen (V4): `/open/title/{kind}/{ref}`, `/open/version/{id}`,
 * `/open/profile/{id}`, `/open/download/{id}`, `/open/problems`, `/open/recycle-bin`, `/open/calendar`. Sie bleiben,
 * wie die Seiten auch heissen; diese Seite loest sie mit der Sitzung auf und springt weiter.
 */
export function OpenPage() {
  const { t } = useTranslation()
  const params = useParams()
  const parts = (params['*'] ?? '').split('/').filter((part) => part !== '')
  const [target, setTarget] = useState<string | null>(null)
  const [missing, setMissing] = useState(false)

  const fixed = fixedTarget(parts)
  useEffect(() => {
    if (fixed !== null) return
    let current = true
    resolve(parts).then(
      (found) => {
        if (!current) return
        if (found === null) setMissing(true)
        else setTarget(found)
      },
      () => current && setMissing(true),
    )
    return () => {
      current = false
    }
    // Die Teile der Adresse sind der Schluessel; ein neues Array je Zeichnen darf nicht neu fragen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parts.join('/')])

  if (fixed !== null) return <Navigate to={fixed} replace />
  if (target !== null) return <Navigate to={target} replace />
  if (missing) {
    return (
      <div className="mx-auto flex max-w-lg flex-col gap-3 py-16 text-center" role="status">
        <p className="text-mist-300">{t('library.open.missing')}</p>
        <Link to="/" className="text-accent-400 hover:underline">
          {t('library.open.home')}
        </Link>
      </div>
    )
  }
  return <PageLoading />
}

function versionsPath(kind: string): string {
  if (kind === 'series') return SERIES_VERSIONS_TAB_PATH
  if (kind === 'album') return MUSIC_VERSIONS_TAB_PATH
  return VERSIONS_TAB_PATH
}

/** Spruenge, die nichts nachschlagen muessen. */
function fixedTarget(parts: string[]): string | null {
  if (parts.length === 1 && parts[0] === 'problems') return `/downloads?reiter=${addressOfView('problems')}`
  if (parts.length === 1 && parts[0] === 'recycle-bin') return `${FILES_TAB_PATH}&unter=${addressOfFileTopic('recycle')}`
  if (parts.length === 1 && parts[0] === 'calendar') return '/kalender'
  return null
}

async function resolve(parts: string[]): Promise<string | null> {
  const [what, first, second] = parts
  if (what === 'title' && first && second && parts.length === 3) {
    const found = await openApi.title(first, second)
    // Ein Kuenstler hat seine eigene Seite.
    if (typeof found.artist_id === 'number') return `/kuenstler/${found.artist_id}`
    return `/titel/${found.title_id}`
  }
  if ((what === 'version' || what === 'profile') && first && parts.length === 2) {
    const found = await openApi.version(first)
    return versionsPath(found.kind)
  }
  if (what === 'download' && first && parts.length === 2) {
    const found = await openApi.download(first)
    return found.view === 'problems' || found.view === 'history' ? `/downloads?reiter=${addressOfView(found.view)}` : '/downloads'
  }
  return null
}
