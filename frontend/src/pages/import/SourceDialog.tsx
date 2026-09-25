import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { sourcesApi } from '../../api/sources'
import type { Source, SourceApp, SourceTest, SourceTestResult, SourceUpdate, Version } from '../../api/types'
import { versionsApi } from '../../api/versions'
import { Dialog } from '../../components/Dialog'
import { PasswordField } from '../../components/PasswordField'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, Field, FormMessage, SelectField } from '../../components/ui'
import { checkText } from './runText'

/** Wert der Auswahl "Neue Fassung anlegen". Echte Fassungen haben Nummern. */
const NEW_VERSION = 'new'

type VersionChoice = number | typeof NEW_VERSION

/**
 * Eine Verbindung zu Radarr oder Sonarr eintragen (`source` null) oder aendern. Dazu gehoert die
 * Fassung, zu der die Filme aus diesem Radarr oder die Serien aus diesem Sonarr werden. Gibt es sie
 * noch nicht, legt man sie hier gleich mit ihrem Namen an, als Fassung fuer Filme oder fuer Serien.
 *
 * ⚠️ Der API-Schluessel ist nie vorbelegt, auch nicht beim Bearbeiten: Der Server
 * gibt ihn nie heraus. Bleibt das Feld beim Bearbeiten leer, geht gar kein Schluessel
 * hinaus und der gespeicherte bleibt. Nach dem Speichern wird das Feld sofort geleert;
 * der Schluessel steht nie in localStorage, in der Adresse oder im Protokoll.
 */
