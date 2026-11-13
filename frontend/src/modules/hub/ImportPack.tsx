import { useEffect, useRef, useState } from 'react'
import { FolderInput, Upload } from 'lucide-react'
import { api, useJob } from '../../api/client'
import { Field, JobBar } from '../../shared/ui'
import type { LicenseInfo } from './api'
import { toast } from './common'

export interface ImportPreset { source: string; license: string; packName?: string; nonce: number }
interface ImportResult { pack: string; added: number; duplicates: number; skipped: number; errors: string[]; anim_import: { available: boolean; submitted: number } }

const SOURCES = [
  { id: 'local', label: 'Other / local files', url: '' },
  { id: 'kenney', label: 'Kenney pack', url: 'https://kenney.nl/assets' },
  { id: 'quaternius', label: 'Quaternius pack', url: 'https://quaternius.com' },
  { id: 'mixamo', label: 'Mixamo FBX folder', url: 'https://www.mixamo.com' },
  { id: 'truebones', label: 'Truebones pack', url: 'https://truebones.gumroad.com' },
]
const DEFAULT_LIC: Record<string, string> = { kenney: 'CC0-1.0', quaternius: 'custom:quaternius-qal-1.0', mixamo: 'custom:mixamo', truebones: 'custom:truebones', local: 'CC0-1.0' }
const HINTS = [['', 'Auto-detect'], ['sprite', 'Images are sprites (2D packs)'], ['texture', 'Images are textures'], ['sfx', 'Audio is sound FX'], ['music', 'Audio is music'], ['voice', 'Audio is voice'], ['animation', 'Models are animations']]

export default function ImportPack({ preset, onChanged }: { preset: ImportPreset | null; onChanged: () => void }) {
  const [source, setSource] = useState('local')
  const [license, setLicense] = useState('CC0-1.0')
  const [licenses, setLicenses] = useState<LicenseInfo[]>([])
  const [path, setPath] = useState('')
  const [packName, setPackName] = useState('')
  const [author, setAuthor] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [hint, setHint] = useState('')
  const [tags, setTags] = useState('')
  const file = useRef<HTMLInputElement>(null)
  const job = useJob<ImportResult>()

  useEffect(() => { api.get<LicenseInfo[]>('/api/hub/licenses').then(setLicenses).catch(() => {}) }, [])
  useEffect(() => {
    if (!preset) return
    setSource(preset.source); setLicense(preset.license || DEFAULT_LIC[preset.source] || 'CC0-1.0')
    if (preset.packName) setPackName(preset.packName)
  }, [preset])
  const pickSource = (s: string) => { setSource(s); setLicense(DEFAULT_LIC[s] ?? 'CC0-1.0'); if (s === 'mixamo') setHint('animation') }
  const li = licenses.find((l) => l.id === license)

  const done = (r: ImportResult | null | undefined) => {
    if (!r) return
    toast(`Imported ${r.added} asset(s) from ${r.pack}` + (r.duplicates ? ` · ${r.duplicates} duplicates` : '') + (r.errors.length ? ` · ${r.errors.length} errors` : '') +
      (r.anim_import.submitted ? ` · ${r.anim_import.submitted} sent to the Animation module` : ''))
    onChanged()
  }
  const runPath = async () => {
    try {
      const j = await job.run(() => api.post('/api/hub/import', {
        path: path.trim().replace(/^"|"$/g, ''), source, license, pack_name: packName || null, author: author || null, source_url: sourceUrl || null,
        kind_hint: hint || null, tags: tags.split(',').map((t) => t.trim()).filter(Boolean), to_anim: source === 'mixamo' || source === 'truebones',
      }))
      done(j.result)
    } catch { /* JobBar */ }
  }
  const runUpload = async (f: File) => {
    const fd = new FormData()
    fd.append('file', f); fd.append('source', source); fd.append('license', license); fd.append('pack_name', packName || f.name.replace(/\.zip$/i, ''))
    fd.append('author', author); fd.append('source_url', sourceUrl); fd.append('kind_hint', hint)
    try {
      const j = await job.run(async () => {
        const r = await fetch('/api/hub/import/upload', { method: 'POST', body: fd })
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText)
        return r.json()
      })
      done(j.result)
    } catch { /* JobBar */ }
  }

  return (
    <div className="hub-scroll" style={{ maxWidth: 900 }}>
      <div className="hub-section">
        <h3>Import a pack you downloaded</h3>
        <p className="hub-note" style={{ marginTop: 0 }}>For sources without an API or that don't allow automated downloads (Kenney, Quaternius, Mixamo, Truebones) and for any local folder:
          download the zip in your browser, then import it here. Every file gets the licence you choose below; models keep their previews, audio/images are also added to the media library.
          Mixamo/Truebones FBX files are also handed to the Animation module when it is available.</p>
        <Field label="What is it?">
          <div className="hub-pill-row">
            {SOURCES.map((s) => <button key={s.id} className={`hub-pill${source === s.id ? ' on' : ''}`} onClick={() => pickSource(s.id)}>{s.label}</button>)}
          </div>
        </Field>
        <div className="hub-form">
          <Field label="Licence applied to every file">
            <select className="select" value={license} onChange={(e) => setLicense(e.target.value)}>
              {licenses.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
            </select>
          </Field>
          <Field label="Pack name"><input className="input" value={packName} onChange={(e) => setPackName(e.target.value)} placeholder="e.g. Kenney Nature Kit" /></Field>
          <Field label="Author (for attribution)"><input className="input" value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="e.g. Kenney" /></Field>
          <Field label="Pack page URL"><input className="input" value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder={SOURCES.find((s) => s.id === source)?.url || 'https://…'} /></Field>
          <Field label="File kinds"><select className="select" value={hint} onChange={(e) => setHint(e.target.value)}>{HINTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          <Field label="Extra tags (comma separated)"><input className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="low-poly, fantasy" /></Field>
        </div>
        {li && <p className="hub-note">{li.label}: commercial use {li.commercial_ok ? 'allowed' : 'NOT allowed'} · modifications {li.modifiable ? 'allowed' : 'not allowed'} · credit {li.attribution_required ? 'required' : 'not required'}
          {li.url && <> · <a href={li.url} target="_blank" rel="noreferrer">licence text</a></>}</p>}
        <Field label="Folder, .zip or file path on this PC">
          <div className="row">
            <input className="input" value={path} onChange={(e) => setPath(e.target.value)} placeholder={source === 'mixamo' ? 'C:\\Users\\me\\Downloads\\mixamo' : 'C:\\Users\\me\\Downloads\\kenney_nature-kit.zip'} />
            <button className="btn primary" disabled={!path.trim() || job.busy} onClick={runPath}><FolderInput size={13} />{source === 'mixamo' ? 'Import FBX folder' : 'Import'}</button>
          </div>
        </Field>
        <div className="row">
          <span className="faint" style={{ fontSize: 12 }}>or</span>
          <button className="btn" disabled={job.busy} onClick={() => file.current?.click()}><Upload size={13} />Upload a .zip…</button>
          <input ref={file} type="file" accept=".zip" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) runUpload(f); e.target.value = '' }} />
        </div>
        <JobBar job={job.job} error={job.error} onCancel={job.cancel} />
        {job.job?.status === 'done' && job.job.result && job.job.result.errors.length > 0 && <div className="hub-log" style={{ marginTop: 8 }}>{job.job.result.errors.join('\n')}</div>}
      </div>
    </div>
  )
}
