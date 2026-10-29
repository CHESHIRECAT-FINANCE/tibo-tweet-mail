'use strict';
window.RelayI18n = {
  language: new URLSearchParams(location.search).get('lang') === 'en' ? 'en' : 'zh',
  text(zh, en) { return this.language === 'en' ? en : zh; },
  apply() {
    document.documentElement.lang = this.language === 'en' ? 'en' : 'zh-CN';
    document.title = this.text('InfoRelay · 信息订阅助手', 'InfoRelay · Your information inbox');
    for (const node of document.querySelectorAll('[data-zh][data-en]')) node.textContent = node.dataset[this.language];
  }
};
