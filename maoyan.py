# -*- coding: utf-8 -*-
"""
猫眼电影 H5 接口客户端（纯 requests 实现，无需浏览器）。

已逆向要点：
  - 影院排片（核心）：
        GET https://m.maoyan.com/mtrade/cinema/cinema/shows.json
        参数：ci=城市ID、cinemaId=影院ID、channelId=4、
              optimus_risk_level=71、optimus_code=10
        返回该影院「今天起约 6 天」的全部电影与场次，结构：
        data.movies[] -> { id, nm, dur, showCount, shows[] }
        movie.shows[]  -> { showDate, plist[] }
        show.plist[]   -> 场次 { seqNo, dt, tm, th(影厅), tp(版本), lang(语言),
                                 ticketStatus(1=在售/0=待售), sellPr(价格,字体加密) }
  - 搜索电影：GET https://m.maoyan.com/searchlist/movies?keyword=&ci=&offset=1&limit=20
  - 搜索影院：GET https://m.maoyan.com/searchlist/cinemas?keyword=&ci=&offset=1&limit=20
  - 电影详情：GET https://i.maoyan.com/ajax/detailmovie?movieId=
  - 影院详情：GET https://m.maoyan.com/api/mtrade/mmcs/cinema/v1/cinema.json?cinemaId=

  只需一个移动端 User-Agent，无需登录、无需签名。票价 sellPr 使用 stonefont
  字体做了反爬编码，本客户端不解析价格（票价以猫眼页面为准）。
"""

import time

import requests

HOST = "https://m.maoyan.com"
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1"
)

# 更接近真实移动端浏览器的请求头，降低被猫眼风控掐断的概率。
# 注意 Accept-Encoding 只声明 gzip/deflate（requests 会自动解压），
# 不声明 br，避免服务端返回 brotli 后 requests 无法自动解压。
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": HOST + "/",
    "Connection": "keep-alive",
}

# 短重试 + 指数退避：应对猫眼偶发的 SSL EOF / 连接重置 / 风控页。
# 第 1、2、3 次失败后分别等待 RETRY_BACKOFF 对应的秒数（最后一次失败后直接抛出）。
MAX_RETRIES = 3
RETRY_BACKOFF = (1.0, 2.0, 4.0)


class MaoyanError(RuntimeError):
    pass


class Maoyan:
    def __init__(self, timeout=20):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    # ---------- 底层请求（带短重试 + 指数退避） ----------
    def _get(self, url, params):
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    return resp.json()
                # 返回了 HTML（多半是风控页）：当作可重试的临时失败
                last_err = MaoyanError(
                    "接口返回非 JSON（可能被猫眼风控拦截），Content-Type={}".format(ct)
                )
            except requests.exceptions.HTTPError:
                # 明确的 4xx/5xx 不重试，直接抛出
                raise
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError) as e:
                last_err = e

            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                time.sleep(wait)

        raise MaoyanError("请求失败（已重试 {} 次）: {}".format(MAX_RETRIES, last_err))

    # ---------- 业务接口 ----------
    def get_cinema_shows(self, cinema_id, city_id):
        """
        获取影院排片：返回「今天起约 6 天」的全部电影与场次。
        返回结构：{cinemaId, cinemaName, movies:[{id,nm,dur,showCount,shows:[{showDate,plist:[...]}]}]}
        """
        j = self._get(
            HOST + "/mtrade/cinema/cinema/shows.json",
            {
                "ci": str(city_id),
                "cinemaId": str(cinema_id),
                "channelId": "4",
                "optimus_risk_level": "71",
                "optimus_code": "10",
            },
        )
        if j.get("code") != 0:
            raise MaoyanError("排片接口失败: code={}".format(j.get("code")))
        return j.get("data") or {}

    def search_movies(self, keyword, city_id, offset=1, limit=20):
        """按关键字搜电影，返回 {type, total, movies:[...]}。offset 从 1 起。"""
        return self._get(
            HOST + "/searchlist/movies",
            {"keyword": keyword, "ci": str(city_id), "offset": str(offset), "limit": str(limit)},
        )

    def search_cinemas(self, keyword, city_id, offset=1, limit=20):
        """按关键字搜影院，返回 {type, total, cinemas:[...]}。offset 从 1 起。"""
        return self._get(
            HOST + "/searchlist/cinemas",
            {"keyword": keyword, "ci": str(city_id), "offset": str(offset), "limit": str(limit)},
        )

    def get_movie_detail(self, movie_id):
        """电影详情，返回 {detailMovie:{...}}。"""
        return self._get("https://i.maoyan.com/ajax/detailmovie", {"movieId": str(movie_id)})

    def get_cinema_detail(self, cinema_id):
        """影院详情，返回 {code, data:{...}}。"""
        j = self._get(
            HOST + "/api/mtrade/mmcs/cinema/v1/cinema.json",
            {"cinemaId": str(cinema_id)},
        )
        if j.get("code") != 0:
            raise MaoyanError("影院详情接口失败: code={}".format(j.get("code")))
        return j.get("data") or {}
