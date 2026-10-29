# 配置参考

以 `subscriptions.example.json` 为起点。未知字段会报错，避免配置拼写错误被静默忽略。

## 全局字段

| 字段 | 默认值 | 含义 |
|---|---|---|
| `version` | `1` | 配置格式版本 |
| `timezone` | `Asia/Shanghai` | IANA 时区；决定行情的「今天」与发送小时 |
| `batch_size` | `20` | 每个来源每轮最多发送多少条；范围 1–50 |
| `sources` | 必填 | 1–20 个信息源 |
| `email` | 预览时可省略 | 实际发送与诊断需要完整 SMTP 配置 |

`email` 使用 `smtp_host`、`smtp_port`、`smtp_user`、`smtp_password`、`to`。
465 为隐式 TLS，其他端口使用 STARTTLS；始终验证证书。每份配置使用一个发件账号和一个收件地址。
不支持地址列表、抄送或 HTML 邮件。密码只用于配置指定的 SMTP 服务器。

## 信息源公共字段

| 字段 | 默认值 | 含义 |
|---|---|---|
| `id` | 必填 | 唯一且稳定的 ID，1–48 位字母、数字、下划线、短横线 |
| `type` | 必填 | `rss` / `webpage` / `github` / `x` / `market` |
| `name` | 同 ID | 邮件中的名称，最多 80 字 |
| `enabled` | `true` | false 时暂停抓取与队列发送 |
| `keywords` | `[]` | 包含词；空列表表示全部 |
| `exclude` | `[]` | 排除词，优先于包含规则 |
| `match` | `any` | `any` 任一词，`all` 全部词 |
| `initial` | `skip` | `skip` 首次只建基线；`send` 首次也发送匹配内容 |

关键词是普通文本，不是正则表达式。对标题与正文做不区分大小写的子串匹配。
每组最多 30 个词，每词最多 100 字。修改筛选配置会重新建立基线，默认不会回放以前忽略的文章。

## 各类来源

```json
[
  {"id":"news","type":"rss","name":"技术新闻","url":"https://example.com/feed.xml","keywords":["AI","发布"],"exclude":["广告"]},
  {"id":"page","type":"webpage","name":"公告页面","url":"https://example.com/announcements"},
  {"id":"releases","type":"github","name":"新版本","repository":"cli/cli","prereleases":false},
  {"id":"reset","type":"x","name":"用量重置","handle":"thsottiaux","reset_only":true,"include_reposts":true,"include_replies":false,"include_quotes":false},
  {"id":"market","type":"market","name":"每日行情","hour":8}
]
```

以上是 `sources` 数组示例。`example.com` 地址是占位符，需换成真实公开信息源。
RSS 与网页 URL 禁止内网、URL 中嵌入账号密码和非 80/443 端口；每次重定向也会校验目标。
网页监控整页可见文字，不做浏览器渲染或 CSS 区域选择。

GitHub 订阅公开仓库 Release，不读取私有仓库、不使用你的 GitHub token。
X 的 `reset_only` 默认 false；启用后复用原项目的中英文重置语义规则。
行情 `hour` 使用全局时区，0–23；正文内报价时间明确标为北京时间。

## 状态与失败恢复

每个来源先把新条目和待发快照写入本地原子检查点；云端运行还会在 SMTP 前推送加密快照。
成功提交后移除已发批次并保存，再处理下一来源。任何检查点写入或推送失败会停止继续发送。
最后的工作流清理步骤会再尝试保存，以降低单次网络故障的影响。

普通来源故障仍会尝试发送该来源之前已入队的内容。SMTP 提交前的连接断开会重试，
提交过程中结果不明则不立即重发。没有投递回执和自动退信处理，不能证明所有邮件进入收件箱。

不要通过删除检查点来修复错误。优先查看 Actions 失败步骤，修复配置、来源或网络后重跑。
密钥遗失无法解密旧记录；需要使用备份恢复。跨设备本地运行也必须沿用同一份检查点。
