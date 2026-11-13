# KimodoDeps：Unreal 启动时自动装好 NVIDIA Kimodo 的 Python 依赖

把 `KimodoDeps` 插件放进 UE 项目。之后每次打开编辑器，UE 自带的 Python 插件（PythonScriptPlugin）会按插件里声明的依赖执行
`pip install`，装到项目的 `Intermediate\PipInstall`，也就是《NVIDIA Kimodo — Unreal Engine 5.9 Python Dependency Fix》里用的那个 Python。
已经装好的包会直接跳过，不会重复下载。

## 用法

```powershell
# 1) 把插件装进项目，并在 .uproject 里启用（会先备份 .uproject）
.\install_kimodo_deps.ps1 -Project "F:\UNREAL PROJECTS\MyProject3\MyProject3.uproject"

# 2) 可选：先关掉编辑器，现在就装好（第一次约 4 GB）。不做这一步的话，下次打开编辑器时会自动装，期间编辑器会一直等着装完
.\install_kimodo_deps.ps1 -Project "F:\UNREAL PROJECTS\MyProject3" -InstallNow
```

打开编辑器后，Output Log 里出现 `KimodoDeps: all Kimodo Python packages present (torch 2.11.0+cu128, numpy 1.26.4)` 就说明装好了。
想做完整检查（包括 CUDA），在编辑器的 Python 控制台里运行：`import kimodo_deps_check; kimodo_deps_check.full()`。

需要满足：
- 项目设置 › Plugins › Python 里的 “Run Pip Install on Startup” 是打开的（默认就是开的）。
- “Pip Strict Hash Check” 保持打开：每个包都带 sha256 校验，下载的文件和锁定时的不一致就会拒绝安装。

## 和原文档的区别（为什么不直接照着跑那 6 条命令）

- **UE 只装 wheel，而且要求每个包都写死版本和哈希**（`--only-binary=:all: --require-hashes`），包括间接依赖。所以这里的
  `KimodoDeps.uplugin` 列出了完整的 78 个包，每个都带版本和 sha256。
- **`torch==2.11.0+cu128`**：这是 PyTorch CUDA 12.8 源上 Python 3.11 / Windows 最新的版本，也就是原文档 `--index-url .../cu128`
  会装到的版本。不写死版本的话，PyPI 上纯 CPU 的 torch 版本号更高（2.15），会被优先选中。
- **`numpy<2` 一开始就锁定**：原文档第 1 步先装 numpy 2.x，第 5 步又降回 1.x。
- **antlr4-python3-runtime 4.9.3 随插件一起提供**：`hydra-core` / `omegaconf` 依赖它，但 PyPI 上只有源码包，没有 wheel，UE 装不了。
  插件里放了从官方源码包构建的纯 Python 版本（`Content/Python/Lib/site-packages`，BSD 许可，见 `ThirdParty/`）。
  UE 在执行 pip 之前会把这个目录加进 Python 环境。
- `gradio` 只有 Kimodo 的网页演示才用得到，为了和原文档一致仍然保留。想精简就从 `requirements.in` 里删掉，再重新生成。

## 更新依赖

改 `requirements.in`，然后用 UE 自带的 Python 重新生成：

```powershell
& "C:\Program Files\Epic Games\UE_5.9\Engine\Binaries\ThirdParty\Python3\Win64\python.exe" -I make_lock.py
```

它会在临时环境里构建 antlr4、解析全部依赖（只读包的元数据，不安装），然后重写 `KimodoDeps.uplugin` 和 `requirements.lock.txt`。
最后对每个项目重新运行一次 `install_kimodo_deps.ps1`。

## 验证记录（2026-11-13，UE 5.9 / Python 3.11.8）

- 按 UE 的参数做了解析检查（`--only-binary=:all: --require-hashes`），78 个包全部通过，antlr4 通过插件目录可以被找到。
- 在临时测试项目上运行 `install_kimodo_deps.ps1`：插件复制和 .uproject 修改都正常，重复运行结果不变。
- 用 UnrealEditor-Cmd 在测试项目上无界面启动（离线模式，不下载）：UE 加载了插件，合并了 78 条带哈希的依赖，
  写入了指向插件 site-packages 的 `.pth`，并用 `--require-hashes` 调用了 pip。启动检查脚本按预期报告“缺少的包”。
- 还没做的：真正下载约 4 GB 的完整安装。
