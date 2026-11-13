// Image / font resource caches for the compositor.
const images = new Map<string, HTMLImageElement>()
let onReady: () => void = () => {}
export const setAssetReadyHandler = (fn: () => void) => { onReady = fn }

export function getImage(url: string): HTMLImageElement | null {
  let im = images.get(url)
  if (!im) {
    im = new Image()
    im.decoding = 'async'
    im.onload = () => onReady()
    im.src = url
    images.set(url, im)
  }
  return im.complete && im.naturalWidth > 0 ? im : null
}

const fontUrls = new Map<string, string>()
const fontState = new Map<string, 'loading' | 'ok' | 'fail'>()
export function registerFonts(list: { family: string; url?: string }[]) {
  for (const f of list) if (f.url && !fontUrls.has(f.family)) fontUrls.set(f.family, f.url)
}
export function ensureFont(family: string) {
  if (!family || fontState.has(family)) return
  const url = fontUrls.get(family)
  if (!url) { fontState.set(family, 'ok'); return } // assume installed system font
  fontState.set(family, 'loading')
  const ff = new FontFace(family, `url("${url}")`)
  ff.load().then((f) => { document.fonts.add(f); fontState.set(family, 'ok'); onReady() })
    .catch(() => { fontState.set(family, 'fail') })
}
