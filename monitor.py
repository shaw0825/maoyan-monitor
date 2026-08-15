# -*- coding: utf-8 -*-
"""
猫眼《奥德赛》IMAX 场次监控主程序

用法：
    python monitor.py            # 常驻循环，每隔 POLL_INTERVAL 秒检查一次
    python monitor.py --once     # 只检查一次后退出（适合测试 / cron 定时触发）
    python monitor.py --report [YYYY-MM-DD]   # 单查某天场次，只打印到控制台不发邮件
    python monitor.py --test-mail              # 只发一封测试邮件验证邮箱配置

首次运行会建立状态基线（不把已存在的场次当“新场次”），并按配置可选发送
一封“监测已启动”邮件。之后每次发现新的 IMAX 场次都会发邮件通知。

与淘票票版（taopiaopiao-monitor）的唯一区别是数据源换成了猫眼：
  - 排片接口：https://m.maoyan.com/mtrade/cinema/cinema/shows.json
  - 场次唯一 ID 用猫眼的 seqNo 做去重
"""

import argparse
import datetime
import json
import os
import smtplib
import ssl
import time
from email.header import Header
from email.mime.text import MIMEText

import requests

import config
from maoyan import Maoyan

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, config.STATE_FILE if hasattr(config, "STATE_FILE") else "state.json")

WECHAT_WEBHOOK_API = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"

# 购票链接（猫眼影院详情页）
CINEMA_DETAIL_URL = "https://m.maoyan.com/cinema/{}".format(config.CINEMA_ID)


