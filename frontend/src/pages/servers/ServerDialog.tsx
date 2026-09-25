import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { MEDIA_SERVER_KINDS, mediaServersApi } from '../../api/mediaServers'
import type { MediaServer, MediaServerCreate, MediaServerKind, MediaServerPathMapping, MediaServerTestResult, MediaServerUpdate, PlexPin, PlexServer, PlexServers } from '../../api/types'
import { buttonClasses } from '../../components/buttonClasses'
import { Dialog } from '../../components/Dialog'
import { PasswordField } from '../../components/PasswordField'
import { Segmented } from '../../components/Segmented'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage, Toggle } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { serverKindText } from './serverText'

/** Wie oft die Seite nachfragt, ob die Anmeldung bei plex.tv bestaetigt ist. */
export const PIN_POLL_MS = 2000

type PinState = 'idle' | 'waiting' | 'claimed' | 'expired'
type Row = MediaServerPathMapping & { key: number }

/**
 * Einen Medienserver eintragen (`server` null) oder aendern. Der Server (Plex, Jellyfin, Emby) ist nur beim Eintragen
 * waehlbar. Speichern prueft beim Server von nexcrate die Verbindung und liest die Bibliotheken;
 * "Verbindung pruefen" tut das vorher ohne zu speichern.
 *
 * ⚠️ Der Token oder API-Schluessel ist nie vorbelegt: Der Server gibt ihn nie heraus. Bleibt das Feld beim Bearbeiten
 * leer, geht keiner hinaus und der gespeicherte bleibt.
 *
 * Plex wie in Nexview: nach der Anmeldung nennt plex.tv die eigenen Server des Kontos, der Besitzer waehlt seinen, und
 * Adresse, Name und Token kommen von selbst. Die Pfadzuordnung schlaegt der Server von nexcrate vor, sobald er die
 * Ordner des Medienservers kennt (nach der Wahl oder nach "Verbindung pruefen"); eingetragen wird der Vorschlag nur,
 * solange noch keine Zuordnung dasteht, und gespeichert erst mit "Speichern".
 *
 * ⚠️ plex.tv: Nur der Knopf "Bei plex.tv anmelden" loest einen Kontakt zu plex.tv aus, und das Nachfragen danach.
 * Wer den Token von Hand eintraegt, loest keinen aus. Der Token aus der Anmeldung kommt nie im Browser an: Die Seite
 * kennt nur die Nummer der Anmeldung und reicht sie beim Speichern mit.
 */
