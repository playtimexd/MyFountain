/**
 * Shell fur for three.js (engine-agnostic: depends on `three` only, so my-engine can copy this file as is).
 *
 * The character is drawn N extra times ("shells"). Shell k is pushed out along the skinned normal by k/N of the strand length,
 * bent by the comb direction, gravity, wind and inertia, and only keeps the pixels that fall inside a strand. Strands are cells
 * of a 3D grid in the bind pose, so they stay glued to the body while it animates and need no UVs.
 *
 * Per-vertex inputs (written by CutLab's Blender groom, see backend/blender/fur_groom.py; synthesised when missing):
 *   _fur_len  float  strand length in mesh units      (glTF attribute _FUR_LEN)
 *   _fur_dir  vec3   comb direction in mesh space     (_FUR_DIR)
 *   _fur_dens float  0..1 strand density              (_FUR_DENS)
 *
 *   const fur = addShellFur(gltf.scene, { shells: 24 })
 *   renderer.setAnimationLoop(() => { mixer.update(dt); fur.update(dt, camera); renderer.render(scene, camera) })
 */
import * as THREE from 'three'

export interface FurParams {
  shells: number            // shell count at close range (draw calls per mesh)
  length: number            // multiplier on _fur_len
  strands: number           // strands across the body height (grid density)
  thickness: number         // strand radius at the root, 0..0.3 of a grid cell
  comb: number              // how far strands lean along _fur_dir
  gravity: number           // droop
  wind: number              // gust strength
  inertia: number           // lag when the character accelerates
  rootTint: [number, number, number]   // multiplies the base colour at the root (and the skin under the fur)
  tipTint: [number, number, number]    // ... and at the tip
  occlusion: number         // extra darkening at the root (fake self-shadowing)
  sheen: number             // soft rim highlight
  lodDistance: number       // full shell count up to this many body heights from the camera; 0 disables LOD
  minShells: number
}

export const DEFAULT_FUR: FurParams = {
  shells: 24, length: 1, strands: 230, thickness: 0.28, comb: 1.1, gravity: 0.3, wind: 0.12, inertia: 0.6,
  rootTint: [0.32, 0.28, 0.26], tipTint: [0.95, 0.86, 0.76], occlusion: 0.5, sheen: 0.5, lodDistance: 4, minShells: 6,
}

export interface ShellFur {
  readonly params: FurParams
  setParams(p: Partial<FurParams>): void
  /** Call once per frame after the animation mixer. `camera` enables distance LOD. */
  update(dt: number, camera?: THREE.Camera): void
  setVisible(v: boolean): void
  stats(): { meshes: number; shells: number; drawCalls: number; synthesised: boolean }
  dispose(): void
}

interface FurUniforms {
  furH: { value: number }
  furLength: { value: number }
  furStrands: { value: number }
  furThickness: { value: number }
  furComb: { value: number }
  furForce: { value: THREE.Vector3 }
  furRoot: { value: THREE.Color }
  furTip: { value: THREE.Color }
  furAO: { value: number }
}

const VERT_HEAD = /* glsl */`
attribute float _fur_len;
attribute vec3 _fur_dir;
attribute float _fur_dens;
uniform float furH;
uniform float furLength;
uniform vec3 furForce;
uniform float furComb;
varying vec3 vFurRest;
varying vec3 vFurRestN;
varying float vFurDens;
`
const VERT_BODY = /* glsl */`
#include <skinning_vertex>
{
  vec3 fd = _fur_dir;
  #ifdef USE_SKINNING
    fd = (skinMatrix * vec4(fd, 0.0)).xyz;
  #endif
  vec3 fn = normalize(objectNormal);
  float L = _fur_len * furLength;
  transformed += (fn * furH + (fd * furComb + furForce) * furH * furH) * L;
  vFurRest = position;
  vFurRestN = normal;
  vFurDens = _fur_dens;
}
`
const FRAG_HEAD = /* glsl */`
uniform float furH;
uniform float furStrands;
uniform float furThickness;
uniform vec3 furRoot;
uniform vec3 furTip;
uniform float furAO;
varying vec3 vFurRest;
varying vec3 vFurRestN;
varying float vFurDens;
vec3 furHash33(vec3 p) {
  p = fract(p * vec3(0.1031, 0.1030, 0.0973));
  p += dot(p, p.yxz + 33.33);
  return fract((p.xxy + p.yxx) * p.zyx);
}
`
const FRAG_BODY = /* glsl */`
#include <color_fragment>
{
  vec3 sp = vFurRest * furStrands;
  vec3 cell = floor(sp);
  vec3 h3 = furHash33(cell);
  vec3 d = fract(sp) - (0.3 + 0.4 * h3);
  vec3 n = normalize(vFurRestN);
  d -= n * dot(d, n);                                  // distance across the strand, in the surface plane
  float own = fract(h3.x * 7.13 + h3.y * 3.71);
  float sLen = mix(0.55, 1.0, fract(h3.z * 13.7 + h3.x));
  float t = furH / sLen;
  if (own > vFurDens || t > 1.0 || length(d) > furThickness * (1.0 - t)) discard;
  vec3 tint = mix(furRoot, furTip, furH);
  diffuseColor.rgb *= tint * mix(1.0 - furAO, 1.0, furH);
}
`

