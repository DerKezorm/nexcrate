import { useState } from 'react'

import { AutoTagSettings } from './AutoTagSettings'
import { TagSettings } from './TagSettings'

/** Der Reiter Tags: die Liste der Tags und darunter die Regeln, die sie vergeben; nach einer Regel zaehlt die Liste neu. */
export function TagsTab() {
  const [round, setRound] = useState(0)
  return (
    <>
      <TagSettings key={round} />
      <AutoTagSettings onChanged={() => setRound((value) => value + 1)} />
    </>
  )
}
