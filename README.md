# Photo Organizer

自动整理 Nikon 相机导入照片的长期维护型 Python 项目。

**当前状态：MVP 骨架 + 元数据读取 + 地点模块已实现。** 配置读取、领域模型、exifread 元数据读取（`inspect`）、GPS 反向地理编码与地点命名（`location-preview`）已完成，均用真实 Nikon 照片验证；命名规划、执行、监听留待后续迭代。

> 本文档同时是**架构蓝图**与**现状说明**；标注「蓝图」的内容尚未实现。

**一般用户请看：[docs/用户指南.md](docs/用户指南.md)** —— 面向非开发者的简体中文使用说明（安装与配置、dry-run 与真实导入、watcher、常见问题、首次安全验证流程）。

---

## 1. 项目概述

**要解决的问题：** 把相机（重点 Nikon）导入的照片从杂乱状态整理成有规律、可检索的目录结构。

**输入：** 相机 SD 卡、下载目录等任意"收件箱"。
**输出：** 按日期组织的目录树 + 可重跑的导入流程。

**Nikon 特有的现实约束（设计必须考虑）：**
- 文件类型多：`NEF`（Raw）、`DNG`、`JPEG`、`MOV/MP4`（视频）、`.XMP`/`.THM`（附属文件）。
- `DSC_0001.NEF` 这类序列文件名会**跨卡、跨天重复**——必须靠 EXIF 拍摄时间重命名，不能靠文件名。
- 双卡槽相机会出现同一照片写入两张卡——天然的重复检测场景。
- EXIF 通常**不含时区**，拍摄时间解析需要可配置的策略。
- MakerNote 含镜头等私货元数据，依赖成熟的元数据引擎而非自己解析。

---

## 2. 核心设计原则

1. **管道化（Pipeline）：** 导入 = 一条单向数据流，每个阶段是可插拔的"步骤"。新功能 = 新增步骤，而非改动现有步骤。
2. **分层 + 依赖方向单一：** 所有模块只依赖 `domain`（领域模型），高层（CLI）依赖低层（reader、planner），**禁止反向依赖与循环依赖**。
3. **接口隔离（Seam）：** 每个"将来可能换实现"的地方（元数据引擎、存储后端、GPS 服务商）都定义协议/接口。
4. **配置与代码分离：** 代码零硬编码路径；所有策略（命名、目录方案、行为）都来自配置。
5. **非破坏性优先：** 默认 `dry-run` + `copy`；真正移动需显式开启。
6. **幂等可重跑：** 同一批照片跑两次结果一致（蓝图：数据库记录去重）。
7. **纯函数可测试：** 命名、规划是"元数据 → 动作"的纯函数，无 I/O，可单测。

---

## 3. 总体架构（数据流）

```
收件箱目录/相机挂载点
        │
        ▼
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│ discover    │────▶│ metadata    │────▶│ enrich       │
│ 扫描文件     │     │ 读EXIF      │     │ 去重/GPS/补全 │
│ → SourceFile│     │ → PhotoRecord│     │ → Enriched   │   [蓝图]
└─────────────┘     └─────────────┘     └──────────────┘
        ┌─────────────┐     ┌─────────────┐     ┌──────────────┐
        │ planner     │────▶│ executor    │────▶│ report/storage│
        │ 规划目标     │     │ 执行/演练    │     │ 汇总/入库     │
        └─────────────┘     └─────────────┘     └──────────────┘
```

各阶段通过不可变的领域对象传递：`SourceFile → PhotoRecord → PlannedAction`。

**关键点：** 管道中段（`metadata` 之后）的所有阶段只消费**标准化后的 `PhotoRecord`**，不接触原始 EXIF 和文件系统。这就是"以后加功能不用大改"的结构性保证——GPS、去重、地点分类都只是在中段插入或替换一个步骤。

---

## 4. 模块职责

| 模块 | 职责 | 状态 |
|---|---|---|
| `domain/models` | 纯数据模型（`PhotoRecord`/`PlannedAction`）+ 枚举，无任何依赖 | ✅ 已实现 |
| `config` | 读取 TOML 配置 → `Config`（stdlib `tomllib`，简单版） | ✅ 已实现 |
| `metadata/reader` | 文件 → 标准化 `PhotoRecord`（exifread 已实现；exiftool 可作第二后端） | ✅ 已实现（真实 NEF 验证） |
| `planner` | `PhotoRecord` → `PlannedAction`（命名模板 + 冲突消解） | 🔧 接口已建 |
| `executor` | 执行/演练 copy/move/symlink；唯一写目标盘文件的模块 | 🔧 接口已建 |
| `watcher` | 监听收件箱，新文件出现即触发管道 | 🔧 接口已建 |
| `location/*` | GPS 反向地理编码 + 地点命名（archive/detail/admin + CJK）+ 每日主导地点 | ✅ 已实现（fake geocoder 测试 + 真实照片验证） |
| `cli` | 参数解析、调用管道 | ✅ 已实现（横幅 + `inspect` + `location-preview` + `plan`） |
| `discover` / `enrich` | 扫文件、去重/GPS/地点富化 | 🚫 蓝图 |
| `reporting` / `storage` | 日志汇总 / SQLite 入库 | 🚫 蓝图 |