function patchMaterial(mat: THREE.MeshPhysicalMaterial, u: FurUniforms) {
  mat.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u)
    shader.vertexShader = VERT_HEAD + shader.vertexShader.replace('#include <skinning_vertex>', VERT_BODY)
    shader.fragmentShader = FRAG_HEAD + shader.fragmentShader.replace('#include <color_fragment>', FRAG_BODY)
  }
  mat.customProgramCacheKey = () => 'cutlab-shell-fur-1'
}

/** Base colour / map of the source material (shells reuse them so painted fur keeps its pattern). */
function baseLook(src: THREE.Material | undefined) {
  const s = src as THREE.MeshStandardMaterial | undefined
  return { color: s?.color?.clone() ?? new THREE.Color(0x6b4a2f), map: s?.map ?? null }
}

/** Fallback fur fields for meshes that were not groomed: uniform length (4% of the height), combed "down the body". */
function synthesise(geo: THREE.BufferGeometry, height: number) {
  const pos = geo.attributes.position as THREE.BufferAttribute
  const nrm = geo.attributes.normal as THREE.BufferAttribute | undefined
  const n = pos.count
  const len = new Float32Array(n).fill(height * 0.04)
  const dens = new Float32Array(n).fill(1)
  const dir = new Float32Array(n * 3)
  const flow = new THREE.Vector3(0, -0.6, -0.8).normalize()
  const v = new THREE.Vector3(), no = new THREE.Vector3()
  for (let i = 0; i < n; i++) {
    if (nrm) no.fromBufferAttribute(nrm, i); else no.set(0, 1, 0)
    v.copy(flow).addScaledVector(no, -flow.dot(no))
    if (v.lengthSq() < 1e-6) v.set(1, 0, 0).addScaledVector(no, -no.x)
    v.normalize().toArray(dir, i * 3)
  }
  geo.setAttribute('_fur_len', new THREE.BufferAttribute(len, 1))
  geo.setAttribute('_fur_dir', new THREE.BufferAttribute(dir, 3))
  geo.setAttribute('_fur_dens', new THREE.BufferAttribute(dens, 1))
}

