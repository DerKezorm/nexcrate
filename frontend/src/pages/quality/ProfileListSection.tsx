import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { profilesApi } from '../../api/profiles'
import type { MediaKind, ProfileBrief } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { ProfileDialog } from '../profiles/ProfileDialog'
import { VERSIONS_TAB_PATH } from '../settings/tabs'

const INPUT = 'rounded-lg border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none'

/**
 * Alle Profile einer Art an einem Ort: die aus dem Assistenten, die von Hand gesetzten und die aus einem Radarr
 * oder Sonarr eingelesenen. Hier wird umbenannt, kopiert und entfernt.
 *
 * ⚠️ Zugewiesen wird ein Profil **bei der Fassung** (Einstellungen → Fassungen → „Profil wählen"), nicht hier:
 * eine Fassung hat genau eines, und dasselbe Profil darf an mehreren hängen.
 */
export function ProfileListSection({ kind }: { kind: MediaKind }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [profiles, setProfiles] = useState<ProfileBrief[] | null>(null)
  const [open, setOpen] = useState<ProfileBrief | null>(null)
  const [newName, setNewName] = useState('')
  const [loadError, setLoadError] = useState<unknown>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      setProfiles(await profilesApi.list(kind))
    } catch (error: unknown) {
      setLoadError(error)
    }
  }, [kind])

  useEffect(() => {
    setProfiles(null)
    void load()
  }, [load])

  async function run(what: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      await what()
      await load()
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  const problemText = (error: unknown): string => {
    if (error instanceof ApiError && error.code === 'profile_in_use') {
      const versions = Array.isArray(error.values.versions) ? error.values.versions.join(', ') : ''
      return versions === '' ? errorText(t, error) : t('profiles.chooser.inUse', { versions })
    }
    return errorText(t, error)
  }

  return (
    <Section title={t('quality.profiles.title')} intro={t('quality.profiles.intro')}>
      {profiles === null ? (
        loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : (
          <p className="flex items-center gap-2 text-sm text-mist-500">
            <Spinner /> {t('quality.profiles.loading')}
          </p>
        )
      ) : (
        <div className="flex flex-col gap-3">
          {problem !== null && <FormMessage>{problemText(problem)}</FormMessage>}
          {profiles.length === 0 && <p className="text-sm text-mist-500">{t('quality.profiles.empty')}</p>}

          <ul className="flex flex-col gap-2">
            {profiles.map((profile) => (
              <li key={profile.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2">
                {/* Die ganze Zeile oeffnet; der Knopf daneben traegt den Namen fuer Vorleseprogramme. */}
                <button type="button" onClick={() => setOpen(profile)} className="min-w-0 flex-1 text-left">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 font-medium wrap-anywhere text-mist-100">{profile.name}</span>
                    {profile.empty === true ? (
                      <Badge tone="neutral">{t('quality.profiles.emptyBadge')}</Badge>
                    ) : (
                      profile.mode === 'expert' && <Badge tone="accent">{t('profiles.chooser.byHand')}</Badge>
                    )}
                    {profile.outdated && <Badge tone="bad">{t('profiles.line.outdated')}</Badge>}
                  </span>
                  <span className="mt-0.5 block text-xs text-mist-500">
                    {profile.used_by.length === 0 ? t('quality.profiles.unused') : t('quality.profiles.usedBy', { versions: profile.used_by.join(', ') })}
                  </span>
                </button>
                <span className="flex flex-wrap items-center gap-1.5">
                  <Button size="sm" variant="ghost" onClick={() => setOpen(profile)} aria-label={t('quality.profiles.openLabel', { name: profile.name })}>
                    {t('quality.profiles.open')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        const copy = await profilesApi.add({ name: t('quality.profiles.copyName', { name: profile.name }), kind, copy_of: profile.id })
                        notify(t('quality.profiles.copied', { name: copy.name }))
                      })
                    }
                    aria-label={t('profiles.chooser.copyLabel', { name: profile.name })}
                  >
                    {t('profiles.chooser.copy')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy || profile.used_by.length > 0}
                    onClick={() => void run(async () => void (await profilesApi.removeProfile(profile.id)))}
                    aria-label={t('profiles.chooser.removeLabel', { name: profile.name })}
                  >
                    <Symbol name="trash" className="h-3.5 w-3.5" />
                  </Button>
                </span>
              </li>
            ))}
          </ul>

          {/* Ein neues Profil entsteht hier, leer; gefuellt wird es im Fenster. */}
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-700 p-3">
            <input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder={t('quality.profiles.addName')}
              aria-label={t('quality.profiles.addName')}
              maxLength={200}
              className={INPUT + ' min-w-0 flex-1'}
            />
            <Button
              size="sm"
              disabled={busy || newName.trim() === ''}
              onClick={() =>
                void run(async () => {
                  const added = await profilesApi.add({ name: newName.trim(), kind })
                  setNewName('')
                  setOpen(added)
                })
              }
            >
              <Symbol name="plus" className="h-3.5 w-3.5" />
              {t('quality.profiles.add')}
            </Button>
          </div>

          <p className="text-xs text-mist-500">
            {t('quality.profiles.assignHint')}{' '}
            <Link to={VERSIONS_TAB_PATH} className="text-accent-400 hover:underline">
              {t('quality.profiles.toVersions')}
            </Link>
          </p>
        </div>
      )}

      {open !== null && (
        <ProfileDialog
          profile={open}
          onClose={() => setOpen(null)}
          onDone={() => {
            void load()
          }}
        />
      )}
    </Section>
  )
}
