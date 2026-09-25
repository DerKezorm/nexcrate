import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { profilesApi } from '../../api/profiles'
import type { ProfileBrief, Version } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, Field, FormMessage, Spinner } from '../../components/ui'

const INPUT = 'rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none'

/**
 * Welches Profil eine Fassung nimmt. Eine Fassung hat genau eines; dasselbe Profil
 * darf an mehreren Fassungen haengen, deshalb steht in jeder Zeile, wer sonst noch danach urteilt.
 *
 * Hier entsteht auch ein neues Profil, leer oder als Kopie, und hier wird umbenannt und entfernt. Womit das
 * Profil urteilt, steht nicht hier: das macht der Assistent oder das Fenster "Von Hand".
 */
export function ProfileChooser({ version, onClose, onDone }: { version: Version; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [profiles, setProfiles] = useState<ProfileBrief[] | null>(null)
  const [chosen, setChosen] = useState<number | null>(version.profile_id ?? null)
  const [renaming, setRenaming] = useState<{ id: number; name: string } | null>(null)
  const [newName, setNewName] = useState('')
  const [copyOf, setCopyOf] = useState<number | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let dropped = false
    profilesApi
      .list(version.kind)
      .then((rows) => {
        if (!dropped) setProfiles(rows)
      })
      .catch((error: unknown) => {
        if (!dropped) setLoadError(error)
      })
    return () => {
      dropped = true
    }
  }, [version.kind])

  async function reload() {
    setProfiles(await profilesApi.list(version.kind))
  }

  async function run(what: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      await what()
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  const inUse = (error: unknown): string => {
    if (error instanceof ApiError && error.code === 'profile_in_use') {
      const versions = Array.isArray(error.values.versions) ? error.values.versions.join(', ') : ''
      return versions === '' ? errorText(t, error) : t('profiles.chooser.inUse', { versions })
    }
    return errorText(t, error)
  }

  return (
    <Dialog
      open
      wide
      title={t('profiles.chooser.title', { label: version.label })}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('profiles.chooser.cancel')}
          </Button>
          <Button
            onClick={() =>
              void run(async () => {
                await profilesApi.choose(version.id, chosen)
                onDone()
              })
            }
            disabled={profiles === null || busy || chosen === (version.profile_id ?? null)}
            loading={busy}
          >
            {t('profiles.chooser.save')}
          </Button>
        </>
      }
    >
      {profiles === null ? (
        loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : (
          <p className="flex items-center gap-2 text-sm text-mist-500">
            <Spinner /> {t('profiles.chooser.loading')}
          </p>
        )
      ) : (
        <div className="flex flex-col gap-4">
          {problem !== null && <FormMessage>{inUse(problem)}</FormMessage>}
          <p className="text-sm text-mist-400">{t('profiles.chooser.intro')}</p>

          <ul className="flex flex-col gap-2">
            {profiles.map((profile) => (
              <li key={profile.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2">
                <label className="flex min-w-0 flex-1 items-center gap-2">
                  <input
                    type="radio"
                    name="profile"
                    checked={chosen === profile.id}
                    onChange={() => setChosen(profile.id)}
                    className="accent-accent-500"
                    aria-label={t('profiles.chooser.take', { name: profile.name })}
                  />
                  <span className="min-w-0 wrap-anywhere text-mist-100">{profile.name}</span>
                  {profile.mode === 'expert' && <Badge tone="accent">{t('profiles.chooser.byHand')}</Badge>}
                  {profile.outdated && <Badge tone="bad">{t('profiles.line.outdated')}</Badge>}
                  {profile.used_by.length > 0 && <span className="text-xs text-mist-500">{t('profiles.chooser.usedBy', { versions: profile.used_by.join(', ') })}</span>}
                </label>
                <span className="flex flex-wrap items-center gap-1.5">
                  <Button size="sm" variant="ghost" onClick={() => setRenaming({ id: profile.id, name: profile.name })} aria-label={t('profiles.chooser.renameLabel', { name: profile.name })}>
                    {t('profiles.chooser.rename')}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setCopyOf(profile.id)} aria-label={t('profiles.chooser.copyLabel', { name: profile.name })}>
                    {t('profiles.chooser.copy')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy || profile.used_by.length > 0}
                    onClick={() =>
                      void run(async () => {
                        await profilesApi.removeProfile(profile.id)
                        if (chosen === profile.id) setChosen(null)
                        await reload()
                      })
                    }
                    aria-label={t('profiles.chooser.removeLabel', { name: profile.name })}
                  >
                    <Symbol name="trash" className="h-3.5 w-3.5" />
                  </Button>
                </span>
              </li>
            ))}
            <li className="flex items-center gap-2 rounded-xl border border-dashed border-ink-700 px-3 py-2">
              <label className="flex items-center gap-2">
                <input type="radio" name="profile" checked={chosen === null} onChange={() => setChosen(null)} className="accent-accent-500" />
                <span className="text-sm text-mist-400">{t('profiles.chooser.none')}</span>
              </label>
            </li>
          </ul>

          {renaming !== null && (
            <div className="flex flex-wrap items-end gap-2 rounded-xl border border-ink-700 p-3">
              <Field label={t('profiles.chooser.newName')} value={renaming.name} maxLength={200} onChange={(event) => setRenaming({ ...renaming, name: event.target.value })} />
              <Button
                size="sm"
                disabled={busy || renaming.name.trim() === ''}
                onClick={() =>
                  void run(async () => {
                    await profilesApi.rename(renaming.id, renaming.name.trim())
                    setRenaming(null)
                    await reload()
                  })
                }
              >
                {t('profiles.chooser.saveName')}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setRenaming(null)}>
                {t('profiles.chooser.cancel')}
              </Button>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-700 p-3">
            <input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder={t('profiles.chooser.addName')}
              aria-label={t('profiles.chooser.addName')}
              maxLength={200}
              className={INPUT + ' min-w-0 flex-1'}
            />
            <Button
              size="sm"
              disabled={busy || newName.trim() === ''}
              onClick={() =>
                void run(async () => {
                  const added = await profilesApi.add({ name: newName.trim(), kind: version.kind, ...(copyOf === null ? {} : { copy_of: copyOf }) })
                  setNewName('')
                  setCopyOf(null)
                  setChosen(added.id)
                  await reload()
                })
              }
            >
              <Symbol name="plus" className="h-3.5 w-3.5" />
              {copyOf === null ? t('profiles.chooser.add') : t('profiles.chooser.addCopy', { name: profiles.find((row) => row.id === copyOf)?.name ?? '' })}
            </Button>
            {copyOf !== null && (
              <Button size="sm" variant="ghost" onClick={() => setCopyOf(null)}>
                {t('profiles.chooser.addEmpty')}
              </Button>
            )}
          </div>
        </div>
      )}
    </Dialog>
  )
}
