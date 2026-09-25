import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { indexerFlagsSwitch, indexersApi } from '../../api/indexers'
import type { Indexer, IndexerTestResult } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner, Switch } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { IndexerDialog } from '../indexers/IndexerDialog'
import { IndexerSearch } from '../indexers/IndexerSearch'
import { kindText, lastErrorText, momentText, pausedUntil } from '../indexers/indexerText'
import { useIndexers } from '../indexers/useIndexers'
import { CardTags } from './CardTags'
import { useSwitchSetting } from './useSwitchSetting'

/**
 * Reiter "Indexer": Newznab und Torznab eintragen, pruefen, aendern, entfernen, dazu eine
 * Testsuche. Die Suche je Film steht seit Schritt 2c auf der Titelseite; das sagt der Reiter auch.
 */
export function IndexerSettings() {
  const { t } = useTranslation()
  const { indexers, error, reload } = useIndexers()
  // undefined: kein Dialog. null: neuer Indexer.
  const [editing, setEditing] = useState<Indexer | null | undefined>(undefined)
  const [removing, setRemoving] = useState<Indexer | null>(null)
  const preferFlags = useSwitchSetting(indexerFlagsSwitch)

  return (
    <div className="flex flex-col gap-4">
      <Section
        title={t('indexers.title')}
        intro={t('indexers.intro')}
        actions={
          indexers !== null ? (
            <Button onClick={() => setEditing(null)}>
              <Symbol name="plus" />
              {t('indexers.add')}
            </Button>
          ) : undefined
        }
      >
        <p className="flex items-start gap-2 text-sm text-mist-500">
          <Symbol name="clock" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
          {t('indexers.later')}
        </p>
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
        {indexers === null ? (
          error === null && (
            <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : indexers.length === 0 ? (
          <p className="text-sm text-mist-500">{t('indexers.empty')}</p>
        ) : (
          <ul className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
            {indexers.map((indexer) => (
              <li key={indexer.id} className="min-w-0">
                <IndexerCard indexer={indexer} onEdit={() => setEditing(indexer)} onRemove={() => setRemoving(indexer)} onTested={reload} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      {preferFlags.enabled !== null && (
        <Section title={t('indexers.options.title')}>
          <Switch
            label={t('indexers.options.preferFlags')}
            hint={t('indexers.options.preferFlagsHint')}
            checked={preferFlags.enabled}
            onChange={(next) => void preferFlags.change(next)}
            disabled={preferFlags.busy}
          />
          {preferFlags.problem !== null && <FormMessage>{errorText(t, preferFlags.problem)}</FormMessage>}
        </Section>
      )}

      {indexers !== null && indexers.length > 0 && <IndexerSearch indexers={indexers} />}

      {editing !== undefined && (
        <IndexerDialog
          indexer={editing}
          onClose={() => setEditing(undefined)}
          onSaved={() => {
            setEditing(undefined)
            reload()
          }}
        />
      )}
      {removing && (
        <RemoveIndexerDialog
          indexer={removing}
          onClose={() => setRemoving(null)}
          onRemoved={() => {
            setRemoving(null)
            reload()
          }}
        />
      )}
    </div>
  )
}

function IndexerCard({ indexer, onEdit, onRemove, onTested }: { indexer: Indexer; onEdit: () => void; onRemove: () => void; onTested: () => void }) {
  const { t, i18n } = useTranslation()
  const [testing, setTesting] = useState(false)
  const [tested, setTested] = useState<IndexerTestResult | null>(null)
  const [testProblem, setTestProblem] = useState<unknown>(null)
  const paused = pausedUntil(indexer)
  const failed = paused === null && indexer.last_error_code !== null

  async function test() {
    setTesting(true)
    setTested(null)
    setTestProblem(null)
    try {
      setTested(await indexersApi.test({ indexer_id: indexer.id }))
    } catch (error) {
      setTestProblem(error)
    } finally {
      setTesting(false)
      // Ein Test aendert Zustand und Pause, die Liste zieht nach.
      onTested()
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/60 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-ink-700 bg-ink-850 text-accent-400">
          <Symbol name="search" className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold wrap-anywhere text-mist-100">{indexer.name}</h3>
          <p className="text-xs break-all text-mist-500">{indexer.url}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Badge>{kindText(t, indexer.kind)}</Badge>
        {indexer.enabled ? (
          <Badge tone="ok">{t('indexers.state.enabled')}</Badge>
        ) : (
          <Badge>
            <Symbol name="eyeOff" className="h-3.5 w-3.5" />
            {t('indexers.state.disabled')}
          </Badge>
        )}
        {paused !== null ? (
          <Badge tone="info">
            <Symbol name="clock" className="h-3.5 w-3.5" />
            {t('indexers.state.paused', { time: momentText(paused, i18n.language) })}
          </Badge>
        ) : failed ? (
          <Badge tone="bad">
            <Symbol name="alert" className="h-3.5 w-3.5" />
            {t('indexers.state.failed')}
          </Badge>
        ) : (
          <Badge tone="ok">
            <Symbol name="check" className="h-3.5 w-3.5" />
            {t('indexers.state.ok')}
          </Badge>
        )}
        <Badge>{t('indexers.state.categories', { count: indexer.categories.length })}</Badge>
        {indexer.priority !== undefined && <Badge>{t('indexers.state.priority', { value: indexer.priority })}</Badge>}
        {typeof indexer.daily_limit === 'number' && <Badge>{t('indexers.state.dailyLimit', { value: formatNumber(indexer.daily_limit, i18n.language) })}</Badge>}
        {!indexer.has_api_key && <Badge>{t('indexers.state.noKey')}</Badge>}
        {indexer.from_source && (
          <Badge tone="accent">
            <Symbol name="import" className="h-3.5 w-3.5" />
            {t('indexers.state.fromSource', { name: indexer.from_source.name })}
          </Badge>
        )}
      </div>
      {failed && indexer.last_error_code !== null && (
        <p className="flex items-start gap-2 text-sm text-bad-500">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0 wrap-anywhere">{t('indexers.lastError.label', { text: lastErrorText(t, indexer.last_error_code) })}</span>
        </p>
      )}
      {indexer.caps === null && (
        <p className="flex items-start gap-2 text-sm text-mist-400">
          <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
          <span className="min-w-0">{t('indexers.state.noCaps')}</span>
        </p>
      )}
      {tested !== null &&
        (tested.feed_items > 0 ? <FormMessage tone="ok">{t('indexers.form.feedOk')}</FormMessage> : <FormMessage>{t('indexers.form.feedEmpty')}</FormMessage>)}
      {testProblem !== null && <FormMessage>{errorText(t, testProblem)}</FormMessage>}
      {indexer.tags !== undefined && (
        <CardTags
          id={indexer.id}
          tags={indexer.tags}
          hint={t('settings.tagField.indexerHint')}
          onSave={async (next) => (await indexersApi.update(indexer.id, { tags: next })).tags ?? next}
        />
      )}
      <div className="mt-auto flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" loading={testing} onClick={() => void test()} aria-label={t('indexers.actions.testLabel', { name: indexer.name })}>
          {t('common.actions.test')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onEdit} aria-label={t('indexers.actions.editLabel', { name: indexer.name })}>
          {t('common.actions.edit')}
        </Button>
        <Button variant="ghost" size="sm" onClick={onRemove} aria-label={t('indexers.actions.removeLabel', { name: indexer.name })}>
          {t('common.actions.remove')}
        </Button>
      </div>
    </div>
  )
}

function RemoveIndexerDialog({ indexer, onClose, onRemoved }: { indexer: Indexer; onClose: () => void; onRemoved: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await indexersApi.remove(indexer.id)
      onRemoved()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog
      open
      title={t('indexers.remove.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('indexers.remove.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">{t('indexers.remove.text', { name: indexer.name })}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}
