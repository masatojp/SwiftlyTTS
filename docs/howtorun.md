## ボット実行方法
Swiftlyは、Docker、Docker Compose、Pterodactyl、もしくは直接実行できます。

### Dockerで実行（推奨）
1. PostgreSQLサーバーを立ち上げ、基本的な設定をする
2. [.env](https://github.com/techfish-11/SwiftlyTTS/blob/main/.env.example)を書き込む。
3. VOICEVOXサーバーを立ち上げる
[VOICEVOX_engineのrepo](https://github.com/VOICEVOX/voicevox_engine)を参照してください
4. Dockerイメージを実行する
```bash
docker run -d --env-file .env --name swiftlytts ghcr.io/techfish-11/swiftlytts-bot
```

### 直接実行（開発者向け）

#### 必須要件
- Python 3.11+
- Rust Toolchain (Cargo) ※一部のライブラリビルドに必要
- Docker (PostgreSQL, Voicevox用)

#### 手順

1. PostgreSQLとVOICEVOXを起動する
   Docker Composeを使うのが最も簡単です。
   ```bash
   docker compose up -d postgres voicevox
   ```

2. 依存関係のインストール
   ```bash
   # 仮想環境の作成と有効化（推奨）
   python3.11 -m venv .venv
   source .venv/bin/activate
   
   # ライブラリのインストール（Rust環境が必要です）
   pip install maturin
   pip install -r requirements.txt
   
   # Rustバインディングのビルド
   cd lib/rust_lib
   maturin develop
   cd ../..
   ```
   ※ Linux環境であれば `./install.sh` を使用すると上記を自動で行えます。

3. 環境変数の設定
   `.env.example` をコピーして `.env` を作成し、自身の環境に合わせて編集してください。

4. Botの起動
   ```bash
   python bot.py
   ```

### Pterodactylで実行
1. [Pterodactyl Egg](https://github.com/techfish-11/SwiftlyTTS/blob/main/pterodactyl-egg.json)をダウンロードしてインポートする
2. サーバーを作成し、環境変数を設定する
3. サーバーを起動する
(VOICEVOXサーバー、PostgreSQLサーバーは別途用意してください)

### Docker Composeで実行（全体）
Bot、DB、Voicevox、Adminerをまとめて起動します。

1. リポジトリをクローンする
2. `.env` を作成して設定する
3. 起動する
   ```bash
   docker compose up -d
   ```

## Web UIの実行方法
Web UIはNext.jsで構築されており、`web/`ディレクトリにあります。

また、ボットとWebサーバーはHTTP APIを介して通信するため、Webダッシュボード使用時はボットも起動しておく必要があります。

### Web UIの実行方法 (`web` ディレクトリ)
Web UIは Next.js (App Router) で構築されています。

#### 必須要件
- Node.js 18+ (20+ 推奨)

#### 手順

1. Webディレクトリへ移動
   ```bash
   cd web
   ```

2. 依存関係のインストール
   ```bash
   npm install
   ```

3. ビルド
   ```bash
   npm run build
   ```

4. 実行
   ```bash
   npm start
   ```

※ 開発モードの場合は `npm run dev` で起動できます。