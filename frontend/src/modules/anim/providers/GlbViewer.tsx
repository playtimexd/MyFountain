import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { FBXLoader } from 'three/examples/jsm/loaders/FBXLoader.js'

export interface GlbInfo { meshes: number; triangles: number; bones: number; clips: string[] }
/** Optional add-on attached to the loaded model (e.g. shell fur); `update` runs every frame after the animation mixer. */
export interface ModelDecoration { update?(dt: number, camera: THREE.Camera): void; dispose?(): void }

/** Small orbit viewer for a GLB (no CDN: three is bundled). Shows the skeleton on request and plays the first clip, if any. */
export default function GlbViewer({ url, height = 320, onInfo, onModel, background = 0x15171d, format = 'auto' }: {
  url: string | null; height?: number; onInfo?: (i: GlbInfo) => void
  onModel?: (root: THREE.Object3D, scene: THREE.Scene) => ModelDecoration | void; background?: number
  /** 'auto' picks by the URL extension; FBX (e.g. Mixamo) is supported too. */
  format?: 'auto' | 'gltf' | 'fbx'
}) {
  const host = useRef<HTMLDivElement>(null)
  const helper = useRef<THREE.SkeletonHelper | null>(null)
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [msg, setMsg] = useState('')
  const [info, setInfo] = useState<GlbInfo | null>(null)
  const [skel, setSkel] = useState(false)
  const infoCb = useRef(onInfo)
  infoCb.current = onInfo
  const modelCb = useRef(onModel)
  modelCb.current = onModel

  useEffect(() => {
    const el = host.current
    if (!el || !url) { setState('idle'); setInfo(null); return }
    let disposed = false
    setState('loading'); setMsg(''); setInfo(null)

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setClearColor(background)
    renderer.outputColorSpace = THREE.SRGBColorSpace
    el.appendChild(renderer.domElement)
    renderer.domElement.style.display = 'block'
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000)
    scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 1.6))
    const sun = new THREE.DirectionalLight(0xffffff, 2.2)
    sun.position.set(3, 5, 4)
    scene.add(sun)
    const grid = new THREE.GridHelper(10, 20, 0x3a4050, 0x242833)
    scene.add(grid)
    const orbit = new OrbitControls(camera, renderer.domElement)
    orbit.enableDamping = true

    const resize = () => {
      const w = el.clientWidth || 300
      renderer.setSize(w, height, false)
      renderer.domElement.style.width = '100%'
      renderer.domElement.style.height = `${height}px`
      camera.aspect = w / height
      camera.updateProjectionMatrix()
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(el)

    let mixer: THREE.AnimationMixer | null = null
    const clock = new THREE.Clock()
    let root: THREE.Object3D | null = null
    let deco: ModelDecoration | void = undefined

    const isFbx = format === 'fbx' || (format === 'auto' && /\.fbx($|\?)/i.test(url))
    const onLoaded = (gltf: { scene: THREE.Object3D; animations: THREE.AnimationClip[] }) => {
        if (disposed) return
        root = gltf.scene
        scene.add(root)
        let tris = 0, meshes = 0, bones = 0
        root.traverse((o) => {
          if ((o as THREE.Mesh).isMesh) {
            meshes++
            const g = (o as THREE.Mesh).geometry
            tris += g.index ? g.index.count / 3 : (g.attributes.position?.count ?? 0) / 3
          }
          if ((o as THREE.Bone).isBone) bones++
        })
        const box = new THREE.Box3().setFromObject(root)
        if (box.isEmpty()) {                       // skeleton-only file (e.g. Mixamo 'without skin'): frame the bones
          root.updateMatrixWorld(true)
          const v = new THREE.Vector3()
          root.traverse((o) => { if ((o as THREE.Bone).isBone) box.expandByPoint(o.getWorldPosition(v)) })
          if (box.isEmpty()) box.set(new THREE.Vector3(-1, 0, -1), new THREE.Vector3(1, 2, 1))
        }
        const size = box.getSize(new THREE.Vector3())
        const center = box.getCenter(new THREE.Vector3())
        const radius = Math.max(size.x, size.y, size.z) || 1
        grid.position.y = box.min.y
        grid.scale.setScalar(Math.max(radius / 5, 0.05))
        orbit.target.copy(center)
        camera.position.copy(center).add(new THREE.Vector3(radius * 1.1, radius * 0.55, radius * 1.6))
        camera.near = radius / 200; camera.far = radius * 100
        camera.updateProjectionMatrix()
        orbit.update()
        helper.current = new THREE.SkeletonHelper(root)
        helper.current.visible = meshes === 0
        if (meshes === 0) setSkel(true)
        scene.add(helper.current)
        if (gltf.animations.length) {
          mixer = new THREE.AnimationMixer(root)
          mixer.clipAction(gltf.animations[0]).play()
        }
        try { deco = modelCb.current?.(root, scene) } catch (e) { console.error('model decoration failed', e) }
        const i: GlbInfo = { meshes, triangles: Math.round(tris), bones, clips: gltf.animations.map((a) => a.name || 'clip') }
        setInfo(i); infoCb.current?.(i); setState('ready')
      }
    const onError = (e: unknown) => { if (!disposed) { setState('error'); setMsg((e as any)?.message || 'Could not load the model') } }
    // FBX vertex colours are usually masks (Unreal mannequins, Mixamo) rather than albedo: ignore them; untextured black -> neutral grey
    const fbxLook = (obj: THREE.Object3D) => {
      obj.traverse((o) => {
        const mats = (o as THREE.Mesh).material
        for (const m of (Array.isArray(mats) ? mats : mats ? [mats] : []) as (THREE.MeshPhongMaterial & THREE.MeshStandardMaterial)[]) {
          if (m.vertexColors) { m.vertexColors = false; m.needsUpdate = true }
          if (!m.map && m.color && m.color.getHex() === 0x000000) m.color.setHex(0xb8b8b8)
        }
      })
      return obj
    }
    if (isFbx) new FBXLoader().load(url, (obj) => onLoaded({ scene: fbxLook(obj), animations: obj.animations }), undefined, onError)
    else new GLTFLoader().load(url, (g) => onLoaded(g), undefined, onError)

    renderer.setAnimationLoop(() => {
      const dt = clock.getDelta()
      mixer?.update(dt)
      deco?.update?.(dt, camera)
      orbit.update()
      renderer.render(scene, camera)
    })

    return () => {
      disposed = true
      renderer.setAnimationLoop(null)
      ro.disconnect()
      orbit.dispose()
      helper.current = null
      deco?.dispose?.()
      scene.traverse((o) => {
        const m = o as THREE.Mesh
        if (m.geometry) m.geometry.dispose()
        const mat = m.material as THREE.Material | THREE.Material[] | undefined
        if (Array.isArray(mat)) mat.forEach((x) => x.dispose()); else mat?.dispose()
      })
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [url, height, background, format])

  useEffect(() => { if (helper.current) helper.current.visible = skel }, [skel, state])

  return (
    <div>
      <div ref={host} style={{ position: 'relative', height, background: 'var(--bg-1)', border: '1px solid var(--line)', borderRadius: 6, overflow: 'hidden' }}>
        {state !== 'ready' && (
          <div className="faint" style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none', textAlign: 'center', padding: 12 }}>
            {state === 'idle' ? 'The generated model will appear here' : state === 'loading' ? 'Loading model...' : <span className="err">{msg}</span>}
          </div>
        )}
      </div>
      {info && (
        <div className="row" style={{ marginTop: 4, fontSize: 11, flexWrap: 'wrap' }}>
          <span className="faint grow">{info.triangles.toLocaleString()} triangles · {info.bones} bones{info.clips.length ? ` · ${info.clips.length} animation(s)` : ''} · drag to orbit, wheel to zoom</span>
          {info.bones > 0 && <label className="row" style={{ gap: 4, cursor: 'pointer' }}><input type="checkbox" checked={skel} onChange={(e) => setSkel(e.target.checked)} />Show skeleton</label>}
        </div>
      )}
    </div>
  )
}
