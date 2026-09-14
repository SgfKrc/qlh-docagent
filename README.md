# docagent

> **Language**: [English](README.en.md) · [简体中文](README.md)

`docagent` 是一个独立的、以标准库优先的只读文档维护扫描器。

```text
python tools/docagent/run.py rules
python tools/docagent/run.py scan --root G:/path/to/project --fail-on none
python tools/docagent/run.py scan --root G:/path/to/project --profile minimal --json --fail-on none
python tools/docagent/run.py audit --root G:/path/to/project --output build/docagent/audit.json
python tools/docagent/run.py audit-entry --root G:/path/to/project --doc docs/plan.md --anchor delivery
python tools/docagent/run.py init --root G:/path/to/project
python tools/docagent/run.py config --root G:/path/to/project
```

P3B 内核按 profile 定义的文档树递归扫描，使用版本化的 R1-R5 规则契约。内置 profile 有 `qlh`（主项目的 Git 感知布局）与 `minimal`（关闭 Git 检查）。项目可以运行 `init` 把 profile 复制到 `.docagent/profile.yaml`，也可以显式传 `--profile <名字或路径>`。`scan` 永不写入目标仓库；`audit --output` 可以在配置的文档目录之外写报告。

主项目的兼容入口是 `python scripts/doc_maintenance_audit.py`：其历史 M1 参数委托给本包，并继续写 `build/doc-audit/audit.json` 与 `audit.md`；M2/M3 扩展参数在专用适配器迁移完成前仍走旧实现。

## 专用环境配置

把 [`.env.docagent.example`](.env.docagent.example) 复制为目标仓库里的 `.env.docagent`（该副本被 Git 忽略）。docagent 从不读取主 `.env`，也从不合并进程环境中的 `DOCAGENT_*` 值。

扫描前先校验该文件：

```text
python tools/docagent/run.py config --root .
python tools/docagent/run.py config --root . --env configs/team.docagent.env --json
```

显式给出的 `--env` 文件必须存在。不带 `--env` 时，`.env.docagent` 缺失不影响 scan/audit 的向后兼容；一旦项目文件存在，就会被加载并 fail-closed 校验。非法或重复字段、不安全 URL、非 loopback 的 Ollama 端点、向上越级的 profile 路径，以及显式选择的远程 provider 配置不完整，都会以退出码 2 和字段级消息返回；错误信息永不包含具体取值。

`DOCAGENT_PROFILE` 接受内置名（`qlh` 或 `minimal`）或相对 env 文件的 profile 路径。选择优先级为 `--profile` > `DOCAGENT_PROFILE` > 既有项目/内置默认。当 env 文件不在 `<root>/.env.docagent` 时，扫描时要显式传入同一个 env：

```text
python tools/docagent/run.py scan --root . --env configs/team.docagent.env --json --fail-on none
```

`config` 会报告哪些非机密字段来自文件、哪些使用了默认值。`DOCAGENT_DEEPSEEK_API_KEY` 仅以"是否已配置"的布尔形式出现；其取值被排除在对象表示、stdout、stderr、JSON 报告与扫描报告之外。独立扫描器不会发起 LLM 请求；这些 provider 设置现在就校验，是为随后单独迁移的 M2 适配器做准备。

CI 约定、稳定退出码、报告格式与 GitHub Actions 示例见 [`CI.md`](CI.md)。

dry-run 基线增量可通过 `--baseline --dry-run` 获得，报告 `new`、`gone`、`changed` 与 `affected_docs`，并可选 `--max-new N`、`--max-gone N` 门。

## 定点条目审查

`audit-entry` 只检查**一个**显式选中的 Markdown 标题段或一条字面条目行；它不递归文档树，也不执行测试。报告会抽取有界的声明行，仅对选中内容复用 R1/R3，检查被引用或显式的证据路径，读取 Git 提交/工作区状态，并输出结构化的"表述 vs 实际"差异清单。

