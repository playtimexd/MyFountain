<#
 Motion-capture weights for CutLab (docs/MOCAP.md).  NOTHING is downloaded unless you pass -Download.
 Same thing as the "prepare" button / POST /api/mocap/prepare {backend:'vitpose', variant:'base'}.

 Usage
   powershell -ExecutionPolicy Bypass -File D:\CutLab\scripts\setup_mocap_weights.ps1                 # just list models + sizes
   powershell -ExecutionPolicy Bypass -File D:\CutLab\scripts\setup_mocap_weights.ps1 -Download -Pose base -Detector r50
   powershell -ExecutionPolicy Bypass -File D:\CutLab\scripts\setup_mocap_weights.ps1 -Download -Pose large     # better accuracy

 vitpose backend (humans + animals) - everything lands in D:\CutLab\data\models\mocap\<name>
   Pose (ViTPose++ mixture-of-experts: expert 0 = COCO humans, expert 3 = AP-10K animals, all in ONE model):
     small  usyd-community/vitpose-plus-small    ~126 MiB   (33M params)
     base   usyd-community/vitpose-plus-base     ~478 MiB   (default)
     large  usyd-community/vitpose-plus-large    ~1.62 GiB
     huge   usyd-community/vitpose-plus-huge     ~3.35 GiB
   Detector (COCO-80, includes person/bear/dog/cat/horse/cow/sheep/elephant/zebra/giraffe):
     r50    PekingU/rtdetr_v2_r50vd              ~164 MiB   (default)
     r18    PekingU/rtdetr_v2_r18vd              ~77 MiB
   Licences: both Apache-2.0 (Hugging Face model cards).
   Default download (base + r50) = ~643 MiB.

 Optional humanoid back-ends are NOT downloaded by this script (they need their own Python env, see docs/MOCAP.md):
   gem    nvidia/GEM-X  body_s240000.ckpt 3.30 GiB + motion ckpt 178 MiB + vitpose.pth 3.16 GiB  (~7.1 GB total), NVIDIA Open Model License
   sam3d  facebook/sam-3d-body-dinov3  model.ckpt 1.96 GiB + MHR 664 MiB (~2.8 GB), gated (request access), SAM License
#>
param(
  [switch]$Download,
  [ValidateSet('small','base','large','huge')][string]$Pose = 'base',
  [ValidateSet('r50','r18')][string]$Detector = 'r50'
)
$py = 'D:\CutLab\backend\.venv\Scripts\python.exe'
$models = @(
  @{ kind='pose';     key='small'; repo='usyd-community/vitpose-plus-small'; mb=126 },
  @{ kind='pose';     key='base';  repo='usyd-community/vitpose-plus-base';  mb=478 },
  @{ kind='pose';     key='large'; repo='usyd-community/vitpose-plus-large'; mb=1657 },
  @{ kind='pose';     key='huge';  repo='usyd-community/vitpose-plus-huge';  mb=3431 },
  @{ kind='detector'; key='r50';   repo='PekingU/rtdetr_v2_r50vd';           mb=164 },
  @{ kind='detector'; key='r18';   repo='PekingU/rtdetr_v2_r18vd';           mb=77 }
)
Write-Host "CutLab mocap models (target: D:\CutLab\data\models\mocap)"
foreach ($m in $models) { Write-Host ("  {0,-9} {1,-6} {2,-40} ~{3} MiB" -f $m.kind, $m.key, $m.repo, $m.mb) }
if (-not $Download) { Write-Host "`nNothing downloaded. Re-run with -Download [-Pose base] [-Detector r50]."; exit 0 }
$sel = @($models | Where-Object { ($_.kind -eq 'pose' -and $_.key -eq $Pose) -or ($_.kind -eq 'detector' -and $_.key -eq $Detector) })
foreach ($m in $sel) {
  $name = $m.repo.Split('/')[1]
  $dst = "D:\CutLab\data\models\mocap\$name"
  Write-Host "Downloading $($m.repo) (~$($m.mb) MiB) -> $dst"
  & $py -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$($m.repo)', local_dir=r'$dst', allow_patterns=['*.json','*.safetensors','*.txt'])"
  if ($LASTEXITCODE -ne 0) { Write-Error "download failed for $($m.repo)"; exit 1 }
}
Write-Host "Done. GET /api/mocap/backends should now report vitpose as available."
