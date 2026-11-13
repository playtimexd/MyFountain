import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'

export interface AnimRig {
  id: string
  name: string
  kind: string
  bone_count: number
  mesh_count: number
  height: number
  notes: string[]
  thumb_url: string | null
  mapped: Record<string, number>
  created: number
}
export interface RigBone { name: string; parent: string | null; head: number[]; tail: number[]; deform: boolean; depth: number; leaf: boolean }
export interface RigFull extends AnimRig { bones: RigBone[]; info: Record<string, any>; bone_maps: Record<string, { source?: string; overall?: number }> }

export interface BoneMapResp {
  map: Record<string, string>
  confidence: Record<string, number>
  unmapped: string[]
  notes: string[]
  overall: number
  coverage?: number
  skeleton: string
  key: string
  source?: string
  source_joints: string[]
  bones: string[]
}

export interface AnimClip {
  id: string
  name: string
  rig_id: string | null
  skeleton: string
  kind: 'source' | 'retargeted'
  fps: number
  frames: number
  duration: number
  loop: boolean
  root_motion: boolean
  tags: string[]
  caption: string
  role: string
  preview_media_id: string | null
  stats: Record<string, any>
  score: number | null
  files: Record<string, string>
  source: Record<string, any>
  created: number
  has_bake?: boolean
}

export interface AnimStatus {
  blender: { available: boolean; exe: string; version: string; note: string }
  my_engine: { reachable: boolean; url: string; hint?: string }
  role_names: Record<string, string>
}

export const SKELETONS = [
  { id: 'ap10k17', label: 'Animal (AP-10K quadruped, 17 joints)' },
  { id: 'smpl24', label: 'Human (SMPL, 24 joints)' },
  { id: 'coco17', label: 'Human (COCO, 17 keypoints)' },
]

export function useAnimData() {
  const [rigs, setRigs] = useState<AnimRig[]>([])
  const [clips, setClips] = useState<AnimClip[]>([])
  const [status, setStatus] = useState<AnimStatus | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const reload = useCallback(async () => {
    try {
      const [r, c] = await Promise.all([api.get<AnimRig[]>('/api/anim/rigs'), api.get<AnimClip[]>('/api/anim/clips')])
      setRigs(r); setClips(c); setErr(null)
    } catch (e: any) { setErr(e.message || String(e)) }
  }, [])
  const reloadStatus = useCallback(() => { api.get<AnimStatus>('/api/anim/status').then(setStatus).catch(() => setStatus(null)) }, [])
  useEffect(() => { reload(); reloadStatus() }, [reload, reloadStatus])
  // the engine may still be (re)starting: retry until the animation router answers
  useEffect(() => {
    if (status && !err) return
    const t = setTimeout(() => { reload(); reloadStatus() }, 3000)
    return () => clearTimeout(t)
  }, [status, err, reload, reloadStatus])
  return { rigs, clips, status, err, reload, reloadStatus }
}

export const fmtScore = (s: number | null | undefined) => (s == null ? '–' : s.toFixed(0))
export const scoreColor = (s: number | null | undefined) => (s == null ? 'var(--text-faint)' : s >= 80 ? 'var(--ok)' : s >= 55 ? 'var(--warn)' : 'var(--danger)')
export const confColor = (c: number) => (c >= 0.85 ? 'var(--ok)' : c >= 0.6 ? 'var(--warn)' : 'var(--danger)')

export const METRIC_LABELS: Record<string, [string, string]> = {
  foot_slide_pct_height_per_s: ['Foot sliding', '% of height / s'],
  ground_penetration_max_pct_height: ['Ground penetration', '% of height'],
  floating_mean_pct_height: ['Floating', '% of height'],
  jitter_p95_deg: ['Jitter (p95)', 'deg'],
  loop_seam_vs_step: ['Loop seam', '× frame step'],
  bone_stretch_max_pct: ['Bone stretch', '%'],
  root_speed_mps: ['Root speed', 'm/s'],
  cycle_hz: ['Gait cycle', 'Hz'],
}
