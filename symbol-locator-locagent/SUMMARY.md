# symbol-locator-locagent 集成总结

> 日期: 2026-08-14  
> 目标: A/B 评测 LocAgent + 自研 symbol-locator 插件在 SWE-Explore-Bench 上的表现

## 1. 项目定位

- **不污染** `/data/workspace/orcaloca_openclaw/openclaw-symbol-locator` (openclaw 版)
- 独立目录 `/data/workspace/orcaloca_openclaw/symbol-locator-locagent`,针对 LocAgent 重写适配层
- 通过 monkey-patch 把 `find_symbol` / `more_symbols` / `reset_symbols` 三个工具注入 LocAgent

## 2. 架构

```
symbol_locator/
├── core.py       # 3 个工具函数 (find_symbol / more_symbols / reset_symbols)
├── install.py    # 4 处 monkey-patch 把工具挂进 LocAgent
├── lsp.py        # PyrightClient (workspace/symbol)
├── rank.py       # 名称匹配 pre-rank
├── scorer.py     # LLM batch scoring (context 相关性)
└── cache.py      # 候选缓存 + 分页
```

### install.py 的 4 处 patch

| # | 位置 | 作用 |
|---|------|------|
| 1 | `plugins.location_tools.repo_ops.repo_ops` | 注入函数 + 扩 `__all__`,让 `import_functions` 拷贝出去 |
| 2 | `util.runtime.function_calling.ALL_FUNCTIONS` | 加白名单,避免 `response_to_actions()` 抛错 |
| 3 | `util.runtime.function_calling.get_tools()` | 追加 ChatCompletionToolParam,让 LLM 看到工具 |
| 4 | `util.runtime.execute_ipython.execute_ipython` | 把函数注入 IPython `user_ns`,让 `print(func(**args))` 能跑 |

## 3. 本次会话解决的核心 Bug

### Bug 1: cold-path / hot-path 竞态

**现象**: `_patched_set_current_issue` 在 `_orig_set` 之后检查 `graph_pkl` 是否存在,而 `_orig_set` 内部的 `build_graph` 恰恰会写这个 pkl → cold path 被误判成 hot path。

**根因**: 检查顺序错。

**修复**: 在调用 `_orig_set` **之前** snapshot `hot_path = os.path.exists(graph_pkl)`。

```python
# 关键 snapshot 必须在 _orig_set 之前
hot_path = bool(graph_pkl) and os.path.exists(graph_pkl)
result = _orig_set(*args, **kwargs)
```

### Bug 2: `FileNotFoundError` on cold path

**现象**: 表象是 pyright-langserver 起不来,实际是 `setup_repo` 返回的 dangling symlink。

**根因**: 
- shim Patch 6 用 `os.symlink(_local_repo, repo_dir)`,`_local_repo` 是 **相对路径**
- `os.path.realpath(repo_dir)` 从 symlink 所在目录解析相对目标 → dangling

**修复**: `_resolve_local_repo()` 多候选回退 (LOCAL_REPO_PATH / OLDPWD / LOCAGENT_ROOT 向上 1~3 层)。

### Bug 3: parser 与 LLM 输出格式不匹配

**现象**: A/B 两面都产出 0 region。

**根因**: glm-5.2 的 raw_output 是 3 行块:
```
sphinx/cmd/quickstart.py
function: is_path
line: 40
```
而 `explorers/parsing.py` 的三个 fallback 全要求 `file.py:qname` 在同一行。

**修复**: 在 `parsing.py` 加 Fallback A2 — 识别 "独立路径行 + `function:` / `class:` / `method:` 前缀" 组合,支持多行块解析。

## 4. A/B 测试结果 (smoke bench, 1 sample)

**样本**: `sphinx-doc__sphinx-9320`  
**GT**: `sphinx/cmd/quickstart.py` 1~604 (**整文件**)

| | Precision | Recall | F1 | HitFile |
|---|---|---|---|---|
| **A vanilla** | 1.000 | 1.000 | 1.000 | 1 |
| **B symloc** | 1.000 | **0.384** | 0.555 | 1 |

### A/B 各自返回的 region

**A** (5 个):
- `quickstart.py:1--1` ← 整文件占位 (parser 兜底)
- `quickstart.py:185-320`
- `quickstart.py:323-421`
- `quickstart.py:160-182`
- `quickstart.py:538-600`

**B** (5 个):
- `quickstart.py:538-600`
- `quickstart.py:132-157`
- `quickstart.py:91-95`
- `quickstart.py:185-320`
- `quickstart.py:98-99`

### 为什么 A 赢 B?

**不是 A 更聪明,而是 GT 粒度太粗。**

- A 的第 1 个 region `1:-1` 被 `_regions_to_lines` 展开成 1..604 全行 → 一口气 recall=1.0
- B 五个精准方法级 region 加起来只 232 行 → 232/604 = 0.384
- 这题 GT = 整个文件,任何"整文件占位"都无脑满分,精细定位反而被惩罚

## 5. find_symbol 真的被用了吗?

**是,真实起作用。**

- **调用次数**: 21 次
- **有效返回**: 20 次 (只 1 次 `No symbols named 'quickstart' found`)
- **高分命中 GT 文件**: ≥8 次 (score≥70 落在 `quickstart.py`)
- **最终 5 个 region 全部来自 find_symbol 的高分位置**:
  - 538 (main), 132 (do_prompt), 91 (is_path), 185 (ask_user), 98

节选调用日志:
| symbol | 候选数 | top score | 位置 |
|--------|-------|-----------|------|
| do_prompt | 4 | 95 | quickstart.py:132 |
| main | 439 | 95 | quickstart.py:538 |
| ask_user | 1 | 95 | quickstart.py:185 |
| generate | 51 | 95 | quickstart.py:323 |
| get_parser | 6 | 95 | quickstart.py:453 |
| is_path | 14 | 70 | quickstart.py:91 |

## 6. 结论 & 下一步

### 结论

1. **插件工程上跑通** — 21 次工具调用、20 次有效返回、hooks/warmup/cold+hot path 全过
2. **单样本 A/B 不能证明有用性** — 这个 GT 是整文件,天然偏袒"整文件占位"策略
3. **要证明 find_symbol 提升定位质量,需要 GT 是窄行范围的样本** (比如 GT `start:100, end:120` 那种)

### 下一步

- 换用 `issue_map.json` 里 GT 是窄 region 的多样本 bench
- 每样本跑 N 次取均值,削 LLM 随机性
- 关注指标: **Precision (窄 GT 下最能反映定位精度)** 与 **每次 tool 调用带来的 line-recall 增量**

## 7. 关键环境变量

```bash
# .env
LLM_MODEL=glm-5.2
LOCAGENT_KEEP_TMP=1                    # 保 tmpdir 到 ~/locagent_keep 方便追日志
LOCAGENT_MAX_ITER=6                    # glm 慢,压小轮次
LOCAGENT_HARD_MAX_ITER=8
SYMBOL_LOCATOR_PATH=/data/workspace/orcaloca_openclaw/symbol-locator-locagent
SYMBOL_LOCATOR_PYRIGHT_BIN=/data/miniconda3/envs/locagent/bin/pyright-langserver
# A/B 开关
SYMBOL_LOCATOR_ENABLED=1  # 开=B; unset=A
```

## 8. 冒烟脚本

```bash
./run_smoke.sh                             # A 面
SYMBOL_LOCATOR_ENABLED=1 ./run_smoke.sh    # B 面
```

日志: `~/locagent_keep/<instance_id>/output/localize.log`  
结果: `results/smoke_{A_vanilla|B_symloc}/locagent_top5.jsonl`
