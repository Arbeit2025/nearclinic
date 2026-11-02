# ============================================================
# nearclinic（近所の病院・クリニック検索）
#
# 目次
#  1. 設定・定数
#  2. 距離計算・住所組み立てなどのユーティリティ
#  3. 外部API（Nominatim / Overpass API）呼び出し
#  4. 検索結果のパース
#  5. 画面（HTML）とルーティング
#
# 地名・駅名を入力すると、OpenStreetMap のデータから
# 周辺の病院・クリニック・診療所を距離順に一覧表示する、
# 単一ファイル Flask アプリ（DB なし）。
# ============================================================

import json
import math
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, render_template_string, request

# ============================
# 1. 設定・定数
# ============================

app = Flask(__name__)

USER_AGENT = "nearclinic/1.0 (+https://github.com/Arbeit2025/nearclinic)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Overpass API は本家（overpass-api.de）が混雑やクラウドIP制限で
# 応答しない／極端に遅いことがあるため、ミラーも含めて順に試す。
OVERPASS_URLS = (
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

RADIUS_CHOICES_KM = (1, 2, 3, 5)
DEFAULT_RADIUS_KM = 2
MAX_RESULTS = 100

AMENITY_LABELS = {
    "hospital": "病院",
    "clinic": "クリニック",
    "doctors": "診療所",
}


class SearchError(Exception):
    """ユーザー向けの分かりやすいメッセージを持つ検索エラー。"""


# ============================
# 2. 距離計算・住所組み立てなどのユーティリティ
# ============================


def haversine_m(lat1, lon1, lat2, lon2):
    """2点間の距離（メートル）を返す。"""
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def format_distance(m):
    """距離を見やすい文字列にする（例: 約850m / 約1.2km）。"""
    if m < 1000:
        return f"約{int(round(m / 10.0)) * 10}m"
    return f"約{m / 1000.0:.1f}km"


def build_address(tags):
    """OSM タグから住所文字列を組み立てる。無ければ空文字。"""
    if tags.get("addr:full"):
        return tags["addr:full"]
    parts = []
    for key in (
        "addr:province",
        "addr:city",
        "addr:quarter",
        "addr:neighbourhood",
        "addr:block_number",
        "addr:housenumber",
    ):
        if tags.get(key):
            parts.append(tags[key])
    if parts:
        return "".join(parts)
    return tags.get("addr:street", "")


# ============================
# 3. 外部API（Nominatim / Overpass API）呼び出し
# ============================


def _request_json(url, data=None, timeout=15):
    """JSON を返す API を呼ぶ。失敗したら SearchError にする。"""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ja"}
    if data is not None:
        body = urllib.parse.urlencode({"data": data}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers)
    else:
        req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SearchError(
            "検索サービスに接続できませんでした。時間をおいてもう一度お試しください。"
        ) from exc


def geocode(place):
    """地名・駅名を緯度経度に変換する。(lat, lon, 表示名) を返す。"""
    query = urllib.parse.urlencode(
        {"q": place, "format": "jsonv2", "limit": 1, "addressdetails": 1}
    )
    results = _request_json(f"{NOMINATIM_URL}?{query}", timeout=10)
    if not results:
        raise SearchError(
            f"「{place}」が見つかりませんでした。地名や駅名を変えてお試しください。"
        )
    first = results[0]
    try:
        lat = float(first["lat"])
        lon = float(first["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SearchError("場所の情報を取得できませんでした。") from exc
    return lat, lon, first.get("display_name", place)


def build_overpass_query(lat, lon, radius_m):
    """病院・クリニック・診療所を半径内検索する Overpass クエリ。"""
    return (
        "[out:json][timeout:25];"
        "("
        f'node["amenity"~"^(hospital|clinic|doctors)$"](around:{radius_m},{lat},{lon});'
        f'way["amenity"~"^(hospital|clinic|doctors)$"](around:{radius_m},{lat},{lon});'
        f'relation["amenity"~"^(hospital|clinic|doctors)$"](around:{radius_m},{lat},{lon});'
        ");"
        "out center tags;"
    )


def fetch_places(lat, lon, radius_km):
    """Overpass API で医療機関を検索し、距離順の一覧を返す。

    複数の Overpass エンドポイントを順に試し、最初に成功した結果を使う。
    すべて失敗したら最後の SearchError を投げる。
    """
    radius_m = int(radius_km * 1000)
    query = build_overpass_query(lat, lon, radius_m)
    last_error = None
    for url in OVERPASS_URLS:
        try:
            data = _request_json(url, data=query, timeout=20)
            return parse_places(data, lat, lon)
        except SearchError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise SearchError("検索サービスに接続できませんでした。時間をおいてもう一度お試しください。")


# ============================
# 4. 検索結果のパース
# ============================


def parse_places(data, origin_lat, origin_lon):
    """Overpass の応答 JSON を表示用の一覧（距離順）に変換する。"""
    places = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        amenity = tags.get("amenity", "")
        if amenity not in AMENITY_LABELS:
            continue
        if "lat" in el and "lon" in el:
            elat, elon = el["lat"], el["lon"]
        elif "center" in el:
            elat, elon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        dist = haversine_m(origin_lat, origin_lon, elat, elon)
        label = AMENITY_LABELS[amenity]
        places.append(
            {
                "name": tags.get("name") or f"{label}（名称未登録）",
                "kind": label,
                "address": build_address(tags),
                "distance_m": dist,
                "distance_label": format_distance(dist),
                "opening_hours": tags.get("opening_hours", ""),
                "phone": tags.get("phone") or tags.get("contact:phone", ""),
                "website": tags.get("website") or tags.get("contact:website", ""),
            }
        )
    places.sort(key=lambda p: p["distance_m"])
    # OSM 側で同一施設が二重登録されている場合があるため、
    # 同名で距離が 100m 以内のものは近い方だけを残す。
    deduped = []
    for p in places:
        if any(
            k["name"] == p["name"] and abs(k["distance_m"] - p["distance_m"]) < 100
            for k in deduped
        ):
            continue
        deduped.append(p)
    return deduped[:MAX_RESULTS]


# ============================
# 5. 画面（HTML）とルーティング
# ============================

INDEX_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nearclinic（近所の病院・クリニック検索）</title>
<style>
  body { font-family: sans-serif; margin: 0; background: #f6f8f7; color: #223; }
  .wrap { max-width: 760px; margin: 0 auto; padding: 20px 16px 48px; }
  h1 { font-size: 1.4rem; margin: 8px 0 4px; }
  .sub { color: #567; font-size: .9rem; margin-bottom: 16px; }
  form.search { background: #fff; border: 1px solid #dde5e1; border-radius: 10px; padding: 14px; display: flex; flex-wrap: wrap; gap: 8px; }
  form.search input[type="text"] { flex: 1 1 220px; padding: 10px; font-size: 1rem; border: 1px solid #bcc; border-radius: 8px; }
  form.search select { padding: 10px; font-size: 1rem; border: 1px solid #bcc; border-radius: 8px; }
  form.search button { padding: 10px 18px; font-size: 1rem; border: 0; border-radius: 8px; background: #2b7a5b; color: #fff; cursor: pointer; }
  .note { background: #fff8e6; border: 1px solid #ecd9a0; border-radius: 10px; padding: 10px 14px; font-size: .88rem; margin: 16px 0; line-height: 1.6; }
  .note strong { color: #a33; }
  .error { background: #fdeaea; border: 1px solid #e5b4b4; border-radius: 10px; padding: 10px 14px; margin: 16px 0; }
  .found { color: #456; margin: 14px 0 6px; font-size: .9rem; word-break: break-all; }
  .card { background: #fff; border: 1px solid #dde5e1; border-radius: 10px; padding: 12px 14px; margin: 10px 0; }
  .card h2 { font-size: 1.05rem; margin: 0 0 4px; }
  .kind { display: inline-block; background: #e3f0e9; color: #256a4c; border-radius: 6px; padding: 1px 8px; font-size: .78rem; margin-right: 6px; }
  .dist { color: #256a4c; font-weight: bold; font-size: .9rem; }
  .meta { color: #456; font-size: .88rem; line-height: 1.7; word-break: break-all; }
  .empty { color: #567; margin: 18px 0; }
  footer { color: #789; font-size: .8rem; margin-top: 28px; line-height: 1.6; }
</style>
</head>
<body>
<div class="wrap">
  <h1>🏥 nearclinic</h1>
  <div class="sub">近所の病院・クリニック検索（データ：OpenStreetMap）</div>

  <form class="search" method="get" action="/">
    <input type="text" name="q" value="{{ q }}" placeholder="地名・駅名（例：八王子駅）" maxlength="80">
    <select name="radius">
      {% for r in radius_choices %}
      <option value="{{ r }}" {% if r == radius %}selected{% endif %}>半径{{ r }}km</option>
      {% endfor %}
    </select>
    <button type="submit">検索</button>
  </form>

  <div class="note">
    ⚠️ このツールの情報は OpenStreetMap の登録データに基づきます。
    <strong>診療科・診療時間・休診日・待ち時間などの情報は、登録されていないことも多く、実際と異なる場合があります。</strong>
    受診の前に、必ず公式サイトや電話でご確認ください。
    <strong>緊急時は 119 番</strong>へ連絡してください。
  </div>

  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}

  {% if searched and not error %}
    <div class="found">📍 {{ place_name }} の半径{{ radius }}km圏内：{{ places|length }}件</div>
    {% if places %}
      {% for p in places %}
      <div class="card">
        <h2><span class="kind">{{ p.kind }}</span>{{ p.name }} <span class="dist">{{ p.distance_label }}</span></h2>
        <div class="meta">
          {% if p.address %}住所：{{ p.address }}<br>{% endif %}
          診療時間：{{ p.opening_hours if p.opening_hours else "登録なし（公式サイト等でご確認ください）" }}<br>
          {% if p.phone %}電話：{{ p.phone }}<br>{% endif %}
          {% if p.website %}Web：<a href="{{ p.website }}" rel="noopener">{{ p.website }}</a>{% endif %}
        </div>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty">この範囲に登録されている病院・クリニックは見つかりませんでした。半径を広げてお試しください。</div>
    {% endif %}
  {% endif %}

  <footer>
    データ出典：© OpenStreetMap contributors（ジオコーディング：Nominatim、検索：Overpass API）。
    本ツールはデータベースを持たず、検索結果を保存しません。
  </footer>
</div>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    q = (request.args.get("q") or "").strip()
    try:
        radius = int(request.args.get("radius", DEFAULT_RADIUS_KM))
    except (TypeError, ValueError):
        radius = DEFAULT_RADIUS_KM
    if radius not in RADIUS_CHOICES_KM:
        radius = DEFAULT_RADIUS_KM

    places = []
    place_name = ""
    error = ""
    searched = False
    if q:
        searched = True
        try:
            lat, lon, place_name = geocode(q)
            places = fetch_places(lat, lon, radius)
        except SearchError as exc:
            error = str(exc)
    return render_template_string(
        INDEX_HTML,
        q=q,
        radius=radius,
        radius_choices=RADIUS_CHOICES_KM,
        places=places,
        place_name=place_name,
        error=error,
        searched=searched,
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    import os

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
