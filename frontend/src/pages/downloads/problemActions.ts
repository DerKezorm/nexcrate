/**
 * Die Aktionen je Problemcode, als Namen. Jeder Code, den der Server kennt, hat mindestens eine: nichts bleibt haengen
 * (S4, Entscheidung 32). `ProblemList.test.tsx` prueft das fuer jeden Code aus `PROBLEM_CODES`.
 *
 * `series`: ein Serien-Download. Scheitert dort eine Datei (etwa ein fremdes Video im Staffelordner), sind die anderen
 * schon abgelegt; "Rest nicht ablegen" beendet ihn dann wie bei offenen Dateien (seit der Durchsicht).
 */
export function problemActions(code: string, state: string, hasProposal: boolean, series = false, album = false): string[] {
  const retry = state === 'problem' || state === 'completed' ? ['retry'] : []
  if (album) {
    // Seit M4: ein Albendownload. Zuordnen oeffnet den Dialog mit Titellisten, "Rest nicht ablegen" beendet ihn.
    switch (code) {
      case 'files_unassigned':
      case 'album_not_better':
        return ['assignAlbum', 'finish', 'remove']
      case 'album_tracks_missing':
      case 'import_failed':
      case 'import_stalled':
        return [...retry, 'finish', 'remove']
      case 'no_audio':
        return ['removeAndBlock', 'toTitle']
      case 'album_single_file':
        return ['toTitle', 'remove']
    }
  }
  switch (code) {
    // Ein fehlgeschlagener Download: sein Release ist schon gesperrt, also fuehrt der Weg zur Suche. "Erledigt"
    // nimmt ihn von den Problemen, im Verlauf bleibt er stehen.
    case 'download_failed':
      return ['toTitle', 'clear']
    case 'path_not_found':
      return hasProposal ? ['mapping', 'remove'] : [...retry, 'remove']
    case 'packed':
    case 'no_video':
      return ['removeAndBlock', 'toTitle']
    // Seit 24.09.2026: bei einer Serie sind die anderen Folgen schon abgelegt, "Rest nicht ablegen" beendet ihn.
    case 'file_truncated':
      return series ? ['finish', 'removeAndBlock'] : ['removeAndBlock', 'toTitle']
    case 'gone_from_client':
      return ['remove', 'toTitle']
    case 'dangerous_file':
    case 'encrypted':
    case 'multi_part':
      return ['toTitle', 'remove']
    case 'client_unreachable':
      return ['toClients']
    case 'client_error':
    case 'stalled':
    case 'too_many_files':
      return ['remove']
    case 'files_unassigned':
      return ['assign', 'finish', 'remove']
    case 'other_series_suspected':
      return ['assign', 'removeAndBlock']
    case 'several_videos':
      return ['choose', 'remove']
    case 'import_stalled':
      return [...retry, 'remove']
    case 'import_failed':
      return series ? [...retry, 'finish', 'remove'] : [...retry, 'remove']
    default:
      return [...retry, 'remove']
  }
}
