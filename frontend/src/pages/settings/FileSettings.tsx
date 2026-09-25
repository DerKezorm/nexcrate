import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { Segmented } from '../../components/Segmented'
import { CompanionSection } from '../files/CompanionSection'
import { FolderSection } from '../files/FolderSection'
import { NamingSection } from '../files/NamingSection'
import { MusicNamingSection } from '../files/MusicNamingSection'
import { SeriesNamingSection } from '../files/SeriesNamingSection'
import { RecycleBinList } from '../files/RecycleBinList'
import { RecycleSection } from '../files/RecycleSection'
import { RenameSection } from '../rename/RenameSection'
import { SubtitlesSection } from '../files/SubtitlesSection'
import { addressOfKind, fileTopicFromAddress, KIND_PARAM, kindFromAddress, VERSION_KINDS } from './tabs'
import { useKindLabel, type KindSection } from './useKindLabel'

/**
 * Reiter "Ordner und Benennung", aufgeteilt nach Thema: Ordner, Benennung, Untertitel, Begleitdateien (`release.nex`,
 * library from disk), Papierkorb. Die Reihe der Unterreiter zeichnet `SettingsPage`; welcher offen ist, steht in der
 * Adresse (`unter`). Wo sich die Medienarten unterscheiden (Ordner, Benennung), waehlt ein Umschalter Filme, Serien
 * oder Musik (`art`). Was fuer alle gilt, hat keinen Umschalter.
 */
export function FileSettings() {
  const { t } = useTranslation()
  const kindLabel = useKindLabel()
  const [params, setParams] = useSearchParams()
  const topic = fileTopicFromAddress(params.get('unter'), params.get('reiter'))
  const kind = kindFromAddress(params.get(KIND_PARAM))

  function changeKind(next: KindSection) {
    const nextParams = new URLSearchParams(params)
    if (next === 'movie') nextParams.delete(KIND_PARAM)
    else nextParams.set(KIND_PARAM, addressOfKind(next))
    setParams(nextParams, { replace: true })
  }

  const kindSwitch = <Segmented value={kind} options={VERSION_KINDS} onChange={changeKind} label={kindLabel} ariaLabel={t('settings.files.kindLabel')} />

  return (
    <div className="flex flex-col gap-4">
      {topic === 'folders' && <FolderSection kind={kind} kindSwitch={kindSwitch} />}
      {topic === 'naming' && (
        <>
          {kindSwitch}
          {kind === 'movie' && <NamingSection />}
          {kind === 'series' && <SeriesNamingSection />}
          {kind === 'music' && <MusicNamingSection />}
        </>
      )}
      {topic === 'rename' && (
        <>
          {kindSwitch}
          <RenameSection key={kind} kind={kind} />
        </>
      )}
      {topic === 'subtitles' && <SubtitlesSection />}
      {topic === 'companions' && <CompanionSection />}
      {topic === 'recycle' && (
        <>
          <RecycleSection />
          <RecycleBinList />
        </>
      )}
    </div>
  )
}
