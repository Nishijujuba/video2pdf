export const meta = {
  name: 'batch-bilibili-pdf',
  description: 'Batch-process Bilibili videos (including multi-P playlists) into independent PDFs via /bilibili-render-pdf.',
  phases: [
    { title: 'Expand', detail: 'Resolve each URL into per-P video URLs using yt-dlp' },
    { title: 'Render', detail: 'Call /bilibili-render-pdf for each individual P in parallel' },
  ],
}

const YT_DLP_PYTHON = 'D:/Project/video2pdf/kimi/.venv/Scripts/python.exe'
const COOKIES = 'C:/Users/juju/Downloads/www.bilibili.com_cookies.txt'

function normalizeUrl(raw) {
  const s = raw.trim()
  if (!s) return null
  if (s.startsWith('BV')) {
    return `https://www.bilibili.com/video/${s}`
  }
  if (s.startsWith('http')) return s
  return null
}

phase('Expand')
const rawInputs = Array.isArray(args) ? args : (typeof args === 'string' ? args.split(/\s+/) : [])
const inputUrls = rawInputs.map(normalizeUrl).filter(Boolean)

if (inputUrls.length === 0) {
  log('No valid Bilibili URLs provided. Expected args like ["BV1xx", "BV1yy?p=2", "https://..."]')
  return { ok: false, error: 'empty input' }
}

log(`Expanding ${inputUrls.length} input URL(s)...`)

const expandSchema = {
  type: 'object',
  properties: {
    urls: {
      type: 'array',
      items: { type: 'string' },
      description: 'List of individual per-P Bilibili URLs to render',
    },
    title: { type: 'string', description: 'Video/playlist title for logging' },
    count: { type: 'integer', description: 'Number of pages detected' },
  },
  required: ['urls', 'title', 'count'],
}

const expanded = await pipeline(
  inputUrls,
  url => agent(
    `You are a Bilibili URL expander. Use the Bash tool to run yt-dlp and determine how many pages (P) this Bilibili URL contains.\n\n` +
    `Command to run:\n` +
    `"${YT_DLP_PYTHON}" -m yt_dlp --cookies "${COOKIES}" --flat-playlist --print "%(webpage_url)s" "${url}"\n\n` +
    `If the command fails due to cookies, report that as an error and stop.\n` +
    `If the URL already has ?p=N, just return that single URL.\n` +
    `If it's a multi-P video, return each page as https://www.bilibili.com/video/BVXXXX?p=N .\n` +
    `Return structured JSON with urls, title (best title you can infer), and count.`,
    { phase: 'Expand', schema: expandSchema, label: `expand:${url}` }
  )
)

const allUrls = []
for (const r of expanded) {
  if (r && Array.isArray(r.urls)) {
    allUrls.push(...r.urls)
  }
}

if (allUrls.length === 0) {
  log('No per-P URLs were resolved. Aborting.')
  return { ok: false, error: 'no urls resolved', expanded }
}

log(`Resolved ${allUrls.length} individual video URL(s) to render.`)

phase('Render')
const renderSchema = {
  type: 'object',
  properties: {
    url: { type: 'string' },
    status: { type: 'string', enum: ['ok', 'error'] },
    outputDir: { type: 'string' },
    pdfPath: { type: 'string' },
    error: { type: 'string' },
  },
  required: ['url', 'status'],
}

const results = await pipeline(
  allUrls,
  url => agent(
    `Render this Bilibili video to PDF. Invoke the Skill tool with skill "bilibili-render-pdf" and args "${url}".\n\n` +
    `Wait for the skill to complete fully. Then report the result: output directory, PDF path if successful, or error message if it failed.`,
    { phase: 'Render', schema: renderSchema, label: `render:${url}` }
  )
)

const ok = results.filter(r => r && r.status === 'ok')
const failed = results.filter(r => !r || r.status !== 'ok')

log(`Batch complete: ${ok.length} succeeded, ${failed.length} failed.`)

return {
  ok: true,
  total: allUrls.length,
  succeeded: ok.length,
  failed: failed.length,
  results,
}
