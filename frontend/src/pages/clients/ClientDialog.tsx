import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import {
  CATEGORY_PATTERN,
  CLIENT_PRIORITY_DEFAULT,
  CLIENT_PRIORITY_MAX,
  CLIENT_PRIORITY_MIN,
  DEFAULT_CATEGORY,
  DOWNLOAD_CLIENT_KINDS,
  downloadClientsApi,
  protocolOf,
  usesUsername,
} from '../../api/downloadClients'
import type { DownloadClient, DownloadClientCreate, DownloadClientKind, DownloadClientTestResult, DownloadClientUpdate, RadarrDownloadClient, Source } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PasswordField } from '../../components/PasswordField'
import { Segmented } from '../../components/Segmented'
import { Button, Field, FormMessage, Toggle } from '../../components/ui'
import { clientCategoryHint, clientKindHint, clientKindText, clientSaveHint, clientSecretHint, clientUrlHint } from './clientText'

/** Ein Programm aus Radarr als Vorlage fuer den Dialog. */
export type ClientTemplate = { source: Pick<Source, 'id' | 'name'>; item: RadarrDownloadClient }

/**
 * Ein Download-Programm eintragen (`client` null), aus Radarr uebernehmen (`template`) oder aendern.
 * Das Programm (SABnzbd, NZBGet, qBittorrent, Transmission oder Deluge) ist nur beim Eintragen waehlbar. Beim Aendern steht es als Text da, und
 * der Server lehnt einen Wechsel ab: Ein anderes Programm ist ein neuer Eintrag.
 * Speichern prueft beim Server die Verbindung und legt die Kategorie an; "Verbindung prüfen" tut das
 * vorher ohne zu speichern.
 *
 * ⚠️ Das Geheimnis (SABnzbds API-Schluessel oder qBittorrents Passwort) ist nie vorbelegt, auch nicht
 * beim Bearbeiten oder aus Radarr: Der Server gibt es nie heraus, Radarr auch nicht. Bleibt das Feld
 * beim Bearbeiten leer, geht keines hinaus und das gespeicherte bleibt. Nach dem Speichern wird es geleert.
 *
 * Die Kategorie aus Radarr wird bewusst nicht uebernommen: Mit ihr uebernaehme dieses Radarr jeden Download.
 */
