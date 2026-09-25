import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Version } from '../../api/types'
import { Section } from '../../components/ui'
import { ReleaseChecker } from '../checker/ReleaseChecker'
import { SeriesChecker } from '../checker/SeriesChecker'
import { profilesApi } from '../../api/profiles'
import type { ProfileBrief } from '../../api/types'
import { ProfileChooser } from '../profiles/ProfileChooser'
import { ProfileDialog } from '../profiles/ProfileDialog'
import type { ProfileActions } from '../profiles/ProfileRow'
import { useVersions } from '../versions/useVersions'
import { VersionList } from '../versions/VersionList'

/**
 * Fassungen je Medienart, als Unterreiter in `SettingsPage`. Filme und Serien: Name, Profil je Fassung (Assistent,
 * Export, Import) und darunter der Release-Pruefer der Art. Musik sagt dort in einem Satz, dass sie spaeter kommt.
 */
export function VersionSettings({ kind }: { kind: 'movie' | 'series' }) {
  return kind === 'series' ? <SeriesVersions /> : <MovieVersions />
}

function MovieVersions() {
  const { t } = useTranslation()
  const { versions, error, reload } = useVersions('movie')
  const [openProfile, setOpenProfile] = useState<ProfileBrief | null>(null)
  const [choosingFor, setChoosingFor] = useState<Version | null>(null)
  // Das Profil-Fenster braucht das Profil selbst; die Fassung kennt nur seine Nummer.
  const openFor = useCallback(async (version: Version) => {
    if (version.profile_id == null) return
    const rows = await profilesApi.list(version.kind)
    const found = rows.find((row) => row.id === version.profile_id)
    if (found !== undefined) setOpenProfile(found)
  }, [])
  const profileActions = useMemo<ProfileActions>(
    () => ({ onOpen: (version) => void openFor(version), onChoose: setChoosingFor }),
    [openFor],
  )

  function done() {
    setOpenProfile(null)
    setChoosingFor(null)
    reload()
  }

  return (
    <div className="flex flex-col gap-4">
      <Section title={t('settings.versions.title')} intro={t('settings.versions.intro')}>
        <VersionList kind="movie" versions={versions} error={error} onChanged={reload} profileActions={profileActions} />
      </Section>

      {versions !== null && versions.length > 0 && <ReleaseChecker versions={versions} onSetUpProfile={(version) => void openFor(version)} />}

      {openProfile && <ProfileDialog profile={openProfile} onClose={() => setOpenProfile(null)} onDone={done} />}
      {choosingFor && <ProfileChooser version={choosingFor} onClose={() => setChoosingFor(null)} onDone={done} />}
    </div>
  )
}

function SeriesVersions() {
  const { t } = useTranslation()
  const { versions, error, reload } = useVersions('series')
  const [openProfile, setOpenProfile] = useState<ProfileBrief | null>(null)
  const [choosingFor, setChoosingFor] = useState<Version | null>(null)
  // Das Profil-Fenster braucht das Profil selbst; die Fassung kennt nur seine Nummer.
  const openFor = useCallback(async (version: Version) => {
    if (version.profile_id == null) return
    const rows = await profilesApi.list(version.kind)
    const found = rows.find((row) => row.id === version.profile_id)
    if (found !== undefined) setOpenProfile(found)
  }, [])
  const profileActions = useMemo<ProfileActions>(
    () => ({ onOpen: (version) => void openFor(version), onChoose: setChoosingFor }),
    [openFor],
  )

  function done() {
    setOpenProfile(null)
    setChoosingFor(null)
    reload()
  }

  return (
    <div className="flex flex-col gap-4">
      <Section title={t('series.versions.title')} intro={t('series.versions.intro')}>
        <VersionList
          kind="series"
          versions={versions}
          error={error}
          onChanged={reload}
          emptyText={t('series.versions.empty')}
          profileActions={profileActions}
        />
      </Section>

      {versions !== null && versions.length > 0 && <SeriesChecker versions={versions} onSetUpProfile={(version) => void openFor(version)} />}

      {openProfile && <ProfileDialog profile={openProfile} onClose={() => setOpenProfile(null)} onDone={done} />}
      {choosingFor && <ProfileChooser version={choosingFor} onClose={() => setChoosingFor(null)} onDone={done} />}
    </div>
  )
}
