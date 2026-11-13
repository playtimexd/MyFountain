/**
 * Fur Lab: full-window shell-fur preview for QA and screenshots (dev server only: http://localhost:5173/fur-lab.html?glb=...).
 * Query: glb (url), view (side|front|three_quarter|close), fur (0|1), t (freeze the animation at this time, seconds),
 * plus any numeric FurParams key (shells, length, strands, thickness, comb, gravity, wind, inertia, occlusion, sheen).
 */
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { addShellFur, DEFAULT_FUR } from './shellFur'
import type { FurParams } from './shellFur'

const q = new URLSearchParams(location.search)
const hud = document.getElementById('hud')!
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true })
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2))
renderer.outputColorSpace = THREE.SRGBColorSpace
renderer.toneMapping = THREE.ACESFilmicToneMapping
renderer.shadowMap.enabled = true
document.body.appendChild(renderer.domElement)
const scene = new THREE.Scene()
scene.background = new THREE.Color(0x2a2f38)
const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100)
scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x3a3024, 1.3))
const sun = new THREE.DirectionalLight(0xfff1dd, 2.6)
sun.position.set(2.5, 4, 3)
sun.castShadow = true
sun.shadow.mapSize.set(2048, 2048)
scene.add(sun)
const rim = new THREE.DirectionalLight(0xbcd2ff, 1.4)
rim.position.set(-3, 2, -3)
scene.add(rim)
const ground = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.MeshStandardMaterial({ color: 0x5a5650, roughness: 0.95 }))
ground.rotation.x = -Math.PI / 2
ground.receiveShadow = true
scene.add(ground)
const orbit = new OrbitControls(camera, renderer.domElement)

const resize = () => { renderer.setSize(innerWidth, innerHeight); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix() }
addEventListener('resize', resize)
resize()

const overrides: Partial<FurParams> = {}
for (const k of Object.keys(DEFAULT_FUR) as (keyof FurParams)[]) {
  const v = q.get(k)
  if (v != null && v !== '' && !Number.isNaN(Number(v))) (overrides as any)[k] = Number(v)
}
const url = q.get('glb')
if (!url) hud.textContent += ' - no ?glb given'
else new GLTFLoader().load(url, (gltf) => {
  const root = gltf.scene
  root.traverse((o) => { if ((o as THREE.Mesh).isMesh) o.castShadow = true })
  scene.add(root)
  const box = new THREE.Box3().setFromObject(root)
  const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3())
  const r = Math.max(size.x, size.y, size.z)
  ground.position.y = box.min.y
  const view = q.get('view') || 'three_quarter'
  const dir = view === 'side' ? new THREE.Vector3(1, 0.15, 0) : view === 'front' ? new THREE.Vector3(0, 0.2, 1)
    : view === 'close' ? new THREE.Vector3(0.75, 0.45, 0.55) : new THREE.Vector3(0.8, 0.3, 0.75)
  const dist = view === 'close' ? r * 0.75 : r * 1.9
  const target = view === 'close' ? c.clone().add(new THREE.Vector3(0, size.y * 0.15, size.z * 0.1)) : c
  camera.position.copy(target).addScaledVector(dir.normalize(), dist)
  orbit.target.copy(target)
  orbit.update()
  const mixer = gltf.animations.length ? new THREE.AnimationMixer(root) : null
  if (mixer) mixer.clipAction(gltf.animations[0]).play()
  const freeze = q.get('t') != null ? Number(q.get('t')) : null
  if (mixer && freeze != null) mixer.setTime(freeze)
  const fur = q.get('fur') === '0' ? null : addShellFur(root, overrides)
  const s = fur?.stats()
  hud.textContent = fur ? `shell fur: ${s!.meshes} mesh × ${s!.shells} shells${s!.synthesised ? ' (synthesised fields)' : ''}` : 'fur off'
  const clock = new THREE.Clock()
  ;(window as any).furLab = { fur, mixer, scene, camera, renderer, ready: true }
  renderer.setAnimationLoop(() => {
    const dt = clock.getDelta()
    if (mixer && freeze == null) mixer.update(dt)
    fur?.update(freeze == null ? dt : 0, camera)
    orbit.update()
    renderer.render(scene, camera)
  })
})
