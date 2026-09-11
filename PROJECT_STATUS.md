# 项目状态

更新时间：2026-09-10

## 可恢复记录

- 本目录原先没有 Git 仓库。本轮在功能修改前创建了本地基线提交 `d54c0f5`；当前远端为 `https://github.com/guanbinli688/global-news.git`，分支 `main`。
- 继续沿用 `app/build.py` 和 `site/`，保留米白/蓝色报刊式界面、八栏目、左侧目录、卡片及地区/议题筛选。
- `state/` 保存本地运行报告、候选、缓存与预算账本，并由 `.gitignore` 排除；Pages 只允许上传 `site/`。

## 已实现

- 24 小时 UTC 截止、未来时间保护、事件聚类、转载独立性、逐条 claim/source/evidence 校验及 schema/日期校验。
- 文章级地区/议题不再继承媒体覆盖范围；USGS 结构化公共数据可确定性生成中文标题、80–150 字摘要、三条来源绑定主张、影响与未知项。
- 生产分析只接收权利已审核的证据片段；只有标题的元数据不能进入正文或模型证据包。
- 增加 NASA 官方 News Releases RSS；仅使用文字摘录、注明 NASA、排除图片/标志和标注的第三方版权材料。来源组最多三条的限制已提前到付费分析之前执行。
- OpenAI Responses 适配器保持 `store:false`，模型、价格、事件/token/美元上限全部外部配置；增加跨 runner 分析缓存和保守预算预留账本。
- 保留上一版、北京版面日期归档、30 小时过期提示、来源/覆盖页、移动筛选、键盘焦点恢复、数据载入失败提示。
- Actions 已包含手动/每日触发、并发与超时、状态恢复、诊断/历史 artifact、Pages 官方部署链和合并失败 Issue；所有云端开关默认关闭。
- 2026-09-10 通过 GitHub 官方 Releases API 核对并固定 Actions 版本：checkout 7.0.1、setup-python 7.0.0、upload-artifact 7.0.1、configure-pages 6.0.0、upload-pages-artifact 5.0.0、deploy-pages 5.0.1。

## 实际验收结果

- 旧版完整本地测试：`43/43` 通过；配置检查：`23/23` 通过。
- 新增 NASA 后的真实联网候选运行：截止 `2026-09-11T01:08:54.819812Z`，14/14 个配置源成功响应，取得 190 个 24 小时候选，聚为 177 个事件，15 个通过证据门槛；付费前队列按 USGS/NASA 各最多三条压缩到 6 个，零付费模式最终保留 3 条真实 USGS 中文候选。
- 当前候选覆盖：东南亚、大洋洲/太平洋；领域为灾害与科学。地区 `2/5`、领域 `2/7`，不足之处已显示，不凑数。
- `ENABLE_AI_ANALYSIS=false`、`ENABLE_PUBLISH=false`；付费调用 0 次，费用 0 美元，Cookie 0，全文发布 0。由于仅 3 条，低于 5 条门槛，`publish_ready=false`，未生成正式日报、未归档为成功版、未部署。
- 本地 HTTP：`index.html`、`data.json`、`coverage.html`、`source-health.html` 均返回 200。当前环境没有可连接的浏览器实例，因此桌面/手机真实截图与交互式视觉验收仍标为未验证；DOM/CSS/JS 自动测试已通过。
- GitHub 首次 `workflow_dispatch` 运行 `34544476818` 实际为 `run_mode=validate`，Ubuntu/Python 3.13 的安装、采集、检查和诊断 artifact 均成功；它取得 185 个候选、生成 3 条确定性 USGS 中文候选，但 `publish_ready=false`，未调用 AI、未生成正式 bundle、未部署。
- 首次 `production` 运行 `34547840146` 正确启用了 AI、模型和预算，但 OpenAI 在生成前以 HTTP 400 拒绝 schema 中不支持的 `uniqueItems`；实际输入/输出 token 均为 0、估算费用为 0。质量闸门阻止了 Pages，失败 Issue 告警成功。修复后 API schema 会去除该不支持关键字，本地 schema 仍执行数组唯一性校验；workflow 也已补齐 `ENABLE_PUBLISH` 环境变量。
- 第二次 `production` 运行 `34548706403` 成功取得 3 个结构化 AI 响应，实际使用 2,129 输入 token、1,408 输出 token，按配置单价估算 0.021154 美元；三个响应的 `why_it_matters`/`mechanism` 不完整，均被深度分析闸门拒绝，Pages 未部署。后续修复不降低该闸门：只选择至少 160 字符证据的模型候选，并要求对一手机构材料近距离改写证据明确支持的影响与机制；预算不足时继续处理无需付费的结构化公共数据。
- 第三次 `production` 运行 `34549139124` 首次通过 pipeline 与 Pages deploy，共发布 5 条（2 条 NASA AI 分析、3 条 USGS 确定性稿件）；当日累计实际用量 3,745 输入 token、2,590 输出 token，估算 0.03857 美元。发布后人工核查发现“吉布提签署”稿被模型误标为东亚，因此新增国家别名到地区的确定性校正；国家全部命中映射时以映射结果覆盖模型地区，并已加入误标与未知国家测试。
- 纠正版 `production` 运行 `34549575046` 使用 2 条缓存 AI 分析，新增 AI token 与费用均为 0；pipeline 与 deploy 均成功。公网 `index.html`、`data.json`、`coverage.html`、`source-health.html` 均返回 200，线上为 `publication_mode=production`、共 5 条；吉布提稿地区已校正为撒哈拉以南非洲，并保留活动地点美国对应的北美标签。