export function SourceDialog({
  app,
  source,
  sources,
  versions,
  onClose,
  onSaved,
  onVersionCreated,
}: {
  app: SourceApp
  source: Source | null
  sources: Source[]
  versions: Version[]
  onClose: () => void
  onSaved: (saved: Source) => void
  /** Eine hier angelegte Fassung. Die Seite laedt ihre Liste neu. */
  onVersionCreated: (created: Version) => void
}) {
  const { t, i18n } = useTranslation()
  const series = app === 'sonarr'
  const takenBy = (versionId: number) => sources.find((other) => other.version_id === versionId && other.id !== source?.id)
  const firstFree = versions.find((version) => !takenBy(version.id))

  const [name, setName] = useState(source?.name ?? '')
  const [url, setUrl] = useState(source?.url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [choice, setChoice] = useState<VersionChoice>(source?.version_id ?? firstFree?.id ?? NEW_VERSION)
  const [newLabel, setNewLabel] = useState('')
  // Eine hier angelegte Fassung, bis die Liste von aussen sie auch kennt.
  const [created, setCreated] = useState<Version | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<SourceTestResult | null>(null)
  const [testProblem, setTestProblem] = useState<string | null>(null)

  const keyRequired = source === null || !source.has_api_key
  const options = created !== null && !versions.some((version) => version.id === created.id) ? [...versions, created] : versions
  // Was je App anders heisst. Alles andere gilt fuer beide.
  const texts = series
    ? {
        dialogAdd: t('series.import.dialogAdd'),
        nameHint: t('series.import.nameHint'),
        urlHint: t('series.import.urlHint'),
        apiKeyHint: t('series.import.apiKeyHint'),
        versionHint: t('series.import.versionHint'),
      }
    : {
        dialogAdd: t('import.sources.dialogAdd'),
        nameHint: t('import.sources.nameHint'),
        urlHint: t('import.sources.urlHint'),
        apiKeyHint: t('import.sources.apiKeyHint'),
        versionHint: t('import.sources.versionHint'),
      }

  function resetTest() {
    setTested(null)
    setTestProblem(null)
  }

  async function test() {
    resetTest()
    const cleanUrl = url.trim()
    const key = apiKey.trim()
    if (cleanUrl === '') {
      setTestProblem(t('import.sources.testNeedsUrl'))
      return
    }
    if (key === '' && keyRequired) {
      setTestProblem(t('import.sources.testNeedsKey'))
      return
    }
    // Eine gespeicherte Verbindung prueft der Server mit ihrer eigenen App.
    let body: SourceTest
    if (source === null) body = { url: cleanUrl, api_key: key, app }
    else body = key === '' ? { source_id: source.id, url: cleanUrl } : { source_id: source.id, url: cleanUrl, api_key: key }
    setTesting(true)
    try {
      setTested(await sourcesApi.test(body))
    } catch (error) {
      setTestProblem(errorText(t, error))
    } finally {
      setTesting(false)
    }
  }

  async function save() {
    if (busy) return
    const cleanName = name.trim()
    const cleanUrl = url.trim()
    const key = apiKey.trim()
    const label = newLabel.trim()
    if (cleanName === '') return setProblem(t('import.sources.missingName'))
    if (cleanUrl === '') return setProblem(t('import.sources.missingUrl'))
    if (key === '' && keyRequired) return setProblem(t('import.sources.missingKey'))
    if (choice === NEW_VERSION && label === '') return setProblem(t('import.sources.missingVersionName'))

    setBusy(true)
    setProblem(null)
    try {
      let versionId: number
      if (choice === NEW_VERSION) {
        const version = await versionsApi.create({ kind: series ? 'series' : 'movie', label })
        // Ab hier gilt die neue Fassung als gewaehlt. Scheitert danach die Verbindung,
        // legt ein zweiter Versuch sie nicht noch einmal an.
        setCreated(version)
        setChoice(version.id)
        setNewLabel('')
        onVersionCreated(version)
        versionId = version.id
      } else {
        versionId = choice
      }

      let saved: Source
      if (source) {
        const change: SourceUpdate = { name: cleanName, url: cleanUrl, version_id: versionId }
        if (key !== '') change.api_key = key
        saved = await sourcesApi.update(source.id, change)
      } else {
        saved = await sourcesApi.create({ name: cleanName, app, url: cleanUrl, api_key: key, version_id: versionId })
      }
      setApiKey('')
      onSaved(saved)
    } catch (error) {
      // version_label_taken, source_url_invalid, version_taken, source_version_locked und die Codes von Radarr und
      // Sonarr kommen mit Text aus errors.json.
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
    setApiKey('')
    onClose()
  }

  const keyHint = source === null ? texts.apiKeyHint : source.has_api_key ? t('import.sources.keySaved') : t('import.sources.keyNone')

  return (
    <Dialog
      open
      title={source ? t('import.sources.dialogEdit', { name: source.name }) : texts.dialogAdd}
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
        <Field
          label={t('import.sources.name')}
          hint={texts.nameHint}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={100}
          autoComplete="off"
          autoFocus
        />
        <Field
          label={t('import.sources.url')}
          hint={texts.urlHint}
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
          label={t('import.sources.apiKey')}
          hint={keyHint}
          value={apiKey}
          onChange={(event) => {
            setApiKey(event.target.value)
            resetTest()
          }}
          autoComplete="new-password"
          maxLength={200}
        />
        <div className="flex flex-wrap items-center gap-3" aria-live="polite">
          <Button variant="ghost" size="sm" onClick={() => void test()} loading={testing}>
            {t('common.actions.test')}
          </Button>
          {testing && <span className="text-xs text-mist-500">{t('import.sources.checking')}</span>}
          {tested && (
            <Badge tone="ok">
              <Symbol name="check" className="h-3.5 w-3.5" />
              {checkText(t, app, tested, i18n.language)}
            </Badge>
          )}
        </div>
        {testProblem && <FormMessage>{testProblem}</FormMessage>}

        <SelectField
          label={t('import.sources.version')}
          hint={texts.versionHint}
          value={String(choice)}
          onChange={(event) => setChoice(event.target.value === NEW_VERSION ? NEW_VERSION : Number(event.target.value))}
        >
          {options.map((version) => {
            const taken = Boolean(takenBy(version.id))
            return (
              <option key={version.id} value={version.id} disabled={taken}>
                {taken ? t('import.sources.versionTaken', { label: version.label }) : version.label}
              </option>
            )
          })}
          <option value={NEW_VERSION}>{t('import.sources.newVersion')}</option>
        </SelectField>
        {choice === NEW_VERSION && (
          <Field
            label={t('import.sources.newVersionName')}
            hint={t('import.sources.newVersionHint')}
            value={newLabel}
            onChange={(event) => setNewLabel(event.target.value)}
            maxLength={200}
            autoComplete="off"
          />
        )}
        {problem && <FormMessage>{problem}</FormMessage>}
      </form>
    </Dialog>
  )
}
