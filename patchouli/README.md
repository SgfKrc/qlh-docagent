# Patchouli — 知识库/文档库管理 TUI（只读）

> 设计与票分解见主仓 `docs/Patchouli知识库管理TUI专项计划-2026-09-14.md`。
> 纪律：**除 `.env.docagent` 外只读**（配置编辑为唯一写路径，自动 .bak 备份）、零第三方依赖（除 Textual 本体）、fail-soft（未知格式不崩溃）。

## 现状

| 票 | 内容 | 状态 |
| --- | --- | --- |
| `PATCH-01` | 只读数据层（catalog）：docs/ 扫描 + 状态行/更新日期/票号/互链/分类解析 | ✅ 完成（2026-09-14） |
| `PATCH-02` | 书架 TUI（三栏：列表→文档卡→预览；分类过滤/归档开关） | ✅ 完成（2026-09-14，Textual） |
| `PATCH-03` | 检索台（本地元数据+全文检索；RAG 对接待知识库服务化后接） | ✅ 完成（2026-09-14） |
| `PATCH-04` | 编目诊断（委托 docagent scan：R1-R5 findings + fail-soft + 异步 worker） | ✅ 完成（2026-09-14） |
| `PATCH-05` | 流通记录（git log --follow 时间线 + ±行统计） | ✅ 完成（2026-09-14） |
| `PATCH-06` | 馆藏统计（状态行覆盖率/分类/月份/票号 Top/互链）+ 收口 | ✅ 完成（2026-09-14） |
| `PATCH-07` | 检索增强：chunk→parent 映射（章节定位+全貌）+ rewrite/rerank 参数对照（`v` 键） | ✅ 完成（2026-09-14） |
| `PATCH-08` | 启动动画（并行加载**不延迟启动**）+ `.env.docagent` 检测引导与 TUI 内编辑（`e` 键） | ✅ 完成（2026-09-14） |

## 用法

```bash
# 安装（一键，npm 式：editable + 全局命令 + 默认根配置 + 自检；幂等可重跑）
powershell -ExecutionPolicy Bypass -File tools/docagent/patchouli/install.ps1
# 或手动两步：python -m pip install -e tools/docagent && python -m patchouli setup

# 统一入口（console script `patchouli`；python -m patchouli 等价）
patchouli                          # 书架 TUI（非 TTY 自动退回 summary）
patchouli shelf --root <repo>      # 书架 TUI（显式；键位：↑↓ 选择 · / 检索台 · 1-7 分类 · 0 全部 · a 归档 · c 编目诊断 · h 流转记录 · s 馆藏统计 · v 参数对照 · e 配置编辑 · r 刷新 · q 退出）
patchouli summary --root <repo>    # 馆藏摘要（分类/票号/缺状态行）
patchouli json --root <repo>       # 全量结构化（qlh.patchouli.catalog.v1）
patchouli --root <repo> --summary  # 直通 catalog 参数
# 检索语法：纯文本（全文）｜ t:PATCH-01（票号）｜ k:report（类型）｜ s:缺（缺状态行）｜ a:（含归档）

# 选项：--no-splash 跳过启动动画（或 PATCHOULI_NO_SPLASH=1）
#       --splash-time 1.5  启动动画最小展示秒数（默认 1.0；加载更慢时不额外等待）

# 免安装 fallback
python tools/docagent/patchouli/run.py summary --root <repo>
python tools/docagent/patchouli/run.py shelf --root <repo>
```

> **任意目录可用**：`patchouli` 缺省自动解析仓库根（优先级：`--root` > `PATCHOULI_ROOT` > 当前目录上溯含 `docs/` > `~/.patchouli/config.json` 的 `default_root`）。

> **依赖边界**：Patchouli 是**开发期工具**（与 docagent 同级），书架 TUI 允许 Textual；
> **引擎产品 TUI（`qlh chat`）仍遵守零第三方依赖基调**（见主仓《QLH-TUI跨平台基调》），两者定位不同。

## 示例输出（主仓 2026-09-14）

```
docs: 90（archive 10） 票号 510
分类: {decision 5, guide 16, other 10, reference 7, report 7, special-plan 43, ticket-plan 2}
缺状态行: 31
```

## 测试

```bash
python -m pytest tools/docagent/patchouli/tests -q   # 6 passed（含真实仓库冒烟）
```
