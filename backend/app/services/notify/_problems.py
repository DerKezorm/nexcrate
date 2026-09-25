"""Generated once from the interface texts on 24.09.2026; keep both in step: a test compares them."""

# ruff: noqa: E501

from __future__ import annotations

#: The reasons of the problems, from the interface's texts (frontend/src/i18n/*/downloads.json, problems.reason),
#: with the client's name replaced; a card and a message say the same.
PROBLEMS: dict[str, dict[str, str]] = {
    'de': {
        'path_not_found': 'Der Download ist fertig, aber nexcrate findet die Dateien nicht unter dem Pfad, den das Download-Programm meldet.',
        'packed': 'Im Download liegen nur gepackte Dateien.',
        'no_video': 'Im Download liegt keine Videodatei.',
        'no_space': 'Dort, wo die Datei hin soll, ist nicht genug Platz frei.',
        'gone_from_client': 'Der Download ist verschwunden, das Download-Programm kennt ihn nicht mehr.',
        'client_error': 'Bei diesem Download meldet das Download-Programm einen Fehler.',
        'import_failed': 'Beim Ablegen ist etwas schiefgegangen.',
        'dangerous_file': 'Im Download lag eine gefährliche Datei.',
        'encrypted': 'Im Download liegt ein Archiv mit Passwort.',
        'client_unreachable': 'Nexcrate erreicht das Download-Programm gerade nicht.',
        'stalled': 'Gerade teilt niemand die Datei.',
        'unknown': 'Mit diesem Download stimmt etwas nicht.',
        'files_unassigned': 'Einige Dateien konnte nexcrate keiner Folge sicher zuordnen.',
        'other_series_suspected': 'Die Dateien heißen wie eine andere Serie.',
        'several_videos': 'Im Download liegen mehrere ähnlich große Videos.',
        'multi_part': 'Mehrteiliger Film (CD1, CD2).',
        'import_stalled': 'Das Ablegen kommt seit 30 Minuten nicht weiter.',
        'too_many_files': 'Der Download hat zu viele Dateien.',
        'no_audio': 'Im Download ist keine Musik.',
        'album_single_file': 'Das Album kam als eine Datei mit CUE-Blatt.',
        'album_not_better': 'Die neuen Dateien sind nicht besser als die vorhandenen.',
        'album_tracks_missing': 'Die Titellisten des Albums fehlen noch.',
        'download_failed': 'Der Download ist fehlgeschlagen. das Download-Programm hat ihn aufgegeben.',
        'file_truncated': 'Ein Video im Download ist abgeschnitten.',
    },
    'en': {
        'path_not_found': 'The download is done, but nexcrate cannot find the files under the path the download client reports.',
        'packed': 'The download only holds packed files.',
        'no_video': 'The download holds no video file.',
        'no_space': 'There is not enough free space where the file should go.',
        'gone_from_client': 'The download has disappeared, the download client does not know it any more.',
        'client_error': 'For this download, the download client reports an error.',
        'import_failed': 'Something went wrong while storing it.',
        'dangerous_file': 'The download held a dangerous file.',
        'encrypted': 'The download holds an archive with a password.',
        'client_unreachable': 'Nexcrate cannot reach the download client right now.',
        'stalled': 'Nobody is sharing the file right now.',
        'unknown': 'Something is wrong with this download.',
        'files_unassigned': 'Nexcrate could not safely assign some files to an episode.',
        'other_series_suspected': 'The files are named like another series.',
        'several_videos': 'The download holds several videos of a similar size.',
        'multi_part': 'A movie in parts (CD1, CD2).',
        'import_stalled': 'Filing away has not moved on for 30 minutes.',
        'too_many_files': 'The download has too many files.',
        'no_audio': 'The download holds no music.',
        'album_single_file': 'The album came as one file with a cue sheet.',
        'album_not_better': 'The new files are not better than those there.',
        'album_tracks_missing': "The album's track lists are still missing.",
        'download_failed': 'The download failed. the download client gave it up.',
        'file_truncated': 'A video in the download is cut off.',
    },
}

#: Why a download failed, from the interface's texts (history.failed), with the client's name replaced.
FAILED: dict[str, dict[str, str]] = {
    'de': {
        'client_failed': 'Der Download ist fehlgeschlagen, das meldet das Download-Programm. nexcrate hat das Release gesperrt.',
        'encrypted': 'Das Release war mit einem Passwort geschützt. nexcrate hat es gesperrt.',
        'not_taken': 'Das Download-Programm hat das Release nicht rechtzeitig angenommen und später nie gezeigt. Das Release ist nicht gesperrt.',
    },
    'en': {
        'client_failed': 'The download failed, as the download client reports. nexcrate blocked the release.',
        'encrypted': 'The release was protected with a password. nexcrate blocked it.',
        'not_taken': 'The download client did not take the release in time and never showed it later. The release is not blocked.',
    },
}