# ---------- 日志 ----------
def log(msg):
    print("[{}] {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


# ---------- 状态持久化 ----------
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("seen", {})          # {seqNo: 首次发现时间戳}
    state.setdefault("initialized", False)
    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------- IMAX 判定 ----------
def is_imax(hall_name, show_version):
    s = "{} {}".format(hall_name or "", show_version or "").upper()
    return any(kw.upper() in s for kw in config.IMAX_KEYWORDS)


# ---------- 抓取并过滤 ----------
def fetch_imax_shows(client):
    rv = client.get_cinema_shows(config.CINEMA_ID, config.CITY_ID)
    movie = None
    for m in rv.get("movies", []):
        if str(m.get("id")) == str(config.MOVIE_ID):
            movie = m
            break

    shows = []
    if movie:
        for group in movie.get("shows", []):
            for p in group.get("plist", []):
                if is_imax(p.get("th", ""), p.get("tp", "")):
                    shows.append(
                        {
                            "scheduleId": str(p.get("seqNo")),
                            "showTime": "{} {}".format(p.get("dt", ""), p.get("tm", "")).strip(),
                            "hallName": p.get("th", ""),
                            "showVersion": p.get("tp", ""),
                            "lang": p.get("lang", ""),
                            "ticketStatus": p.get("ticketStatus"),   # 1=在售 0=待售
                        }
                    )
    # 按时间排序，并去掉可能的重复 seqNo
    uniq = {s["scheduleId"]: s for s in shows}
    return [uniq[k] for k in sorted(uniq, key=lambda k: uniq[k]["showTime"])]


def fetch_day_imax_shows(client, day):
    """单次查询后，只返回指定某天(day, YYYY-MM-DD)的《奥德赛》IMAX 场次。"""
    return [s for s in fetch_imax_shows(client) if s["showTime"].startswith(day)]


# ---------- 邮件 ----------
def _status(show):
    return "在售" if show.get("ticketStatus") == 1 else "待售"


def _version(show):
    # 版本 + 语言，如 "IMAX2D·英语" / "2D"
    ver = show["showVersion"] or ""
    if show.get("lang"):
        ver = "{}·{}".format(ver, show["lang"]) if ver else show["lang"]
    return ver or "-"


def _time_only(show_time):
    # showTime 形如 "2026-08-15 13:00"，当天快照里只显示 HH:MM
    return show_time[11:16] if len(show_time) >= 16 else show_time


def _format_show_line(s):
    return "  {}  {}  {}  [{}]".format(s["showTime"], s["hallName"], _version(s), _status(s))


def _format_day_line(s):
    return "  {}  {}  {}  [{}]".format(_time_only(s["showTime"]), s["hallName"], _version(s), _status(s))


def _tail():
    return [
        "",
        "购票链接：{}".format(CINEMA_DETAIL_URL),
        "",
        "（票价猫眼做了字体加密，请以购票页面显示为准）",
        "—— 本邮件由奥德赛IMAX监控脚本自动发送",
    ]


def build_body(new_shows):
    lines = [
        "《{}》在 {} 出现新的 IMAX 场次：\n".format(config.MOVIE_NAME, config.CINEMA_NAME),
    ]
    for s in new_shows:
        lines.append(_format_show_line(s))
    lines.extend(_tail())
    return "\n".join(lines)


def build_start_body(shows):
    lines = ["监控脚本已启动。\n当前《{}》IMAX 场次共 {} 个：\n".format(config.MOVIE_NAME, len(shows))]
    for s in shows:
        lines.append(_format_show_line(s))
    if not shows:
        lines.append("  （暂无）")
    lines.extend(_tail())
    return "\n".join(lines)


def is_dry_run():
    return (not config.SMTP_PASSWORD) or config.SMTP_PASSWORD.startswith("请填写") or config.SMTP_PASSWORD.startswith("你的")


def _smtp_host_port():
    """根据 SMTP_PROVIDER 解析 SMTP 服务器与端口。"""
    provider = getattr(config, "SMTP_PROVIDER", "custom")
    if provider == "custom":
        return config.SMTP_HOST, config.SMTP_PORT
    preset = getattr(config, "SMTP_PRESETS", {}).get(provider)
    if not preset:
        raise ValueError("未知的 SMTP_PROVIDER: {}（可选：{}）".format(
            provider, ", ".join(getattr(config, "SMTP_PRESETS", {}).keys())))
    return preset[0], preset[1]


def send_email(subject, body):
    if is_dry_run():
        log("[模拟发送] 未配置真实密码/授权码，仅打印邮件内容：")
        log("-" * 50)
        log("收件人: {}".format(", ".join(config.MAIL_TO)))
        log("主题: {}".format(subject))
        log("正文:\n{}".format(body))
        log("-" * 50)
        return

    host, port = _smtp_host_port()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = config.SMTP_USER
    msg["To"] = ", ".join(config.MAIL_TO)

    ctx = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
    else:
        # 587 等端口走 STARTTLS
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
    try:
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.SMTP_USER, config.MAIL_TO, msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError(
            "SMTP 登录认证失败(535)，请检查 config.py 邮件配置："
            "SMTP_PROVIDER 要与邮箱类型一致（qq/exmail/163/gmail）；"
            "QQ个人邮箱必须填16位“授权码”而非登录密码；"
            "腾讯企业邮箱填邮箱密码或“客户端专用密码”。"
            "原始错误：{}".format(e)
        )
    finally:
        server.quit()
    log("邮件已发送: {}".format(subject))


# ---------- 企业微信群机器人 ----------
def _wechat_webhook_url():
    """从配置解析出完整的 Webhook 地址。"""
    if getattr(config, "WECHAT_BOT_WEBHOOK", ""):
        return config.WECHAT_BOT_WEBHOOK.strip()
    key = (getattr(config, "WECHAT_BOT_KEY", "") or "").strip()
    if not key:
        raise ValueError("WECHAT_BOT_WEBHOOK / WECHAT_BOT_KEY 均未配置，无法推送微信")
    return "{0}?key={1}".format(WECHAT_WEBHOOK_API, key)


def wechat_dry_run():
    """未启用或未填 key 时视为模拟模式。"""
    return not (
        getattr(config, "WECHAT_BOT_ENABLED", False)
        and (getattr(config, "WECHAT_BOT_WEBHOOK", "") or getattr(config, "WECHAT_BOT_KEY", ""))
    )


def send_wechat_text(content):
    """向企业微信群机器人推送一条消息（纯文本，≤2000字节，超长自动截断）。

    content: str，纯文本内容。
    """
    if wechat_dry_run():
        log("[模拟推送] 企业微信机器人未启用或未配置，仅打印内容：")
        log("-" * 50)
        log(content)
        log("-" * 50)
        return

    url = _wechat_webhook_url()
    msgtype = (getattr(config, "WECHAT_BOT_MSGTYPE", "text") or "text").lower()
    if msgtype == "markdown":
        payload = {"msgtype": "markdown", "markdown": {"content": content[:4096]}}
    else:
        payload = {"msgtype": "text", "text": {"content": content[:2000]}}
    resp = requests.post(url, json=payload, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    j = resp.json()
    if j.get("errcode") != 0:
        raise RuntimeError("企业微信机器人推送失败: errcode={} errmsg={}".format(
            j.get("errcode"), j.get("errmsg")))


# ---------- 统一通知（邮件 + 企业微信机器人） ----------
def notify(subject, body):
    """同时通过所有已启用的渠道发送通知；某个渠道失败不影响其它渠道。"""
    errors = []

    # 1) 邮件（保持原有行为：未配置授权码时自动进入模拟发送）
    try:
        send_email(subject, body)
    except Exception as e:  # noqa: BLE001
        errors.append("邮件: {}".format(e))

    # 2) 企业微信群机器人
    if not wechat_dry_run():
        try:
            # 标题加粗一行 + 正文，适合 markdown；text 类型也无损兼容
            send_wechat_text("**{}**\n{}".format(subject, body))
        except Exception as e:  # noqa: BLE001
            errors.append("企业微信: {}".format(e))

    if errors:
        raise RuntimeError("部分通知渠道发送失败 -> " + " | ".join(errors))


# ---------- 抓取失败告警 ----------
FAILURE_NOTIFY_COOLDOWN = getattr(config, "FAILURE_NOTIFY_COOLDOWN", 3600)  # 秒；两次失败告警之间的最小间隔


def notify_check_failure(err, last_notify_ts):
    """抓取持续失败（短重试已用尽）时，按冷却时间发送一次失败告警，返回最新告警时间戳。"""
    now = int(time.time())
    if now - last_notify_ts < FAILURE_NOTIFY_COOLDOWN:
        return last_notify_ts

    subject = "{} 抓取失败告警".format(config.MAIL_SUBJECT_PREFIX)
    body = (
        "监控抓取猫眼排片失败（短重试已用尽）：\n\n"
        "  时间：{}\n"
        "  错误：{}\n\n"
        "脚本会继续按 {} 秒间隔重试；若持续失败，请检查网络/代理，或降低轮询频率以免被猫眼风控。\n\n"
        "—— 本邮件由奥德赛IMAX监控脚本自动发送"
    ).format(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        err,
        config.POLL_INTERVAL,
    )
    try:
        notify(subject, body)
        return now
    except Exception as ne:  # noqa: BLE001
        log("失败告警发送出错：{}".format(ne))
        return last_notify_ts


# ---------- 单次检查 ----------
def check_once(client, state):
    shows = fetch_imax_shows(client)
    now = int(time.time())
    seen = state["seen"]

    new_shows = [s for s in shows if s["scheduleId"] not in seen]
    if not state["initialized"]:
        # 首次运行：建立基线，不把现有场次当“新场次”
        state["initialized"] = True
        for s in shows:
            seen.setdefault(s["scheduleId"], now)
        save_state(state)
        log("首次运行：已建立基线，当前 IMAX 场次 {} 个".format(len(shows)))
        if config.NOTIFY_ON_START:
            subject = "{} 监测已启动".format(config.MAIL_SUBJECT_PREFIX)
            notify(subject, build_start_body(shows))
        return

    if new_shows:
        for s in new_shows:
            seen[s["scheduleId"]] = now
        save_state(state)
        log("发现 {} 个新 IMAX 场次！".format(len(new_shows)))
        subject = "{} 发现 {} 个新场次".format(config.MAIL_SUBJECT_PREFIX, len(new_shows))
        notify(subject, build_body(new_shows))
    else:
        log("无新增 IMAX 场次（当前共 {} 个在售/待售）".format(len(shows)))

    # 清理已过期很久的历史 ID，避免状态文件无限增长
    cutoff = now - 30 * 24 * 3600
    for sid in [k for k, v in seen.items() if v < cutoff]:
        del seen[sid]


# ---------- 主入口 ----------
def main():
    parser = argparse.ArgumentParser(description="猫眼《奥德赛》IMAX 场次监控")
    parser.add_argument("--once", action="store_true", help="只检查一次后退出")
    parser.add_argument("--test-mail", action="store_true", help="发一封测试通知（邮件+企业微信机器人）验证配置，然后退出")
    parser.add_argument("--report", nargs="?", const="today", default=None, metavar="YYYY-MM-DD",
                        help="单次查询指定某天(缺省=今天)的《奥德赛》IMAX 场次，只打印到控制台不发邮件，然后退出")
    args = parser.parse_args()

    log("启动监控：影院={}({}) 电影={}({}) 城市={}".format(
        config.CINEMA_NAME, config.CINEMA_ID, config.MOVIE_NAME, config.MOVIE_ID, config.CITY_ID))
    if (is_dry_run() or wechat_dry_run()) and args.report is None and not args.test_mail:
        if is_dry_run():
            log("提示：邮件为模拟发送模式，请在 config.py 中填入真实密码/授权码以真正发邮件。")
        if wechat_dry_run():
            log("提示：企业微信机器人未启用（WECHAT_BOT_ENABLED=False 或未填 KEY），如需微信推送请在 config.py 中配置。")

    if args.test_mail:
        notify("{} 测试通知".format(config.MAIL_SUBJECT_PREFIX),
               "这是一条测试通知：如果你收到它（邮件或微信），说明通知配置正确。")
        log("测试通知流程结束。")
        return

    if args.report is not None:
        day = args.report
        if day in ("", "today", "今天"):
            day = datetime.date.today().isoformat()
        else:
            try:
                day = datetime.date.fromisoformat(day).isoformat()
            except ValueError:
                log("日期格式错误：{}（应为 YYYY-MM-DD，例如 2026-08-20）".format(args.report))
                return
        client = Maoyan(timeout=config.REQUEST_TIMEOUT)
        shows = fetch_day_imax_shows(client, day)
        # 只查询返回场次信息并打印到控制台，不发送邮件
        print("《{}》{} IMAX 场次（共 {} 场）：".format(config.MOVIE_NAME, day, len(shows)))
        if shows:
            for s in shows:
                print(_format_day_line(s))
        else:
            print("  （{} 暂无《{}》IMAX 场次，可能尚未开票或超出预售窗口）".format(day, config.MOVIE_NAME))
        return

    client = Maoyan(timeout=config.REQUEST_TIMEOUT)
    state = load_state()
    last_failure_notify = 0

    while True:
        try:
            check_once(client, state)
        except KeyboardInterrupt:
            log("已手动停止。")
            break
        except Exception as e:  # noqa: BLE001
            log("检查出错：{}".format(e))
            last_failure_notify = notify_check_failure(e, last_failure_notify)

        if args.once:
            break
        log("等待 {} 秒后进行下一次检查...".format(config.POLL_INTERVAL))
        try:
            time.sleep(config.POLL_INTERVAL)
        except KeyboardInterrupt:
            log("已手动停止。")
            break


if __name__ == "__main__":
    main()
