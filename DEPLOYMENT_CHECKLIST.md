# 首次云端配置与发布确认清单

当前工作流已经包含每日计划，但计划任务的 job 默认保持跳过；Pages 部署也有第二道独立开关。缺少任一开关、合规证据、AI 配置或最低条目数时都不会发布。

## 一、首次需要决定或申请的事项

1. **仓库与公开范围**
   - 选择 GitHub 仓库和默认分支。
   - 决定仓库公开或私有，并核对该账户方案是否支持所需的 GitHub Pages 可见性。
   - 在仓库 Settings → Pages 中把 Source 设为 **GitHub Actions**。
   - 启用 Issues，供失败工作流创建或更新告警 Issue。

2. **新闻来源许可**
   - 逐项审核 `config/production_sources.yaml` 的使用条款、robots、RSS/API 条款与公开摘要权利。
   - 只有审核通过的来源才能改成 `rights_review: approved`。
   - 只有允许把内容送入模型时才设 `allow_substantive_analysis: true`；只有允许公开自写摘要时才设 `allow_public_summary: true`。
   - 只有明确允许页面抓取时才设 `article_fetch: true`。否则保持仅 RSS/API 或元数据模式。
   - 当前按用途放行 11 个来源：Agência Brasil（须署名）、NASA News Releases（只用署名文字摘录）、USGS（公共数据）、美国司法部、FDA、NIH、GOV.UK、European Commission、Horizon Magazine、Global Voices 与 NIST。Horizon/Global Voices 必须保留作者与许可署名；NIST 只抓 robots 允许的正文并公开自写摘录。其余候选保持 `pending`，只能用于发现线索和交叉核验，不能进入正文或 AI 证据包。
   - 放行来源仍须满足独立双来源或一手资料规则；授权本身不等于证据充分。
   - “未来24小时”还需要在 `config/calendar_sources.yaml` 添加经过审核的官方 RSS/Atom/ICS 日历；空配置时该栏目保持空白。

