/**
 * Genres und Sprachen kommen englisch, so wie Radarr sie nennt. Was die Oberflaeche
 * kennt, wird uebersetzt, alles andere steht da, wie es kommt. Die Schluessel stehen
 * woertlich da, damit `keys.test.ts` sie sieht.
 */

type T = (key: string) => string

export function genreText(t: T, name: string): string {
  switch (name.trim().toLowerCase()) {
    case 'action':
      return t('common.genre.action')
    case 'adventure':
      return t('common.genre.adventure')
    case 'animation':
      return t('common.genre.animation')
    case 'comedy':
      return t('common.genre.comedy')
    case 'crime':
      return t('common.genre.crime')
    case 'documentary':
      return t('common.genre.documentary')
    case 'drama':
      return t('common.genre.drama')
    case 'family':
      return t('common.genre.family')
    case 'fantasy':
      return t('common.genre.fantasy')
    case 'history':
      return t('common.genre.history')
    case 'horror':
      return t('common.genre.horror')
    case 'music':
      return t('common.genre.music')
    case 'mystery':
      return t('common.genre.mystery')
    case 'romance':
      return t('common.genre.romance')
    case 'science fiction':
      return t('common.genre.scifi')
    case 'thriller':
      return t('common.genre.thriller')
    case 'tv movie':
      return t('common.genre.tvMovie')
    case 'war':
      return t('common.genre.war')
    case 'western':
      return t('common.genre.western')
    default:
      return name
  }
}

export function languageText(t: T, name: string): string {
  switch (name.trim().toLowerCase()) {
    case 'german':
      return t('common.languageName.german')
    case 'english':
      return t('common.languageName.english')
    case 'french':
      return t('common.languageName.french')
    case 'spanish':
      return t('common.languageName.spanish')
    case 'italian':
      return t('common.languageName.italian')
    case 'japanese':
      return t('common.languageName.japanese')
    case 'korean':
      return t('common.languageName.korean')
    case 'chinese':
      return t('common.languageName.chinese')
    case 'dutch':
      return t('common.languageName.dutch')
    case 'swedish':
      return t('common.languageName.swedish')
    case 'danish':
      return t('common.languageName.danish')
    case 'norwegian':
      return t('common.languageName.norwegian')
    case 'finnish':
      return t('common.languageName.finnish')
    case 'polish':
      return t('common.languageName.polish')
    case 'russian':
      return t('common.languageName.russian')
    case 'portuguese':
      return t('common.languageName.portuguese')
    case 'turkish':
      return t('common.languageName.turkish')
    case 'unknown':
      return t('common.languageName.unknown')
    default:
      return name
  }
}