export function ServerDialog({ server, onClose, onSaved }: { server: MediaServer | null; onClose: () => void; onSaved: (saved: MediaServer) => void }) {
  const { t, i18n } = useTranslation()
  const [kind, setKind] = useState<MediaServerKind>(server?.kind ?? 'plex')
  const [name, setName] = useState(server?.name ?? '')
  const [url, setUrl] = useState(server?.url ?? '')
  const [token, setToken] = useState('')
  const [enabled, setEnabled] = useState(server?.enabled ?? true)
  const [rows, setRows] = useState<Row[]>(() => (server?.path_mappings ?? []).map((mapping, index) => ({ ...mapping, key: index })))
  const [nextKey, setNextKey] = useState(server?.path_mappings.length ?? 0)
  const [pin, setPin] = useState<PlexPin | null>(null)
  const [pinState, setPinState] = useState<PinState>('idle')
  const [pinBusy, setPinBusy] = useState(false)
  const [pinProblem, setPinProblem] = useState<string | null>(null)
  // Die eigenen Server des angemeldeten Kontos; null, solange sie noch nicht gelesen sind.
  const [plexServers, setPlexServers] = useState<PlexServers | null>(null)
  const [choosing, setChoosing] = useState<string | null>(null)
  const [chosen, setChosen] = useState<{ name: string; url: string } | null>(null)
  const [mappingNote, setMappingNote] = useState<'suggested' | 'notNeeded' | null>(null)
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<MediaServerTestResult | null>(null)
  const [testProblem, setTestProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const plex = kind === 'plex'

  // Nachfragen nur, solange eine Anmeldung laeuft, die der Knopf gestartet hat.
  useEffect(() => {
    if (pin === null || pinState !== 'waiting') return
    let current = true
    const timer = window.setInterval(() => {
      mediaServersApi.plexPinState(pin.pin_id).then(
        (answer) => {
          if (current) setPinState(answer.state)
        },
        (error: unknown) => {
          if (!current) return
          setPinProblem(errorText(t, error))
          setPinState('idle')
          setPin(null)
        },
      )
    }, PIN_POLL_MS)
    return () => {
      current = false
      window.clearInterval(timer)
    }
  }, [pin, pinState, t])

  // Bestaetigt: die Server des Kontos lesen, einmal je Anmeldung. Das ist der dritte und letzte Kontakt zu plex.tv.
  useEffect(() => {
    if (pin === null || pinState !== 'claimed') return
    let current = true
    mediaServersApi.plexServers(pin.pin_id).then(
      (found) => {
        if (current) setPlexServers(found)
      },
      (error: unknown) => {
        // Ohne Liste geht es von Hand weiter: die Anmeldung gilt trotzdem.
        if (current) {
          setPinProblem(errorText(t, error))
          setPlexServers({ servers: [], shared_hidden: 0 })
        }
      },
    )
    return () => {
      current = false
    }
  }, [pin, pinState, t])

  function resetTest() {
    setTested(null)
    setTestProblem(null)
  }

  function resetPin() {
    setPin(null)
    setPinState('idle')
    setPinProblem(null)
    setPlexServers(null)
    setChosen(null)
    setChoosing(null)
  }

  /** Was der Server von nexcrate als Pfadpaare anbietet, eintragen: nur solange noch keine Zuordnung dasteht. */
  function offerMappings(result: MediaServerTestResult) {
    const empty = rows.every((row) => row.local.trim() === '' && row.remote.trim() === '')
    const offered = result.suggested_mappings ?? []
    if (empty && offered.length > 0) {
      setRows(offered.map((pair, index) => ({ local: pair.local, remote: pair.remote, key: nextKey + index })))
      setNextKey((value) => value + offered.length)
      setMappingNote('suggested')
    } else if (empty && result.mappings_needed === false) {
      setMappingNote('notNeeded')
    }
  }

  async function choose(found: PlexServer) {
    if (pin === null || choosing !== null) return
    setChoosing(found.machine_id)
    setPinProblem(null)
    resetTest()
    try {
      const answer = await mediaServersApi.plexChoose(pin.pin_id, found.machine_id)
      setUrl(answer.url)
      if (name.trim() === '') setName(answer.name)
      setChosen({ name: answer.name, url: answer.url })
      setTested(answer)
      offerMappings(answer)
    } catch (error) {
      setPinProblem(errorText(t, error))
    } finally {
      setChoosing(null)
    }
  }

  async function startPin() {
    setPinBusy(true)
    setPinProblem(null)
    resetTest()
    try {
      setPin(await mediaServersApi.plexPin())
      setPinState('waiting')
    } catch (error) {
      setPinProblem(errorText(t, error))
    } finally {
      setPinBusy(false)
    }
  }

  /** Der Zugang, wie er hinausgeht: der getippte Token, sonst die bestaetigte Anmeldung, sonst nichts. */
  function access(): { token?: string; plex_pin_id?: number } {
    const typed = token.trim()
    if (typed !== '') return { token: typed }
    if (plex && pin !== null && pinState === 'claimed') return { plex_pin_id: pin.pin_id }
    return {}
  }

  /** Das Formular als Anfrage, oder der Satz, warum es so nicht geht. */
  function readForm(): { body: MediaServerCreate } | { error: string } {
    const cleanName = name.trim()
    const cleanUrl = url.trim()
    if (cleanName === '') return { error: t('settings.servers.form.missingName') }
    if (cleanUrl === '') return { error: t('settings.servers.form.missingUrl') }
    const mappings = rows.map((row) => ({ local: row.local.trim(), remote: row.remote.trim() })).filter((row) => row.local !== '' || row.remote !== '')
    if (mappings.some((row) => row.local === '' || row.remote === '')) return { error: t('settings.servers.mappings.incomplete') }
    return { body: { name: cleanName, kind, url: cleanUrl, enabled, path_mappings: mappings, ...access() } }
  }

  async function test() {
    resetTest()
    const cleanUrl = url.trim()
    if (cleanUrl === '') return setTestProblem(t('settings.servers.form.missingUrl'))
    setTesting(true)
    try {
      const answer = await mediaServersApi.test({ kind, url: cleanUrl, ...access(), ...(server === null ? {} : { id: server.id }) })
      setTested(answer)
      offerMappings(answer)
    } catch (error) {
      setTestProblem(errorText(t, error))
    } finally {
      setTesting(false)
    }
  }

  async function save() {
    if (busy) return
    const form = readForm()
    if ('error' in form) return setProblem(form.error)
    const { body } = form
    setBusy(true)
    setProblem(null)
    try {
      let saved: MediaServer
      if (server === null) {
        saved = await mediaServersApi.create(body)
      } else {
        // Name, Schalter und Zuordnungen gehen immer mit; was eine neue Pruefung ausloest, nur wenn es sich aendert.
        const change: MediaServerUpdate = { name: body.name, enabled: body.enabled, path_mappings: body.path_mappings }
        if (body.url !== server.url) change.url = body.url
        if (body.token !== undefined) change.token = body.token
        if (body.plex_pin_id !== undefined) change.plex_pin_id = body.plex_pin_id
        saved = await mediaServersApi.update(server.id, change)
      }
      setToken('')
      onSaved(saved)
    } catch (error) {
      setProblem(errorText(t, error))
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void save()
  }

  function close() {
    if (busy) return
    setToken('')
    onClose()
  }

  function changeRow(key: number, side: 'local' | 'remote', value: string) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, [side]: value } : row)))
  }

  const urlHint = plex ? t('settings.servers.form.urlHintPlex') : kind === 'jellyfin' ? t('settings.servers.form.urlHintJellyfin') : t('settings.servers.form.urlHintEmby')
  const secretHint =
    server !== null
      ? server.has_token
        ? t('settings.servers.form.secretSaved')
        : t('settings.servers.form.secretNone')
      : plex
        ? t('settings.servers.form.tokenHint')
        : kind === 'jellyfin'
          ? t('settings.servers.form.apiKeyHintJellyfin')
          : t('settings.servers.form.apiKeyHintEmby')

  return (
    <Dialog
      open
      wide
      title={server !== null ? t('settings.servers.form.dialogEdit', { name: server.name }) : t('settings.servers.form.dialogAdd')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void save()} loading={busy}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <p className="text-sm font-medium text-mist-300">{t('settings.servers.kind.label')}</p>
          {server === null ? (
            <Segmented
              value={kind}
              options={MEDIA_SERVER_KINDS}
              onChange={(next) => {
                setKind(next)
                resetTest()
                resetPin()
              }}
              label={(value) => serverKindText(t, value)}
              ariaLabel={t('settings.servers.kind.label')}
            />
          ) : (
            <p className="text-sm text-mist-100">{serverKindText(t, kind)}</p>
          )}
        </div>
        {plex && (
          <div className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-850 p-3">
            <p className="text-sm font-medium text-mist-300">{t('settings.servers.plexTv.title')}</p>
            <p className="text-xs text-mist-500">{t('settings.servers.plexTv.intro')}</p>
            {pinState === 'idle' || pinState === 'expired' ? (
              <div className="flex flex-col items-start gap-2">
                {pinState === 'expired' && <FormMessage tone="info">{t('settings.servers.plexTv.expired')}</FormMessage>}
                <Button variant="ghost" size="sm" onClick={() => void startPin()} loading={pinBusy}>
                  <Symbol name="globe" />
                  {t('settings.servers.plexTv.start')}
                </Button>
              </div>
            ) : pinState === 'waiting' && pin !== null ? (
              <div className="flex flex-col items-start gap-2">
                <a
                  href={pin.auth_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={buttonClasses('primary', 'sm')}
                >
                  {t('settings.servers.plexTv.open')}
                </a>
                <p className="text-xs text-mist-400" role="status">
                  {t('settings.servers.plexTv.waiting')}
                </p>
                <Button variant="ghost" size="sm" onClick={resetPin}>
                  {t('settings.servers.plexTv.cancel')}
                </Button>
              </div>
            ) : (
              <div className="flex flex-col items-start gap-2">
                <FormMessage tone="ok">{t('settings.servers.plexTv.claimed')}</FormMessage>
                {plexServers === null ? (
                  <p className="text-xs text-mist-400" role="status">
                    {t('settings.servers.plexTv.servers.loading')}
                  </p>
                ) : plexServers.servers.length === 0 ? (
                  <p className="text-xs text-mist-400">{t('settings.servers.plexTv.servers.none')}</p>
                ) : (
                  <>
                    <p className="text-xs text-mist-400">{t('settings.servers.plexTv.servers.choose')}</p>
                    <div className="flex flex-wrap gap-1.5">
                      {plexServers.servers.map((found) => (
                        <Button
                          key={found.machine_id}
                          variant={chosen !== null && chosen.name === found.name ? 'primary' : 'ghost'}
                          size="sm"
                          onClick={() => void choose(found)}
                          loading={choosing === found.machine_id}
                          disabled={choosing !== null}
                          aria-label={t('settings.servers.plexTv.servers.useLabel', { name: found.name })}
                        >
                          <Symbol name="server" />
                          {found.name}
                        </Button>
                      ))}
                    </div>
                  </>
                )}
                {plexServers !== null && plexServers.shared_hidden > 0 && (
                  <p className="text-xs text-mist-500">{t('settings.servers.plexTv.servers.sharedHidden', { count: plexServers.shared_hidden })}</p>
                )}
                {choosing !== null && (
                  <p className="text-xs text-mist-400" role="status">
                    {t('settings.servers.plexTv.servers.choosing')}
                  </p>
                )}
                {chosen !== null && <FormMessage tone="ok">{t('settings.servers.plexTv.servers.chosen', { name: chosen.name, url: chosen.url })}</FormMessage>}
              </div>
            )}
            {pinProblem !== null && <FormMessage>{pinProblem}</FormMessage>}
          </div>
        )}
        <Field
          label={t('settings.servers.form.name')}
          hint={t('settings.servers.form.nameHint')}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={100}
          autoComplete="off"
          autoFocus
        />
        <Field
          label={t('settings.servers.form.url')}
          hint={urlHint}
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
          label={plex ? t('settings.servers.form.token') : t('settings.servers.form.apiKey')}
          hint={secretHint}
          value={token}
          onChange={(event) => {
            setToken(event.target.value)
            resetTest()
          }}
          autoComplete="new-password"
          maxLength={512}
        />
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium text-mist-300">{t('settings.servers.mappings.title')}</p>
          <p className="text-xs text-mist-500">{t('settings.servers.mappings.intro')}</p>
          {mappingNote === 'suggested' && <FormMessage tone="info">{t('settings.servers.mappings.suggested')}</FormMessage>}
          {mappingNote === 'notNeeded' && <FormMessage tone="info">{t('settings.servers.mappings.notNeeded')}</FormMessage>}
          {rows.map((row, index) => (
            <div key={row.key} className="grid grid-cols-[minmax(0,1fr)] gap-2 rounded-lg border border-ink-700 bg-ink-850 p-2.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
              <label className="flex min-w-0 flex-col gap-1 text-xs text-mist-500">
                {t('settings.servers.mappings.local')}
                <input
                  aria-label={t('settings.servers.mappings.localLabel', { row: index + 1 })}
                  value={row.local}
                  onChange={(event) => changeRow(row.key, 'local', event.target.value)}
                  maxLength={1024}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder="/media"
                  className="w-full min-w-0 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                />
              </label>
              <label className="flex min-w-0 flex-col gap-1 text-xs text-mist-500">
                {t('settings.servers.mappings.remote')}
                <input
                  aria-label={t('settings.servers.mappings.remoteLabel', { row: index + 1 })}
                  value={row.remote}
                  onChange={(event) => changeRow(row.key, 'remote', event.target.value)}
                  maxLength={1024}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder="/volume1/media"
                  className="w-full min-w-0 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                />
              </label>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setRows((current) => current.filter((entry) => entry.key !== row.key))}
                aria-label={t('settings.servers.mappings.removeLabel', { row: index + 1 })}
              >
                {t('settings.servers.mappings.remove')}
              </Button>
            </div>
          ))}
          <div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setRows((current) => [...current, { key: nextKey, local: '', remote: '' }])
                setNextKey((value) => value + 1)
              }}
            >
              <Symbol name="plus" />
              {t('settings.servers.mappings.add')}
            </Button>
          </div>
        </div>
        <Toggle label={t('settings.servers.form.enabled')} hint={t('settings.servers.form.enabledHint')} checked={enabled} onChange={setEnabled} />
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => void test()} loading={testing}>
            {t('common.actions.test')}
          </Button>
          {testing && (
            <span className="text-xs text-mist-500" role="status">
              {t('settings.servers.form.checking')}
            </span>
          )}
        </div>
        {tested !== null && (
          <FormMessage tone="ok">
            {t('settings.servers.test.ok', {
              kind: serverKindText(t, kind),
              version: tested.version,
              count: tested.libraries.length,
              value: formatNumber(tested.libraries.length, i18n.language),
            })}
          </FormMessage>
        )}
        {testProblem !== null && <FormMessage>{testProblem}</FormMessage>}
        <p className="text-xs text-mist-500">{t('settings.servers.form.saveHint')}</p>
        {problem !== null && <FormMessage>{problem}</FormMessage>}
      </form>
    </Dialog>
  )
}
