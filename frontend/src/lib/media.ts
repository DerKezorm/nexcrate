import type { MediaAudio, MediaInfo } from '../api/types'

const LANGUAGE_NAMES = new Map<string, Intl.DisplayNames | null>()

/** The name of an ISO 639-1 language in the interface's language, else the code as it came. */
export function languageNameOf(code: string, language: string): string {
  const clean = code.trim().toLowerCase()
  if (clean === '') return code
  let names = LANGUAGE_NAMES.get(language)
  if (names === undefined) {
    try {
      names = new Intl.DisplayNames([language], { type: 'language', fallback: 'none' })
    } catch {
      names = null
    }
    LANGUAGE_NAMES.set(language, names)
  }
  if (names === null) return clean
  try {
    return names.of(clean) ?? clean
  } catch {
    return clean
  }
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}

/** One audio stream in words: "Deutsch TrueHD Atmos 7.1". Parts that are missing are left out. */
function audioText(stream: MediaAudio, language: string): string {
  const code = text(stream.language)
  const parts = [code !== null ? languageNameOf(code, language) : null, text(stream.codec), text(stream.channels)]
  return parts.filter((part): part is string => part !== null).join(' ')
}

/**
 * The stored media data in one line, as the title page and the assign dialog show it: "1920 × 800, HEVC 10 Bit,
 * HDR10, Deutsch TrueHD Atmos 7.1, Englisch DTS-HD MA 5.1". `bitWord` is the word for "Bit" in the interface's
 * language. null when nothing readable is in it.
 */
export function mediaSummary(media: MediaInfo | null | undefined, language: string, bitWord: string): string | null {
  if (!media || typeof media !== 'object') return null
  const parts: string[] = []
  const video = media.video
  if (video && typeof video === 'object') {
    if (typeof video.width === 'number' && typeof video.height === 'number' && video.width > 0 && video.height > 0) {
      parts.push(`${video.width} × ${video.height}`)
    }
    const codec = text(video.codec)
    const depth = typeof video.bit_depth === 'number' && video.bit_depth > 0 ? `${video.bit_depth} ${bitWord}` : null
    if (codec !== null || depth !== null) parts.push([codec, depth].filter((part): part is string => part !== null).join(' '))
    const range = text(video.dynamic_range)
    if (range !== null) parts.push(range)
  }
  for (const stream of Array.isArray(media.audio) ? media.audio : []) {
    if (!stream || typeof stream !== 'object') continue
    const line = audioText(stream, language)
    if (line !== '') parts.push(line)
  }
  return parts.length > 0 ? parts.join(', ') : null
}