> `storage`（SQLAlchemy + Alembic）在 **v2 引入**，MVP 刻意不加入——先跑通管道，再用迁移演进 schema。

**依赖方向：**

```
cli → planner/executor/watcher → metadata → domain
                          ↕
                      domain（所有箭头终点，最底层）
```

规则：`executor` 不能 import `cli`；`planner` 不知道数据库；`metadata` 不认识目标目录。

---

## 5. 目录结构（当前 MVP）

```
photo-organizer/
├── pyproject.toml              # 项目元数据 + 依赖（PEP 621，hatchling）
├── README.md                   # 本文档
├── .gitignore
├── config/
│   ├── default.toml            # 默认配置（可选；缺失键回退内建默认）
│   └── local.toml.example      # 本地覆盖模板（local.toml 本身 gitignore）
├── src/
│   └── photo_organizer/
│       ├── __init__.py         # 包版本
│       ├── cli.py              # Typer 入口（inspect / location-preview / plan）
│       ├── config.py           # tomllib 读取配置 → Config dataclass
│       ├── domain/
│       │   ├── __init__.py
│       │   └── models.py       # PhotoRecord / PlannedAction / MediaKind / ActionKind
│       ├── metadata/
│       │   ├── __init__.py
│       │   └── reader.py       # MetadataReader 协议 + ExifReader（exifread 后端骨架）
│       ├── planner.py          # plan(records, config) → list[PlannedAction]
│       ├── executor.py         # Executor.execute(actions) → ExecutionReport
│       └── watcher.py          # Watcher（inbox + callback）
├── tests/
│   ├── test_cli.py             # 冒烟测试（CLI 横幅 + 版本）
│   └── (unit/ integration/ fixtures/ 待补)
├── logs/
└── skills/                     # Claude Code 技能目录
```

**蓝图中的演进**（结构无需大改，在既有接缝上扩展）：
`discover/`、`enrich/`（去重、GPS）、`reporting/`、`storage/`（SQLite+alembic）、`observers/` 归入 watcher 实现、`scripts/`、`tests/{unit,integration,fixtures}`。

---

## 6. 技术选型

### MVP 依赖

| 用途 | 库 | 为什么 |
|---|---|---|
| CLI | `typer` | 类型提示驱动、自动帮助文档、基于 click |
| 元数据 | `exifread` | **纯 Python**、零外部依赖，能读 NEF/JPEG 的 EXIF；MVP 阶段足够 |
| 配置读取 | `tomllib`（stdlib） | Python 3.11+ 内置 TOML 解析，零依赖 |
| 反向地理编码 | `geopy` + Nominatim | 结构化地址字段提取（非 display_name 解析）；`language="zh"` 获取中文地名 |

> **验证结论（2026-08-04，847 个真实 NIKON Z 30 NEF）：** exifread 解析 **847/847 成功**；DateTimeOriginal、机型、镜头型号全部读取；GPS 819/847（其余为拍摄时未定位）。MakerNote 简单标量（序列号、快门数、白平衡等）可解码，但深层子 IFD（AFInfo、LensData、FlashInfo、ShotInfo、VRInfo、PictureControl、曲线）以原始字节/空值暴露——**MVP 目标字段均不受影响**（镜头型号走标准 EXIF `LensModel`）。如需这些深层数据，届时引入 ExifTool 作为第二 reader。

### 蓝图依赖（按需引入）

| 用途 | 库 | 引入时机 |
|---|---|---|
| 元数据升级 | `exiftool`（外部二进制）+ 封装 | 需完整 Nikon MakerNote/写入能力时，作为第二个 reader 实现 |
| 配置校验 | `pydantic-settings` | 配置复杂度上升后替换 `config.py` 内部，调用方不变 |
| 输出 | `rich` | 需要进度条/表格时 |
| 持久化 | `SQLAlchemy 2.0` + `alembic` | v2 加 SQLite 时 |
| 监听 | `watchdog` | 实现 `Watcher.start()` 时 |
| 去重 | `hashlib` + `imagehash` | v2 |
| GPS 地点 | `geopy` / `reverse_geocoder` | v3 |