export function addShellFur(root: THREE.Object3D, init: Partial<FurParams> = {}): ShellFur {
  const params: FurParams = { ...DEFAULT_FUR, ...init }
  const targets: THREE.Mesh[] = []
  root.updateMatrixWorld(true)
  root.traverse((o) => { const m = o as THREE.Mesh; if (m.isMesh && m.geometry?.attributes.position && !m.userData.furShell) targets.push(m) })
  const box = new THREE.Box3().setFromObject(root)
  const worldHeight = Math.max(box.max.y - box.min.y, 1e-3)

  let synthesised = false
  interface Layer { mesh: THREE.Mesh; mat: THREE.MeshPhysicalMaterial; u: FurUniforms }
  const groups: { src: THREE.Mesh; layers: Layer[]; meshHeight: number; inv: THREE.Matrix3; skin: THREE.MeshStandardMaterial | null; skinColor: THREE.Color | null; orig: THREE.Material | THREE.Material[] }[] = []
  const MAX_SHELLS = 64

  for (const src of targets) {
    const geo = src.geometry
    geo.computeBoundingBox()
    const meshHeight = Math.max(geo.boundingBox!.max.y - geo.boundingBox!.min.y, 1e-3)
    if (!geo.attributes._fur_len || !geo.attributes._fur_dir || !geo.attributes._fur_dens) { synthesise(geo, meshHeight); synthesised = true }
    const dens = geo.attributes._fur_dens.array as ArrayLike<number>
    let furry = false
    for (let i = 0; i < dens.length && !furry; i++) furry = dens[i] > 0.01
    if (!furry) continue                                  // bare part (nose, eyes, claws): no shells at all
    const look = baseLook(Array.isArray(src.material) ? src.material[0] : src.material)
    const layers: Layer[] = []
    for (let k = 0; k < MAX_SHELLS; k++) {
      const u: FurUniforms = {
        furH: { value: 0 }, furLength: { value: 1 }, furStrands: { value: 200 }, furThickness: { value: 0.28 }, furComb: { value: 0.5 },
        furForce: { value: new THREE.Vector3() }, furRoot: { value: new THREE.Color() }, furTip: { value: new THREE.Color() }, furAO: { value: 0.5 },
      }
      const mat = new THREE.MeshPhysicalMaterial({ color: look.color, map: look.map, roughness: 0.85, metalness: 0, sheen: 0.6, sheenRoughness: 0.45 })
      patchMaterial(mat, u)
      const skinned = (src as THREE.SkinnedMesh).isSkinnedMesh
      const shell: THREE.Mesh = skinned ? new THREE.SkinnedMesh(geo, mat) : new THREE.Mesh(geo, mat)
      if (skinned) {
        const s = src as THREE.SkinnedMesh
        const sh = shell as THREE.SkinnedMesh
        sh.bindMode = s.bindMode
        sh.bind(s.skeleton, s.bindMatrix)
      }
      shell.userData.furShell = true
      shell.frustumCulled = false
      shell.castShadow = false
      shell.receiveShadow = src.receiveShadow
      shell.renderOrder = (src.renderOrder || 0) + 1 + k
      shell.matrixAutoUpdate = true
      src.add(shell)                                  // identity transform under the source: same world matrix
      layers.push({ mesh: shell, mat, u })
    }
    // the skin between strands should read as the root colour: tint a private copy of the source material
    const orig = src.material
    const one = Array.isArray(orig) ? null : (orig as THREE.MeshStandardMaterial)
    const skin = one && (one as THREE.MeshStandardMaterial).color ? (one.clone() as THREE.MeshStandardMaterial) : null
    if (skin) src.material = skin
    groups.push({ src, layers, meshHeight, inv: new THREE.Matrix3(), skin, skinColor: skin ? skin.color.clone() : null, orig })
  }

  let visible = true
  let active = params.shells
  const last = new THREE.Vector3(), cur = new THREE.Vector3(), vel = new THREE.Vector3(), smooth = new THREE.Vector3()
  const lag = new THREE.Vector3(), force = new THREE.Vector3(), tmp = new THREE.Vector3(), camPos = new THREE.Vector3()
  let first = true
  let time = 0

  const apply = () => {
    for (const g of groups) {
      if (g.skin && g.skinColor) {
        g.skin.color.copy(g.skinColor)
        if (visible) g.skin.color.multiply(new THREE.Color().setRGB(...params.rootTint))
      }
      const strands = params.strands / g.meshHeight
      g.layers.forEach((l, k) => {
        const on = visible && k < active
        l.mesh.visible = on
        if (!on) return
        l.u.furH.value = (k + 1) / active
        l.u.furLength.value = params.length
        l.u.furStrands.value = strands
        l.u.furThickness.value = Math.min(0.3, Math.max(0.02, params.thickness))
        l.u.furComb.value = params.comb
        l.u.furRoot.value.setRGB(...params.rootTint)
        l.u.furTip.value.setRGB(...params.tipTint)
        l.u.furAO.value = params.occlusion
        l.mat.sheen = params.sheen
      })
    }
  }
  apply()

  return {
    params,
    setParams(p) {
      Object.assign(params, p)
      params.shells = Math.max(1, Math.min(MAX_SHELLS, Math.round(params.shells)))
      params.minShells = Math.max(1, Math.min(params.shells, Math.round(params.minShells)))
      active = params.shells
      apply()
    },
    update(dt, camera) {
      dt = Math.min(Math.max(dt, 0), 0.1)
      time += dt
      root.getWorldPosition(cur)
      if (first) { last.copy(cur); first = false }
      if (dt > 0) vel.copy(cur).sub(last).divideScalar(dt)
      last.copy(cur)
      smooth.lerp(vel, Math.min(1, dt * 6))
      lag.copy(smooth).sub(vel).multiplyScalar(params.inertia / Math.max(worldHeight, 1e-3) * 0.25)   // strands trail behind sudden moves
      if (lag.length() > 1.2) lag.setLength(1.2)
      const gust = params.wind * (0.6 + 0.4 * Math.sin(time * 1.7) * Math.sin(time * 0.63 + 1.3))
      force.set(gust, -params.gravity, gust * 0.35).add(lag)

      if (camera && params.lodDistance > 0) {
        camera.getWorldPosition(camPos)
        const dist = camPos.distanceTo(cur) / worldHeight
        const f = THREE.MathUtils.clamp(params.lodDistance / Math.max(dist, 1e-3), 0, 1)
        const want = Math.max(params.minShells, Math.round(params.shells * f))
        if (want !== active) { active = want; apply() }
      }
      for (const g of groups) {
        g.inv.setFromMatrix4(g.src.matrixWorld).invert()      // world force -> mesh space
        tmp.copy(force).applyMatrix3(g.inv)
        if (tmp.lengthSq() > 0) tmp.setLength(force.length())
        for (const l of g.layers) if (l.mesh.visible) l.u.furForce.value.copy(tmp)
      }
    },
    setVisible(v) { visible = v; apply() },
    stats() { return { meshes: groups.length, shells: active, drawCalls: groups.length * active, synthesised } },
    dispose() {
      for (const g of groups) {
        for (const l of g.layers) { l.mesh.removeFromParent(); l.mat.dispose() }
        if (g.skin) { g.src.material = g.orig; g.skin.dispose() }
      }
      groups.length = 0
    },
  }
}
