(function () {
  'use strict';
  // One guide for chat, the figure workspace, and previously bookmarked guide URLs.
  if (window.CitrusGuide) return;
  var pages = [
    {id: 'research', nav: '研究与文献', title: '从研究问题到来源证据', lead: '说清目标与范围，再沿着引用核对结论。', sections: [
      {id: 'research', title: '提出研究问题',
        flow: ['研究问题', '范围与材料', '有依据的回答'],
        steps: [['说明目标', '写出研究对象、时间范围和希望得到的成果；比较、综述、数据核查都可以直接提出。'], ['补充条件', '说明已知背景、使用哪些文件，以及希望保留的单位、分组和引用格式。'], ['核对执行结果', '展开执行过程看实际完成的步骤。缺少资料、工具失败或覆盖不足时，先补充条件再继续。']],
        example: '比较柑橘黄龙病的主要防控方法，区分田间证据与实验室证据，并列出来源。', note: '回答和工具结果需要结合原始资料复核，不能代替专业判断。'},
      {id: 'sources', title: '检索文献与核对来源',
        flow: ['检索相关文献', '展开引用', '核对原文与 DOI'],
        steps: [['限定检索范围', '写出主题、年份、关键词和需要比较的内容；可用来源取决于当前检索服务和语料。'], ['阅读引用', '展开回答中的引用，查看对应来源；能读取到原文时，再核对结论、方法与上下文。'], ['整理参考文献', '要求核对 DOI 或导出 RIS；检查返回的匹配与未匹配项，避免把相似题名当成同一篇文章。']],
        example: '查找近五年柑橘抗逆研究，核对引用的 DOI，并把确认的文献导出为 RIS。', note: '检索命中不等于已读全文；来源不可访问或信息不足时，引用核查也可能不完整。'}
    ]},
    {id: 'files', nav: '文件与资料库', title: '把材料带进当前研究', lead: '上传资料和生成成果都可以在账号资料库中查看。',
      flow: ['上传文件', '预览 / 引用', '带着材料提问'],
      steps: [['上传与预览', '用输入框附件按钮或拖入文件，支持 PDF、Word、表格及图片；是否读全以解析结果为准。'], ['引用已有材料', '打开左侧「资料库」，找到文件并引用到提问中；账号文件可跨会话使用。'], ['明确文件与范围', '多份文件一起使用时，写清文件名、工作表、字段或页码。不要只说“这个数据”而省略对应关系。']],
      example: '比较这两份表格中的处理组，使用“测量结果”工作表，以样本编号对齐。', note: '预览用于确认文件内容；扫描件、复杂排版和特殊表格的解析可能不完整。'},
    {id: 'data', nav: '数据审查与统计', title: '先核查数据，再解释统计结果', lead: '带上原始记录与实验设计，分别核对疑点和分析方法。', sections: [
      {id: 'audit', title: '数据完整性审查',
        flow: ['表格 / 图片', '检查与定位', '人工复核'],
        steps: [['表格检查', '可检查缺失、重复、异常、报告精度及多表数值复用；说明主键、重复测量、允许缺失项和换算关系。'], ['图片检查', '两张及以上图片可检查限定范围内的精确重复、翻转和裁剪关系；不能据此保证发现所有图像处理。'], ['阅读覆盖范围', '查看工作表、行列或图片区域的位置、触发规则和未完成项，再用原始记录解释疑点。']],
        example: '检查这些表格的重复区域，列出双方位置；区分共享对照、单位换算和需要复核的数值复用。', note: '异常线索不是造假结论；未发现异常也不证明数据真实。检查不会替你修改原始数据。'},
      {id: 'statistics', title: '统计分析',
        flow: ['字段与分组', '方法与假设', '结果及限制'],
        steps: [['说明数据结构', '提供指标列、分组列、样本单位及缺失值含义；配对实验必须给出配对键，不能按行序猜配对。'], ['提出分析目标', '可做描述统计、独立或配对 t 检验、单因素方差分析、部分非参数检验、相关与卡方分析。'], ['一起看方法与结果', '核对样本量、适用条件、效应量和多重比较处理；条件不满足时，按报告提示调整分析。']],
        example: '以处理组分组分析果实重量，先给描述统计，再检查组间比较的适用条件，说明效应量。', note: '统计显著不等于生物学意义；复杂实验设计需要额外的方法确认。'}
    ]},
    {id: 'figures', nav: '绘图与编辑', title: '创建、编辑并交付图件', lead: '从脚本出图开始，依次选择对象、调整文字和系列，最后保存下载。', sections: [
      {id: 'create', title: '脚本与图件创建',
        flow: ['数据与作图要求', '生成图件', '打开工作台'],
        steps: [['给出作图信息', '在问答中说明数据文件、图型、分组、坐标单位、误差线含义和用途，系统据此生成绘图脚本与图件，无需开启额外绘图开关。'], ['检查生成状态', '自定义绘图依赖可用的隔离运行环境。执行失败时先看错误；有真实图件卡片后再打开工作台。'], ['进入编辑', '从回答中的图件卡片或左侧图表入口进入。工作台也可从资料库导入表格创建图件。']],
        example: '使用附件数据绘制分组散点图，保留原始点，注明单位和组名，生成可编辑图件。', note: '可编辑范围以图件实际识别出的对象和属性为准，不保证任意绘图脚本中的所有元素都能编辑。'},
      {id: 'objects', title: '分组与对象选择', diagram: 'objects',
        steps: [['按类别展开', '左侧分为文字、数据系列、图例和坐标轴；刻度可按 X / Y 轴展开，系列下可继续找到点或柱。'], ['批量或单项选择', '选中分组时，只编辑组内共同支持的样式；要改标题内容、系列名称或某一个点，请选中具体对象。'], ['定位与恢复', '可从画板点选或在左侧搜索、筛选。已隐藏对象仍可在列表中选中并恢复显示。']],
        note: '批量修改作为一次本地操作撤销。隐藏数据点不删除原数据，但交付图件时应说明筛选依据。'},
      {id: 'text', title: '文字与字体', diagram: 'text',
        steps: [['修改内容', '选中单个标题、X / Y 轴标签或注释，在内容字段中改写；输入按纯文本处理，最多 2,000 字符。'], ['统一字体', '设置可用的字体、字号、字重与颜色。字体列表来自实际渲染环境，分组可批量应用共同的文字样式。'], ['调整位置', '可移动的文字支持拖动或方向键微调，Shift + 方向键加大步长。刻度内容不作为任意文本改写。']],
        note: '只有面板列出的属性可编辑；坐标比例、多面板布局和任意绘图元素不属于通用编辑能力。'},
      {id: 'series', title: '曲线、散点与图例', diagram: 'series',
        steps: [['修改系列', '单独选中系列可修改名称；支持的曲线可调整颜色、线宽、线型及标记样式。'], ['修改散点', '按提供的控件调整系列或单点的形状、大小、填充色、边缘色和透明度；数据坐标保持来源值。'], ['检查图例同步', '受支持的系列名称与样式会同步到图例。若重复名称导致对应不明确，按错误提示处理，不要假定同步已成功。']],
        note: '属性取决于所选对象；不支持的选项不会提供。误差长度等数据含义应回到数据与作图要求中修改。'},
      {id: 'save', title: '预览、保存与下载',
        flow: ['当前编辑预览', '保存修改', '下载已保存版本'],
        steps: [['看预览', '修改属性或拖动后自动重绘；失败时保留编辑并提示错误，画布可能仍显示上次成功预览。'], ['撤销与保存', '顶部「撤销 / 重做」处理本页编辑。点击「保存修改」替换最近保存；预览不会积累历史版本。'], ['下载与查看', '「导出」下载已保存的 SVG、PNG 或 PDF，「已保存」查看最近保存记录。离开前先保存需要保留的修改。']],
        note: '缩放、平移、适应窗口、网格和标尺仅辅助查看，不进入导出。其他页面修改同一图件后，请重新加载再编辑。'}
    ]},
    {id: 'results', nav: '研究成果与交付', title: '把确认的结果整理成文件', lead: '说清内容、结构和格式，完成后从成果卡片下载。',
      flow: ['确定内容与格式', '生成成果', '核对并下载'],
      steps: [['描述交付要求', '可请求把研究内容整理为 Word，或将已确认的参考文献导出为 RIS；说明章节、表格、引用和图件要求。'], ['核对生成结果', '查看执行状态与实际文件卡片。文本中说“已生成”不能替代可下载的文件，失败时需要按提示重试。'], ['在资料库查找', '上传资料与生成成果分区展示；下载后核对正文、引用、表格和图件，确保与当前确认的结果一致。']],
      example: '把已确认的结果整理成 Word，包含方法、结果、限制与参考文献；保留来源，不补造缺失信息。', note: '文件内容依赖已提供的材料与完成的工具结果；重要交付仍需人工审阅。'},
    {id: 'account', nav: '会话与账号', title: '掌握进度，也掌握自己的资料', lead: '历史会话、账号偏好与资料库各自保留对应内容。',
      flow: ['查看执行进度', '停止 / 恢复查看', '管理账号资料'],
      steps: [['进度与停止', '展开执行过程查看步骤，点击停止可请求取消。连接中断不代表任务结束；注意服务端未确认终止等提示。'], ['历史与偏好', '从会话列表继续已有研究。账户菜单可设置偏好；自动记忆已停用，项目材料请使用科研笔记保存。'], ['资料与隐私', '账号数据彼此隔离，上传文件不默认进入共享语料库；文献共享需单独授权。问题、回答和工具摘要供管理员排障，保留一年。']],
      note: '上传文件默认 30 天未使用后清理；公开访问的连接经过 Cloudflare 转发。重要成果请自行下载保留。'}
  ];
  // Previously linked topics still open the chapter containing their instructions.
  var aliases = {start: 'research', sources: 'research', audit: 'data', statistics: 'data', edit: 'figures', objects: 'figures', text: 'figures', series: 'figures', save: 'figures', keys: 'figures'};
  var dialog, current = 0, opener = null, onClose = null;
  var escape = function (value) { return String(value).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); };
  function diagram(page) {
    // Original interface diagrams, not measured data or fabricated research results.
    var svg = '<svg viewBox="0 0 640 150" role="img" aria-label="' + escape(page.diagram ? '选择对象后在右侧修改对应属性的操作示意' : page.flow.join('，然后')) + '" xmlns="http://www.w3.org/2000/svg">';
    if (page.diagram) {
      var rows = page.diagram === 'objects' ? ['文字', '数据系列', '  系列 A / 数据点', '图例 / 坐标轴'] : page.diagram === 'text' ? ['文字', '  标题', '  X / Y 轴标签', '  注释'] : ['数据系列', '  系列 A', '    单个数据点', '图例'];
      var fields = page.diagram === 'objects' ? ['选择分组', '共同支持的样式', '或展开选择子对象'] : page.diagram === 'text' ? ['内容：果实重量', '字体 / 字号 / 字重', '文字颜色 / 位置'] : ['系列名称 / 标记形状', '大小 / 填充 / 边缘', '透明度 / 图例同步'];
      svg += '<rect class="cg-svg-panel" x="1" y="1" width="638" height="148" rx="8"/><path class="cg-svg-rule" d="M190 1v148M395 1v148"/>';
      rows.forEach(function (text, i) { svg += (i === 1 ? '<rect class="cg-svg-active" x="12" y="42" width="166" height="26" rx="4"/>' : '') + '<text x="23" y="' + (30 + i * 29) + '">' + escape(text) + '</text>'; });
      svg += '<rect class="cg-svg-paper" x="220" y="25" width="145" height="100" rx="3"/><rect class="cg-svg-selection" x="236" y="42" width="113" height="29" rx="2"/><text x="292" y="62" text-anchor="middle">选中的对象</text><path class="cg-svg-rule" d="M240 88h105M240 103h73"/>';
      fields.forEach(function (text, i) { svg += '<text x="414" y="' + (42 + i * 37) + '">' + escape(text) + '</text>'; });
    } else {
      page.flow.forEach(function (label, i) {
        var x = 7 + i * 220;
        svg += '<rect class="cg-svg-panel" x="' + x + '" y="32" width="186" height="86" rx="8"/><circle class="cg-svg-active" cx="' + (x + 25) + '" cy="53" r="10"/><text class="cg-svg-small" x="' + (x + 25) + '" y="57" text-anchor="middle">' + (i + 1) + '</text><text x="' + (x + 93) + '" y="87" text-anchor="middle">' + escape(label) + '</text>';
        if (i < 2) svg += '<path class="cg-svg-arrow" d="M' + (x + 194) + ' 75h18m-6-6 6 6-6 6"/>';
      });
    }
    return svg + '</svg>';
  }
  function content(topic, heading) {
    return '<figure class="cg-illustration">' + diagram(topic) + '<figcaption>操作示意</figcaption></figure><ol class="cg-steps">' + topic.steps.map(function (step) { return '<li><' + heading + '>' + escape(step[0]) + '</' + heading + '><p>' + escape(step[1]) + '</p></li>'; }).join('') + '</ol>' + (topic.example ? '<div class="cg-example"><span>可以这样提问</span><p>“' + escape(topic.example) + '”</p></div>' : '') + '<p class="cg-note">' + escape(topic.note) + '</p>';
  }
  function mount() {
    if (dialog) return;
    dialog = document.createElement('dialog');
    dialog.id = 'citrusGuideDialog';
    dialog.className = 'cg-dialog';
    dialog.setAttribute('aria-labelledby', 'citrusGuideTitle');
    dialog.innerHTML = '<header class="cg-header"><div><span class="cg-brand">CitrusSchalor</span><h1 id="citrusGuideTitle">使用指南</h1></div><button type="button" class="cg-close" data-guide-close aria-label="关闭使用指南">×</button></header>' +
      '<div class="cg-layout"><nav class="cg-nav" aria-label="按研究任务浏览使用指南">' + pages.map(function (page, index) { return '<button type="button" data-guide-page="' + index + '" aria-controls="citrusGuidePage">' + escape(page.nav) + '</button>'; }).join('') +
      '</nav><article id="citrusGuidePage" class="cg-page" tabindex="-1" aria-labelledby="citrusGuidePageTitle"></article></div>' +
      '<footer class="cg-footer"><button type="button" data-guide-prev>上一章</button><span id="citrusGuideIndex" aria-live="polite" aria-atomic="true"></span><button type="button" data-guide-next>下一章</button></footer>';
    document.body.appendChild(dialog);
    dialog.addEventListener('click', function (event) {
      var target = event.target.closest('button');
      if (!target || !dialog.contains(target)) return;
      if (target.hasAttribute('data-guide-close')) close();
      else if (target.hasAttribute('data-guide-page')) go(Number(target.getAttribute('data-guide-page')), true);
      else if (target.hasAttribute('data-guide-prev')) go(current - 1, true);
      else if (target.hasAttribute('data-guide-next')) { if (current === pages.length - 1) close(); else go(current + 1, true); }
    });
    dialog.addEventListener('cancel', function (event) { event.preventDefault(); close(); });
    dialog.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      else if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault(); event.stopPropagation(); go(current + (event.key === 'ArrowRight' ? 1 : -1), true);
      } else if (event.key === 'Tab') {
        var buttons = Array.from(dialog.querySelectorAll('button')).filter(function (button) { return !button.disabled && !button.hidden; });
        var first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === document.getElementById('citrusGuidePage'))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
  }
  function go(index, focus) {
    current = Math.max(0, Math.min(pages.length - 1, Number(index) || 0));
    var page = pages[current], article = document.getElementById('citrusGuidePage');
    article.dataset.page = page.id;
    article.innerHTML = '<h2 id="citrusGuidePageTitle">' + escape(page.title) + '</h2><p class="cg-lead">' + escape(page.lead) + '</p>' + (page.sections ? page.sections.map(function (section) {
      return '<section class="cg-section" data-guide-section="' + escape(section.id) + '" aria-labelledby="cg-section-' + escape(section.id) + '"><h3 id="cg-section-' + escape(section.id) + '">' + escape(section.title) + '</h3>' + content(section, 'h4') + '</section>';
    }).join('') : content(page, 'h3'));
    dialog.querySelectorAll('[data-guide-page]').forEach(function (button, index) {
      button.setAttribute('aria-current', index === current ? 'page' : 'false');
      if (index === current && dialog.open && button.scrollIntoView) button.scrollIntoView({block: 'nearest', inline: 'nearest'});
    });
    dialog.querySelector('[data-guide-prev]').disabled = current === 0;
    dialog.querySelector('[data-guide-next]').textContent = current === pages.length - 1 ? '完成阅读' : '下一章';
    document.getElementById('citrusGuideIndex').textContent = '第 ' + (current + 1) + ' / ' + pages.length + ' 章 · ' + page.nav;
    article.scrollTop = 0;
    if (focus) article.focus();
  }
  function open(page, options) {
    mount();
    options = options || {};
    if (!dialog.open) opener = document.activeElement;
    onClose = typeof options.onClose === 'function' ? options.onClose : null;
    page = Object.prototype.hasOwnProperty.call(aliases, page) ? aliases[page] : page;
    var index = typeof page === 'number' ? page - 1 : pages.findIndex(function (item) { return item.id === page; });
    go(index < 0 ? 0 : index, false);
    if (!dialog.open) dialog.showModal();
    dialog.querySelector('[data-guide-close]').focus();
    var selected = dialog.querySelector('[aria-current="page"]');
    if (selected && selected.scrollIntoView) selected.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
  function close(notify) {
    if (!dialog || !dialog.open) return;
    var callback = onClose;
    onClose = null;
    dialog.close();
    if (opener && opener.isConnected && typeof opener.focus === 'function') {
      opener.focus();
      // Resizing can hide the sidebar containing the original guide button.
      var fallbackSelector = opener.getAttribute('data-guide-focus-fallback');
      if (document.activeElement !== opener && fallbackSelector) {
        var fallback = document.querySelector(fallbackSelector);
        if (fallback && !fallback.disabled) fallback.focus();
      }
    }
    opener = null;
    if (notify !== false && callback) callback();
  }
  window.CitrusGuide = {open: open, close: close, pageCount: pages.length, version: 3};
  document.addEventListener('click', function (event) {
    var target = event.target.closest('[data-citrus-guide]');
    if (!target) return;
    event.preventDefault();
    open(target.getAttribute('data-citrus-guide') || 'research');
  });
  if (document.body && document.body.classList.contains('guide-page')) {
    try { var theme = localStorage.getItem('citrus_theme'); if (['light', 'cartoon', 'dark'].includes(theme)) document.documentElement.dataset.theme = theme; } catch (_) {}
    open(location.hash.slice(1) || 'research');
  }
}());
