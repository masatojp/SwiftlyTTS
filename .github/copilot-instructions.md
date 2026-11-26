# Copilot Instructions for SwiftlyTTS

## 概要
SwiftlyTTSは、Discordボイスチャンネル向けの高性能TTS（Text-to-Speech）Botです。Python（discord.py, FastAPI, asyncpg）とNext.js（Web UI）で構成され、VOICEVOXエンジンとPostgreSQLを利用します。

## アーキテクチャ
- **bot.py**: Discordボット本体。AutoShardedBotでコグ（cogs/）を動的ロード。FastAPIサーバー（lib/bot_http_server.py）をバックグラウンドで起動し、Web UIと連携。
- **cogs/**: 機能ごとに分割されたBot拡張（例: system/admin.py, voice/basic.py）。コグは`setup(bot)`で登録。
- **lib/**: DB（lib/postgres.py）、VOICEVOX連携（lib/VOICEVOXlib.py）、読み上げ前処理（lib/text_pipeline.py, lib/dictionary_replacer.py）、読み上げキュー（lib/tts_player.py）などの共通ロジック。
- **web/**: Next.js製Webダッシュボード。APIは`app/api/`配下、UIは`app/`配下。
- **docker-compose.yml**: Bot/DB/VOICEVOX/Adminerの一括起動。開発・本番どちらも対応。

## 開発・ビルド・実行
- **Bot本体**
  - 依存: Python 3.11+, PostgreSQL, VOICEVOXエンジン
  - `.env`必須（例: DISCORD_TOKEN, DB_HOST, VOICEVOX_URL）
  - 開発: `python bot.py` で起動
  - Docker: `docker compose up -d` で一括起動
- **Web UI**
  - `web/`で `npm install && npm run build && npm start`
  - `.env`でAPIエンドポイント等を指定

## 主要な設計・実装パターン
- **コグの自動ロード**: `cogs/`配下の全.pyを`load_all_cogs()`で動的ロード
- **DBアクセス**: `lib/postgres.py`の`PostgresDB`クラスを各コグで使う
- **VOICEVOX連携**: `lib/VOICEVOXlib.py`の`VOICEVOXLib`で複数VOICEVOXサーバーを冗長化
- **Web API連携**: Bot起動時にFastAPIサーバーをバックグラウンド起動し、Web UIとHTTPで連携
- **環境変数・設定**: `.env`と`config.yml`で管理。コグやlibは都度`load_dotenv()`で再読込可
- **シャーディング**: `SHARD_COUNT`で分割。大規模サーバー対応

## プロジェクト固有の注意点
- **DBスキーマ自動マイグレーション**: Bot起動時に`PostgresDB.initialize()`でテーブル自動作成・型変換
- **VOICEVOXサーバーURLの複数指定**: `VOICEVOX_URL`はカンマ区切りで複数指定可。自動フェイルオーバー
- **Web UIとの連携**: Bot起動中のみWebダッシュボードが機能。APIは`/servers`等でBotの状態取得
- **管理者コマンド**: `/admin`コマンドは`ADMIN_ID`環境変数で制御

## 読み上げ処理の構成
- `lib/text_pipeline.py`: 前処理（NFKC→メンション等の変換→辞書置換→未保護部分の加工→文字数制限→文分割）。純粋関数。
- `lib/dictionary_replacer.py`: 辞書の置換器（最長一致・1パス、置換部分は保護）。
- `lib/tts_player.py`: ギルドごとの読み上げキュー。1文目の合成後すぐ再生し、再生中に次の文を合成する。
- `lib/VOICEVOXlib.py`: VOICEVOX Engine クライアント（セッション共有・同時合成数の制限・LRUキャッシュ）。
- テスト: `python -m unittest discover -s tests -t .`

## 参考ファイル
- `README.md`, `docs/howtorun.md`, `docker-compose.yml`, `bot.py`, `lib/`, `cogs/`, `web/`

## Web UI（web/）の詳細
- **技術スタック**: Next.js（App Router, TypeScript, MUI, Tailwind, Chart.js, next-auth, pg, i18next）
- **ディレクトリ構成**:
  - `web/app/`: ルーティング・ページ・APIエンドポイント（`api/`配下）
  - `web/lib/`: 認証（auth.ts）、DB接続（db.ts）、テーマ（theme.ts）、ユーティリティ
  - `web/components/`: UI部品
- **認証**: next-auth + Discord OAuth2。`/auth/signin`でDiscord認証、APIは`getServerSession(authOptions)`で保護。
- **DB連携**: `pg`でPostgreSQLに接続。API Route（例: `/api/guild-dictionary`）で直接クエリ。
- **API設計**:
  - `/api/guild-dictionary`・`/api/user-dictionary`: Bot本体と同じDBスキーマを直接操作。POST/DELETEでBot HTTPサーバーにキャッシュクリア通知。
  - `/api/servers`: Discord APIから所属ギルド一覧を取得。ジョブIDによるポーリング・キャッシュ対応。
  - `/api/servercount`: Bot HTTPサーバーの/serversエンドポイントをプロキシ。
  - `/api/prometheus/range`: Prometheusのrange queryをプロキシし、グラフ描画用データを返す。
- **UI/UX設計**:
  - MUIテーマ（lib/theme.ts）でMaterial Design 3風。Tailwind併用。
  - ダッシュボード（/dashboard）は辞書管理・サーバー選択・ユーザー情報・サインアウト等を提供。
  - コマンド一覧（/commands）は静的に定義。
  - ホーム（/）はメトリクス・特徴・CTA・グラフ（Prometheus/Chart.js）を表示。
- **i18n**: next-i18next/i18next/react-i18nextで国際化対応（lib/i18n.ts等）。
- **開発ワークフロー**:
  - `npm install`→`npm run build`→`npm start`（開発は`npm run dev`）
  - .envでAPIエンドポイント・DB接続・Discord認証情報を指定
  - API RouteのDB接続は`pg`のPoolを都度生成（コネクションリーク注意）
- **運用・注意点**:
  - Bot本体のFastAPI HTTPサーバーが起動していないと一部API（/servercount等）がエラーになる
  - DBスキーマはBot本体と共通。マイグレーション時は両者の互換性に注意
  - サーバー一覧取得はDiscord APIのrate limitに注意し、ポーリング・キャッシュで対策
  - Prometheus連携は`PROMETHEUS_URL`環境変数で指定
  - next-authのコールバックでアクセストークン・ユーザーIDをセッションに付与
  - API Routeでのfetchはnode-fetch互換（CORSやcookieに注意）
