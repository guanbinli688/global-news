(() => {
  const staleWarning = document.querySelector('#stale-warning');
  if (staleWarning && window.NEWS_AS_OF) {
    const ageHours = (Date.now() - new Date(window.NEWS_AS_OF).getTime()) / 3600000;
    staleWarning.hidden = !(Number.isFinite(ageHours) && ageHours > Number(window.NEWS_STALE_HOURS || 30));
  }

  const template = document.querySelector('#story-cards-template');
  if (!template) return;

  const fragment = template.content.cloneNode(true);
  fragment.querySelectorAll('.story-card').forEach((card) => {
    const target = document.querySelector(`[data-cards-for="${CSS.escape(card.dataset.section)}"]`);
    if (target) target.appendChild(card);
  });

  let activeTopic = 'all';
  let activeRegion = 'all';
  const cards = [...document.querySelectorAll('.story-card')];
  const filterToggle = document.querySelector('.filter-toggle');
  const filterBody = document.querySelector('#filter-body');
  filterToggle?.addEventListener('click', () => {
    const expanded = filterToggle.getAttribute('aria-expanded') === 'true';
    filterToggle.setAttribute('aria-expanded', String(!expanded));
    filterBody?.classList.toggle('is-collapsed', expanded);
  });

  function applyFilters() {
    cards.forEach((card) => {
      const topicMatch = activeTopic === 'all' || card.dataset.topics.split(' ').includes(activeTopic);
      const regionMatch = activeRegion === 'all' || card.dataset.regions.split(' ').includes(activeRegion);
      card.classList.toggle('is-filtered', !(topicMatch && regionMatch));
    });
  }

  document.querySelectorAll('[data-filter-topic]').forEach((button) => {
    button.addEventListener('click', () => {
      activeTopic = button.dataset.filterTopic;
      document.querySelectorAll('[data-filter-topic]').forEach((item) => item.classList.toggle('is-active', item === button));
      applyFilters();
    });
  });
  document.querySelectorAll('[data-filter-region]').forEach((button) => {
    button.addEventListener('click', () => {
      activeRegion = button.dataset.filterRegion;
      document.querySelectorAll('[data-filter-region]').forEach((item) => item.classList.toggle('is-active', item === button));
      applyFilters();
    });
  });

  const dialog = document.querySelector('#evidence-dialog');
  const content = document.querySelector('#evidence-content');
  let eventIndex = new Map();
  let lastEvidenceTrigger = null;

  fetch('data.json')
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((data) => {
      eventIndex = new Map(data.events.map((event) => [event.event_id, event]));
    })
    .catch(() => {
      document.querySelectorAll('.evidence-button').forEach((button) => { button.disabled = true; });
      const warning = document.createElement('p');
      warning.className = 'data-error';
      warning.textContent = '证据数据暂时无法载入；当前页面仍可阅读，但证据按钮已停用。';
      document.querySelector('.status-ribbon')?.insertAdjacentElement('afterend', warning);
    });

  function addText(tag, text, parent = content) {
    const node = document.createElement(tag);
    node.textContent = text;
    parent.appendChild(node);
    return node;
  }

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-open-evidence]');
    if (!button || !dialog) return;
    const record = eventIndex.get(button.dataset.openEvidence);
    if (!record) return;
    lastEvidenceTrigger = button;
    content.replaceChildren();
    const methodNames = {
      metadata_preview: '元数据预览',
      deterministic_public_data: '公共数据规则化解读',
      openai: '模型结构化分析',
      cached_openai: '复用已核验模型分析',
    };
    addText('p', `EVIDENCE RECORD · ${methodNames[record.analysis_method] || '来源核验'}`, content).className = 'eyebrow';
    addText('h2', record.title_zh);
    addText('h4', '逐条主张与证据');
    const claimList = document.createElement('ul');
    record.claims.forEach((claim) => addText('li', `${claim.text_zh}〔${claim.source_ids.join('、')}〕${claim.verification_note ? ` — ${claim.verification_note}` : ''}`, claimList));
    content.appendChild(claimList);
    addText('h4', '来源');
    const sourceList = document.createElement('ul');
    record.sources.forEach((source) => {
      const row = document.createElement('li');
      const credit = source.attribution && source.attribution !== source.publisher
        ? ` · 署名：${source.attribution}` : '';
      const link = addText('a', `${source.id} · ${source.publisher}${credit} · ${source.title}`, row);
      try {
        const parsed = new URL(source.url);
        if (['http:', 'https:'].includes(parsed.protocol)) link.href = parsed.href;
      } catch (_) {}
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      sourceList.appendChild(row);
    });
    content.appendChild(sourceList);
    addText('h4', '仍未知');
    const list = document.createElement('ul');
    record.unknowns.forEach((item) => addText('li', item, list));
    content.appendChild(list);
    addText('h4', '分析状态');
    const analysis = record.analysis || {};
    if (analysis.why_it_matters) {
      addText('p', `为何重要：${analysis.why_it_matters}`);
      addText('p', `作用机制：${analysis.mechanism || '证据不足'}`);
      addText('p', `影响群体：${analysis.affected_groups || '证据不足'}`);
      addText('p', `反证/限制：${analysis.counter_evidence || '证据不足'}`);
      addText('p', `下一步观察：${analysis.watch_next || '证据不足'}`);
    } else {
      addText('p', '尚未生成深度分析；发布前仍需人工复核。');
    }
    dialog.showModal();
  });

  document.querySelector('.dialog-close')?.addEventListener('click', () => dialog.close());
  dialog?.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
  dialog?.addEventListener('close', () => lastEvidenceTrigger?.focus());
})();