export function ClientDialog({
  client,
  template = null,
  onClose,
  onSaved,
}: {
  client: DownloadClient | null
  template?: ClientTemplate | null
  onClose: () => void
  onSaved: (saved: DownloadClient) => void
}) {
  const { t } = useTranslation()
  const [kind, setKind] = useState<DownloadClientKind>(client?.kind ?? template?.item.kind ?? 'sabnzbd')
  const [name, setName] = useState(client?.name ?? template?.item.name ?? '')
  const [url, setUrl] = useState(client?.url ?? template?.item.url ?? '')
  const [username, setUsername] = useState(client?.username ?? template?.item.username ?? '')
  const [secret, setSecret] = useState('')
  const [category, setCategory] = useState(client?.category ?? DEFAULT_CATEGORY)
  const [priority, setPriority] = useState(String(client?.priority ?? template?.item.priority ?? CLIENT_PRIORITY_DEFAULT))
  const [enabled, setEnabled] = useState(client?.enabled ?? template?.item.enabled ?? true)
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<{ result: DownloadClientTestResult; category: string } | null>(null)
  const [testProblem, setTestProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const torrent = protocolOf(kind) === 'torrent'
  const withUsername = usesUsername(kind)
  // Nur SABnzbd nimmt einen API-Schluessel; alle anderen ein Passwort, das nexcrate nicht kuerzt.
  const apiKey = kind === 'sabnzbd'

  function resetTest() {
    setTested(null)
    setTestProblem(null)
  }

  /** Das Formular als Anfrage, oder der Satz, warum es so nicht geht. */
  function readForm(): { body: DownloadClientCreate } | { error: string } {
    const cleanName = name.trim()
    const cleanUrl = url.trim()
    const cleanCategory = category.trim()
    const cleanPriority = priority.trim()
    if (cleanName === '') return { error: t('settings.clients.form.missingName') }
    if (cleanUrl === '') return { error: t('settings.clients.form.missingUrl') }
    if (!CATEGORY_PATTERN.test(cleanCategory)) return { error: t('settings.clients.form.categoryInvalid') }
    const number = Number(cleanPriority)
    if (!/^\d{1,2}$/.test(cleanPriority) || number < CLIENT_PRIORITY_MIN || number > CLIENT_PRIORITY_MAX) return { error: t('settings.clients.form.priorityInvalid') }
    const body: DownloadClientCreate = { name: cleanName, kind, url: cleanUrl, category: cleanCategory, priority: number, enabled }
    const user = username.trim()
    if (withUsername && user !== '') body.username = user
    // Ein Passwort kann mit Leerzeichen beginnen oder enden, ein API-Schluessel nicht.
    const key = apiKey ? secret.trim() : secret
    if (key !== '') body.secret = key
    return { body }
  }

  async function test() {
    resetTest()
    const form = readForm()
    if ('error' in form) return setTestProblem(form.error)
    setTesting(true)
    try {
      const result = await downloadClientsApi.test(client === null ? form.body : { ...form.body, id: client.id })
      setTested({ result, category: form.body.category ?? DEFAULT_CATEGORY })
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
      let saved: DownloadClient
      if (client === null) {
        saved = await downloadClientsApi.create(template === null ? body : { ...body, from: { source_id: template.source.id, radarr_id: template.item.radarr_id }, ...(template.item.tags?.length ? { tags: template.item.tags } : {}) })
      } else {
        // Name, Prioritaet und Schalter gehen immer mit; was eine neue Pruefung ausloest, nur wenn es sich aendert. Das Programm nie.
        const change: DownloadClientUpdate = { name: body.name, priority: body.priority, enabled: body.enabled }
        if (body.url !== client.url) change.url = body.url
        if (body.category !== client.category) change.category = body.category
        if (withUsername && (body.username ?? '') !== (client.username ?? '')) change.username = body.username ?? ''
        if (body.secret !== undefined) change.secret = body.secret
        saved = await downloadClientsApi.update(client.id, change)
      }
      setSecret('')
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
    setSecret('')
    onClose()
  }

  const title =
    client !== null
      ? t('settings.clients.form.dialogEdit', { name: client.name })
      : template !== null
        ? t('settings.clients.form.dialogFromRadarr', { name: template.item.name })
        : t('settings.clients.form.dialogAdd')
  const secretHint =
    client !== null
      ? client.has_secret
        ? t('settings.clients.form.secretSaved')
        : t('settings.clients.form.secretNone')
      : clientSecretHint(t, kind)
  const radarrCategory = template?.item.radarr_category ?? null

  return (
    <Dialog
      open
      wide
      title={title}
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
        {template !== null && <p className="text-sm wrap-anywhere text-mist-300">{t('settings.clients.form.fromRadarrIntro', { source: template.source.name })}</p>}
        <div className="flex flex-col gap-1.5">
          <p className="text-sm font-medium text-mist-300">{t('settings.clients.kind.label')}</p>
          {client === null ? (
            <Segmented
              value={kind}
              options={DOWNLOAD_CLIENT_KINDS}
              onChange={(next) => {
                setKind(next)
                resetTest()
              }}
              label={(value) => clientKindText(t, value)}
              ariaLabel={t('settings.clients.kind.label')}
            />
          ) : (
            <p className="text-sm text-mist-100">{clientKindText(t, kind)}</p>
          )}
          <p className="text-xs text-mist-500">{clientKindHint(t, kind)}</p>
          {/* Befund 6 vom 14.09.2026: Wer nur Usenet kennt, fragt, warum nicht einfach verschoben wird. */}
          {torrent && <p className="text-sm text-mist-400">{t('settings.clients.kind.qbittorrentFiling')}</p>}
        </div>
        <Field
          label={t('settings.clients.form.name')}
          hint={t('settings.clients.form.nameHint')}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={100}
          autoComplete="off"
          autoFocus
        />
        <Field
          label={t('settings.clients.form.url')}
          hint={clientUrlHint(t, kind)}
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
        {withUsername && (
          <Field
            label={t('settings.clients.form.username')}
            value={username}
            onChange={(event) => {
              setUsername(event.target.value)
              resetTest()
            }}
            maxLength={200}
            autoComplete="off"
            spellCheck={false}
          />
        )}
        <PasswordField
          label={apiKey ? t('settings.clients.form.apiKey') : t('settings.clients.form.password')}
          hint={secretHint}
          value={secret}
          onChange={(event) => {
            setSecret(event.target.value)
            resetTest()
          }}
          autoComplete="new-password"
          maxLength={500}
        />
        <Field
          label={t('settings.clients.form.category')}
          hint={radarrCategory !== null ? t('settings.clients.form.categoryFromRadarr', { category: radarrCategory }) : clientCategoryHint(t, kind)}
          value={category}
          onChange={(event) => {
            // Klein wie in SABnzbd. So steht da, was gespeichert wird.
            setCategory(event.target.value.toLowerCase())
            resetTest()
          }}
          maxLength={64}
          autoComplete="off"
          spellCheck={false}
          className="w-full min-w-0 font-mono"
        />
        <Field
          label={t('settings.clients.form.priority')}
          hint={t('settings.clients.form.priorityHint')}
          type="number"
          inputMode="numeric"
          min={CLIENT_PRIORITY_MIN}
          max={CLIENT_PRIORITY_MAX}
          step={1}
          value={priority}
          onChange={(event) => setPriority(event.target.value)}
          className="w-28 tabular-nums"
        />
        <Toggle label={t('settings.clients.form.enabled')} hint={t('settings.clients.form.enabledHint')} checked={enabled} onChange={setEnabled} />
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => void test()} loading={testing}>
            {t('common.actions.test')}
          </Button>
          {testing && (
            <span className="text-xs text-mist-500" role="status">
              {t('settings.clients.form.checking')}
            </span>
          )}
        </div>
        {tested !== null && (
          <FormMessage tone="ok">
            {t('settings.clients.test.ok', { kind: clientKindText(t, kind), version: tested.result.version })}{' '}
            {tested.result.category_exists
              ? t('settings.clients.test.categoryExists', { category: tested.category })
              : t('settings.clients.test.categoryMissing', { category: tested.category })}
          </FormMessage>
        )}
        {testProblem !== null && <FormMessage>{testProblem}</FormMessage>}
        <p className="text-xs text-mist-500">{clientSaveHint(t, kind)}</p>
        {problem !== null && <FormMessage>{problem}</FormMessage>}
      </form>
    </Dialog>
  )
}
