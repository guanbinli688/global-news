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

- 完整本地测试：`40/40` 通过；配置检查：`23/23` 通过。
- 新增 NASA 后的真实联网候选运行：截止 `2026-09-11T00:31:49.248626Z`，14/14 个配置源成功响应，取得 191 个 24 小时候选，聚为 178 个事件，15 个通过证据门槛；付费前队列按 USGS/NASA 各最多三条压缩到 6 个，零付费模式最终保留 3 条真实 USGS 中文候选。
- 当前候选覆盖：东南亚、大洋洲/太平洋；领域为灾害与科学。地区 `2/5`、领域 `2/7`，不足之处已显示，不凑数。
- `ENABLE_AI_ANALYSIS=false`、`ENABLE_PUBLISH=false`；付费调用 0 次，费用 0 美元，Cookie 0，全文发布 0。由于仅 3 条，低于 5 条门槛，`publish_ready=false`，未生成正式日报、未归档为成功版、未部署。
- 本地 HTTP：`index.html`、`data.json`、`coverage.html`、`source-health.html` 均返回 200。当前环境没有可连接的浏览器实例，因此桌面/手机真实截图与交互式视觉验收仍标为未验证；DOM/CSS/JS 自动测试已通过。
- GitHub 首次 `workflow_dispatch` 运行 `34544476818` 实际为 `run_mode=validate`，Ubuntu/Python 3.13 的安装、采集、检查和诊断 artifact 均成功；它取得 185 个候选、生成 3 条确定性 USGS 中文候选，但 `publish_ready=false`，未调用 AI、未生成正式 bundle、未部署。

## 尚未真实验证 / 外部阻塞

- Agência Brasil、NASA 与 USGS 已通过当前用途审核；Agência Brasil 的单一报道仍需独立证据，不能因许可已通过就自动入选。其余生产候选源保持许可待审。
- GitHub 仓库目前没有 Actions Secrets 或 Variables；`OPENAI_API_KEY`、精确模型、价格与事件/token/美元预算均缺失。
- GitHub Pages 尚未启用，`github-pages` 环境不存在；workflow 的 deploy job 已声明 `pages:write` 与 `id-token:write`，但首次 Pages 创建/公开部署尚未执行。
- `production` 模式、真实 AI、Pages 部署和真正的 `schedule` 均未运行。定时发布变量保持缺失/关闭。
