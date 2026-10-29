(function (root, factory) {
  const model = factory();
  if (typeof module === 'object' && module.exports) module.exports = model;
  else root.RelayModel = model;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const kinds = {
    rss: {label: 'RSS 订阅', name: 'Python 博客', url: 'https://blog.python.org/feeds/posts/default'},
    webpage: {label: '网页更新', name: 'Python 下载页面', url: 'https://www.python.org/downloads/'},
    github: {label: 'GitHub 发布', name: 'GitHub CLI', repository: 'cli/cli'},
    x: {label: 'X 动态', name: 'Tibo 的动态', handle: 'thsottiaux', include_reposts: true},
    market: {label: '每日行情', name: '汇率、原油与黄金', hour: 8}
  };
  function createSource(type, id) {
    if (!kinds[type]) throw new Error('请选择支持的信息源');
    const {label, ...fields} = kinds[type];
    return {id, type, ...fields, enabled: true, keywords: [], exclude: [], match: 'any', initial: 'skip'};
  }
  function words(value) { return [...new Set(value.split(/[,，\n]/).map(x => x.trim()).filter(Boolean))]; }
  function checkURL(value) {
    if (typeof value !== 'string' || value.length > 2000 || /[\x00-\x20\x7f]/.test(value)) throw new Error('地址不能包含空格或控制字符');
    let u;
    try { u = new URL(value); } catch (_) { throw new Error('请输入完整的网页或 RSS 地址'); }
    if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || (u.port && !['80', '443'].includes(u.port))) {
      throw new Error('请使用不含账号密码的 HTTP(S) 公网地址');
    }
    const host = u.hostname.toLowerCase();
    if (host === 'localhost' || /\.(local|localhost|internal)$/.test(host) || /^127\.|^10\.|^192\.168\.|^169\.254\.|^0\./.test(host) || /^172\.(1[6-9]|2\d|3[01])\./.test(host) || host.includes(':')) {
      throw new Error('请使用公网地址，不能监控本机或内网页面');
    }
    return value;
  }
  function validateSources(sources) {
    if (!sources.length || sources.length > 20) throw new Error('请添加 1 到 20 个信息源');
    const ids = new Set();
    for (const source of sources) {
      if (!kinds[source.type] || !/^[a-zA-Z0-9_-]{1,48}$/.test(source.id)) throw new Error('信息源类型或 ID 无效');
      if (!['any', 'all'].includes(source.match) || !['skip', 'send'].includes(source.initial)) throw new Error('信息源筛选模式无效');
      for (const flag of ['enabled', 'prereleases', 'reset_only', 'include_reposts', 'include_replies', 'include_quotes']) {
        if (source[flag] !== undefined && typeof source[flag] !== 'boolean') throw new Error('开关配置必须为 true 或 false');
      }
      if (!source.name.trim() || source.name.length > 80) throw new Error('请给每个订阅填写 1 到 80 字的名称');
      if (ids.has(source.id)) throw new Error('订阅 ID 重复');
      ids.add(source.id);
      if (['rss', 'webpage'].includes(source.type)) checkURL(source.url);
      if (source.type === 'github' && !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(source.repository)) throw new Error('GitHub 仓库格式为 owner/repository');
      if (source.type === 'x' && !/^[A-Za-z0-9_]{1,15}$/.test(source.handle)) throw new Error('请输入不带 @ 的 X 用户名');
      if (source.type === 'market' && (!Number.isInteger(source.hour) || source.hour < 0 || source.hour > 23)) throw new Error('行情时间必须为 0 到 23 点');
      for (const field of ['keywords', 'exclude']) {
        if (!Array.isArray(source[field]) || source[field].length > 30 || source[field].some(x => typeof x !== 'string' || !x.trim() || x.length > 100 || /[\x00-\x1f\x7f]/.test(x))) throw new Error('每组最多填写 30 个关键词，每个词不超过 100 字');
      }
    }
    return true;
  }
  function build(sources, email, timezone, batchSize = 20, allowSavedPassword = false) {
    validateSources(sources);
    if (!Number.isInteger(batchSize) || batchSize < 1 || batchSize > 50) throw new Error('每批发送数量必须为 1 到 50');
    try { new Intl.DateTimeFormat('en', {timeZone: timezone}); } catch (_) { throw new Error('时区无效'); }
    if (!/^[A-Za-z0-9.-]+$/.test(email.smtp_host)) throw new Error('请填写 SMTP 服务器名称');
    if (!Number.isInteger(email.smtp_port) || email.smtp_port < 1 || email.smtp_port > 65535) throw new Error('请填写有效的 SMTP 端口');
    for (const value of [email.smtp_user, email.to]) {
      if (!/^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/.test(value)) throw new Error('请填写完整的发件和收件邮箱');
    }
    if (!email.smtp_password.trim() && !allowSavedPassword) throw new Error('请填写邮箱的 SMTP 授权码或应用专用密码');
    if ([...Object.values(email).filter(x => typeof x === 'string'), ...sources.map(x => x.name)].some(x => /[\x00-\x1f\x7f]/.test(x))) throw new Error('输入中不能包含控制字符');
    return {version: 1, timezone, batch_size: batchSize, email: {...email}, sources: JSON.parse(JSON.stringify(sources))};
  }
  return {kinds, createSource, words, build, validateSources, checkURL};
});
