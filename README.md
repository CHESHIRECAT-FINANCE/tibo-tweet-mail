# InfoRelay · 信息订阅助手

把你关心的公开信息，按自己的条件送到邮箱。

InfoRelay 从一个 X 重置通知和每日行情邮件工具发展而来。现在你可以订阅
RSS / Atom、网页文字变化、GitHub 新版本、X 动态和每日行情，按关键词筛选，
在自己的 GitHub 仓库中自动运行。没有托管账户，也不依赖付费 AI 服务。

**第一版面向个人使用：中文配置页面、邮件通知、MIT 开源。**

## 不写代码也能开始

1. [Fork 这个项目](https://github.com/CHESHIRECAT-FINANCE/tibo-tweet-mail/fork) 到自己的 GitHub。
   在仓库 **Code → Download ZIP** 下载源码并解压，双击 `web/index.html` 打开中文配置页面。
   如果浏览器阻止本地脚本，可在项目目录运行 `python3 -m http.server 8765 --bind 127.0.0.1 --directory web`，
   再打开 `http://127.0.0.1:8765`。
2. 在页面中选择信息源、设置关键词、填写邮箱，点击「生成我的订阅配置」。
   页面只在本机处理输入，不连接数据源、不上传密码，也不会发送邮件。
3. 在自己仓库的 **Settings → Secrets and variables → Actions → Secrets** 创建：

   | Secret 名称 | 填写内容 |
   |---|---|
   | `INFO_RELAY_CONFIG` | 页面生成的完整 JSON 配置 |
   | `INFO_RELAY_STATE_KEY` | 页面生成的加密密钥，需妥善备份 |

4. 打开 **Actions → InfoRelay subscriptions → Run workflow**。Fork 后如果显示工作流停用，先启用 Actions。
   先选 `diagnose` 检查连接，再选 `test-email` 发送一封测试邮件。
5. 收到测试邮件后，在 **Actions → Variables** 创建变量 `INFO_RELAY_ENABLED`，值填 `true`。
   手动选 `run` 运行一次，确认任务成功、检查点已经保存。之后约每 10 分钟请求自动检查。

首次运行默认只记录当前内容，不把历史文章全部塞进邮箱。需要历史内容时，可以在配置页面中明确选择。
行情订阅例外：到发送时段后就发送当天日报。电脑关机不影响 GitHub 云端运行。

> 配置包含邮箱授权码，必须放在 **Secrets**，不要上传到代码仓库、Issue 或截图。
> QQ / 163 等邮箱要开启 SMTP 并使用授权码；Gmail 使用应用专用密码。
> 密钥不是每次更新配置都重新生成：已经开始运行后，必须保留原来的 `INFO_RELAY_STATE_KEY`。

## 支持哪些信息

| 信息源 | 获取的内容 | 说明 |
|---|---|---|
| RSS / Atom | 订阅源里的新文章 | 使用条目 GUID / ID 去重，也支持相对链接与常用命名空间 |
| 网页更新 | 公共网页文字发生变化 | 忽略脚本、样式和空白变化；首次建立基线；A → B → A 也会通知 |
| GitHub 发布 | 公开仓库的新 Release | 默认不含预发布；不是 Issue、提交记录或仅创建的标签 |
| X 动态 | 指定公开账号的新帖子 | 可只收用量重置通知，分别控制转发、回复和引用；依赖 FxTwitter |
| 每日行情 | 人民币汇率、WTI / 布伦特原油和黄金期货 | 每天指定小时后发送一次；来源 Yahoo Finance，报价可能延迟 |

每个来源可以设置包含关键词、排除关键词、任一 / 全部匹配。关键词不区分大小写，排除规则优先。
网页关键词针对本次页面全文筛选，不是仅针对变化的部分；复杂页面建议优先使用 RSS。

多个来源独立运行。每个来源每轮最多一封汇总邮件，默认每封 20 条；超过的条目留在队列等待后续运行。
一个来源暂时失败，不阻止其他来源。已经入队的内容即使从原始窗口消失，也能继续重试。

## 修改订阅

在配置页面选择「导入已有配置」，修改后只更新 `INFO_RELAY_CONFIG`。导入会保留来源 ID。
请保留下载的私密配置文件和原加密密钥，并避免把文件放进共享文件夹。

- `id` 是订阅的长期标识。改名称不会重发历史内容。
- 改 URL、筛选条件等会重新建立基线；如果还有待发内容，会暂停该来源并要求先用原配置清空队列。
- 暂停或移除来源不会自动删除其待发记录。恢复相同 ID 和配置后继续处理。
- 从旧版迁移时，先验证新订阅，再停用旧任务，避免同一主题收到两份通知。

## 本地运行

Python 3.9+。建议使用虚拟环境；`cryptography` 用于云端检查点加密。

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m info_relay init
# 编辑 subscriptions.json，也可以使用配置页面生成的 subscriptions.private.json
python -m info_relay check
python -m info_relay preview
python -m info_relay diagnose
python -m info_relay test-email   # 只有明确运行此命令才发测试邮件
python -m info_relay run
```

也可以使用 `--config /path/to/subscriptions.private.json` 或环境变量 `INFO_RELAY_CONFIG`。
本地不想把密码写进配置文件时，可以留空 `smtp_password`，另设 `INFO_RELAY_SMTP_PASSWORD`。
状态默认位于 `.inforelay/state.json`，用 `--state` 指定其他位置。

`preview` 显示每个来源最多 5 条匹配样例，不发送、不修改状态；它不是下一次待发队列的列表。
`diagnose` 只显示连接检查结果，不在日志中输出文章正文。`run` 才实际发送订阅邮件。
可以用 cron / systemd 等定期运行 `run`。不要同时开本地和云端两个独立调度器。

完整配置示例见 [subscriptions.example.json](subscriptions.example.json)，字段说明见 [配置参考](docs/configuration.md)。

## 使用前了解

- **GitHub 调度是尽力执行。** 10 分钟是请求频率，不是准时承诺；可能延迟或被跳过。
  公共仓库长期不活跃时也可能停用定时任务。需要严格时效时，用常驻服务器调度。
  参见 [GitHub schedule 文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。
- 标准公共仓库 GitHub-hosted runner 通常免费，私有仓库和其他 runner 以账户额度为准。
  参见 [Actions 计费说明](https://docs.github.com/en/actions/concepts/billing-and-usage)。
- 只支持公开 HTTP(S) 信息源，不支持登录、付费墙、私有 API 或执行网页 JavaScript。
  每个响应最多 2 MB；每个 feed 最多 1000 条；GitHub 每次最新 10 个发布、X 最新 20 条。
  长时间停机可能错过尚未抓到、已离开来源窗口的内容。
- 公共 GitHub API 有未认证访问频率限制。请减少频繁重复的来源；程序遵守有限范围内的 `Retry-After`，
  更长的等待交给后续调度。使用前确认目标站点允许你的访问方式和频率。
- 邮件服务器接受消息不等于进入收件箱，请检查垃圾邮件、过滤规则和 SMTP 授权码。
  SMTP 不能保证端到端「恰好一次」：服务器已接收但连接断开，或刚发出邮件就保存失败，可能造成后续重复。
- 云端的待发快照和记录使用 [Fernet 认证加密](https://cryptography.io/en/latest/fernet/)，
  `inforelay-state` 分支只保存 `subscriptions.enc`。更新次数、时间和大致大小仍可见。
  **不要删除此分支或直接更换密钥**，否则可能无法恢复队列和去重记录。
  本地 `.inforelay` 是明文私有状态，已列入 `.gitignore`；不要公开分享它。
- 单来源最多保留 500 条待发记录和最近 5000 个已见条目，总状态最多 20 MB。容量不足会停止追加，
  保留已有队列，等待修复邮箱后继续处理。有限去重窗口以外的极旧条目重新出现时可能再次通知。

## 原有 Tibo 邮件功能

旧的 `tibo_tweet_mail.py`、`mail-state` 分支、`TIBO_MAIL_CONFIG` 和 `MAIL_ENABLED` 继续使用原配置。
新工作流默认关闭，使用独立的开关、Secrets 和加密状态分支，安装新源码不会自动改变旧订阅。
详见 [旧版使用说明与已修复问题](docs/legacy.md)。

## 开发与贡献

```sh
python -m pip install -r requirements.txt
python -m unittest -v
node test_web_model.js
```

测试使用本地 fixtures、模拟 SMTP 和临时 Git 仓库，不发送邮件。CI 覆盖 Python 3.9 / 3.13。
纯静态配置页面无需前端构建工具或第三方脚本。

欢迎贡献新的公开信息源和通知渠道，见 [CONTRIBUTING.md](CONTRIBUTING.md)。
安全问题见 [SECURITY.md](SECURITY.md)。采用 [MIT License](LICENSE)。