## 5 条新闻问题复盘与扩源修正

- 旧版并非只找到 5 条：当时抓到 190 个 24 小时候选、15 个事件通过证据门槛，但正文许可白名单只有 3 个来源，分析队列又按来源组最多 3 条截断，最终成为 2 条 NASA 加 3 条 USGS。最低 5 条只能防止空版，不能保证多样性，因而错误地让地震占 60%。
- 新增并实际连通 8 个合规来源：美国司法部、FDA、NIH、GOV.UK、European Commission、Horizon Magazine、Global Voices 与 NIST；连同原有 3 个，正文许可白名单为 11 个。对需要署名的来源保留作者/机构署名；NIST 只在 robots 允许时抓取语义正文；NIH 缺少可解析发布时间的条目继续被 24 小时闸门拒绝。
- 发布质量闸门从单一的 `5 条` 升级为：至少 10 条、5 个被引用来源组、4 个有内容栏目、4 个地区、6 个主题；单一来源组最多 2 条，灾害主题最多 2 条。证据不足或覆盖不足时不发布，也不覆盖现有 Pages。
- 扩源版本地测试 `45/45` 通过，配置检查 `27/27` 通过。免费真实联网验收截止 `2026-09-11T01:38:30.201019Z`：22/22 个生产源成功响应，取得 222 个 24 小时候选，聚为 208 个事件，44 个通过证据闸门，11 个进入分析队列，覆盖 6 个可用证据来源组。
- 该次验收显式保持 `ENABLE_AI_ANALYSIS=false`、`ENABLE_PUBLISH=false`，付费调用 0、部署 0。只有 USGS 能在无模型时确定性生成 2 条候选，因此质量闸门按 `2/10 条、1/5 来源组、1/4 栏目、2/4 地区、2/6 主题` 正确阻断。这 2 条不是付费生产版的预计条数；队列中另有 9 个文本事件需要模型生成与逐条验证。
- 最终代码提交 `3fb95f0` 已推送到 `main`；GitHub `validate` 运行 `34552525482` 在 Ubuntu/Python 3.13 上成功。云端实际取得 223 个候选、209 个聚类、43 个证据合格事件、11 个分析候选；`ai_enabled=false`，付费调用为 0，Pages 配置、artifact 上传与 deploy 全部跳过，原有公开版未被覆盖。云端 27 项配置检查全部通过。
- 把“files/filed complaint”加入无人值守敏感指控排除后，无 AI 预算探针仍选出 9 个模型候选，并用另一条已裁决/和解事项替换诉状。单次输入估算合计 25,928 token；按最多三次尝试预留为 77,784 输入、43,200 输出、0.673968 美元。为保留小幅波动空间，再次生产建议硬上限为累计 14 个事件、85,000 输入、45,000 输出、0.70 美元。

## 尚未真实验证 / 外部阻塞

- 11 个正文来源已通过当前用途审核；Agência Brasil、Horizon Magazine 与 Global Voices 的单一新闻编辑部报道仍需独立证据，不能因许可已通过就自动入选。其余生产候选源保持许可待审。
- GitHub 已配置 `OPENAI_API_KEY` Secret，以及 `gpt-5.6-terra`、5 个累计事件、75,000 输入 token、14,400 输出 token、0.35 美元等预算 Variables；Secret 值未进入仓库或日志。当前这些上限不足以跑完扩源队列；生产前需确认是否改为 14、85,000、45,000、0.70 美元，并重新确认付费和公开覆盖。
- GitHub Pages 已按 Actions workflow 模式发布至 `https://guanbinli688.github.io/global-news/`；workflow 的 deploy job 使用 `pages:write` 与 `id-token:write`。
- 手动 `production`、AI、缓存恢复、历史归档、失败告警、质量闸门和 Pages 部署均已真实运行。一次性发布完成后 `ENABLE_AI_ANALYSIS=false`、`ENABLE_PUBLISH=false`、`ENABLE_SCHEDULED_PUBLISH=false`；真正的 `schedule` 仍未运行。
