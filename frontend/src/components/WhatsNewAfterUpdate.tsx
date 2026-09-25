import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { whatsNewApi, type WhatsNewState } from '../api/whatsNew'
import { entryFor, latestVersion, unseen } from '../lib/whatsnew'
import { WhatsNewDialog } from './WhatsNewDialog'

/**
 * Nach einem Update einmal "Was ist neu" zeigen.
 *
 * ⚠️ Das Fenster geht sofort zu, der Vermerk an den Server folgt (Befund aus nexdeck: dort wartete jeder Ausgang auf
 * die Antwort, ohne Server liess sich das Fenster nicht schliessen). Scheitert der Vermerk, kommt es beim naechsten
 * Besuch wieder.
 */
export function WhatsNewAfterUpdate() {
  const { i18n } = useTranslation()
  const [state, setState] = useState<WhatsNewState | null>(null)
  const [closed, setClosed] = useState(false)

  useEffect(() => {
    let current = true
    whatsNewApi.read().then(
      (result) => {
        if (current) setState(result)
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [])

  if (closed || state === null) return null
  const version = latestVersion(state.version)
  if (version === null || !unseen(state.seen, version)) return null
  const entry = entryFor(version, i18n.language)
  if (entry === null) return null
  const shown = version

  function close() {
    setClosed(true)
    whatsNewApi.seen(shown).catch(() => undefined)
  }

  return <WhatsNewDialog version={version} entry={entry} onClose={close} />
}
