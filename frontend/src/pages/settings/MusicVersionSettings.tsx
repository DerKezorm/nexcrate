import { type FormEvent, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { CountriesOut } from '../../api/types'
import { Button, Field, FormMessage, Section, Spinner, Switch } from '../../components/ui'
import { countryText } from '../../lib/country'
import { formatNumber } from '../../lib/format'
import { MusicChecker } from '../checker/MusicChecker'
import { MusicProfileCard } from '../profiles/MusicProfile'
import { useVersions } from '../versions/useVersions'
import { DelayRow } from '../versions/DelayRow'
import { Tile, TileHeader } from './parts'

/** Die Laenderliste als Text, wie der Besitzer sie tippt: Kennungen mit Leerzeichen oder Komma dazwischen. */
function codesOf(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((item) => item.trim().toUpperCase())
    .filter((item) => item !== '')
}

/** Die Laender fuer den Gleichstand der Zielausgabe (Entscheidung 28): sichtbar und aenderbar, ab Werk aus der Sprache. */
function CountryOrder() {
  const { t, i18n } = useTranslation()
  const [state, setState] = useState<CountriesOut | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    const abort = new AbortController()
    musicApi.countries(abort.signal).then(
      (result) => {
        // Eine Antwort ohne Liste (aelterer Server) zeigt das Feld nicht, statt die Seite zu kippen.
        if (!result || !Array.isArray(result.default)) return
        setState(result)
        setText((result.countries ?? result.default).join(' '))
      },
      (error: unknown) => setProblem(error),
    )
    return () => abort.abort()
  }, [])

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    setSaved(false)
    try {
      const result = await musicApi.changeCountries(codesOf(text))
      setState(result)
      setText((result.countries ?? result.default).join(' '))
      setSaved(true)
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  async function reset() {
    setBusy(true)
    setProblem(null)
    setSaved(false)
    try {
      const result = await musicApi.changeCountries(null)
      setState(result)
      setText(result.default.join(' '))
      setSaved(true)
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  if (state === null) {
    return problem !== null ? <FormMessage>{errorText(t, problem)}</FormMessage> : null
  }
  const named = codesOf(text)
    .map((code) => countryText(code, i18n.language) ?? code)
    .join(', ')
  return (
    <form onSubmit={(event) => void save(event)} className="flex flex-col gap-3">
      <Field
        label={t('music.versions.countriesLabel')}
        hint={t('music.versions.countriesHint', { list: named || t('music.versions.countriesNone') })}
        value={text}
        onChange={(event) => setText(event.target.value)}
        autoComplete="off"
        spellCheck={false}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" loading={busy}>
          {t('music.versions.countriesSave')}
        </Button>
        {state.countries !== null && (
          <Button type="button" variant="ghost" onClick={() => void reset()} disabled={busy}>
            {t('music.versions.countriesDefault', { list: state.default.join(', ') })}
          </Button>
        )}
        {saved && (
          <span className="text-sm text-ok-500" role="status">
            {t('music.versions.countriesSaved')}
          </span>
        )}
      </div>
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </form>
  )
}

/**
 * "Fassungen, Musik" (M1.5): genau eine Fassungsdefinition der Art Musik (Entscheidung 10), read-only, ohne
 * "Fassung anlegen": nexcrate legt sie selbst an, beim ersten Kuenstler, ersten Album oder der ersten
 * Lidarr-Verbindung. Umbenennen und Entfernen gibt es hier bewusst nicht, das ist Sache der uebrigen Fassungen.
 * Darunter die Laenderreihenfolge fuer die Zielausgabe (Entscheidung 28), das Profil mit seinen vier Fragen und der
 * Release-Pruefer fuer Musik (Musik M2). Das Profil gibt es auch ohne Fassung: Speichern legt sie an.
 */
/**
 * "Tags schreiben" (M4, Entscheidung 24): ab Werk an. Aus schreibt nexcrate nichts in abgelegte Musik, und ein Torrent
 * wird verlinkt statt kopiert. Gespeichert wird sofort.
 */
function WriteTagsSwitch() {
  const { t } = useTranslation()
  const [state, setState] = useState<boolean | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    const abort = new AbortController()
    musicApi.filesSettings(abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) setState(result.write_tags)
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setLoadError(error)
      },
    )
    return () => abort.abort()
  }, [])

  async function change(next: boolean) {
    setBusy(true)
    setProblem(null)
    try {
      setState((await musicApi.changeFilesSettings({ write_tags: next })).write_tags)
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  if (state === null) return loadError !== null ? <FormMessage>{errorText(t, loadError)}</FormMessage> : null
  return (
    <div className="flex flex-col gap-2">
      <Switch label={t('music.versions.writeTags')} hint={t('music.versions.writeTagsHint')} checked={state} onChange={(next) => void change(next)} disabled={busy} />
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </div>
  )
}

export function MusicVersionSettings() {
  const { t, i18n } = useTranslation()
  const { versions, error, reload } = useVersions('album')
  // Der Pruefer bittet um das Fenster des Profils; ein geaendertes Profil wirft sein altes Ergebnis weg.
  const [openRequest, setOpenRequest] = useState(0)
  const [profileVersion, setProfileVersion] = useState(0)

  return (
    <>
    <Section title={t('settings.versions.title')} intro={t('music.versions.intro')}>
      {versions === null ? (
        error !== null ? (
          <FormMessage>{errorText(t, error)}</FormMessage>
        ) : (
          <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )
      ) : versions.length === 0 ? (
        <p className="text-sm text-mist-500">{t('music.versions.empty')}</p>
      ) : (
        <Tile>
          <TileHeader title={versions[0].label} sub={t('settings.versions.titles', { count: versions[0].title_count, value: formatNumber(versions[0].title_count, i18n.language) })} />
          <DelayRow version={versions[0]} onChanged={reload} />
        </Tile>
      )}
      <CountryOrder />
      <WriteTagsSwitch />
      <MusicProfileCard openRequest={openRequest} onChanged={() => setProfileVersion((before) => before + 1)} />
    </Section>
    <MusicChecker profileVersion={profileVersion} onSetUpProfile={() => setOpenRequest((before) => before + 1)} />
    </>
  )
}
