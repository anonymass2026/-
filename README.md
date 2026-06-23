# エネルギーとお金の地図 — 経済×エネルギー 自動ダイジェスト

経済・エネルギーのニュースをRSSから取得し、要約＋出典リンクを付けて静的サイトを自動更新する一式です。
**GitHubにpush → GitHub Actionsが定時実行 → Cloudflare Pagesが自動公開** という流れで動きます。

## ファイル構成

```
generate.py                  本体（RSS取得→要約→HTML生成）
feeds.json                   購読するRSSの一覧（自由に編集可）
requirements.txt             依存ライブラリ
.github/workflows/update.yml 定時自動実行の設定（3時間ごと）
public/index.html            生成される公開ページ（初回実行で上書き）
data/articles.json           記事データの保存先（自動生成・自動commit）
```

## セットアップ手順

### 1. リポジトリに置く
このフォルダ一式を新規GitHubリポジトリに入れてpushします。

### 2.（任意）要約の品質を上げる場合だけ：APIキーを登録
- キー未登録でも動きます（RSSの抜粋をそのまま要約として使用＝**完全無料**）。
- Claudeで要約させたい場合のみ、リポジトリの
  **Settings → Secrets and variables → Actions → New repository secret** で
  `ANTHROPIC_API_KEY` を登録（値は外部に出さない／コードに直書きしない）。
- 1回の実行で要約する新規記事数は `MAX_NEW_SUMMARIES`（既定15）で上限管理。課金はごく少額（Haikuモデル想定）。

### 3. 初回実行
GitHubの **Actions タブ → update-digest → Run workflow** を手動実行。
`public/index.html` と `data/articles.json` が生成・commitされます。

### 4. Cloudflare Pages に接続して公開
- Cloudflare → **Workers & Pages → Create → Pages → Connect to Git** で当リポジトリを選択。
- **Build command：空欄**（ビルド不要の静的サイト）
- **Build output directory：`public`**
- 以後、Actionsがpushするたびに自動で再デプロイされます。

## カスタマイズ

- **テーマ変更：** `feeds.json` の `url` を編集。Googleニュース検索RSSは
  `https://news.google.com/rss/search?q=【キーワード】&hl=ja&gl=JP&ceid=JP:ja` 形式で
  任意の話題に絞れます（例：`原油 OR 石油`、`電気料金`、`円相場`）。`when:3d` で直近3日に限定。
- **更新頻度：** `.github/workflows/update.yml` の `cron` を変更。
- **掲載件数：** 環境変数 `MAX_ITEMS`（既定60）。

## 注意・リスク（事業利用の前提）

- **著作権：** 掲載するのは「短い要約＋元記事リンク」に限定。本文の丸ごと転載はしない。
  出典（配信元）はページに明記済み。
- **景品表示法（ステマ規制）：** 将来アフィリエイトリンクを足す場合は「PR」「広告」表記を必須に。
  フッターに自動生成・出典の明示を入れてあります。
- **無料運用の限界：** Googleニュース等のRSSは仕様変更があり得ます。死んだフィードは
  自動でスキップしログに残るので、定期的に `feeds.json` を見直してください。
- **APIキー管理：** キーは必ず GitHub Secrets に保存し、公開リポジトリのコードに書かない。

## ローカルで試す

```bash
pip install -r requirements.txt
python generate.py          # APIキー無し＝無料モード
# 要約をClaudeで：
ANTHROPIC_API_KEY=sk-xxxx python generate.py
```
