# symbol-locator-locagent — 交接包

作者:(离职前打包)· 日期: 2026-08-17

本目录是把 **symbol-locator 插件** 和 **SWE-Explore-Bench 基准 + 实验结果** 一起交给下一个负责人的物理包。解压之后按下面三节走,就能跑起原来所有的实验。

- 想看设计文档 → **`./项目文档.md`**(架构、pyright 索引、指标、A/B 结论、下一步)
- 想跑实验 → 本文件第 2~4 节

---

## 0. 目录结构

```
./                                            # handoff 根目录(本 README 所在处)
├─ README.md              ← 你正在看的
├─ 项目文档.md            ← 详细设计+实验结论(60 KB)
│
├─ symbol-locator-locagent/       # 插件源码(独立 pip 项目)
│  ├─ symbol_locator/             # 5 个模块 core/install/lsp/rank/scorer/cache
│  ├─ bench.smoke3.jsonl          # 3 题 smoke bench
│  ├─ bench.narrow20.jsonl        # 20 题 IntentQ20 bench
│  ├─ bench.random30.jsonl        # 30 题 RandomQ30 bench(repo 未下载,备用)
│  ├─ issue_map_smoke3.json       # smoke3 的 issue → problem_statement 映射
│  ├─ issue_map_narrow20.json     # IntentQ20 的映射
│  ├─ narrow_gt_python.jsonl      # IntentQ20 用的 fine-grained GT
│  └─ README.md                   # 插件本体自述(monkey-patch 4 处、env 变量)
│
└─ SWE-Explore-Bench/            # 基准+跑评脚本+已有结果
   ├─ .env                       # ★ 需要你填 API key 和几个路径
   ├─ eval_runner.py             # 评测入口
   ├─ eval.py                    # 指标定义(hit_file/hit_region/noise/...)
   ├─ explorers/                 # 各个 explorer 的适配层(locagent_explorer.py 关键)
   ├─ third_party/LocAgent/      # LocAgent 源码(已 vendored,别再拉)
   ├─ commit_map.json            # bench → git commit 映射,fetch_repos 用
   ├─ fetch_repos.py             # 一键 clone + checkout 22 个仓库
   ├─ run_smoke3.sh              # ★ smoke3 A/B 脚本
   ├─ run_narrow20.sh            # ★ IntentQ20 A/B 脚本
   ├─ run_intentq20_ab_x2.sh     # IntentQ20 各跑 2 次(稳定性用)
   ├─ run_intentq20_pilot.sh     # 单题 pilot 用
   ├─ run_smoke.sh               # 单题 smoke(历史)
   ├─ results/                   # ★ 我跑过的所有结果都在这里,见第 5 节
   └─ (没有 repos/ — 下面第 3 步一键拉)
```

**打包时刻意排除的**:各 repo(`SWE-Explore-Bench/repos/`,5-10 GB,用 `fetch_repos.py` 一键复现)、`__pycache__/`、`*.log`、`results/*_logs/`、`.claude/`。

---

## 1. 依赖清单

| 依赖 | 版本要求 | 装法 |
|---|---|---|
| Python | ≥ 3.10 | `conda create -n locagent python=3.11` |
| pyright | 任意 | `pip install pyright` **或** `npm i -g pyright` |
| Node.js | pyright 底层需要 | 系统包管理器,或 conda 的 nodejs |
| conda | 任意版本 | 脚本用 `conda run -n locagent` 隔离环境 |
| LocAgent 依赖 | 见 `SWE-Explore-Bench/pyproject.toml` | `cd SWE-Explore-Bench && pip install -e .[locagent]` |
| symbol-locator 依赖 | 无额外 | 靠 LocAgent 已装的 `litellm` |

装完后:

```bash
which pyright-langserver     # 记下这个路径,后面 .env 要填
which conda                  # 同上
```

---

## 2. 一次性配置

### 2.1 填 `.env`

编辑 `SWE-Explore-Bench/.env`,把 6 个 `<FILL_ME_IN>` 换成你机器上的实际值:

```bash
LLM_API_KEY=<你的 API key>
HANDOFF=<解压出来的 handoff 根目录绝对路径>        # 例如 /home/foo/symbol-locator-handoff
CONDA_EXE=<which conda 的输出>                    # 例如 /opt/miniconda3/bin/conda
SYMBOL_LOCATOR_PYRIGHT_BIN=<which pyright-langserver 的输出>
```

其它字段(`LLM_API_BASE` / `LLM_MODEL` / `LOCAGENT_MAX_ITER` / ...)默认值适配我们跑过的 `glm-5.2`,想换模型改 `LLM_MODEL` 即可。

