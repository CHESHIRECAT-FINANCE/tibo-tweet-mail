# InfoRelay 中文使用指南

[English](../en/guide.md) · [项目首页](../../README.zh-CN.md) · [配置参考](../configuration.md)

## 先选运行方式

| 方式 | 适合谁 | 需要什么 | 电脑关机后 |
|---|---|---|---|
| 本地向导（推荐先体验） | 不想编辑配置或学习 GitHub 的用户 | Python 3.9+、支持 SMTP 的邮箱 | 停止检查 |
| GitHub Actions | 希望全天运行的个人用户 | GitHub 账号、邮箱、一次性设置 Secrets | 继续由 GitHub 调度 |
| CLI / Docker | 开发者、服务器或 NAS 用户 | Python 或 Docker、持续运行的主机 | 取决于主机是否运行 |

这不是已经托管好的在线订阅服务。第一版仍需要用户提供自己的邮箱授权码；本地运行还需要安装 Python。配置页面不会帮你创建邮箱账户，也不会自动开通 SMTP。

## 本地向导：从下载到第一封邮件

### 1. 下载并启动

从仓库 **Code → Download ZIP** 下载并解压。[直接下载](https://github.com/CHESHIRECAT-FINANCE/tibo-tweet-mail/archive/refs/heads/main.zip)。

没有 Python 时，从 [Python 官方网站](https://www.python.org/downloads/) 安装 Python 3.9 或以上版本；新安装建议使用受支持的新版本。Windows 勾选 **Add Python to PATH**。

- Windows：双击解压目录里的 `start.bat`。
- macOS：双击 `start.command`。如果提示没有执行权限，打开终端，进入解压目录，运行 `python3 start.py`。
- Linux：在项目目录运行 `python3 start.py`。系统缺少 venv 时，先通过发行版包管理器安装 Python 的 venv 支持。

首次运行会在项目的 `.venv` 中安装 `requirements.txt` 的依赖，不修改系统 Python 包。启动窗口出现 `http://127.0.0.1:8766/` 后，浏览器会打开控制台。浏览器未自动打开时，手动访问这个地址。

页面右上角应显示 **本地控制台**。如果显示“静态配置向导”，说明你打开的是 `web/index.html` 或静态 HTTP 服务；它只能生成云端配置，不能直接运行本地任务。

### 2. 选择内容

默认提供 Python 博客示例。可以编辑它，也可以删除后添加自己的来源。上方三个示例按钮会添加来源，不会替换其他订阅。

- **RSS / Atom：**粘贴网站公布的 feed 链接，不是普通首页。网站没有 RSS 时，可以尝试网页更新。
- **网页更新：**比较公开页面的提取文字。网页里的时间、广告等文字变化也可能触发；复杂动态页优先使用 RSS。
- **GitHub：**输入 `作者/仓库`，例如 `cli/cli`。只跟踪 Release，不包含普通提交和 Issue。
- **X：**输入不带 `@` 的账号。依赖公开的 FxTwitter 时间线，可能因第三方服务异常而失败。
- **行情：**设置每天几点后发送。汇率、原油和黄金期货可能延迟，属于信息简报。

“筛选条件与更多设置”里可填写关键词。多个词用逗号隔开；排除词优先；空的包含词表示不限制关键词。默认首次只记录当前内容，不发送旧文章。想立即收到当前匹配内容，可明确选“首次也发送”。

点击 **下一步：连接邮箱**。

### 3. 连接邮箱

选择 QQ Mail、163 Mail、Gmail 或自定义服务商，预设会填入 SMTP 服务器和 TLS 端口。填写发件邮箱、收件邮箱及授权码。默认把邮件发给自己，也可修改收件地址。

| 服务商 | 服务器 / 端口 | 需要准备 |
|---|---|---|
| QQ Mail | `smtp.qq.com` / `465` | 在邮箱设置中开启 SMTP，生成授权码；[官方帮助](https://help.mail.qq.com/detail/106/985) |
| 163 Mail | `smtp.163.com` / `465` | 开启 SMTP，使用客户端授权密码；[官方帮助中心](https://help.mail.163.com/) |
| Gmail | `smtp.gmail.com` / `587` | 账户允许使用的应用专用密码；通常需两步验证；[官方说明](https://support.google.com/accounts/answer/185833?hl=zh-Hans) |
| 自定义 | 按服务商文档填写 | 支持 SMTP 用户名 / 密码认证和 TLS |

授权码不是普通网页登录密码。某些组织账户、安全策略或账户类型不支持应用专用密码，需要管理员允许，或使用另一个支持 SMTP 的邮箱。当前没有 OAuth 邮箱登录流程。

“服务器与时间设置”可以修改时区。每日行情按你选的时区决定哪一天和何时发送，行情正文中原有报价时间标注仍可能为北京时间。

### 4. 验证并启动

1. **保存并检查：**把配置保存到这台电脑，检查来源、SMTP 连接、TLS 和登录；不发邮件。
2. **预览内容：**显示每个来源最多 5 条最新匹配样例；不发邮件，不修改去重记录。这不是待发队列列表。
3. **发一封测试邮件：**明确发送一封测试邮件，检查收件箱和垃圾邮件。服务端“提交成功”只表示邮箱服务器接受，不保证进入主收件箱。
4. 确认邮箱可用后，勾选自动发送许可，选择间隔，点击 **开启自动订阅**。第一轮立即运行，之后在上一轮结束后等待所选间隔再检查。

右侧显示运行状态、最近完成时间和下次检查时间。首次 RSS / GitHub / X 运行可能发送 0 条，这是记录基线；之后出现新条目才通知。一个成功返回的空来源也会建立空基线，因此它以后的第一条内容不会被漏掉。

### 修改、暂停与退出

先点击 **停止自动检查**，等待当前任务结束，再编辑配置并重新保存。停止不会取消已开始的发送；当前任务会完成并保存状态。

重新打开控制台会恢复订阅和邮箱设置。密码框为空但提示已保存时，留空可沿用原密码；换发件账号、服务器或端口时必须重新输入。导出云端配置时也需要重新输入密码。

- 关闭浏览器标签：后台启动进程继续检查。
- 关闭启动窗口 / 电脑关机：停止本地检查。
- 重新启动：配置保留，但不会自动发送；在控制台重新开启。
- 仅暂停某个来源：在该来源的更多设置里取消启用。待发记录仍保留。

本地配置和状态在项目 `.inforelay/` 中，含私密内容，已列入 `.gitignore`。不要放进公开仓库或共享文件夹。备份时先停止检查，保留整个目录；Windows 文件隐私依赖你自己的账户和文件夹权限。

## 在 GitHub 云端运行

这种方式不要求自己的电脑长期在线，但需要一次性完成 GitHub 设置。

1. [Fork 项目](https://github.com/CHESHIRECAT-FINANCE/tibo-tweet-mail/fork)。无需改代码，也不要把邮箱配置提交到仓库。
2. 使用本地控制台第三步的“GitHub 云端运行”，或双击 `web/index.html` 打开静态配置向导。填写配置后点击 **生成云端配置**。
3. 在你自己的仓库打开 **Settings → Secrets and variables → Actions → Secrets → New repository secret**，分别创建：

   | 名称 | 内容 |
   |---|---|
   | `INFO_RELAY_CONFIG` | 页面生成的完整 JSON |
   | `INFO_RELAY_STATE_KEY` | 页面生成的加密密钥 |

4. 在 **Actions** 页面启用 Fork 的工作流（如果出现提示）。打开 **InfoRelay subscriptions → Run workflow**，先选择 `diagnose`，等绿色成功；再运行 `test-email`，确认收到。
5. 在 **Settings → Secrets and variables → Actions → Variables** 添加 `INFO_RELAY_ENABLED`，值为小写 `true`。
6. 再手动以 `run` 模式运行一次。确认来源处理和加密状态保存成功，然后等待自动调度。

以后修改订阅，只更新 `INFO_RELAY_CONFIG`。**保留原来的密钥和 `inforelay-state` 分支。** 导入旧配置时向导不会生成替代密钥。如果是首次部署而手头只有导入配置，可运行 `python -m info_relay keygen` 生成首次密钥。

不要同时开启本地和云端两套独立调度器监控同一订阅，否则可能收到两份通知。本地状态不会自动迁移到云端，云端首次运行会建立自己的基线。

停止云端发送：把 `INFO_RELAY_ENABLED` 改为 `false`。手动 `run` 仍会按你的请求运行；仅诊断时请选择 `diagnose`。

GitHub 每约 10 分钟请求一次检查，实际执行可能延迟或跳过。公共仓库长时间不活跃时，定时工作流可能停用。[调度说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。标准公共仓库 runner 通常免费，账户与 runner 计费以 [GitHub 官方说明](https://docs.github.com/en/actions/concepts/billing-and-usage) 为准。

## 服务器与 Docker

安装到虚拟环境后，可直接使用 CLI：

```sh
python -m pip install .
inforelay init --config subscriptions.json
# 编辑 subscriptions.json 后：
inforelay diagnose --config subscriptions.json
inforelay watch --config subscriptions.json --state .inforelay/state.json --interval 600
```

`run` 只执行一轮；`watch` 在每轮结束后等待指定秒数再运行，不并发重叠。收到 SIGTERM 时完成当前轮次后退出。使用 cron、systemd 或 Windows 任务计划时，执行 `run` 并确保所有任务使用同一状态路径。

仓库提供 `Dockerfile` 和 `compose.yaml`。先用向导下载 `subscriptions.private.json` 到项目根目录，然后：

```sh
docker compose up --build -d
docker compose logs -f
docker compose stop
```

这会启动无界面的定时发送进程。配置文件只读挂载，状态在命名 volume `inforelay-data` 中。不要执行删除 volume 的命令来“重试”，否则会丢失记录。容器不提供公开 Web 控制台。

只想体验处理流程、不配置邮箱：

```sh
python -m info_relay preview --config examples/diy.json
python -m info_relay run --config examples/diy.json --state .inforelay/diy-state.json
```

输出在 `outbox/`。这是 JSON 文件渠道，不发送邮件。

## 故障排查

| 现象 | 检查与处理 |
|---|---|
| 找不到 Python | 安装 Python 3.9+；Windows 勾选 PATH，重新打开终端 |
| 首次安装依赖失败 | 检查网络、PyPI 访问与 venv 支持；重新启动会重试未完成的安装 |
| 8766 端口已被使用 | 先检查是否已经打开控制台；或运行 `python -m info_relay serve --port 8767` |
| 页面显示静态向导 | 用 `start.py` 启动本地服务，访问 `127.0.0.1:8766`；普通静态服务器不能运行任务 |
| 403 / 本地服务未连接 | 使用启动窗口显示的 `127.0.0.1` 地址；不要改成主机名或反向代理。服务重启后刷新页面 |
| 邮箱检查失败 | 确认 SMTP 已开启、授权码正确、账号与服务器匹配、端口为 465 或 587、网络可达 |
| 服务器接受但没收到 | 检查垃圾邮件、过滤规则、发件服务商限制；确认收件地址无误 |
| 信息源失败 | RSS 是否误填首页；网页是否需要登录或 JS；X / GitHub / 行情服务是否限流或临时不可用 |
| 首次成功但发送 0 条 | 默认只建立基线，等待新内容；或明确选择首次发送当前内容 |
| 修改配置后提示仍有待发内容 | 恢复原来源配置，修好邮箱并清空队列，再修改 URL / 筛选条件；不要删除状态 |
| 状态损坏 / 密钥错误 | 停止发送并恢复备份。不要删状态分支、换密钥或清空目录来绕过错误 |
| 自动任务没按时运行 | 本地电脑/启动进程是否运行；云端变量是否为 `true`、Actions 是否启用、任务是否排队 |

提交问题时，提供版本、运行方式、错误类型和经过脱敏的配置结构。**不要贴授权码、密钥、完整配置或未脱敏的截图。**
