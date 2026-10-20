# K1 智能用药平台开源整理进度

更新日期：2026-10-20

## 已完成

- 已复制运行工程到 `D:/智能药盒项目开源/K1智能用药平台_开源整理`；K1 工程与完整归档保持只读。
- 已按 `backend/`、`frontend/`、`models/`、`data/`、`deployment/`、`docs/` 归整，并记录依赖、硬件边界和发布审计。
- 已逐项调研进迭时空官网 6 个案例及对应 GitHub 仓库，调研文档：`OPEN_SOURCE_REFERENCE.md`。
- 已修正安装脚本和 systemd 路径变量，加入可选 kiosk 模板，并从完整归档补回网关 OCR 动态导入的 4 个模块。
- 已从完整归档补回 ASR 使用的 8 个 SpaceMIT 兼容层 Python 模块；候选 ASR 服务恢复 K1 上的 CPU 亲和性、ONNX 动态库路径和线程配置。
- 已核对前端另外 35 个硬编码 WAV 引用并纳入预检；确认 5 个备用药丸模型在候选代码/文档中无引用，物理文件仍留在候选中，待执行策略允许清理。
- 已逐文件比对候选前端 241 个文件，均与只读归档一致；220 段 WAV 均逐个哈希匹配。问药 API、音频角色管理器和主要 UI 入口哈希一致。
- 已建立临时第三方来源与授权清单 `docs/THIRD_PARTY_NOTICES.md`；从归档元数据确认 pypinyin 0.55.0 为 MIT 并补入许可证原文，其余模型、音频和人脸代码授权仍待核实。
- 已调整人脸脚本，使其尊重当前用户的 XDG/Wayland 会话变量。
- 最新静态预检通过：54 个必需文件、33 份 JSON、87 个 Python 文件、两份 OCR YAML、Qwen 模型大小/哈希、185 条索引语音和 35 个直接引用语音均通过；三个 shell 脚本语法检查和 systemd 单元解析通过。

## 三步进度

1. 必需文件闭包：代码、模型、前端、OCR worker 和 ASR 兼容层已纳入候选与预检；Bianbu/BSP/SpaceMIT 运行时仍需板端系统包与来源，授权也未清。
2. 目录重构：模块目录已整理，主要入口使用仓库相对路径；静态路径与资源检查通过。41 个 `.pyc` 仍物理存在但被 Git 忽略。
3. K1 对照：已完成只读服务状态和关键文件哈希比对；portable service 模板尚未部署到 K1，外设链路也未做实体验证，因此运行对应性未最终签收。

已完成的 OCR 闭包补齐：4 个动态导入模块、`frame_geometry.py` 和两份 `inference.yml` 已纳入预检；worker 显式设置 gateway Python import 路径。Python GObject/GStreamer typelib、PyYAML、pyclipper 已列入依赖说明。

已完成的 ASR 闭包补齐：归档中的服务先导入项目内 `spacemit_asr_runtime`；其中 `llm_asr.py` 支持线程参数，而系统 `/opt/spacemit-asr` 版本不支持，候选漏掉覆盖层会导致模型初始化参数错误。现已复制覆盖层 8 个 Python 文件并逐个校验哈希；ASR 单元保留 K1 CPUs 0-3 亲和性、ONNX Runtime GenAI 动态库路径、单线程和 `K1_ASR_USE_SPACEMIT=0` 设置。由于当前无麦克风且未在 K1 部署候选，仍未做实时加载验证。

## 发布前仍未完成

- 尚无项目 `LICENSE`；`THIRD_PARTY_NOTICES.md` 仅为临时证据清单，模型、数据、语音素材和部分第三方代码的来源与再分发权仍未确认。
- UI 含姓名和健康信息的演示病例，语音素材含可识别姓名；需确认虚构性与再分发权，或替换/匿名化。
- Bianbu、K1 BSP、SpaceMIT ONNX/LLM/ASR/TTS 运行时及相机配置不由仓库完全提供；安装来源和兼容版本还需确认。
- 没有连接摄像头、屏幕、麦克风、扬声器和药柜执行器完成实体端到端测试；不能承诺“下载即用”。
- 候选目录仍有 41 个 `.pyc` 文件，虽被 `.gitignore` 排除；本轮不绕过执行策略删除它们。

## 只读审查边界

之前记录的 K1 服务 HTTP 可响应只证明部分进程可用；ASR 无麦克风、TTS 被 mask、图形显示不可用，完整 readiness 和实体外设链路未验证。K1 上曾有审查探针写入 `/tmp/health.readonly` 与 `/tmp/codex-health-body` 的记录，未核验或清理；后续继续保持板端只读。
