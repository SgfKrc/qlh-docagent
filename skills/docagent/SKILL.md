---
name: docagent
description: 文档维护与审计（帕秋莉机械扫描）：当用户要求扫描/审计文档库、检查状态行/链接/票号规范、维护 docs/、汇报前对齐文档状态时启用。基于 docagent 规则扫描（R1-R5）与 Patchouli 查询层（summary/search/diag/stats），只读、建议式处置。写代码、调试、通用问答不要用。
---

# docagent 文档维护（帕秋莉机械扫描）

## 何时用
- 用户提到：文档维护/审计/扫描/状态行/链接失效/编目/馆藏
- 任意文档库（有 md 的目录）需要体检；汇报前对齐"文档状态 vs 实际"
- 不用于：写代码、调试、通用问答

## 工具速查（先确认可用；任意目录可用）
- `patchouli summary [--root X | --lib N]` —— 馆藏摘要（份数/票号/缺状态行）
- `patchouli json` —— 全量结构化；`patchouli`（无参）= 书架 TUI
- `python -m docagent scan --root <库根> --json` —— 规则扫描 R1-R5
- `python -m docagent audit --root <库根> [--json]` —— 结构化审计报告（可落盘）
- `python -m docagent audit-entry` —— 单条目/锚点定点审查
- 未安装：`pip install -e <repo>/tools/docagent` 或跑 `tools/docagent/patchouli/install.ps1`

## R1-R5 规则与处置（建议式）
| 规则 | 含义 | 建议处置 |
| --- | --- | --- |
| R1 | 完成未收口 | 状态行改"已完成"并附验收证据/提交号 |
| R2 | 未提交登记 | 补提交，或从票表下架 |
| R3 | 状态行滞后（更新日期早于关联代码提交） | 复核后更新日期，或改注"参考" |
| R4 | 链接失效 | 修相对路径；`scripts/check_doc_links.py` 兜底全量校验 |
| R5 | 状态行缺失 | 补 `> 状态：…`（建议同加 `> 更新日期：YYYY-MM-DD`） |

## 文档规范（写文档时）
- 状态行：`> 状态：**…**（细节）`；日期行：`> 更新日期：YYYY-MM-DD`
- 票号：大写连字符风格（如 `EX-CACHE-01`、`PATCH-08`），会被 catalog 解析成票号
- 互链用相对路径；目录移动/归档后必须跑链接检查
- 清单类放 `local_docs/`（不进 git）；决策/规范类进 `docs/`

## 纪律（硬）
- **只读工具、建议式处置**：docagent/Patchouli 自身不改文档；改动由 agent 按项目纪律执行（删除/移动先 dry-run 列清单，确认后执行，执行后给验证证据）
- 汇报审计结果必须给**命令 + 数字**（如 "scan：4 findings（R3×3/R5×1）"），不臆测
- `.env.docagent` 是唯一允许经 Patchouli 编辑的配置（TUI `e` 键）；校验 `python -m docagent config`

## 示例工作流
1) `patchouli summary --root .`（体检总览）
2) `python -m docagent scan --root . --json`（findings）
3) R3/R5 逐条核对：文档内容 vs `git log -1 --format=%cs -- <file>`
4) 处置（改状态行/日期/链接）→ 复扫至 findings 清零或逐条说明
5) 提交（中文 message + 验收数字）并核验 `HEAD == origin/<branch>`