3. **AI 提供商、模型与预算**
   - 当前只实现了 OpenAI Responses API 适配器，但默认关闭；没有暗中选择模型。
   - 在首次使用当天，从 [OpenAI 模型与价格页面](https://developers.openai.com/api/docs/pricing) 确认可用模型、输入价、输出价与账户限制，不使用旧价格猜测。
   - 适配器使用 [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create) 的 JSON Schema 结构化输出、输出 token 上限和 usage 记录。
   - 请求设置 `store: false`，只把权利审核通过的证据包发给模型。
   - 如不选择 OpenAI，需要先实现并测试另一适配器，不能把其他服务的密钥塞进 `OPENAI_API_KEY`。

## 二、GitHub Actions Variables

在 Settings → Secrets and variables → Actions → Variables 中配置：

| 名称 | 首次安全值 | 用途 |
|---|---|---|
| `ENABLE_AI_ANALYSIS` | `false` | 确认模型与预算后才改为 `true` |
| `ENABLE_PUBLISH` | `false` | 你确认首次发布后才改为 `true` |
| `ENABLE_SCHEDULED_PUBLISH` | `false` | 验证手动生产运行后才改为 `true` |
| `GLOBAL_NEWS_CONTACT_URL` | 公开联系页 URL | 写入采集器 User-Agent |
| `NEWS_AI_MODEL` | 待确认 | 精确模型 ID |
| `NEWS_AI_MAX_EVENTS` | 待确认 | 每日最多分析事件数 |
| `NEWS_AI_MAX_INPUT_TOKENS` | 待确认 | 每日输入 token 硬上限 |
| `NEWS_AI_MAX_OUTPUT_TOKENS` | 待确认 | 每日输出 token 硬上限 |
| `NEWS_AI_MAX_DAILY_USD` | 待确认 | 每日美元硬上限 |
| `NEWS_AI_INPUT_USD_PER_1M` | 待确认 | 当天官方输入单价，用于预算闸门 |
| `NEWS_AI_OUTPUT_USD_PER_1M` | 待确认 | 当天官方输出单价，用于预算闸门 |

价格变量缺失、非正数或预计超预算时，分析器会停止，不会“先调用后记账”。

首次配置已于 2026-09-11 完成并通过手动生产验收：模型为 `gpt-5.6-terra`，输入 75,000 token、输出 14,400 token、美元硬上限 0.35；首次发布结束后三个 `ENABLE_*` 开关已重新设为 `false`。预算账本按北京时间日期跨 runner 累计。扩源版无 AI 探针在 9 个模型候选上计算出三次重试的最坏预留为 77,784 输入 token、43,200 输出 token、0.673968 美元；账本另已记录 5 个完成事件。因此再次生产前建议把硬上限设为：事件 14、输入 85,000、输出 45,000、0.70 美元。实际账单通常低于最坏预留，但 0.70 美元是本轮授权上限而不是费用承诺；不得清空账本规避限制。

## 三、GitHub Secret

只需要在 Actions Secrets 中增加：

- `OPENAI_API_KEY`：选择 OpenAI 并批准付费调用后再添加。

不要上传 `.env`、浏览器 Cookie、个人访问令牌、新闻站账号、日志中的响应正文或本机配置。`GITHUB_TOKEN` 由每次运行自动签发，无需手工创建。

## 四、需要上传到目标仓库的文件

- `.github/workflows/daily-news.yml`
- `app/`
- `assets/`
- `config/`
- `schemas/`
- `tests/`
- `site/`（作为首次发布前可检查的上一版静态回退）
- `requirements.txt`
- `.gitignore`、`.env.example`
- `README.md`、`EDITORIAL_SPEC.md`、`CODEX_TASK.md`、`SOURCE_NOTES.md`
- `NEXT_BUILD.md`、`LOCAL_PREVIEW.md`、`PROJECT_STATUS.md`、`DEPLOYMENT_CHECKLIST.md`
- `source-health.md`、`source-health.json`、`validation-report.json`

不要上传 `.env`、`state/history/`、`state/publishable-events.json`、`state/run-report.json`、`state/run-source-health.json` 或 `restored-state/`。运行状态由 Actions artifact 保存。

## 五、首次发布顺序

1. 上传上述文件，但保持三个 `ENABLE_*` 变量为 `false`。
2. 手动运行 `workflow_dispatch → validate`，检查 24 小时采集和 diagnostics artifact。此时不会调用 AI 或部署。
3. 完成来源许可审核，填写模型、价格、token 与美元预算，把 `ENABLE_AI_ANALYSIS` 改为 `true`，仍保持 `ENABLE_PUBLISH=false`。
4. 手动运行 `workflow_dispatch → production`。从 diagnostics artifact 的 `state/candidate-edition.json` 检查生成事件、逐条引用、覆盖缺口和预算报告；因为发布开关仍关闭，不会部署。
5. 把结果交给你确认。确认后才把 `ENABLE_PUBLISH=true`，再次手动运行 production，完成首次 Pages 发布。
6. 首次发布验证通过后，才把 `ENABLE_SCHEDULED_PUBLISH=true`。计划时间为 UTC 23:37，即北京时间次日 07:37；GitHub schedule 不是准点 SLA。

截至 2026-09-11，旧的 5 条版已完成公网验收；扩源修正版尚未付费生成或覆盖 Pages，第 6 步仍未执行，定时发布保持关闭。

GitHub Pages 工作流采用官方的 `configure-pages`、`upload-pages-artifact` 和 `deploy-pages` 流程，配置依据见 [GitHub Pages 自定义 Actions 工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。工作流中的官方 Action 已在 2026-09-10 通过各自 GitHub Releases API 核对并锁定到具体版本；上传前仍应复核一次兼容性和安全公告。

## 六、保留与失败行为

- 只有成功通过全部闸门的构建才上传 Pages artifact，因此失败运行不会替换现有 Pages 版本。
- 最近一次成功的历史归档以 `news-state` artifact 保存 90 天；每轮先恢复它，再生成新的历史页。
- diagnostics artifact 保存 14 天。
- 页面超过 30 小时未更新会显示过期警告。
- pipeline 失败会创建或更新同一个 GitHub Issue，并附运行链接。