### 2.2 插件自检(不需要 LLM,不需要 API key,90 秒跑完)

```bash
cd $HANDOFF/symbol-locator-locagent
python -m symbol_locator.cache
python -m symbol_locator.scorer
python -m symbol_locator.core
python -m symbol_locator.install
```

四条命令都打印 `... demo OK` 就说明插件本体 + monkey-patch 层完好。

### 2.3 拉取 bench 用到的仓库(20~30 分钟, 5-10 GB 硬盘)

```bash
cd $HANDOFF/SWE-Explore-Bench

# 拉 IntentQ20 用到的 20 个 repo(django×14, sphinx×2, pytest×1, matplotlib×1, sklearn×1, sympy×1)
python fetch_repos.py clone-bench \
    --bench-file ../symbol-locator-locagent/bench.narrow20.jsonl \
    --commit-map-file commit_map.json

# smoke3 的 3 题也在这 20 个里,不需要再拉
```

拉完的仓库放 `SWE-Explore-Bench/repos/<owner>__<name>/`,每个都 checkout 到 bench 记录的那个 commit。

---

## 3. 跑 smoke3(3 题, 约 15~20 分钟, 快速烟测)

**目的**:验证整个 A/B pipeline 通,大约 15 分钟出结果。3 题分别覆盖 sphinx / matplotlib / sympy。

```bash
cd $HANDOFF/SWE-Explore-Bench

# A 面(vanilla LocAgent)
./run_smoke3.sh
# 结果: results/smoke3_A_vanilla/locagent_top5.jsonl (3 行)

# B 面(加插件)
SYMBOL_LOCATOR_ENABLED=1 ./run_smoke3.sh
# 结果: results/smoke3_B_symloc/locagent_top5.jsonl (3 行)
```

**注意脚本会 refuse 覆盖非空目录**,重跑前 `rm -rf results/smoke3_{A_vanilla,B_symloc}`。

### 评测

```bash
python eval_runner.py --score-only \
    --bench ../symbol-locator-locagent/bench.smoke3.jsonl \
    --issue-map ../symbol-locator-locagent/issue_map_smoke3.json \
    --results results/smoke3_A_vanilla/locagent_top5.jsonl \
              results/smoke3_B_symloc/locagent_top5.jsonl
```

### 我跑过的 smoke3 结果(仅供对比)

- **口径**: `hit_file_rate` = 官方口径,仅计 `read_core_files`,不看 `modified/main`
- 我们前后跑了 3 轮(pre-parserfix / pre-promptfix / post-promptfix / rollback),都保留在 `results/smoke3_*/` 目录

| 组 | prompt 状态 | hit_file | hit_region |
|---|---|---|---|
| A vanilla | pre-promptfix | 0.583 | - |
| A vanilla | post-promptfix ("MUST BE MODIFIED") | 0.417 | - |
| A vanilla | **rollback**(HEAD, 当前) | **0.667** | 0.583 |
| B symloc | pre-promptfix | 0.750 | - |
| B symloc | post-promptfix | 0.417 | - |
| B symloc | **rollback**(HEAD, 当前) | **0.750** | 0.667 |

**关键结论**:`MUST BE MODIFIED` prompt 把 B 相对 A 的 +29 % 优势抹平(它强制丢弃 read-only 上下文文件,而这些恰好落在评测的 `read_core_files` 里)。已回滚到 HEAD 版本,`explorers/locagent_explorer.py:92-101`。同事再改 prompt 前请先看这段 diff 历史。

---

## 4. 跑 IntentQ20(20 题, 约 3 小时/面, 主实验)

**目的**:主要 A/B 结论就靠这 20 题。选题规则见 `项目文档.md` 第 6.1 节。

```bash
cd $HANDOFF/SWE-Explore-Bench

# 旧结果留着的话先 mv 走(脚本 refuse 覆盖非空目录)
mv results/narrow20_A_vanilla   results/narrow20_A_vanilla.$(date +%s) 2>/dev/null || true
mv results/narrow20_B_symloc    results/narrow20_B_symloc.$(date +%s)  2>/dev/null || true

# A 面(约 90 min)
./run_narrow20.sh
# → results/narrow20_A_vanilla/locagent_top5.jsonl (20 行)

# B 面(约 80 min)
SYMBOL_LOCATOR_ENABLED=1 ./run_narrow20.sh
# → results/narrow20_B_symloc/locagent_top5.jsonl  (20 行)
```

想要跑 2 次取均值(稳定性验证),用:

```bash
./run_intentq20_ab_x2.sh
# 顺序: A_run1 → A_run2 → B_run1 → B_run2, 总耗时 5~6 小时
# 结果: results/intentq20_{A_vanilla,B_symloc}_run{1,2}/
```

