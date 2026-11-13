# Motion capture (video -> 3D skeletal motion -> `motion.npz`)

Owner: mocap engineer. Code: `backend/app/routers/mocap.py`, `backend/app/services/mocap/**`. Tests: `backend/tests/mocap_*.py`.
Consumers: anim-core (Blender retargeting). Format and endpoints follow `docs/ANIMATION.md` section 3.

## Pipeline

```
video --ffmpeg--> frames (fps, <=1280px)
      --RT-DETRv2 (COCO)--> detections --IoU tracker--> main subject track (largest/most confident, or user box)
      --ViTPose++ (expert 0 human COCO-17 / expert 3 animal AP-10K-17)--> 2D keypoints + confidence
      --fit3d.py (our own torch optimiser)--> 3D joints, Y-up metres
      --postprocess--> gait period, seamless-loop points, quality stats --> motion.npz  (+ debug overlay video)
```

`fit3d.py` fits a canonical skeleton tree (human `coco17`, quadruped `ap10k17`, plus virtual pelvis/neck) with per-frame
bone directions, per-clip bone lengths (left/right tied, prior to the canonical proportions or to the target rig when `rigId`
supplies a bone map), a yaw heading per frame, a fixed weak-perspective gravity-aligned camera, and a free root. Losses:
confidence-weighted robust reprojection (graduated), acceleration/velocity smoothness, ground contact (feet on the floor +
no sliding while the 2D foot is quasi-static), hinge angle limits + bend direction, abduction limits, a sagittal-plane/rest-pose
depth prior and bone-length priors. Missing or low-confidence joints get weight 0 and are filled by the priors. 8 initial
headings are solved as one batch, the best one is refined. Short clips run on the CPU (faster than the GPU for tiny
kernels); clips over 700 frames use CUDA.

## Endpoints (prefix `/api/mocap`)

| Endpoint | Notes |
|---|---|
| `GET /backends` | `[{id,name,available,note,size,license_note,subjects,kind,skeleton,...}]`: `vitpose`, `gem`, `sam3d` (+ `mock` only when `CUTLAB_MOCAP_DEV=1`) |
| `POST /prepare {backend, variant?}` | job; downloads weights ONLY when called. `vitpose` (variant `small|base|large|huge`) works fully. `gem`/`sam3d` only fetch weights into an existing manual install (we never create their environments). |
| `POST /` `{mediaId,start?,end?,subject:'humanoid'\|'quadruped'\|'auto',backend?,rigId?,fps?,box?,size_m?,pose_variant?,overlay?}` | job; result `{motion_file, motion_url, overlay_media, stats{frames,fps,mean_conf,jitter,coverage,...}, skeleton{name,joint_names,subject,edges}, gait, backend, notes}`. `box=[x,y,w,h]` (source pixels) picks the subject in the first frame; `size_m` = real human height / quadruped shoulder height. Missing weights = job error `Backend vitpose needs weights (~643 MB). Run prepare.` |
| `GET /motion/{name}.npz` | download; `GET /motion/{name}.json` = fps, skeleton, joint_names, meta; `GET /motions` lists files |

## `motion.npz`

`fps`, `skeleton` (`coco17` / `ap10k17` / `soma77` / `mhr70`), `joint_names (J,)`, `joints3d (T,J,3)`, `conf (T,J)`, `root (T,3)`, `meta` (JSON string).
Extra keys (safe to ignore): `joints3d_local` (pelvis-relative, facing +Z), `contacts (T,F)` + `contact_joints` (foot contact probability),
`heading (T,)`, `interpolated (T,J)` (2D point was missing), `virtual_joint_names` + `virtual_joints3d` (pelvis = mid-hip, human neck = mid-shoulder).

Conventions (vitpose backend; also in `meta.coordinates`): metres, **+Y up, subject faces +Z, subject's left = +X** (glTF-style), ground at y=0,
`joints3d` = absolute positions, path starts at x=z=0; `root` = pelvis (mid-hip) trajectory. Output metres are for the *canonical body size*
(human 1.70 m, quadruped shoulder height 0.97 m) unless `size_m`/`rigId` is given; anim-core scales to the target rig anyway.
Depth along the camera axis is inferred (not measured): the root path follows the heading, absolute forward distance is approximate.
`meta` also carries: camera assumptions, per-bone fitted lengths, bone hierarchy, gait `{period_s, cycles, confidence, loop_start, loop_end, loop_cycles, loop_error}`
(use frames `[loop_start, loop_end)` as a seamless loop), quality stats, fit losses, notes (e.g. "3 subjects tracked").
`gem`/`sam3d` files use their own skeletons, are Y-up with the ground at 0 but their forward axis is NOT normalised.

## Backends and weights

