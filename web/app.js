'use strict';
const M = window.RelayModel, I = window.RelayI18n;
const $ = id => document.getElementById(id), t = (zh, en) => I.text(zh, en);
const kindNames = {rss: ['RSS / Atom', 'RSS / Atom'], webpage: ['网页更新', 'Website changes'], github: ['GitHub 发布', 'GitHub releases'], x: ['X 动态', 'X posts'], market: ['每日行情', 'Daily markets']};
const providers = {qq: ['smtp.qq.com', 465, 'https://help.mail.qq.com/detail/106/985'], '163': ['smtp.163.com', 465, 'https://help.mail.163.com/'], gmail: ['smtp.gmail.com', 587, 'https://support.google.com/accounts/answer/185833'], custom: ['', 465, '']};
let subscriptions = [M.createSource('rss', 'source-1')], serial = 1, batchSize = 20;
if (I.language === 'en') subscriptions[0].name = 'Python blog';
let generatedConfig = null, generatedKey = '', importedCloud = false, dirty = true;
let session = null, savedIdentity = null, hasSavedPassword = false, runtime = null, step = 1;
function el(tag, props = {}, text = '') { const node = document.createElement(tag); Object.assign(node, props); if (text) node.textContent = text; return node; }
function status(message, error = false) { $('status').textContent = message; $('status').className = error ? 'error' : ''; }
function friendly(error) {
  if (I.language === 'zh' || !/[\u3400-\u9fff]/.test(error.message)) return error.message;
  const map = {'请添加 1 到 20 个信息源': 'Add 1–20 sources.', '请填写完整的发件和收件邮箱': 'Enter valid sender and recipient email addresses.', '请填写邮箱的 SMTP 授权码或应用专用密码': 'Enter your email app password or authorization code.', '请填写 SMTP 服务器名称': 'Enter a valid SMTP server hostname.', '请输入完整的网页或 RSS 地址': 'Enter a complete public website or feed URL.', '请给每个订阅填写 1 到 80 字的名称': 'Give every source a name (1–80 characters).', 'GitHub 仓库格式为 owner/repository': 'Use owner/repository for GitHub.', '请输入不带 @ 的 X 用户名': 'Enter an X username without @.', '时区无效': 'Choose a valid time zone.'};
  return map[error.message] || 'Check the source address, email settings, keywords and selected options.';
}
function invalidate() { dirty = true; generatedConfig = null; $('generated').hidden = true; $('config-output').value = ''; $('allow-send').checked = false; status(''); }
function go(next) {
  step = next;
  for (const panel of document.querySelectorAll('[data-panel]')) panel.hidden = Number(panel.dataset.panel) !== step;
  for (const button of document.querySelectorAll('[data-step]')) {
    if (Number(button.dataset.step) === step) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
  }
  status(''); document.querySelector(`[data-panel="${step}"] h2`).focus();
}
function field(source, key, label, numeric = false) {
  const wrap = el('label', {}, label), input = el('input', {type: numeric ? 'number' : 'text', value: source[key] ?? '', id: `${source.id}-${key}`, autocomplete: 'off'});
  if (numeric) { input.min = '0'; input.max = '23'; }
  wrap.append(input); input.addEventListener('input', () => { source[key] = numeric ? Number(input.value) : input.value; invalidate(); preview(); }); return wrap;
}
function checkbox(source, key, label) {
  const wrap = el('label', {className: 'check-label'}), input = el('input', {type: 'checkbox', checked: Boolean(source[key])});
  wrap.append(input, el('span', {}, label)); input.addEventListener('change', () => { source[key] = input.checked; invalidate(); preview(); }); return wrap;
}
function selectField(source, key, label, options) {
  const wrap = el('label', {}, label), input = el('select');
  for (const [value, labelText] of options) input.append(el('option', {value, selected: source[key] === value}, labelText));
  input.addEventListener('change', () => { source[key] = input.value; invalidate(); preview(); }); wrap.append(input); return wrap;
}
function renderSources() {
  $('source-list').replaceChildren();
  for (const source of subscriptions) {
    const article = el('article', {className: 'source'}), header = el('div', {className: 'source-header'}), remove = el('button', {type: 'button', className: 'remove'}, t('移除', 'Remove'));
    remove.setAttribute('aria-label', t('移除 ', 'Remove ') + source.name);
    remove.addEventListener('click', () => { subscriptions = subscriptions.filter(x => x !== source); invalidate(); renderSources(); $('add-source').focus(); });
    header.append(el('span', {className: 'source-label'}, t(...kindNames[source.type])), remove); article.append(header);
    const fields = el('div', {className: 'source-fields'}); fields.append(field(source, 'name', t('订阅名称', 'Subscription name')));
    if (['rss', 'webpage'].includes(source.type)) fields.append(field(source, 'url', source.type === 'rss' ? t('RSS / Atom 订阅链接', 'RSS / Atom feed URL') : t('公开网页链接', 'Public website URL')));
    if (source.type === 'github') fields.append(field(source, 'repository', t('仓库（owner/repository）', 'Repository (owner/repository)')));
    if (source.type === 'x') fields.append(field(source, 'handle', t('X 用户名（不带 @）', 'X username (without @)')));
    if (source.type === 'market') fields.append(field(source, 'hour', t('每天几点后发送', 'Send after this hour each day'), true));
    article.append(fields);
    const hints = {rss: ['需要 RSS / Atom 链接，不是网站首页。', 'Use a feed URL, not the website home page.'], webpage: ['比较公开页面文字，不执行 JavaScript，不支持登录页面。', 'Compares public page text. No JavaScript execution or logged-in pages.'], github: ['跟踪 Release，不包含提交、Issue 或仅创建的标签。', 'Tracks Releases, not commits, issues or bare tags.'], x: ['依赖 FxTwitter 公开时间线，每次最多 20 条。', 'Uses the public FxTwitter timeline, up to 20 recent posts.'], market: ['每日一次，人民币汇率、原油与黄金期货。报价可能延迟。', 'Daily CNY exchange rates, oil and gold futures. Quotes may be delayed.']};
    article.append(el('p', {className: 'source-hint'}, t(...hints[source.type])));
    const details = el('details', {className: 'filters'}); details.append(el('summary', {}, t('筛选条件与更多设置', 'Filters and more settings')));
    const grid = el('div', {className: 'filters-grid'}); grid.append(checkbox(source, 'enabled', t('启用此订阅', 'Enable this subscription')));
    for (const [key, zh, en] of [['keywords', '包含关键词（留空接收全部）', 'Include keywords (blank matches all)'], ['exclude', '排除关键词', 'Exclude keywords']]) {
      const label = el('label', {}, t(zh, en)), input = el('input', {value: source[key].join(', '), placeholder: t('多个词用逗号分隔', 'Separate words with commas')});
      input.addEventListener('input', () => { source[key] = M.words(input.value); invalidate(); preview(); }); label.append(input); grid.append(label);
    }
    grid.append(selectField(source, 'match', t('匹配方式', 'Match mode'), [['any', t('包含任一个关键词', 'Match any keyword')], ['all', t('包含所有关键词', 'Match all keywords')]]));
    if (source.type !== 'market') grid.append(selectField(source, 'initial', t('首次检查', 'First check'), [['skip', t('只记录，不发送历史内容（推荐）', 'Remember existing items, send future updates')], ['send', t('也发送当前匹配内容', 'Also send currently matching items')]]));
    if (source.type === 'github') grid.append(checkbox(source, 'prereleases', t('包含预发布版本', 'Include prereleases')));
    if (source.type === 'x') for (const [key, zh, en] of [['reset_only', '只收用量重置通知', 'Only usage reset notices'], ['include_reposts', '包含转发', 'Include reposts'], ['include_replies', '包含回复', 'Include replies'], ['include_quotes', '包含引用', 'Include quotes']]) grid.append(checkbox(source, key, t(zh, en)));
    details.append(grid); article.append(details); $('source-list').append(article);
  }
  $('add-source').disabled = subscriptions.length >= 20; preview();
}
function preview() {
  $('preview-list').replaceChildren();
  if (!subscriptions.length) $('preview-list').append(el('p', {className: 'hint'}, t('添加一个来源，开始订阅。', 'Add a source to begin.')));
  for (const source of subscriptions) {
    const row = el('div', {className: 'preview-item'});
    row.append(el('div', {className: 'preview-type'}, t(...kindNames[source.type])), el('strong', {}, source.name || t('未命名订阅', 'Untitled subscription')));
    row.append(el('small', {}, source.enabled === false ? t('已暂停', 'Paused') : source.keywords.length ? source.keywords.join(' / ') : t('全部新内容', 'All new items')));
    row.append(el('small', {}, source.type === 'market' ? `${source.hour}:00 · ${t('每日一次', 'once daily')}` : source.initial === 'send' ? t('首次也发当前内容', 'Includes current items on first check') : t('首次记录，以后更新再发', 'First check sets a baseline'))); $('preview-list').append(row);
  }
}
function email() { return {smtp_host: $('smtp-host').value.trim(), smtp_port: Number($('smtp-port').value), smtp_user: $('smtp-user').value.trim(), smtp_password: $('smtp-password').value, to: $('mail-to').value.trim()}; }
function canKeepPassword() { const e = email(); return Boolean(session && hasSavedPassword && savedIdentity && ['smtp_user', 'smtp_host', 'smtp_port'].every(k => e[k] === savedIdentity[k])); }
function build(exporting = false) { return M.build(subscriptions, email(), $('timezone').value, batchSize, !exporting && canKeepPassword()); }
function addSource(kind) {
  if (subscriptions.length >= 20) return;
  do { serial++; } while (subscriptions.some(x => x.id === `source-${serial}`));
  const source = M.createSource(kind, `source-${serial}`);
  if (I.language === 'en') source.name = {rss: 'Python blog', webpage: 'Python downloads', github: 'GitHub CLI', x: 'Tibo on X', market: 'FX, oil and gold'}[kind];
  subscriptions.push(source); invalidate(); renderSources(); $(`${source.id}-name`).focus();
}
function applyConfig(cfg, storedPassword = false, imported = false) {
  if (cfg.version !== 1 || Object.keys(cfg).some(k => !['version', 'timezone', 'batch_size', 'email', 'sources', 'delivery'].includes(k)) || !cfg.email || !Array.isArray(cfg.sources) || (cfg.delivery && cfg.delivery.type !== 'email') || cfg.sources.some(s => !M.kinds[s.type] || s.processors?.length || Object.keys(s.options || {}).length)) throw new Error(t('高级插件配置请使用命令行编辑，向导不会覆盖它。', 'Use the CLI for advanced plugin settings; the wizard will not overwrite them.'));
  const incoming = cfg.sources.map(s => ({...M.createSource(s.type, s.id), ...s}));
  M.build(incoming, cfg.email, cfg.timezone, cfg.batch_size ?? 20, storedPassword);
  subscriptions = incoming; batchSize = cfg.batch_size ?? 20;
  for (const [id, key] of [['smtp-host', 'smtp_host'], ['smtp-port', 'smtp_port'], ['smtp-user', 'smtp_user'], ['smtp-password', 'smtp_password'], ['mail-to', 'to']]) $(id).value = cfg.email[key] ?? '';
  if (![...$('timezone').options].some(o => o.value === cfg.timezone)) $('timezone').append(el('option', {value: cfg.timezone}, cfg.timezone));
  $('timezone').value = cfg.timezone; $('provider').value = Object.keys(providers).find(k => providers[k][0] === cfg.email.smtp_host) || 'custom';
  savedIdentity = {...cfg.email}; hasSavedPassword = storedPassword; importedCloud = imported; generatedKey = ''; invalidate(); renderSources(); updateProvider(false);
}
function updateProvider(fill = true) {
  const provider = providers[$('provider').value];
  if (fill && provider[0]) { $('smtp-host').value = provider[0]; $('smtp-port').value = provider[1]; invalidate(); }
  $('provider-help').href = provider[2] || 'https://github.com/CHESHIRECAT-FINANCE/tibo-tweet-mail/blob/main/docs/en/guide.md';
  $('password-hint').textContent = canKeepPassword() ? t('已在本机保存授权码。留空沿用；导出云端配置时需重新填写。', 'An app password is saved locally. Leave blank to keep it; re-enter it to export cloud settings.') : t('使用邮箱服务商提供的授权码，不是网页登录密码。某些账户需先开启两步验证。', 'Use an app password from your provider, not your regular login password. Some accounts require two-step verification.');
}
async function api(path, data) {
  const response = await fetch('/api/' + path, {method: data === undefined ? 'GET' : 'POST', cache: 'no-store', headers: {'X-InfoRelay-Token': session.token, ...(data === undefined ? {} : {'Content-Type': 'application/json'})}, ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error === 'busy' ? t('请先停止自动检查，等待当前任务结束后再操作。', 'Stop automatic checks and wait for the current task to finish.') : t('操作未完成。请核对配置，检查启动窗口；若已重启请刷新页面。', 'Operation failed. Check your settings and launcher. Refresh this page if the launcher restarted.'));
  return payload;
}
async function save() {
  if (!dirty) return;
  const cfg = build(); await api('config', cfg); savedIdentity = {...cfg.email}; hasSavedPassword = Boolean(cfg.email.smtp_password) || hasSavedPassword; dirty = false; updateProvider(false);
}
async function task(operation) {
  try { await save(); await api('task', {operation}); status(t('任务已开始，结果会显示在下方。', 'Task started. Results will appear below.')); await poll(); }
  catch (error) { status(friendly(error), true); }
}
function renderRuntime() {
  if (!runtime) return;
  $('runtime-status').textContent = runtime.busy ? t('正在检查，请稍候…', 'Working, please wait…') : runtime.running ? t('自动检查已开启', 'Automatic checks are running') : t('已停止自动检查', 'Automatic checks are stopped');
  $('runtime-time').textContent = runtime.next_run ? t('下次检查：', 'Next check: ') + new Date(runtime.next_run).toLocaleString(I.language === 'en' ? 'en' : 'zh-CN') : runtime.last_run ? t('最近完成：', 'Last completed: ') + new Date(runtime.last_run).toLocaleString(I.language === 'en' ? 'en' : 'zh-CN') : t('尚未执行检查。', 'No checks have run yet.');
  $('stop').hidden = !runtime.running; $('start').disabled = runtime.running || runtime.busy;
  for (const id of ['diagnose', 'preview-live', 'test-email']) $(id).disabled = runtime.running || runtime.busy;
  const box = $('task-result'); box.replaceChildren(); if (!runtime.result) return;
  const result = runtime.result;
  box.append(el('p', {className: result.ok ? 'success' : 'error'}, result.ok ? t('本次操作已完成。', 'Operation completed.') : t('部分检查未通过，请按下方提示修正。', 'Some checks failed. Review the details below.')));
  if (result.code === 'test_submitted') box.append(el('p', {}, t('测试邮件已提交，请检查收件箱和垃圾邮件。', 'Test email submitted. Check your inbox and spam folder.')));
  if (result.code === 'operation_failed') box.append(el('p', {}, t('请检查配置、网络或发送记录。不要直接删除状态；详见故障排查指南。', 'Check settings, network and saved state. Do not delete state to retry; see the troubleshooting guide.')));
  for (const report of result.reports || []) {
    const item = el('div', {className: 'result-item'}), name = report.id === 'email' ? t('邮箱连接', 'Email connection') : subscriptions.find(s => s.id === report.id)?.name || report.id;
    item.append(el('strong', {}, (report.status === 'ok' ? '✓ ' : '× ') + name));
    if (report.error) item.append(el('p', {}, report.error));
    if (report.fetched !== undefined) item.append(el('p', {}, t(`获取 ${report.fetched} 条，匹配 ${report.matched} 条。`, `Fetched ${report.fetched}, matched ${report.matched}.`)));
    if (report.sent !== undefined) item.append(el('p', {}, t(`新增 ${report.new} 条，发送 ${report.sent} 条，待发 ${report.pending} 条。首次运行通常只建立记录。`, `New: ${report.new}. Sent: ${report.sent}. Pending: ${report.pending}. The first run normally sets a baseline.`)));
    for (const row of report.items || []) {
      const preview = el('div', {className: 'content-item'}); preview.append(el('b', {}, row.title), el('p', {}, row.excerpt));
      if (row.url) { try { M.checkURL(row.url); preview.append(el('a', {href: row.url, target: '_blank', rel: 'noopener noreferrer'}, t('阅读原文', 'Read source'))); } catch (_) {} }
      item.append(preview);
    }
    box.append(item);
  }
}
async function poll() { try { runtime = await api('status'); renderRuntime(); } catch (_) { $('runtime-status').textContent = t('本地服务未连接，请检查启动窗口并刷新页面。', 'Local service disconnected. Check the launcher and refresh.'); } }
function translate() {
  I.apply(); $('language').textContent = I.language === 'zh' ? 'English' : '中文';
  $('mode-label').textContent = session ? t('本地控制台', 'Local console') : t('静态配置向导', 'Static setup guide');
  $('privacy-copy').textContent = session ? t('只有点击保存才写入本机。授权码不回传给页面；不会上传到 InfoRelay 服务器。', 'Settings are saved locally only when you click Save. Saved passwords are not returned to the page. Nothing is uploaded to an InfoRelay server.') : t('输入只在本页面内处理。请将生成的配置妥善保存到自己的 GitHub Secrets。', 'Inputs stay in this page. Store generated settings in your own GitHub Secrets.');
  $('guide-link').href = `https://github.com/CHESHIRECAT-FINANCE/tibo-tweet-mail/blob/main/docs/${I.language === 'zh' ? 'zh' : 'en'}/guide.md`;
  updateProvider(false); renderSources(); renderRuntime();
}
$('language').addEventListener('click', () => { I.language = I.language === 'zh' ? 'en' : 'zh'; const url = new URL(location.href); url.searchParams.set('lang', I.language); history.replaceState(null, '', url); translate(); });
for (const button of document.querySelectorAll('[data-step]')) button.addEventListener('click', () => go(Number(button.dataset.step)));
for (const button of document.querySelectorAll('[data-back]')) button.addEventListener('click', () => go(Number(button.dataset.back)));
$('next-sources').addEventListener('click', () => { try { M.validateSources(subscriptions); go(2); } catch (error) { status(friendly(error), true); } });
$('next-mail').addEventListener('click', () => { try { build(); go(3); } catch (error) { status(friendly(error), true); } });
for (const button of document.querySelectorAll('[data-preset]')) button.addEventListener('click', () => addSource(button.dataset.preset));
$('add-source').addEventListener('click', () => addSource($('new-kind').value));
$('provider').addEventListener('change', () => updateProvider());
let recipientEdited = false;
$('mail-to').addEventListener('input', () => { recipientEdited = true; });
$('smtp-user').addEventListener('input', () => { if (!recipientEdited) $('mail-to').value = $('smtp-user').value; updateProvider(false); });
for (const id of ['smtp-user', 'smtp-host', 'smtp-password', 'smtp-port', 'mail-to', 'timezone']) $(id).addEventListener('input', invalidate);
$('import-config').addEventListener('click', () => $('import-file').click());
$('import-file').addEventListener('change', async () => { const file = $('import-file').files[0]; if (!file) return; try { if (file.size > 100000) throw new Error(t('配置文件过大。', 'Settings file is too large.')); applyConfig(JSON.parse(await file.text()), false, true); recipientEdited = true; status(t('已导入。更新云端订阅时，请沿用原来的加密密钥。', 'Imported. Keep your original encryption key when updating cloud subscriptions.')); } catch (error) { status(t('无法导入配置：', 'Cannot import settings: ') + friendly(error), true); } $('import-file').value = ''; });
$('diagnose').addEventListener('click', () => task('diagnose')); $('preview-live').addEventListener('click', () => task('preview')); $('test-email').addEventListener('click', () => task('test-email'));
$('start').addEventListener('click', async () => { try { if (!$('allow-send').checked) throw new Error(t('请先确认邮箱可用，并勾选自动发送许可。', 'Verify your mailbox and tick the automatic delivery checkbox first.')); await save(); await api('start', {interval: Number($('interval').value)}); await poll(); status(t('自动订阅已开启。请保持启动窗口运行。', 'Automatic checks started. Keep the launcher running.')); } catch (error) { status(friendly(error), true); } });
$('stop').addEventListener('click', async () => { try { await api('stop', {}); await poll(); status(t('已停止后续检查。正在进行的任务会完成并保存记录。', 'Future checks stopped. Any current task will finish and save its records.')); } catch (error) { status(friendly(error), true); } });
$('generate').addEventListener('click', () => { try {
  generatedConfig = build(true);
  if (!generatedKey && !importedCloud) { const bytes = crypto.getRandomValues(new Uint8Array(32)); generatedKey = btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_'); }
  $('config-output').value = JSON.stringify(generatedConfig, null, 2); $('state-key').value = generatedKey; $('copy-key').disabled = !generatedKey; $('generated').hidden = false;
  $('key-hint').textContent = importedCloud ? t('你导入了已有配置：请沿用原 INFO_RELAY_STATE_KEY，不要更换。首次部署请使用全新配置生成密钥，或运行 python -m info_relay keygen。', 'Imported settings: keep the original INFO_RELAY_STATE_KEY. For a first deployment, use a fresh setup or run python -m info_relay keygen.') : t('备份此密钥。以后修改订阅时保留原密钥，不要重新生成替换。', 'Back up this key. Keep it when changing subscriptions; do not replace it with a newly generated key.');
  status(t('配置已生成，尚未启用云端运行。请完成下方步骤。', 'Settings generated. Cloud delivery is not enabled yet; follow the steps below.'));
} catch (error) { status(friendly(error), true); } });
async function copy(id) { try { await navigator.clipboard.writeText($(id).value); status(t('已复制。', 'Copied.')); } catch (_) { $(id).focus(); $(id).select(); status(t('已选中文本，请手动复制。', 'Text selected. Copy it manually.')); } }
$('copy-config').addEventListener('click', () => copy('config-output')); $('copy-key').addEventListener('click', () => copy('state-key'));
$('download').addEventListener('click', () => { if (!generatedConfig) return; const url = URL.createObjectURL(new Blob([JSON.stringify(generatedConfig, null, 2) + '\n'], {type: 'application/json'})); const link = el('a', {href: url, download: 'subscriptions.private.json'}); document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url); });
translate();
(async () => {
  if (location.protocol !== 'http:' || location.hostname !== '127.0.0.1') return;
  try {
    const response = await fetch('/api/session', {headers: {'X-InfoRelay-Client': '1'}, cache: 'no-store'});
    if (!response.ok) return; const result = await response.json(); if (result.mode !== 'local') return; session = result;
    $('local-controls').hidden = false; $('runtime-panel').hidden = false; $('cloud-controls').open = false;
    const stored = await api('config'); if (stored.config) { applyConfig(stored.config, stored.has_password); dirty = false; recipientEdited = true; }
    translate(); await poll(); setInterval(poll, 3000);
  } catch (error) { status(t('无法读取本地配置，请检查启动窗口。', 'Cannot load local settings; check the launcher.') + ' ' + friendly(error), true); }
})();
