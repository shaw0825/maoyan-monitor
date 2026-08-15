# -*- coding: utf-8 -*-
"""
猫眼《奥德赛》IMAX 场次监控 —— 配置文件

修改本文件后重新运行 monitor.py 即可，无需改其它代码。
"""

# =============================
# 1. 监控目标（已为你填好，一般无需修改）
# =============================
CINEMA_ID = "37534"       # MOViE MOViE 影城（前滩太古里店）
MOVIE_ID = "1545360"      # 电影《奥德赛》(The Odyssey, 2026 诺兰版)
CITY_ID = 10              # 上海城市编码（猫眼上海 = 10）
CINEMA_NAME = "MOViE MOViE 影城（前滩太古里店）"   # 仅用于邮件正文展示
MOVIE_NAME = "奥德赛"      # 仅用于邮件标题/正文展示

# IMAX 判定关键词：影厅名(th)或版本(tp)命中任一关键词即视为 IMAX 场次（不区分大小写）
# 猫眼返回的 IMAX 场次形如：th="IMAX 激光厅"、tp="IMAX2D"；普通场次为 th="2号厅"、tp="2D"
IMAX_KEYWORDS = ["IMAX"]

# =============================
# 2. 运行参数
# =============================
POLL_INTERVAL = 300            # 轮询间隔（秒），默认 5 分钟。热映期可设 120~300。
NOTIFY_ON_START = True         # 首次运行是否发一封“监测已启动”邮件（用于验证邮箱配置）
REQUEST_TIMEOUT = 20           # 单次请求超时（秒）
STATE_FILE = "state.json"      # 已见场次的状态文件（脚本目录下）

# =============================
# 3. 邮件通知（支持 QQ 邮箱 / 腾讯企业邮箱 / 163 / Gmail / 自定义）
# =============================
# 选择发件邮箱类型，脚本会自动匹配对应的 SMTP 服务器：
#   "qq"      QQ 个人邮箱             smtp.qq.com
#   "exmail"  腾讯企业邮箱(QQ企业邮箱)  smtp.exmail.qq.com
#   "163"     网易163邮箱             smtp.163.com
#   "gmail"   Gmail                  smtp.gmail.com
#   "custom"  自定义（使用下面的 SMTP_HOST / SMTP_PORT）
SMTP_PROVIDER = "qq"

SMTP_PRESETS = {
    "qq":     ("smtp.qq.com",        465),
    "exmail": ("smtp.exmail.qq.com", 465),
    "163":    ("smtp.163.com",       465),
    "gmail":  ("smtp.gmail.com",     465),
}

# 仅当 SMTP_PROVIDER = "custom" 时，下面两项才会生效
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465                # 465=SSL；也支持 587(STARTTLS)

# 发件邮箱地址 + 密码/授权码（各服务商填法不同）：
#   - QQ 个人邮箱：填“授权码”（设置->账户->开启SMTP->生成，16位），不是登录密码
#   - 腾讯企业邮箱：填邮箱登录密码；若企业管理员开启了“安全登录”，则填“客户端专用密码”
#   - 163：填“客户端授权密码”；Gmail：填“应用专用密码”
SMTP_USER = "your_qq@qq.com"      # 发件邮箱地址（企业邮箱填类似 name@yourcompany.com）
SMTP_PASSWORD = "请填写授权码或密码"  # ← 务必改成你的
MAIL_TO = ["your_qq@qq.com"]      # 收件邮箱，可填多个，例如 ["a@qq.com", "b@163.com"]
MAIL_SUBJECT_PREFIX = "[奥德赛IMAX监控]"

# 若 SMTP_PASSWORD 仍为占位符，脚本会自动进入“模拟发送”模式（只打印邮件内容，不真正发送），
# 方便先跑通流程，等填好授权码后再真正发邮件。
