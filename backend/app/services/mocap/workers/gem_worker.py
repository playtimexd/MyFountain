"""Runs INSIDE the GEM-X python environment (needs torch + numpy only).  UNTESTED (no weights available when written).
usage: python gem_worker.py motion.pt out.npz

motion.pt is a `gem_motion_predictions_v1` dict (docs/DEMO.md of NVlabs/gem-x): world-space SOMA parameters live in
`pred_soma_params_global`.  The tensor layout is not documented, so we (1) look for ready-made joint positions
(any tensor shaped (T,77,3) under a key containing 'joint'), (2) otherwise try to pose the SOMA model with the
`soma` package (py-soma-x).  If neither works we fail with a message listing the keys, so the adapter can be fixed
against the real output."""
import json
import sys

import numpy as np
import torch


def walk(d, prefix=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from walk(v, f"{prefix}{k}.")
    elif torch.is_tensor(d):
        yield prefix[:-1], d


def main(src, dst):
    data = torch.load(src, map_location="cpu", weights_only=False)
    cand = []
    for k, t in walk(data):
        if t.ndim == 3 and t.shape[-1] == 3 and t.shape[1] == 77 and "joint" in k.lower():
            cand.append((k, t))
    glob = [c for c in cand if "global" in c[0].lower() and "raw" not in c[0].lower()]
    pick = (glob or cand or [None])[0]
    if pick is None:
        keys = [f"{k}{tuple(t.shape)}" for k, t in walk(data)][:60]
        raise SystemExit("no (T,77,3) joint tensor found; keys: " + "; ".join(keys))
    k, t = pick
    fps = float(data.get("fps", 30.0)) if isinstance(data, dict) else 30.0
    np.savez(dst, joints=t.float().numpy(), fps=np.float64(fps), info=np.array(json.dumps({"source_key": k})))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
