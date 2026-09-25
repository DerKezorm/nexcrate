import type { Proposal, TmdbResult } from '../../api/types'
import { proposalPoster } from './diskText'

/** A movie the assign dialog can assign: from a proposal, from TMDB's search, or from the add dialog. */
export type PickedMovie = {
  tmdb_id: number
  title: string
  year: number | null
  poster_url: string | null
  title_id: number | null
  /** How the movie was found, for a proposal. null for a TMDB result. */
  from: string | null
}

export function pickedFromProposal(proposal: Proposal): PickedMovie {
  return { tmdb_id: proposal.tmdb_id, title: proposal.title, year: proposal.year, poster_url: proposalPoster(proposal), title_id: proposal.title_id, from: proposal.from }
}

export function pickedFromResult(result: TmdbResult): PickedMovie {
  return { tmdb_id: result.tmdb_id, title: result.title, year: result.year, poster_url: result.poster_url, title_id: result.title_id, from: null }
}