```text
python tools/docagent/run.py audit-entry --root . \
  --doc docs/release-plan.md --anchor acceptance \
  --evidence test-results/acceptance.xml --json --fail-on warn

python tools/docagent/run.py audit-entry --root . \
  --doc docs/tickets.md --entry DOC-AUDIT-ENTRY-01 --occurrence 2 \
  --markdown --output build/docagent/entry-audit.md --fail-on none
```

`--doc` 与每个显式 `--evidence` 值都必须是仓库相对路径，且文档必须位于 profile 的 `docs_dir` 之下。`--entry` 匹配到多处时 fail-closed，除非给出 1-based 的 `--occurrence`。`--anchor` 接受 GitHub 风格的标题 slug（带不带 `#` 均可），包含 `-1` 这类重复标题后缀。输出文件若落在 `docs_dir` 内会被拒绝。

JSON schema 为 `qlh.docagent.entry-audit.v1`。`read_only=true` 与 `tests_executed=false` 把证据边界显式化：没有对应结果产物的 `12 passed` 声明会被报告为 `TEST_RESULT_UNBOUND`。较小的 JSON/JUnit XML/日志产物会被解析并比对其可用计数；不可读或不匹配的证据仍记为差异，而不会被当作已验证。`--fail-on` 接受 `none`、`info`、`warn`、`error`、`R1` 或 `R3`；选择器/配置类错误返回 2。

报告包含覆盖其完整无路径 JSON 内容的 `report_fingerprint`。可在一次 audit 调用中生成 CI 门产物，并可选追加一条 M3 事件：

```text
python tools/docagent/run.py audit --root . --profile qlh --json --fail-on error \
  --output build/docagent-gates/report.json \
  --gate build/docagent-gates/gate.json \
  --events build/docagent-gates/events.sqlite
```

在接收产物之前，用规则文件与所有提供的绑定校验报告。报告或规则文件发生变化返回 1；产物或配置畸形返回 2：

```text
python tools/docagent/run.py gate verify \
  --report build/docagent-gates/report.json \
  --rules .docagent/rules.yaml --profile qlh \
  --gate build/docagent-gates/gate.json
```

规则经 Git 回退后，再对未变的基线重扫。该命令是基线 dry-run 的显式简写且必须有基线，因此成功的回滚由"零增量"来证明：

```text
git revert <rules-change-commit>
python tools/docagent/run.py gate rescan --root . \
  --baseline build/doc-audit/baseline.json --json --fail-on none
```

规则演进通过 `rules evolve` 设门。agent 可以准备候选规则文件，但候选只是数据：扫描器与校验器代码不在这个入口里。每条演进记录必须带非空 `change_note`，保存新旧指纹与稳定的演进指纹，并遵循 `proposed -> preflight -> approved -> released`（或 `rejected`）。

```text
python tools/docagent/run.py rules evolve \
  --old .docagent/rules.yaml --new rules-next.yaml \
  --change-note "why this candidate is needed" \
  --state approved --output build/docagent-gates/evolution.json
```

低风险的词表/参数变更自动批准。新增或删除 `warn`/`error` 规则、以及严重级别变更，会停在 `preflight` 并以退出码 1 结束。人工复核后，提供 `evolution_fingerprint` 与 preflight 记录匹配的批准 JSON，再把记录转为 `approved`、最后转为 `released`。批准记录的形状与门的行止见 [`CI.md`](CI.md)。

## Patchouli TUI（交互书架）

`patchouli/` 为本仓库的只读交互前端（Textual）：书架浏览 / 全文与票号检索 / 编目诊断（委托本仓库 `scan`）/ 流通记录（git log）/ 馆藏统计 / RAG 参数对照 / 启动动画与 `.env.docagent` 检测引导与编辑。

```bash
python -m pip install -e .
python -m patchouli.bookshelf --root <repo>   # 详见 patchouli/README.md
```
