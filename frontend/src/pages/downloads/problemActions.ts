/**
 * Die Aktionen je Problemcode, als Namen. Jeder Code, den der Server kennt, hat mindestens eine: nichts bleibt haengen
 *. `ProblemList.test.tsx` prueft das fuer jeden Code aus `PROBLEM_CODES`.
 *
 * `series`: ein Serien-Download. Scheitert dort eine Datei (etwa ein fremdes Video im Staffelordner), sind die anderen
 * schon abgelegt; "Rest nicht ablegen" beendet ihn dann wie bei offenen Dateien (seit der Durchsicht).
 *
 * `pathSearch`: bei `path_not_found` der Grund ohne Vorschlag (`twice`, `cut`, `nowhere`, `no_path`); null bei
 * aelteren Problemen, die ihn nicht tragen.
 */
export function problemActions(
  code: string,
  state: string,
  hasProposal: boolean,
  series = false,
  album = false,
  pathSearch: string | null = null,
): string[] {
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
      // Seit 29.09.2026 (Issue #3): kommt die Musik erst per Sync, legt "Erneut versuchen" sie ab.
      case 'no_audio':
        return ['removeAndBlock', ...retry, 'toTitle']
      case 'album_single_file':
        return ['toTitle', 'remove']
    }
  }
  switch (code) {
    // Ein fehlgeschlagener Download: sein Release ist schon gesperrt, also fuehrt der Weg zur Suche. "Erledigt"
    // nimmt ihn von den Problemen, im Verlauf bleibt er stehen.
    case 'download_failed':
      return ['toTitle', 'clear']
    // Seit 28.09.2026 (Issue #3): Erneut versuchen auch neben einem Vorschlag, und ohne Vorschlag den Ordner selbst
    // waehlen. Bei einer Seedbox kommen die Dateien erst spaeter.
    // Seit 12.10.2026 (Issue #12): Hat die Suche alles durchgesehen und nichts gefunden, oder nennt das Programm keinen
    // Pfad, hilft kein Ordner von Hand; dann bleibt Erneut versuchen, die Karte sagt, was zu tun ist.
    case 'path_not_found':
      if (hasProposal) return ['mapping', ...retry, 'remove']
      if (pathSearch === 'nowhere' || pathSearch === 'no_path') return [...retry, 'remove']
      return [...retry, 'mapFolder', 'remove']
    case 'packed':
      return ['removeAndBlock', 'toTitle']
    // Seit 29.09.2026 (Issue #3): kommt das Video erst per Sync, legt "Erneut versuchen" es ab.
    case 'no_video':
      return ['removeAndBlock', ...retry, 'toTitle']
    // Seit 24.09.2026: bei einer Serie sind die anderen Folgen schon abgelegt, "Rest nicht ablegen" beendet ihn.
    case 'file_truncated':
      return series ? ['finish', 'removeAndBlock'] : ['removeAndBlock', 'toTitle']
    // Seit 26.09.2026: Der Name passt nicht zum Film. Ablegen nur, wenn du es willst; sonst sperren.
    case 'title_mismatch':
      return ['fileAnyway', 'removeAndBlock', 'toTitle']
    case 'gone_from_client':
      return ['remove', 'toTitle']
    // Seit 20.10.2026 (Issue #20): das Programm hat ihn noch, nur in einer anderen Kategorie.
    case 'moved_in_client':
      return ['takeBack', 'remove']
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
