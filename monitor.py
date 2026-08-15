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

import config
from maoyan import Maoyan

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, config.STATE_FILE if hasattr(config, "STATE_FILE") else "state.json")

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
            send_email(subject, build_start_body(shows))
        return

    if new_shows:
        for s in new_shows:
            seen[s["scheduleId"]] = now
        save_state(state)
        log("发现 {} 个新 IMAX 场次！".format(len(new_shows)))
        subject = "{} 发现 {} 个新场次".format(config.MAIL_SUBJECT_PREFIX, len(new_shows))
        send_email(subject, build_body(new_shows))
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
    parser.add_argument("--test-mail", action="store_true", help="只发一封测试邮件验证邮箱配置，然后退出")
    parser.add_argument("--report", nargs="?", const="today", default=None, metavar="YYYY-MM-DD",
                        help="单次查询指定某天(缺省=今天)的《奥德赛》IMAX 场次，只打印到控制台不发邮件，然后退出")
    args = parser.parse_args()

    log("启动监控：影院={}({}) 电影={}({}) 城市={}".format(
        config.CINEMA_NAME, config.CINEMA_ID, config.MOVIE_NAME, config.MOVIE_ID, config.CITY_ID))
    if is_dry_run() and args.report is None:
        log("提示：当前为模拟发送模式，请在 config.py 中填入真实密码/授权码以真正发邮件。")

    if args.test_mail:
        send_email("{} 测试邮件".format(config.MAIL_SUBJECT_PREFIX),
                   "这是一封测试邮件：如果你收到它，说明邮件配置正确。")
        log("测试邮件流程结束。")
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

    while True:
        try:
            check_once(client, state)
        except KeyboardInterrupt:
            log("已手动停止。")
            break
        except Exception as e:  # noqa: BLE001
            log("检查出错：{}".format(e))

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
