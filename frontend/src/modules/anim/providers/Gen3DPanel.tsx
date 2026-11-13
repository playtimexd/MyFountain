import { useEffect, useMemo, useState } from 'react'
import { Box, Download, Eye, Wallet } from 'lucide-react'
import { api, useJob } from '../../../api/client'
import { Field, JobBar } from '../../../shared/ui'
import type { JobInfo, MediaRow } from '../../../types/api'
import GlbViewer from './GlbViewer'
import { CostConfirm, ImagePicker, Note, ProviderStatus, useEstimate, useProviders } from './providerUi'
import type { ProviderPanelProps } from './VideoGenPanel'

export type { ProviderPanelProps } from './VideoGenPanel'

const EXAMPLES = [
  'A brown bear standing on all four legs, full body, realistic fur, neutral pose',
  'A wolf standing on all fours, full body, side view',
  'A medieval knight in plain armor, T-pose, full body',
  'A cartoon dragon with wings spread, full body',
]
const RIG_LABEL: Record<string, string> = {
  auto: 'Auto-detect (Tripo rig check)', biped: 'Biped (humanoid)', quadruped: 'Quadruped (bear, dog, horse...)', hexapod: 'Hexapod (6 legs)',
  octopod: 'Octopod (8 legs)', avian: 'Avian (bird / winged)', serpentine: 'Serpentine (snake)', aquatic: 'Aquatic (fish)', others: 'Other topology',
}

interface GenResult {
  file: string; name: string; url: string; provider: string; rigged: boolean; rig_type?: string | null
  credits?: number | null; credit_unit?: string
  rig?: { id: string; name: string }; rig_import?: { imported: boolean; reason?: string }
  animations?: Record<string, { url: string }>; rigged_file?: { url: string }; model?: { url: string }
}

