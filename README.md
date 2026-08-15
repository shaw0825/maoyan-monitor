# 猫眼《奥德赛》IMAX 场次监控

定时监控 **猫眼** 上 **MOViE MOViE 影城（前滩太古里店）** 里电影 **《奥德赛》(The Odyssey, 2026)** 的 **IMAX 场次**，一旦出现新场次就通过 **QQ 邮箱** 通知你。

本项目是对 [taopiaopiao-monitor](../taopiaopiao-monitor) 的“换数据源”改造：把对接淘票票 `acs.m.taopiaopiao.com` 的排片接口，替换为对接猫眼 `m.maoyan.com` 的排片接口，其余监控/去重/邮件逻辑保持一致。

纯 `requests` 实现，无需浏览器、无需登录、无需签名，单文件依赖，适合 7×24 小时常驻运行。

---

## 一、它能做什么

- 每 `POLL_INTERVAL` 秒（默认 5 分钟）抓一次该影院的最新排片（约未来 6 天）。
- 过滤出《奥德赛》在 IMAX 影厅（`IMAX 激光厅` / 版本 `IMAX2D`）的场次。
- 用场次唯一 `seqNo` 做去重，只在出现**新场次**时发邮件（已存在的场次不会重复打扰）。
- 首次运行只建立基线，不会把历史场次误报为“新场次”。

---

## 二、环境准备

需要 Python 3.8+。安装依赖（国内建议用清华镜像）：

```bash
pip install -r requirements.txt
# 或
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 三、配置

编辑 `config.py`，重点是 **邮件通知** 相关配置。脚本已支持多种邮箱，通过 `SMTP_PROVIDER` 一键切换：

| 配置项 | 说明 |
| --- | --- |
| `CINEMA_ID` | 猫眼影院 ID（已填 `37534` = MOViE MOViE 影城·前滩太古里店） |
| `MOVIE_ID` | 猫眼电影 ID（已填 `1545360` = 奥德赛 2026 诺兰版） |
| `CITY_ID` | 猫眼城市编码（`10` = 上海） |
| `IMAX_KEYWORDS` | IMAX 判定关键词，默认 `["IMAX"]` |
| `SMTP_PROVIDER` | `qq`=QQ个人邮箱 · `exmail`=腾讯企业邮箱 · `163` · `gmail` · `custom`=自定义 |
| `SMTP_USER` | 发件邮箱地址（企业邮箱填类似 `name@yourcompany.com`） |
| `SMTP_PASSWORD` | 各服务商填法不同，见下 |
| `MAIL_TO` | 收件邮箱，可填多个 |

### 各邮箱的密码/授权码填法

**QQ 个人邮箱（`SMTP_PROVIDER = "qq"`）**
1. 网页登录 QQ 邮箱 → 顶部「**设置**」→「**账户**」。
2. 找到「**POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务**」区域，开启「**SMTP 服务**」（需短信验证）。
3. 页面显示一串 **16 位授权码**，复制填入 `SMTP_PASSWORD`（不是登录密码）。

**腾讯企业邮箱 / QQ 企业邮箱（`SMTP_PROVIDER = "exmail"`）**
1. 服务器固定为 `smtp.exmail.qq.com`（脚本已内置，无需手填）。
2. `SMTP_USER` 填完整企业邮箱地址，如 `zhangsan@yourcompany.com`。
3. `SMTP_PASSWORD` 填**邮箱登录密码**；若企业管理员在管理后台开启了「**安全登录**」，则需登录网页版邮箱 →「设置 → 邮箱绑定 → 安全登录」生成并填写「**客户端专用密码**」。

**163 / Gmail**
- 163：`SMTP_PASSWORD` 填「客户端授权密码」（网页版设置里开启 SMTP 后获取）。
- Gmail：需开启两步验证，`SMTP_PASSWORD` 填「应用专用密码」。

> 未填真实密码/授权码时脚本会进入“模拟发送”模式：只把邮件内容打印到控制台，不发真邮件，方便先跑通流程。
>
> 端口说明：脚本对 465 端口用 SSL，其它端口（如 587）自动走 STARTTLS，两种均可。

其余配置项（影院 ID、电影 ID、城市 ID、IMAX 关键词、轮询间隔）均已按本影院/本片填好，一般无需改动。

---

## 四、运行

```bash
# 前台常驻运行（默认每 5 分钟检查一次，Ctrl+C 停止）
python monitor.py

