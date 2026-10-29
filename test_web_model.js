const assert = require('node:assert/strict');
const M = require('./web/model.js');
const email = {smtp_host: 'smtp.example.com', smtp_port: 465, smtp_user: 'sender@example.com', to: 'reader@example.com', smtp_password: 'test-only'};
for (const kind of Object.keys(M.kinds)) {
  const source = M.createSource(kind, 'source-1');
  const config = M.build([source], email, 'Asia/Shanghai');
  assert.equal(config.sources[0].type, kind);
  assert.equal(config.sources[0].initial, 'skip');
  config.sources[0].keywords.push('modified');
  assert.deepEqual(source.keywords, []);
}
assert.deepEqual(M.words('AI，发布, AI\nnews'), ['AI', '发布', 'news']);
assert.throws(() => M.build([], email, 'UTC'));
assert.throws(() => M.build([M.createSource('rss', 'one')], {...email, smtp_password: ''}, 'UTC'));
for (const url of ['file:///etc/passwd', 'http://127.0.0.1', 'http://169.254.169.254', 'https://user:pass@example.com', 'http://[::1]']) assert.throws(() => M.checkURL(url));
const source = M.createSource('github', 'one'); source.repository = 'not a repository';
assert.throws(() => M.build([source], email, 'UTC'));
console.log('Configuration model checks passed (all 5 source types).');