export default function Gen3DPanel({ onAddRig }: ProviderPanelProps) {
  const { items: providers, loading, err, reload } = useProviders('/api/gen3d/providers')
  const [provider, setProvider] = useState('auto')
  const [mode, setMode] = useState<'prompt' | 'image'>('prompt')
  const [prompt, setPrompt] = useState(EXAMPLES[0])
  const [image, setImage] = useState<MediaRow | null>(null)
  const [rigOn, setRigOn] = useState(true)
  const [rigType, setRigType] = useState('quadruped')
  const [animation, setAnimation] = useState('')
  const [quality, setQuality] = useState('draft')
  const [localQuality, setLocalQuality] = useState('standard')
  const [name, setName] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [showReq, setShowReq] = useState(false)
  const [result, setResult] = useState<GenResult | null>(null)
  const [viewUrl, setViewUrl] = useState<string | null>(null)
  const [balance, setBalance] = useState<string>('')
  const [importMsg, setImportMsg] = useState<string>('')
  const { job, error, run, cancel, busy } = useJob<GenResult>()
  const imp = useJob<{ imported: boolean; rig?: { id: string; name: string }; reason?: string }>()

  const meshy = providers.find((p) => p.id === 'meshy')
  const tripo = providers.find((p) => p.id === 'tripo')
  const localP = providers.find((p) => p.id === 'local')
  // mirror the server: no cloud keys -> local TRELLIS.2; animal rigs and "keys only for Tripo" go to Tripo, otherwise Meshy first
  const resolved = useMemo(() => {
    if (provider !== 'auto') return providers.find((p) => p.id === provider)
    if (!meshy?.key_set && !tripo?.key_set && localP?.available) return localP
    const animal = rigOn && rigType !== 'biped'
    if (animal || (rigOn && !meshy?.key_set)) return tripo
    return meshy?.available || !tripo?.available ? meshy : tripo
  }, [provider, providers, rigOn, rigType, meshy, tripo, localP])
  const isLocal = resolved?.id === 'local'
  const textOk = !isLocal || !!resolved?.capabilities?.text_to_3d

  const rigTypes: string[] = useMemo(() => {
    const r = (resolved?.capabilities?.rig as string[] | undefined) ?? ['biped']
    return r
  }, [resolved])
  useEffect(() => { if (rigOn && resolved && !rigTypes.includes(rigType)) setRigType(rigTypes.includes('quadruped') ? 'quadruped' : rigTypes[0]) }, [rigTypes, resolved, rigOn, rigType])
  const animals = !!resolved?.capabilities?.animals_rig
  const animOptions = rigType === 'biped' ? ['walk', 'run'] : ['walk']

  const usingImage = mode === 'image'
  const body = {
    prompt: usingImage ? null : prompt.trim(), image_mediaId: usingImage ? image?.id ?? null : null, provider,
    rig: rigOn && !isLocal ? { type: rigType } : null, quality: resolved?.id === 'meshy' ? quality : isLocal ? localQuality : 'draft',
    animation: rigOn && !isLocal && animation ? animation : null, name: name.trim() || null,
  }
  const hasInput = usingImage ? !!image : prompt.trim().length > 0
  const { est, req, problem } = useEstimate('/api/gen3d', body, hasInput)
  const canGo = !busy && hasInput && !!resolved?.available && !problem && (confirm || isLocal) && (usingImage || textOk)

  const generate = async () => {
    setResult(null); setViewUrl(null); setImportMsg('')
    try {
      const done = await run(() => api.post('/api/gen3d', { ...body, confirm_cost: confirm && !isLocal }))
      if (done.result) { setResult(done.result); setViewUrl(done.result.url) }
      reload()
    } catch { /* shown by JobBar */ }
  }

  const checkBalance = async () => {
    if (!resolved) return
    setBalance('...')
    try {
      const b = await api.get<{ balance: number; frozen?: number; unit: string }>(`/api/gen3d/balance?provider=${resolved.id}`)
      setBalance(`${b.balance} ${b.unit}${b.frozen ? ` (${b.frozen} frozen)` : ''}`)
    } catch (e: any) { setBalance(e.message || 'failed') }
  }

  const addToRigs = async () => {
    if (!result) return
    if (result.rig) { onAddRig?.(result.rig); setImportMsg(`Added "${result.rig.name}" to the rig library.`); return }
    setImportMsg('')
    try {
      const done = await imp.run(() => api.post<JobInfo>('/api/gen3d/rig-import', { file: result.name }))
      const r = done.result
      if (r?.imported && r.rig) { onAddRig?.(r.rig); setImportMsg(`Added "${r.rig.name}" to the rig library.`) }
      else setImportMsg(r?.reason || 'The rig library did not return a rig.')
    } catch { /* shown by JobBar */ }
  }

  return (
    <div style={{ padding: 12, overflow: 'auto', height: '100%' }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <Box size={16} /><b>3D model generation</b>
        <span className="faint" style={{ fontSize: 11 }}>text / image to 3D model · local TRELLIS.2 (free) or cloud with auto-rig (credits)</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
        <div style={{ flex: '1 1 340px', minWidth: 0 }}>
          <Field label="Provider">
            <select className="select" value={provider} onChange={(e) => { setProvider(e.target.value); setConfirm(false) }}>
              <option value="auto">Auto (local without cloud keys; Tripo for animals, Meshy for humanoids)</option>
              {providers.map((p) => <option key={p.id} value={p.id}>{p.name}{p.available ? '' : p.needs_key ? ' - needs API key' : ' - not installed'}</option>)}
            </select>
          </Field>
          {err && <Note tone="err">Could not reach the engine: {err}. Restart the engine if the gen3d router was just added.</Note>}
          {resolved && <ProviderStatus p={resolved} reload={reload} loading={loading} />}

          <div className="seg-tabs" style={{ marginBottom: 10 }}>
            <button className={mode === 'prompt' ? 'on' : ''} onClick={() => setMode('prompt')}>Text prompt</button>
            <button className={mode === 'image' ? 'on' : ''} onClick={() => setMode('image')}>From image</button>
          </div>
          {mode === 'prompt' ? (
            <>
              <Field label="Describe the character">
                <textarea className="textarea" rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              </Field>
              <div className="row" style={{ flexWrap: 'wrap', gap: 4, margin: '-4px 0 8px' }}>
                {EXAMPLES.map((x) => <button key={x} className="chip" style={{ cursor: 'pointer', border: 0 }} onClick={() => setPrompt(x)} title={x}>{x.split(',')[0]}</button>)}
              </div>
              {isLocal && <Note tone={textOk ? undefined : 'warn'}>{String(resolved?.capabilities?.text_note ?? '')}</Note>}
            </>
          ) : (
            <ImagePicker label={isLocal ? 'Image of the object (one subject, whole body visible; the background is removed automatically)'
              : 'Reference image (a clear full-body view on a plain background works best)'} value={image?.id ?? null} onChange={setImage} />
          )}

          {isLocal && (
            <>
              <Field label="Quality (TRELLIS.2)">
                <select className="select" value={localQuality} onChange={(e) => setLocalQuality(e.target.value)}>
                  <option value="draft">Draft (~2 min, 100k faces, 1K textures)</option>
                  <option value="standard">Standard (~7 min, 300k faces, 2K textures)</option>
                  <option value="high">High (700k faces, 4K textures)</option>
                </select>
              </Field>
              <Note>{String(resolved?.capabilities?.rig_note ?? '')}</Note>
            </>
          )}
          {!isLocal && <label className="row" style={{ cursor: 'pointer', marginBottom: 6 }}>
            <input type="checkbox" checked={rigOn} onChange={(e) => setRigOn(e.target.checked)} />
            <b style={{ fontSize: 12 }}>Auto-rig the result</b>
          </label>}
          {rigOn && !isLocal && (
            <>
              <div className="row" style={{ alignItems: 'flex-start' }}>
                <div className="field grow"><label>Skeleton type</label>
                  <select className="select" value={rigType} onChange={(e) => setRigType(e.target.value)}>
                    {rigTypes.map((t) => <option key={t} value={t}>{RIG_LABEL[t] ?? t}</option>)}
                  </select></div>
                <div className="field" style={{ width: 130 }}><label>Preset clip</label>
                  <select className="select" value={animation} onChange={(e) => setAnimation(e.target.value)}>
                    <option value="">None</option>{animOptions.map((a) => <option key={a} value={a}>{a}</option>)}
                  </select></div>
              </div>
              {!animals && <Note tone="warn">{String(resolved?.capabilities?.rig_note ?? 'This provider can only rig humanoid characters.')}</Note>}
              {animals && rigType !== 'biped' && <Note>{String(resolved?.capabilities?.rig_note ?? '')}</Note>}
            </>
          )}
          {resolved?.id === 'meshy' && (
            <Field label="Quality (Meshy)">
              <select className="select" value={quality} onChange={(e) => setQuality(e.target.value)}>
                <option value="draft">Draft (cheaper, meshy-6-lite)</option><option value="standard">Standard (meshy-7.1)</option>
              </select>
            </Field>
          )}
          <Field label="File name (optional)"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. brown_bear" /></Field>

          {isLocal ? (est?.note && <div className="faint" style={{ fontSize: 11, marginBottom: 8 }}>{est.note}</div>) : (
            <CostConfirm checked={confirm} onChange={setConfirm} est={est}
              text={`I understand this spends credits on my ${resolved?.name.split(' - ')[0] ?? 'cloud'} account.`} />
          )}
          {problem && <div className="err" style={{ marginBottom: 8 }}>{problem}</div>}
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <button className="btn primary" disabled={!canGo} onClick={generate}>Generate 3D model</button>
            <button className="btn ghost sm" onClick={() => setShowReq((v) => !v)} disabled={!req}><Eye size={12} /> {showReq ? 'Hide' : 'Show'} requests</button>
            {!isLocal && <button className="btn ghost sm" onClick={checkBalance} disabled={!resolved?.key_set} title="Ask the provider for your remaining credits (free)"><Wallet size={12} /> Balance</button>}
            {balance && <span className="chip">{balance}</span>}
          </div>
          {showReq && !!req && (
            <pre style={{ fontSize: 11, maxHeight: 220, overflow: 'auto', background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 5, padding: 8, userSelect: 'text' }}>{JSON.stringify(req, null, 2)}</pre>
          )}
          <JobBar job={job} error={error} onCancel={cancel} />
          {busy && <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>{isLocal
            ? 'Generating on your GPU (Draft 2-3 min, Standard ~7 min, High longer). Cancelling stops ComfyUI.'
            : 'Generation takes a few minutes. Cancelling stops waiting; tasks already started on the provider may still be charged.'}</div>}
        </div>

        <div style={{ flex: '1 1 380px', minWidth: 0 }}>
          <div className="faint" style={{ fontSize: 12, marginBottom: 4 }}>Preview</div>
          <GlbViewer url={viewUrl} height={340} />
          {result && (
            <div style={{ marginTop: 8 }}>
              <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                <span className="chip">{result.provider}</span>
                <span className="chip">{result.rigged ? `rigged (${result.rig_type ?? '?'})` : 'no rig'}</span>
                {result.credits != null && <span className="chip">{result.credits} {result.credit_unit ?? 'credits'} used</span>}
              </div>
              <div className="faint" style={{ fontSize: 11, margin: '4px 0', wordBreak: 'break-all' }}>{result.file}</div>
              <div className="row" style={{ flexWrap: 'wrap' }}>
                {result.rigged
                  ? <button className="btn primary sm" disabled={imp.busy} onClick={addToRigs}>Add to rig library</button>
                  : <span className="chip" title="Rig it with Tripo/Meshy or Mixamo first; it is already in 3D Models > Model library">Unrigged - in Model library</span>}
                <a className="btn sm" href={result.url} download={result.name}><Download size={12} /> Download GLB</a>
                {result.model && result.rigged && <button className="btn ghost sm" onClick={() => setViewUrl(viewUrl === result.url ? result.model!.url : result.url)}>{viewUrl === result.url ? 'View unrigged mesh' : 'View rigged'}</button>}
                {Object.entries(result.animations ?? {}).map(([k, a]) => <button key={k} className="btn ghost sm" onClick={() => setViewUrl(a.url)}>Preview "{k}"</button>)}
              </div>
              {result.rig_import && !result.rig && !result.rig_import.imported && <Note tone="warn">Not added automatically: {result.rig_import.reason} Use "Add to rig library" later, or import the file above from the Animation tab.</Note>}
              <JobBar job={imp.job} error={imp.error} />
              {importMsg && <Note tone="ok">{importMsg}</Note>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