> 元数据引擎被封装在 `MetadataReader` 协议后：exiftool 是更强的引擎，但 exifread 让 MVP 保持纯 Python；升级时管道其余部分完全无感。

---

## 7. 领域模型

所有模型是**无行为、无依赖的 dataclass**，是管道各阶段的唯一契约。

```python
class MediaKind(StrEnum):          # RAW | IMAGE | VIDEO | SIDECAR
class ActionKind(StrEnum):         # COPY | MOVE | SYMLINK | SKIP

@dataclass(frozen=True)
class PhotoRecord:
    source_path: Path
    media_kind: MediaKind
    captured_at: datetime | None = None
    camera_make: str | None = None     # "NIKON CORPORATION"
    camera_model: str | None = None    # "NIKON D850"
    lens: str | None = None
    iso: int | None = None
    exposure: str | None = None        # "1/250s"
    aperture: str | None = None        # "f/4"
    focal_length: str | None = None    # "50mm"
    gps: tuple[float, float] | None = None   # (lat, lon)

@dataclass(frozen=True)
class PlannedAction:
    kind: ActionKind
    source: Path
    dest: Path
    reason: str = ""                   # 便于日志与审计
```

`PhotoRecord` 是**最大公约数**：无论相机品牌、元数据引擎，管道后段只见这个结构。

---

## 8. 配置设计（MVP 为单文件简单版）

```toml
# config/default.toml
inbox = "~/Pictures/Inbox"            # 收件箱 / 相机挂载点
dest_root = "~/Pictures/Organized"    # 输出根目录
mode = "copy"                         # copy | move | symlink
dry_run = true                        # true: 只规划不落盘
```

读取规则：文件缺失或键缺失 → 回退内建默认值；未知键当前忽略。
`Config` 是 frozen dataclass，`load_config(path)` 加载。
**蓝图：** 演进为三层合并（default / local / 环境变量）+ pydantic-settings 校验，调用方不变。

---

## 9. 错误处理、日志与幂等性（蓝图）

- 单文件失败不中断整批；结束后汇总 `成功 / 跳过 / 失败`。
- 三级日志：console + `logs/*.log`（rotating）+ 入库状态。
- 幂等：以哈希 + 源路径为唯一索引（v2 引入 SQLite 后），重跑跳过已入库项。
- 安全执行：同盘 move 原子操作；跨盘先 copy → 校验 → 删源。

---

## 10. 测试策略

| 层级 | 覆盖内容 | 状态 |
|---|---|---|
| 冒烟 | CLI 横幅、包版本 | ✅ 已有（`tests/test_cli.py`） |
| 单元 | `metadata` 辅助函数（suffix/DMS 换算/mtime 回退） | ✅ 已有（`tests/test_metadata.py`） |
| 单元 | `planner` 命名/冲突、`config` 加载 | 🚫 待业务逻辑落地后 |
| 集成 | 临时目录 + 样例照片跑完整管道 | 🚫 蓝图 |
| 契约 | 换 `MetadataReader` 实现，管道结果不变 | 🚫 蓝图 |

需要 exiftool 的测试用 `pytest.mark.skipif` 在缺失二进制时自动跳过。

### Watcher `--execute` 集成验证脚本

`tests/watch_execute_verify.sh` 验证 watcher 的**真实 `--execute` 行为**（非 dry-run）：真实复制到目标库、状态文件落盘且含 done 记录、重启后相同 size/mtime 的已处理文件不再触发批次、目标内容与源一致。运行：

```bash
bash tests/watch_execute_verify.sh
```

- **全断言通过**：自动清理本轮临时目录（`/tmp/watchvfy.*`）。
- **任一断言失败**：保留临时目录作为现场，打印检查指引与 `rm -rf` 删除命令。
- 全程使用隔离的临时 inbox / dest / watch-state / config（含 executor 日志），强制临时 `--state`，不碰 `~/.cache/photo-organizer/`；**不得触碰**：
  - `/mnt/d/Photography_Progress_Test`
  - `/mnt/d/Photography_Progress_Test_inbox`

---

## 11. 扩展性路线图

| 未来功能 | 落点 | 为什么不用改结构 |
|---|---|---|
| **SQLite 数据库** | v2：`storage/` 仓储层 + alembic 迁移 | 从 v1 就预留接口边界；schema 演进靠迁移 |
| **重复检测** | 中段插入 `dedup` 步骤 | 输入输出仍是既定模型 |
| **GPS 地点分类** | `enrich` 步骤 + `PhotoRecord.gps` 字段 | 后段只见标准化字段 |
| **Lightroom 配合** | ① 目标目录设为 Lr 监视目录（纯配置）② 写 XMP sidecar（末段新步骤）③ 读 Lr 目录（新 reader 实现） | 都落在既有接缝上 |
| **自动监听导入** | `Watcher` 复用同一管道 + `watch` 命令 | CLI 保持薄，逻辑不复制 |
| **其他品牌相机** | 新增 reader 实现 | 管道后段只见 `PhotoRecord` |