| id | what | weights (verified sizes) | licence |
|---|---|---|---|
| `vitpose` | humans + animals (bear, dog, cat, horse, cow, sheep, elephant, zebra, giraffe) | `usyd-community/vitpose-plus-base` 478 MiB (also small 126 MiB, large 1.62 GiB, huge 3.35 GiB) + `PekingU/rtdetr_v2_r50vd` 164 MiB (r18: 77 MiB). Default total 643 MiB. One ViTPose++ model serves both subjects (MoE expert 0 = COCO, 3 = AP-10K). | Apache-2.0 (HF model cards) |
| `gem` | NVIDIA GEM-X video model, SOMA 77 joints, world-space motion + contact/IK post-process. https://github.com/NVlabs/gem-x | `nvidia/GEM-X`: `body_s240000.ckpt` 3.30 GiB, `motion_native77_e774_s292175.ckpt` 178 MiB, `vitpose.pth` 3.16 GiB (+ `nvidia/SOMA-X` v0.3.3 assets, YOLOX HumanArt ONNX); ~7.1 GB. Needs its own Python env (torch 2.10, `py-soma-x==0.3.3`); validated by NVIDIA on Linux/A100. | code Apache-2.0; weights NVIDIA Open Model License (model card allows commercial use); SOMA/SMPL assets have their own terms. The older NVlabs/GENMO repo states a non-commercial licence: read it before shipping. |
| `sam3d` | Meta SAM 3D Body: per-image mesh recovery (MHR rig, 70 keypoints); we prompt it with our tracked box on every frame and smooth. https://github.com/facebookresearch/sam-3d-body | `facebook/sam-3d-body-dinov3` `model.ckpt` 1.96 GiB + `assets/mhr_model.pt` 664 MiB (~2.8 GB; ViT-H variant 2.4 GB). **Gated: request access on Hugging Face.** Own env. | SAM License |
| `mock` | dev/test only (`CUTLAB_MOCAP_DEV=1`): replays a `<video>.kp.npz` sidecar as the 2D pose | none | n/a |

`auto` backend: humanoid -> `gem` if installed, else `vitpose`; quadruped/auto -> `vitpose`. `subject='auto'` lets the detector class decide (person = humanoid; cat/dog/horse/sheep/cow/elephant/bear/zebra/giraffe = quadruped).
Install how-tos for gem/sam3d are returned in `GET /backends` (`note`) and in `services/mocap/gem.py` / `sam3d.py`.
`scripts/setup_mocap_weights.ps1` lists the models (and downloads the chosen ones with `-Download`).

### What has been verified without weights
Fitter, tracker, gait/loop detection, converters (GEM/SAM3D on synthetic arrays), motion.npz round trip, endpoints end-to-end with the mock 2D backend
(video in -> motion.npz + overlay video), and the `transformers` plumbing of the detector and ViTPose++ wrappers using randomly initialised tiny models.
**Not verified:** the real accuracy of the detector and ViTPose++ on real footage (weights not downloaded), and everything about GEM-X and SAM 3D Body
(no weights, no environments; the GEM `motion.pt` -> joints step is a best-effort guess because the tensor layout is undocumented).

## Shooting guidelines (for reference videos and for generated ones)

* Fixed camera (tripod, no zoom, no pan). The fitter assumes a static, level, gravity-aligned camera; a moving camera makes the root path meaningless.
* Whole body in frame the whole time, feet visible, no cropping at the frame edge.
* Side or three-quarter view (30-90 degrees from the direction of travel is best; pure front/back views are ambiguous).
* Plain background, subject clearly larger than 25 % of the frame height, good light, no motion blur (shutter >= 1/250).
* 3-5 gait cycles (a walk is ~1 s per cycle, so 4-6 s), constant speed, straight path. Extra animals/people are tracked and the largest one is used, or give `box`.
* 24-30 fps source. Higher fps is subsampled to <=30.
* For game assets: pick a clip segment of whole cycles and use the reported `loop_start/loop_end`.

## Known limits

* Monocular depth is ambiguous. The model resolves it with priors (sagittal-plane limbs, rest-pose torso, joint limits, left/right labels), so a lateral-view gait is reconstructed well while fast spins, crawling towards the camera or heavy occlusion are not.
* ViTPose++ left/right labels on animals are sometimes swapped; the fitter is robust to a few swapped frames but a consistently mirrored track produces mirrored legs.
* Bears and other animals outside AP-10K families are handled by the closest expert; tail/ears/paw details are not modelled (17 keypoints). Fur/skin occlusion of joints is common for bears: expect noisier elbows/knees.
* Camera pitch is assumed 0; a camera tilted by more than ~10 degrees tilts the ground plane in the result and biases the root height. Distance changes over time (walking toward the camera) are not modelled (fixed scale).
* Foot sliding is reduced, not eliminated (about 0.1-0.25 m/s on noisy synthetic data vs 0.9-1.25 m/s walking speed). Final foot locking/IK belongs to the Blender retarget stage (`contacts` is provided for that).
* Output scale is the canonical body size unless `size_m` or `rigId` proportions are given.
* Clips are capped at 1800 frames (60 s at 30 fps).