### 评测

```bash
python eval_runner.py --score-only \
    --bench ../symbol-locator-locagent/bench.narrow20.jsonl \
    --issue-map ../symbol-locator-locagent/issue_map_narrow20.json \
    --results results/narrow20_A_vanilla/locagent_top5.jsonl \
              results/narrow20_B_symloc/locagent_top5.jsonl
```

### 我跑过的 IntentQ20 结果(用于回归对齐)

已在 `results/` 里的 6 组数据:

| 目录 | 说明 | 记录条数 |
|---|---|---|
| `intentq20_A_vanilla_run1/` | A 主跑第 1 次 | 20 |
| `intentq20_A_vanilla_run2/` | A 主跑第 2 次 | 20 |
| `intentq20_B_symloc_run1/` | B 主跑第 1 次 | 20 |
| `intentq20_B_symloc_run2/` | B 主跑第 2 次 | 20 |
| `narrow20_A_vanilla/` | 早期一次跑(23 行,含 append 污染,不建议直接对比) | 23 |
| `narrow20_B_symloc/` | 同上 | 22 |

**主要指标(项目文档 6.1 节, 20 题算术均值)**:

| 指标 | A vanilla | B symloc | Δ | 相对变化 |
|---|---|---|---|---|
| hit_file_rate ↑ | 0.550 | **0.700** | +0.150 | +27 % |
| hit_region_rate ↑ | 0.400 | **0.550** | +0.150 | +38 % |
| noise_region_rate ↓ | 0.360 | **0.180** | −0.180 | **−50 %**(越低越好)|
| F1 (region) | 0.312 | **0.435** | +0.123 | +39 % |

新负责人复跑后若与上表偏差 < 0.05(20 题量级,LLM 温度产生的抖动范围),就算对齐成功。

---

## 5. `results/` 里的所有目录说明

| 目录 | 实验 | 何时跑 | 是否有价值 |
|---|---|---|---|
| `smoke_*/` | 单题 smoke(sphinx-9320) | 项目最早期 | 历史,可删 |
| `smoke3_A_vanilla{,.pre_parserfix,.pre_promptfix,.post_promptfix}/` | 3 题 A 面各阶段 | 修 bug 期间 | 保留,追根因用 |
| `smoke3_B_symloc{,.pre_parserfix,.pre_promptfix,.post_promptfix,.for_inspect,.v3_range}/` | 3 题 B 面各阶段 | 同上 | 同上 |
| `intentq20_pilot_{A,B}/` | 1 题 pilot,验 issue_map 通道 | 主实验前 | 历史 |
| `narrow20_{A,B}/` | 20 题第一次跑,有 append 污染 | 主实验第一版 | 参考,不建议做主对比 |
| `intentq20_{A,B}_run{1,2}/` | 20 题各跑 2 次,干净结果 | 主实验最终版 | **本项目主要证据,别删** |

想删干净,只留主证据:

```bash
cd $HANDOFF/SWE-Explore-Bench
rm -rf results/smoke_* results/smoke3_*.pre_* results/smoke3_*.post_* \
       results/smoke3_*.for_inspect results/smoke3_*.v3_range \
       results/intentq20_pilot_* results/narrow20_*
```

---

## 6. 下一步(项目文档 6.2 节里的原话)

- **稳定性**:再跑 1-2 次 IntentQ20 A/B(用 `run_intentq20_ab_x2.sh`),做 paired t-test / bootstrap,证明 +0.15 不是抖动。
- **RandomQ30**:`bench.random30.jsonl` 已抽好但仓库没下载。第一步先 `python fetch_repos.py clone-bench --bench-file ../symbol-locator-locagent/bench.random30.jsonl --commit-map-file commit_map.json`,再照 IntentQ20 写一个 `run_random30.sh`。
- **调 scorer**:`symbol_locator/scorer.py` 的 batch prompt 是快速版,可以尝试蒸馏成小模型跑 local,减少 LLM 调用成本。
- **加语言**:`symbol_locator/lsp.py` 现在只跑 pyright,同样的思路可以加 typescript-language-server / clangd,不需要动上层。

---

## 7. 有问题问谁

设计层的问题 → 先读 `项目文档.md` 前 5 节(架构 + pyright 索引 + rank/scorer/cache 内部)。数据/实验的具体口径 → 项目文档第 6 节(选题规则 + 指标定义)。工程细节 → `symbol-locator-locagent/README.md`(monkey-patch 4 处、env 变量清单)。

打包时的旧 session 记录、修 bug 过程都不在这个包里,如需要问原作者。
