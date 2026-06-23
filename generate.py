#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
エネルギーとお金の地図 — 経済×エネルギー 自動ダイジェスト生成スクリプト

やること:
  1. feeds.json のRSSを取得
  2. 新着記事を既存データと突き合わせて重複を除外
  3. 新着のみ要約を生成（ANTHROPIC_API_KEY があればClaudeで要約、無ければRSS本文を整形）
  4. 直近の記事をまとめて public/index.html と data/articles.json を書き出す

設計方針:
  - APIキーが無くても動く（無料運用が可能）。あれば要約品質が上がる。
  - 死んでいるフィードはスキップしてログに残す（全体は止めない）。
  - 1回の実行で新規要約数に上限を設け、API課金を制御する。
"""

import os
import re
import json
import html
import time
import hashlib
import datetime as dt
from pathlib import Path

import feedparser

# ---- 設定（環境変数で上書き可） -------------------------------------------
ROOT = Path(__file__).resolve().parent
FEEDS_FILE = ROOT / "feeds.json"
DATA_FILE = ROOT / "data" / "articles.json"
OUT_HTML = ROOT / "public" / "index.html"

MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "60"))            # サイトに残す最大件数
MAX_NEW_SUMMARIES = int(os.environ.get("MAX_NEW_SUMMARIES", "15"))  # 1回の要約上限（課金制御）
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
JST = dt.timezone(dt.timedelta(hours=9))


# ---- ユーティリティ --------------------------------------------------------
def clean_text(s: str, limit: int = 400) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)          # タグ除去
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def entry_id(link: str, title: str) -> str:
    return hashlib.sha1((link or title).encode("utf-8")).hexdigest()[:16]


def to_iso(entry) -> str:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime(*t[:6], tzinfo=dt.timezone.utc).astimezone(JST).isoformat()
    return dt.datetime.now(JST).isoformat()


# ---- 要約 ------------------------------------------------------------------
def summarize_with_claude(title: str, body: str) -> str:
    """Claude APIで2〜3文の日本語要約を作る。失敗時は空文字を返す。"""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=API_KEY)
        prompt = (
            "次のニュースを、日本語で2〜3文・全角120字以内に要約してください。"
            "事実のみを中立に、誇張や憶測は避ける。前置きや記号は不要、要約本文だけを返す。\n\n"
            f"見出し: {title}\n本文/抜粋: {body}"
        )
        msg = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        print(f"  [warn] 要約APIエラー: {e}")
        return ""


def make_summary(title: str, body: str, use_api: bool) -> str:
    if use_api:
        s = summarize_with_claude(title, body)
        if s:
            return s
    # フォールバック: RSS抜粋を整形（無料運用 or API失敗時）
    return clean_text(body, 140) or clean_text(title, 140)


# ---- メイン処理 ------------------------------------------------------------
def load_existing() -> dict:
    if DATA_FILE.exists():
        try:
            return {a["id"]: a for a in json.loads(DATA_FILE.read_text("utf-8"))}
        except Exception:
            return {}
    return {}


def fetch_all(feeds) -> list:
    items = []
    for f in feeds:
        name, url = f["name"], f["url"]
        print(f"[fetch] {name}")
        try:
            d = feedparser.parse(url)
            if d.bozo and not d.entries:
                print(f"  [skip] フィード解析失敗: {getattr(d, 'bozo_exception', '')}")
                continue
            for e in d.entries:
                link = e.get("link", "")
                title = clean_text(e.get("title", ""), 200)
                if not title:
                    continue
                items.append({
                    "id": entry_id(link, title),
                    "title": title,
                    "link": link,
                    "source": name,
                    "published": to_iso(e),
                    "raw": clean_text(e.get("summary", "") or e.get("description", ""), 400),
                })
        except Exception as ex:
            print(f"  [skip] 取得エラー: {ex}")
    return items


def build():
    feeds = json.loads(FEEDS_FILE.read_text("utf-8"))["feeds"]
    existing = load_existing()
    fetched = fetch_all(feeds)

    use_api = bool(API_KEY)
    print(f"[mode] 要約: {'Claude API' if use_api else 'RSS抜粋(無料)'}  モデル: {MODEL if use_api else '-'}")

    new_count = 0
    for it in fetched:
        if it["id"] in existing:
            continue
        if new_count < MAX_NEW_SUMMARIES:
            it["summary"] = make_summary(it["title"], it["raw"], use_api)
            new_count += 1
        else:
            it["summary"] = clean_text(it["raw"], 140) or it["title"]
        it.pop("raw", None)
        existing[it["id"]] = it

    print(f"[new] 新規記事: {new_count} 件")

    articles = sorted(existing.values(), key=lambda a: a["published"], reverse=True)[:MAX_ITEMS]
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(articles, ensure_ascii=False, indent=2), "utf-8")

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(render_html(articles), "utf-8")
    print(f"[done] {OUT_HTML} を更新（{len(articles)}件）")


# ---- HTML（公開ページ） ----------------------------------------------------
def render_html(articles) -> str:
    updated = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    cards = []
    last_day = None
    for a in articles:
        try:
            d = dt.datetime.fromisoformat(a["published"])
        except Exception:
            d = dt.datetime.now(JST)
        day = d.strftime("%m/%d")
        clock = d.strftime("%H:%M")
        day_sep = ""
        if day != last_day:
            day_sep = f'<li class="daymark"><span>{day}</span></li>'
            last_day = day
        cards.append(day_sep + f'''<li class="item">
      <div class="tick"><time>{clock}</time></div>
      <div class="card">
        <div class="src">{html.escape(a["source"])}</div>
        <h2><a href="{html.escape(a["link"])}" target="_blank" rel="noopener">{html.escape(a["title"])}</a></h2>
        <p>{html.escape(a.get("summary", ""))}</p>
      </div>
    </li>''')
    feed_html = "\n".join(cards)

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>エネルギーとお金の地図｜経済×エネルギー ダイジェスト</title>
<meta name="description" content="経済とエネルギーの最新ニュースを自動要約。原油・電力・為替・LNGの動きを毎日まとめてお届けします。">
<meta property="og:title" content="エネルギーとお金の地図">
<meta property="og:description" content="経済×エネルギーの最新ニュースを自動要約でお届け">
<meta property="og:type" content="website">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Zen+Kaku+Gothic+New:wght@500;700;900&family=Noto+Sans+JP:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{{
    --paper:#e9ebe4; --grid:#d6d9cf; --ink:#15181b; --muted:#5c6168;
    --crude:#e0590c;   /* エネルギー＝原油の炎 */
    --ledger:#0b6e5a;  /* お金＝台帳の緑 */
    --line:#c2c6ba;
  }}
  *{{box-sizing:border-box}}
  html{{-webkit-text-size-adjust:100%}}
  body{{
    margin:0; color:var(--ink); background:var(--paper);
    background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
    background-size:28px 28px; background-position:-1px -1px;
    font-family:"Noto Sans JP",system-ui,sans-serif; line-height:1.75;
  }}
  a{{color:inherit}}
  .wrap{{max-width:760px; margin:0 auto; padding:0 18px 80px}}

  /* マストヘッド */
  header{{padding:40px 0 22px; border-bottom:2px solid var(--ink); margin-bottom:8px}}
  .kicker{{font-family:"Roboto Mono",monospace; font-size:12px; letter-spacing:.18em;
    color:var(--crude); text-transform:uppercase; margin:0 0 8px}}
  h1{{font-family:"Zen Kaku Gothic New",sans-serif; font-weight:900;
    font-size:clamp(30px,8vw,54px); line-height:1.05; margin:0; letter-spacing:.01em}}
  h1 .amp{{color:var(--ledger)}}
  .tagline{{margin:12px 0 0; color:var(--muted); font-size:14px}}
  .meta{{font-family:"Roboto Mono",monospace; font-size:12px; color:var(--muted);
    margin-top:14px; display:flex; gap:14px; flex-wrap:wrap; align-items:center}}
  .dot{{width:7px;height:7px;border-radius:50%;background:var(--crude);display:inline-block;
    box-shadow:0 0 0 4px rgba(224,89,12,.15)}}

  /* 計器スケール（左の目盛り軸）＝シグネチャ */
  ul.feed{{list-style:none; margin:24px 0 0; padding:0; position:relative}}
  ul.feed::before{{content:""; position:absolute; left:54px; top:0; bottom:0;
    width:2px; background:var(--line)}}
  .item{{position:relative; padding:0 0 4px 78px; min-height:18px}}
  .tick{{position:absolute; left:0; top:6px; width:54px; text-align:right; padding-right:14px}}
  .tick time{{font-family:"Roboto Mono",monospace; font-size:12px; color:var(--muted)}}
  .item::before{{content:""; position:absolute; left:49px; top:11px; width:12px; height:2px; background:var(--line)}}
  .item::after{{content:""; position:absolute; left:50px; top:8px; width:9px; height:9px;
    border-radius:50%; background:var(--paper); border:2px solid var(--crude)}}

  .daymark{{position:relative; list-style:none; margin:26px 0 12px; padding-left:78px}}
  .daymark span{{font-family:"Roboto Mono",monospace; font-weight:500; font-size:12px;
    letter-spacing:.1em; color:#fff; background:var(--ink); padding:3px 9px; border-radius:2px}}

  .card{{padding:2px 0 18px}}
  .src{{font-family:"Roboto Mono",monospace; font-size:11px; letter-spacing:.04em;
    color:var(--ledger); margin-bottom:3px}}
  .card h2{{font-family:"Zen Kaku Gothic New",sans-serif; font-weight:700;
    font-size:18px; line-height:1.4; margin:0 0 6px}}
  .card h2 a{{text-decoration:none; background-image:linear-gradient(transparent 60%,rgba(224,89,12,.18) 0); }}
  .card h2 a:hover{{color:var(--crude)}}
  .card p{{margin:0; font-size:14.5px; color:#2b2f33}}

  footer{{margin-top:48px; padding-top:18px; border-top:1px solid var(--line);
    font-size:12px; color:var(--muted); line-height:1.9}}
  footer .pr{{font-family:"Roboto Mono",monospace; color:var(--crude)}}

  @media (max-width:480px){{
    .wrap{{padding:0 14px 60px}}
    ul.feed::before{{left:42px}} .item{{padding-left:60px}}
    .tick{{width:42px}} .item::before{{left:37px}} .item::after{{left:38px}}
    .daymark{{padding-left:60px}}
  }}
  @media (prefers-reduced-motion:no-preference){{
    .item{{animation:rise .4s ease both}}
    @keyframes rise{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <p class="kicker">経済 × エネルギー ダイジェスト</p>
      <h1>エネルギーと<span class="amp">お金</span>の地図</h1>
      <p class="tagline">原油・電力・為替・LNG──毎日の動きを自動で要約してまとめます。</p>
      <p class="meta"><span><span class="dot"></span> 自動更新</span><span>最終更新 {updated} JST</span></p>
    </header>

    <ul class="feed">
{feed_html}
    </ul>

    <footer>
      <span class="pr">［自動生成］</span> 本ページはRSSをもとに見出し・要約・出典リンクを自動でまとめたものです。要約は元記事の内容と異なる場合があります。正確な情報は各見出しのリンク先（出典：各メディア）をご確認ください。著作権は各配信元に帰属します。
    </footer>
  </div>
</body>
</html>'''


if __name__ == "__main__":
    build()