**节奏：**
- **v1（MVP，当前）：** 骨架 + 接口。
- **v2：** discover 扫描、exifread 映射、plan 命名规划、executor 执行（dry-run 默认）、SQLite 导入记录、重复检测、`watch` 监听。
- **v3：** GPS 地点分类、Lightroom 集成、报告与缩略图。

---

## 12. 开发规范

- **类型提示是强制要求**，`mypy --strict` 通过才能合入；`ruff` 负责 lint 与格式。
- 测试随功能走；契约测试保证换实现不出错。
- commit 用 conventional commits（`feat` / `fix` / `refactor` / `docs`）。
- 依赖锁定；升级依赖走 CI。
- 项目成熟后维护一份 `CLAUDE.md` 记录约定（模块边界、测试命令、命名规范）。

### 本地开发

```bash
.venv/bin/pip install -e '.[dev]'   # 安装包 + dev 工具
.venv/bin/photo-organizer           # 运行 CLI（输出 "Photo Organizer"）
.venv/bin/photo-organizer inspect <path>   # 单文件元数据调试（NEF/JPG）
.venv/bin/photo-organizer location-preview <file|folder> [--mode archive|detail|admin] [--location-name TEXT]
                                         # 每日主导地点预览（只读，不移动文件）
.venv/bin/photo-organizer plan <source> <dest_root> [--limit N] [--location-mode archive|detail|admin]
                                         # 只读跑完整管道：discover→metadata→location→planner（预览报告，不落盘）
.venv/bin/python -m pytest          # 跑测试
.venv/bin/ruff check src tests      # lint
.venv/bin/mypy src                  # 类型检查
```

---

## 13. 当前文件说明

| 文件 | 状态 | 说明 |
|---|---|---|
| `README.md` | ✅ | 本文档 |
| `pyproject.toml` | ✅ | 元数据 + 依赖 + 工具配置（替代 `requirements.txt`） |
| `config.py` | ✅ | 独立模块 `src/photo_organizer/config.py` + `config/*.toml` |
| `requirements.txt` | ❌ | 已删除，由 `pyproject.toml` 取代 |
| `logs/` | ✅ | 保留，接入 rotating 日志（蓝图） |
| `tests/` | ✅ | 冒烟测试已就位 |
| `skills/` | ✅ | 保留（Claude Code 技能） |
| `src/photo_organizer/location/` | ✅ | 地点模型 / 归一化 / 地理编码 / 缓存 / 每日主导地点 |

---

## 14. Location 地点模块

借鉴 Apple Photos 的思路：不止行政区划，而是从反向地理编码提取**多个地点候选**，再按用途选名。

**目录结构**

```
location/
├── models.py      # LocationCandidate / LocationInfo / DailyLocationResult / LocationMode
├── normalizer.py  # LocationNameNormalizer：archive/detail/admin + CJK 规则 + 文件名清理
├── geocoder.py    # ReverseGeocoder 协议 + NominatimGeocoder（geopy，≥1s 间隔，失败抛 GeocodingError）
├── cache.py       # ~/.cache/photo-organizer/geocoding.json，坐标 4 位小数，读写失败不中断
└── resolver.py    # DailyLocationResolver：按日分组 → GPS 簇聚合 → 主导地点 + 置信度
```

**命名模式**
- `archive`（默认，文件夹用）：scenic area → city/town/locality → municipality/county → admin1 → country。
- `detail`（日志/预览）：POI → park → neighborhood → locality → city。
- `admin`：纯行政区划。

**CJK 规则**（CN/HK/MO/TW/JP/KR）：中文短名、剥离常见后缀（市/县/市町村后缀/특별시等）、罗马字别名映射（Tokyo→東京、Seoul→首尔…）；香港→香港、澳门→澳门、北京/上海/天津/重庆直用。

**主导地点判定**：≥60% → high；40–60% 且领先第二名 ≥15% → medium；否则 Multi_Location/low；无 GPS → Unknown_Location/none。手动优先级：`date_overrides` > `--location-name` > GPS > Unknown。

**验证备注**：真实 Z30 照片（深圳/江西弋阳）输出中文地点正确；Nominatim 对深圳点把区级"南山区"放入 `city` 字段、地级市"深圳市"仅在 display_name——按"优先结构化字段"的设计，archive 输出"南山区"。若日后需要严格到地级市，可扩展 `_cn()` 的字段选择。

> 本模块**只读**：不创建目录、不移动/复制文件、不接入 planner。