# 只检查一次后退出（适合测试，或交给系统 cron 定时触发）
python monitor.py --once

# 单次查询“指定某天”的《奥德赛》IMAX 场次，只打印到控制台（不发送邮件），然后退出
python monitor.py --report 2026-08-20
python monitor.py --report            # 不带日期则默认查今天

# 只发一封测试邮件验证邮箱配置，然后退出（排查邮件问题时用它）
python monitor.py --test-mail
```

> 注意：猫眼排片接口一次只返回「今天起约 6 天」的窗口，`--report` 指定的日期若超出该窗口（或更远的未来尚未开票），会打印“暂无场次/超出预售窗口”。

首次运行会输出类似：

```
[2026-08-15 00:27:44] 启动监控：影院=MOViE MOViE 影城（前滩太古里店）(37534) 电影=奥德赛(1545360) 城市=10
[2026-08-15 00:28:03] 首次运行：已建立基线，当前 IMAX 场次 N 个
[模拟发送] ... 主题: [奥德赛IMAX监控] 监测已启动 ...
```

出现新场次时会发邮件，正文列出日期/时间/影厅/版本/语言/售票状态，并附购票链接。

---

## 五、让它 7×24 小时后台常驻

### 方式 A：`nohup`（最简单）

```bash
nohup python monitor.py > monitor.log 2>&1 &
```

查看日志：`tail -f monitor.log`；停止：`pkill -f "python monitor.py"`。

### 方式 B：`tmux` / `screen`（推荐，方便随时查看）

```bash
tmux new -s my
python monitor.py
# 按 Ctrl+B 再按 D 脱离；tmux attach -t my 重新进入
```

### 方式 C：系统定时任务（每次只跑一次）

如果你更习惯 cron，可改用 `--once` 模式，让系统每 5 分钟触发一次：

```bash
crontab -e
# 追加一行（路径改成你的实际路径）：
*/5 * * * * cd /path/to/maoyan-monitor && /usr/bin/python3 monitor.py --once >> monitor.log 2>&1
```

---

## 六、常见问题

**Q：发信报 `535 ... authentication failed`？**
这是 SMTP 认证失败（不是脚本 bug，排片抓取是正常的）。用 `python monitor.py --test-mail` 单独验证邮件配置，并逐项检查：
1. `SMTP_PROVIDER` 必须与邮箱类型一致——QQ 个人邮箱用 `"qq"`，企业邮箱用 `"exmail"`，别混用。
2. QQ 个人邮箱的 `SMTP_PASSWORD` 填 **16 位授权码**（不是 QQ 登录密码）；一旦在网页上重新生成授权码，旧授权码立即失效，需同步更新。
3. 企业邮箱：若管理员开启了「安全登录」，需填「客户端专用密码」而非登录密码。
4. `SMTP_USER` 必须是**完整邮箱地址**，且授权码/密码复制时**不要带首尾空格**。

**Q：提示 `接口返回非 JSON（可能被猫眼风控拦截）`？**
脚本只用一个移动端 User-Agent 访问猫眼排片接口，通常正常；若被风控，请降低 `POLL_INTERVAL`、检查网络/代理，稍后重试。

**Q：邮件里没有票价？**
猫眼对票价 `sellPr` 做了 **stonefont 字体反爬**（把数字编码成私有区字符），脚本为保持“单文件 `requests` 依赖”不做价格解码，票价请以邮件里的购票链接页面为准。

**Q：想换影院或换电影？**
改 `config.py` 里的 `CINEMA_ID` / `MOVIE_ID` / `CITY_ID`。可用 `maoyan.py` 里的 `search_cinemas()` / `search_movies()` 方法查新 ID。

**Q：IMAX 识别不准？**
调整 `IMAX_KEYWORDS`。默认 `["IMAX"]`，能覆盖本影院的「IMAX 激光厅」与「IMAX2D」；如某些影城把 IMAX 标成别的名字，可自行追加关键词（如 `"激光"`）。

**Q：轮询间隔想改？**
改 `POLL_INTERVAL`（秒）。建议 ≥120 秒，避免给服务器造成压力。

---

## 七、免责声明

本脚本仅用于个人学习与自用提醒，请**控制请求频率**、勿用于商业或高频抓取，遵守猫眼服务条款及相关法律法规。
