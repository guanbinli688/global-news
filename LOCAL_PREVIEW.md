# 本地预览运行说明

本地支持两种明确分开的输出：默认的元数据界面预览，以及由本轮真实公开证据生成、但绝不发布的中文候选版。生产管线必须同时通过合规、证据、分析、预算、最少条目数和显式发布开关；本地预览不会发布网站，不会读取或上传浏览器 Cookie，也不会保存新闻全文。

## 1. 重新诊断信息源

在 `global-news-v1` 目录运行：

```powershell
python -m app.source_diagnostics
```

输出：

- `source-health.json`：机器可读的状态、HTTP 响应、条目数、最新时间和失败原因。
- `source-health.md`：人工阅读版本。
- `state/latest-items.json`：仅包含允许进入本地预览的标题、链接、时间和来源元数据。

ReliefWeb 没有预批准的 `RELIEFWEB_APPNAME` 时保持关闭。403、404、429、空列表和过期订阅源分别记录，不绕过限制。

## 2. 校验并构建静态网站

```powershell
python -m app.validate
python -m app.build
# 也兼容原入口：python app/build.py
```

构建结果位于 `site/`。首页包含八个栏目、15 个议题筛选、10 个地区筛选、证据抽屉、来源健康页、历史页和更正日志页。没有达到证据门槛的栏目会保持空白。

## 3. 生成真实来源、零付费的未发布候选

```powershell
$env:ENABLE_AI_ANALYSIS='false'
$env:ENABLE_PUBLISH='false'
python -m app.pipeline --mode production --build-candidate
```

该命令会联网读取 `config/production_sources.yaml` 的公开接口，只把通过权利和证据门槛的结构化公共数据改写成中文。若仍低于正式日报最少条目数，命令以非零状态结束，但会保留 `state/candidate-edition.json` 并将 `site/` 标为 `LIVE SOURCE · UNPUBLISHED CANDIDATE`；这不是发布失败，而是发布闸门按设计关闭。需要模型翻译/分析的条目不会被伪造。

## 4. 启动本地服务器

```powershell
python -m http.server 8765 --directory site
```

打开 `http://127.0.0.1:8765/`。停止服务器请按 `Ctrl+C`。

## 5. 运行测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖去重、上游转载、仅日期时间、栏目不足不凑数、冲突归因、无模型不编造分析、模型429/超时、密钥缺失、预算账本、404/429/空列表、来源失败覆盖、未来日历、更正结构、敏感信息与转义、主张来源引用、移动筛选、键盘焦点及项目子路径。

## 6. 无付费调用的生产闸门回归

```powershell
$env:ENABLE_AI_ANALYSIS='false'
$env:ENABLE_PUBLISH='false'
python -m app.pipeline --mode production
```

证据或 AI 不足时该命令应返回非零状态、写出 `state/run-report.json`，且不生成/替换生产站点。这是预期的 fail-closed 行为。
