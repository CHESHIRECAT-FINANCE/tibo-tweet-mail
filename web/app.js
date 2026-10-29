'use strict';
const M = window.RelayModel;
let serial = 1;
let subscriptions = [M.createSource('rss', 'source-1')];
let generatedConfig = null;
let generatedKey = '';
let batchSize = 20;
const $ = id => document.getElementById(id);
function el(tag, props = {}, text = '') { const node = document.createElement(tag); Object.assign(node, props); if (text) node.textContent = text; return node; }
function status(message, error = false) { $('status').textContent = message; $('status').className = error ? 'error' : ''; }
function invalidate() { generatedConfig = null; $('generated').hidden = true; $('config-output').value = ''; status(''); }
function field(source, key, label, options = {}) {
  const wrap = el('label', {className: options.full ? 'full' : ''}, label);
  const input = el('input', {type: options.type || 'text', value: source[key] ?? '', id: `${source.id}-${key}`, autocomplete: 'off'});
  if (options.placeholder) input.placeholder = options.placeholder;
  if (options.type === 'number') { input.min = '0'; input.max = '23'; }
  wrap.append(input);
  input.addEventListener('input', () => { source[key] = options.type === 'number' ? Number(input.value) : input.value; invalidate(); preview(); });
  return wrap;
}
function checkbox(source, key, label) {
  const wrap = el('label', {className: 'check-label'});
  const input = el('input', {type: 'checkbox', checked: Boolean(source[key])});
  wrap.append(input, el('span', {}, label));
  input.addEventListener('change', () => { source[key] = input.checked; invalidate(); preview(); });
  return wrap;
}
function render() {
  const list = $('source-list'); list.replaceChildren();
  subscriptions.forEach(source => {
    const article = el('article', {className: 'source'});
    const header = el('div', {className: 'source-header'});
    const remove = el('button', {type: 'button', className: 'remove'}, '移除');
    remove.setAttribute('aria-label', `移除 ${source.name}`);
    remove.addEventListener('click', () => { subscriptions = subscriptions.filter(x => x !== source); invalidate(); render(); $('add-source').focus(); });
    header.append(el('span', {className: 'source-label'}, M.kinds[source.type].label), remove);
    article.append(header);
    const fields = el('div', {className: 'source-fields'});
    fields.append(field(source, 'name', '订阅名称', {full: true}));
    if (['rss', 'webpage'].includes(source.type)) fields.append(field(source, 'url', source.type === 'rss' ? 'RSS / Atom 订阅地址' : '公开网页地址', {full: true, type: 'url'}));
    if (source.type === 'github') fields.append(field(source, 'repository', 'GitHub 仓库', {full: true, placeholder: 'owner/repository'}));
    if (source.type === 'x') fields.append(field(source, 'handle', 'X 用户名（不含 @）', {full: true}));
    if (source.type === 'market') fields.append(field(source, 'hour', '每天几点后发送', {type: 'number'}));
    article.append(fields);
    const hints = {rss: '填写网站提供的 RSS 或 Atom 链接，不是普通首页地址。', webpage: '比较公开网页中的文字，忽略脚本和样式。暂不支持登录页面或动态加载内容。', github: '跟踪正式 Release；普通提交、Issue 和仅创建的标签不包含在内。', x: '使用公开第三方时间线，服务偶尔可能不可用；每次获取最新 20 条。', market: '人民币汇率、WTI / 布伦特原油及黄金期货。每天一封，报价可能延迟。'};
    article.append(el('p', {className: 'source-hint'}, hints[source.type]));
    const details = el('details', {className: 'filters'}); details.append(el('summary', {}, '筛选条件与首次发送'));
    const grid = el('div', {className: 'filters-grid'});
    grid.append(checkbox(source, 'enabled', '启用此订阅（关闭后暂停获取与发送）'));
    for (const [key, label] of [['keywords', '只关注这些关键词'], ['exclude', '排除这些关键词']]) {
      const labelNode = el('label', {}, label); const input = el('input', {value: source[key].join('，'), placeholder: key === 'keywords' ? '留空表示全部；多个词用逗号分隔' : '例如：广告，推广'});
      input.addEventListener('input', () => { source[key] = M.words(input.value); invalidate(); preview(); }); labelNode.append(input); grid.append(labelNode);
    }
    const matchLabel = el('label', {}, '关键词匹配'); const match = el('select');
    for (const [value, label] of [['any', '任一关键词匹配即可'], ['all', '必须包含所有关键词']]) match.append(el('option', {value, selected: source.match === value}, label));
    match.addEventListener('change', () => { source.match = match.value; invalidate(); preview(); }); matchLabel.append(match); grid.append(matchLabel);
    if (source.type !== 'market') {
      const initialLabel = el('label', {}, '首次启用'); const initial = el('select');
      for (const [value, label] of [['skip', '只记录现有内容，之后有更新再发（推荐）'], ['send', '也发送当前可获取的匹配内容']]) initial.append(el('option', {value, selected: source.initial === value}, label));
      initial.addEventListener('change', () => { source.initial = initial.value; invalidate(); preview(); }); initialLabel.append(initial); grid.append(initialLabel);
    }
    if (source.type === 'github') grid.append(checkbox(source, 'prereleases', '同时订阅预发布版本'));
    if (source.type === 'x') {
      grid.append(checkbox(source, 'reset_only', '只接收用量重置通知'));
      grid.append(checkbox(source, 'include_reposts', '包含转发'));
      grid.append(checkbox(source, 'include_replies', '包含回复'));
      grid.append(checkbox(source, 'include_quotes', '包含引用帖子'));
    }
    details.append(grid); article.append(details); list.append(article);
  });
  $('add-source').disabled = subscriptions.length >= 20;
  preview();
}
function preview() {
  const list = $('preview-list'); list.replaceChildren();
  if (!subscriptions.length) list.append(el('p', {className: 'hint'}, '添加一个来源，开始你的订阅计划。'));
  subscriptions.forEach(source => {
    const row = el('div', {className: 'preview-item'});
    row.append(el('div', {className: 'preview-type'}, M.kinds[source.type].label), el('strong', {}, source.name || '未命名订阅'));
    if (source.enabled === false) row.append(el('small', {}, '已暂停，不会获取或发送'));
    row.append(el('small', {}, source.keywords.length ? `${source.match === 'all' ? '全部匹配' : '任一匹配'}：${source.keywords.join('、')}` : '接收全部匹配的新内容'));
    if (source.exclude.length) row.append(el('small', {}, `排除：${source.exclude.join('、')}`));
    row.append(el('small', {}, source.type === 'market' ? `每天 ${source.hour}:00 后，一天一封` : source.initial === 'send' ? '首次也发送当前内容' : '首次记录基线，之后更新再发'));
    list.append(row);
  });
}
$('import-config').addEventListener('click', () => $('import-file').click());
$('import-file').addEventListener('change', async () => {
  const file = $('import-file').files[0];
  if (!file) return;
  try {
    if (file.size > 100000) throw new Error('配置文件过大，请选择订阅配置 JSON');
    const cfg = JSON.parse(await file.text());
    if (cfg.version !== 1 || !Array.isArray(cfg.sources) || !cfg.email || ![...$('timezone').options].some(o => o.value === cfg.timezone)) throw new Error('不支持此配置版本或时区，请使用命令行编辑');
    const seen = new Set();
    const imported = cfg.sources.map(source => {
      if (!M.kinds[source.type] || !/^[a-zA-Z0-9_-]{1,48}$/.test(source.id) || seen.has(source.id)) throw new Error('配置中的信息源类型或 ID 无效');
      seen.add(source.id);
      return {...M.createSource(source.type, source.id), ...source};
    });
    M.build(imported, cfg.email, cfg.timezone, cfg.batch_size ?? 20);
    batchSize = cfg.batch_size ?? 20;
    subscriptions = imported;
    while (seen.has(`source-${serial + 1}`)) serial++;
    for (const [id, key] of [['smtp-host', 'smtp_host'], ['smtp-port', 'smtp_port'], ['smtp-user', 'smtp_user'], ['smtp-password', 'smtp_password'], ['mail-to', 'to']]) $(id).value = cfg.email[key];
    $('timezone').value = cfg.timezone;
    invalidate(); render(); status('已导入，订阅 ID 已保留。更新 GitHub 配置时请继续使用原加密密钥。');
  } catch (_) { status('无法导入：请选择完整、有效的 InfoRelay 私密配置文件。', true); }
  $('import-file').value = '';
});
$('add-source').addEventListener('click', () => {
  do { serial++; } while (subscriptions.some(source => source.id === `source-${serial}`));
  subscriptions.push(M.createSource($('new-kind').value, `source-${serial}`));
  invalidate(); render(); $(`source-${serial}-name`).focus();
});
for (const id of ['smtp-user', 'smtp-host', 'smtp-password', 'smtp-port', 'mail-to', 'timezone']) $(id).addEventListener('input', invalidate);
$('generate').addEventListener('click', () => {
  try {
    generatedConfig = M.build(subscriptions, {smtp_host: $('smtp-host').value.trim(), smtp_port: Number($('smtp-port').value), smtp_user: $('smtp-user').value.trim(), smtp_password: $('smtp-password').value, to: $('mail-to').value.trim()}, $('timezone').value, batchSize);
    if (!generatedKey) { const bytes = crypto.getRandomValues(new Uint8Array(32)); generatedKey = btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_'); }
    $('config-output').value = JSON.stringify(generatedConfig, null, 2); $('state-key').value = generatedKey; $('generated').hidden = false;
    status('配置已生成。按下方步骤启用；本页面没有发送邮件。');
  } catch (error) { generatedConfig = null; $('generated').hidden = true; status(error.message, true); }
});
async function copy(id) {
  try { await navigator.clipboard.writeText($(id).value); status('已复制，请粘贴到对应的 GitHub Secret。'); }
  catch (_) { $(id).focus(); $(id).select(); status('浏览器未允许复制，已选中文本，请手动复制。'); }
}
$('copy-config').addEventListener('click', () => copy('config-output'));
$('copy-key').addEventListener('click', () => copy('state-key'));
$('download').addEventListener('click', () => {
  if (!generatedConfig) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(generatedConfig, null, 2) + '\n'], {type: 'application/json'}));
  const link = el('a', {href: url, download: 'subscriptions.private.json'}); document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
  status('配置文件已下载。请妥善保管其中的邮箱授权码。');
});
render();
