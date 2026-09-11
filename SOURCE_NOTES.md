# 信息源核验与官方依据

核验日期：2026-09-11。下列是来源许可与接口设计所参照的官方网站/文档；实际连通情况另见 `source-health.md`。

## 已核对的重要技术事实

- ReliefWeb API V2，2025-11-01起需要预先批准的appname；资料原发布者可能保留版权。
  https://apidoc.reliefweb.int/
- USGS提供供程序使用的GeoJSON地震订阅；本次样例地址返回有效JSON。空features可以是正常结果。
  https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php
  https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson
  https://www.usgs.gov/data-management/data-licensing
- EBC 当前说明 Agência Brasil 内容可在注明来源时免费转载；生产白名单只使用 RSS 摘录，不抓文章页，并保留署名与原链。
  https://www.ebc.com.br/sobre/agencia-brasil
- NASA有官方RSS目录，分新闻发布、最近内容及任务/主题。官方媒体指南允许新闻媒体和信息性网站在注明NASA、不暗示背书并排除标注的第三方版权材料时使用NASA内容；本站仅使用News Releases文字摘录生成署名摘要，不复制图片或标志。
  https://www.nasa.gov/rss-feeds/
  https://www.nasa.gov/nasa-brand-center/images-and-media/
- 美国司法部说明其网站材料除非另有标注通常属于公有领域；本站引用部门并排除印章、标志与第三方材料。
  https://www.justice.gov/legalpolicies
  https://www.justice.gov/news
- FDA说明其网站文字与图形除非另有标注通常属于公有领域；本站优先使用官方新闻稿RSS并保留来源链接。
  https://www.fda.gov/about-fda/about-website/website-policies
  https://www.fda.gov/about-fda/contact-fda/subscribe-podcasts-and-news-feeds
- NIH说明其大部分网站内容属于公有领域，但仍须识别例外；当前RSS中无法解析具体发布时间的条目不能通过24小时闸门。
  https://www.nih.gov/about-nih/frequently-asked-questions
  https://www.nih.gov/news-events/news-releases
- GOV.UK内容默认按Open Government Licence提供；使用时须遵守署名与例外条款。
  https://www.gov.uk/help/reuse-govuk-content
  https://www.nationalarchives.gov.uk/information-management/re-using-public-sector-information/uk-government-licensing-framework/open-government-licence/
- European Commission默认按CC BY 4.0或CC0规则允许再利用，但第三方内容、标志和其他例外不在默认许可内。
  https://digital-strategy.ec.europa.eu/en/news/rules-reuse-commission-information
- Horizon Magazine明确允许按CC BY 4.0转载故事，并要求保留作者、原链与来源；采集器保留feed作者署名。
  https://projects.research-and-innovation.ec.europa.eu/en/horizon-magazine/republish-our-stories
- Global Voices网站标明CC BY 3.0并提供署名政策；其单一编辑部报道仍需第二独立来源才能成为事实性正文。
  https://globalvoices.org/
- NIST说明未另行标注的信息通常不受美国版权保护并建议保留作者署名；本站仅对robots允许的NIST文章抓取语义正文，公开端只显示自写摘要和证据摘录。
  https://www.nist.gov/copyrights-disclaimers
  https://www.nist.gov/coo/nist-rss-feeds
- 美联储有公告、演讲、数据等RSS目录。
  https://www.federalreserve.gov/feeds/feeds.htm
- UNESCO世界遗产中心列有新闻RSS，但syndication条款对再发布要求事先书面授权；RSS存在不是公开转载许可。
  https://whc.unesco.org/en/news/
  https://whc.unesco.org/en/syndication
- Guardian有RSS使用说明和Open Platform文档；不能把个人阅读许可推定为公共网站再发布许可。
  https://www.theguardian.com/help/feeds
  https://open-platform.theguardian.com/documentation/
- AP提供数据许可产品；本方案没有购买授权。
  https://www.ap.org/content/formats/data/
- GDELT DOC API官方介绍包括跨语言检索和JSON输出。它是发现索引，不是事实证明；旧文档的覆盖/限制须部署时复核。
  https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
- GitHub Actions schedule可能延迟，高负载时任务可能丢弃；只在默认分支运行；公共仓库长期无活动可能被停用。当前文档支持可选IANA timezone。
  https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- GitHub Pages是静态托管，访问范围必须独立核对；不能仅凭仓库私有就认定网页私有。
  https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages

## 本次查阅的专题来源

文化遗产： https://whc.unesco.org/en/news/
生态： https://www.unep.org/news-and-stories
气候气象： https://wmo.int/news
保护与环境报道： https://news.mongabay.com/
公共卫生： https://www.who.int/emergencies/disease-outbreak-news
劳工： https://www.ilo.org/resource/news
粮食农业： https://www.fao.org/newsroom/en
人道援助： https://www.icrc.org/en/news
经济： https://www.imf.org/en/news
发展： https://www.worldbank.org/ext/en/news
贸易： https://www.wto.org/english/news_e/news_e.htm
非西方科技社会： https://restofworld.org/about/

## 本次查阅的媒体/区域页面

Reuters： https://reutersagency.com/about/
AP： https://www.ap.org/about/
BBC： https://bbcnews.bbcstudios.com/
CNA： https://www.channelnewsasia.com/
Dawn： https://www.dawn.com/
Africanews： https://www.africanews.com/
EL PAÍS： https://english.elpais.com/

## 尚不能作出的承诺

1. 清单上的78个条目没有全部完成接口、内容质量及使用许可检查；当前只有 `config/production_sources.yaml` 中标为 `approved` 的11个来源能进入正文证据包。
2. 不把本研究工具遇到的403/429/robots限制视为用户机器一定不能访问；也不能把浏览器打开成功当作允许爬取。
3. 同一机构的不同频道、同一故事的多个转载链接不增加独立证据链。
4. 模型API与GitHub权限已经过一次人工生产验收，但扩源版尚未再次付费分析或覆盖公开页面。
5. 真正的GitHub `schedule` 尚未运行；不能把人工触发成功写成无人值守定时已验证。
