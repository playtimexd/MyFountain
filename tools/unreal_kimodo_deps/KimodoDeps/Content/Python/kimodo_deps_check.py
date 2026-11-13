"""Full check from the editor's Python console:   import kimodo_deps_check; kimodo_deps_check.full()"""
import importlib


def full():
    import unreal
    for name in ("numpy", "scipy", "torch", "hydra", "omegaconf", "transformers", "peft", "einops", "trimesh", "bvhio", "av", "tqdm"):
        try:
            m = importlib.import_module(name)
            unreal.log(f"  ok  {name} {getattr(m, '__version__', '')}")
        except Exception as e:  # noqa: BLE001
            unreal.log_error(f"  FAIL {name}: {e}")
    try:
        import torch
        if torch.cuda.is_available():
            unreal.log(f"  CUDA {torch.version.cuda} on {torch.cuda.get_device_name(0)}")
        else:
            unreal.log_warning("  torch is installed but CUDA is not available (CPU-only build or no NVIDIA driver)")
    except Exception as e:  # noqa: BLE001
        unreal.log_error(f"  torch check failed: {e}")
