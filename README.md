# SwiftlyTTS Ver.Masato.JP

> [!IMPORTANT]
> このリポジトリはアーカイブされたhttps://github.com/techfish-11/SwiftlyTTS のフォーク版です。
> 現在も開発しており、主に生成AIを用いた開発を行っています。

無駄のない読み上げBot

## 特徴
- **読みはVOICEVOXに任せる**: 漢字かな交じり文のままVOICEVOX Engineに渡し、読みとアクセントはEngine内蔵のOpenJTalkで判定します（外部AI APIは使いません）。
- **軽量・高速**: 長文は文ごとに分けて合成し、1文目の合成が終わればすぐ再生を始めます（再生中に次の文を合成）。合成済み音声はキャッシュします。
- **VOICEVOX対応**: ずんだもんなどのVOICEVOXキャラクターを利用可能。

## 実行方法
詳細な手順は [docs/howtorun.md](docs/howtorun.md) を参照してください。

## クイックスタート (Docker Compose)
1. リポジトリをクローン
2. `.env` と `config.yml` を作成
   ```bash
   cp .env.example .env
   cp config.yml.example config.yml
   ```
3. `.env` を編集してトークン等を設定
4. 起動: `docker compose up -d`

## 読み上げの仕組みと設定

### 前処理の流れ
```
受信テキスト
 → NFKC正規化（全角英数→半角、半角カナ→全角など）
 → メンション・チャンネル・ロールを表示名に、カスタム絵文字を名前に、URLを「URL省略」に変換
 → 辞書の置換（最長一致・1パス。置換した部分は以降の加工を受けない）
 → 辞書以外の部分のみ：同じ文字の連続を圧縮（「あああああ」→「あああ」）
 → 文字数制限（超過分は「以下略」）
 → 文に分割して、1文目の合成が終わり次第再生（再生中に次の文を合成）
```
読み（漢字・英単語の読み、アクセント）は VOICEVOX Engine に任せます。

### 読み上げ辞書
- 優先順位は **ユーザー辞書 ＞ サーバー辞書 ＞ 共通辞書** です（同じ語が登録されている場合）。
- 長い語が優先され、置換結果がさらに別の語で置換されることはありません。
- 読みを **かなだけ** で登録した場合は、カタカナに統一して VOICEVOX に送ります（ひらがなのままだと前後の助詞とつながって読みが崩れることがあるため）。漢字を含む読みはそのまま送ります。
  - カタカナでは「ハ」「ヘ」を助詞の「ワ」「エ」として読まないため、たとえば「こんにちは」と読ませたい場合は「コンニチワ」と登録してください。
- 辞書は Bot のメモリに保持し、登録・削除時（Web ダッシュボードからの変更も通知で反映）と、一定間隔（`tts.dictionary_refresh_interval`）で読み直します。

### 設定項目（`config.yml` の `tts:`）
設定例は [config.yml.example](config.yml.example) を参照してください。省略した項目は既定値になります。

| 項目 | 既定値 | 内容 |
|---|---|---|
| `max_length` | 150 | 読み上げる最大文字数（辞書置換後）。0で無制限 |
| `omission_text` | 以下略 | 超過時に末尾で読む文言 |
| `url_text` | URL省略 | URLの読み替え |
| `custom_emoji` | name | カスタム絵文字を `name`=名前で読む / `skip`=読まない |
| `repeat_max` | 3 | 同じ文字の連続をこの回数までに縮める。0で無効 |
| `sentence_soft_max` | 50 | 1文がこれより長いと読点などで分けて合成 |
| `synthesis_concurrency` | 1 | VOICEVOX への同時合成数（全サーバー合計）※再起動で反映 |
| `audio_cache_mb` | 32 | 合成済み音声キャッシュの上限MB（約1MB=10秒）。0で無効 ※再起動で反映 |
| `audio_cache_entries` | 512 | キャッシュの最大件数 ※再起動で反映 |
| `synthesis_timeout` | 90 | 1回の合成のタイムアウト秒 ※再起動で反映 |
| `engine_sampling_rate` | 0 | Engine に要求するサンプリングレート。0=Engine の既定（ffmpeg で変換）。`48000` で ffmpeg なしで再生（`scripts/check_audio.py` で確認してから）※再起動で反映 |
| `playback` | pcm | 再生方式。`pcm`=48kHz の音声は ffmpeg を使わず直接送る / `ffmpeg`=常に ffmpeg で変換する |
| `voice_monitor_interval` | 30 | VC の接続状況を調べる間隔（秒）。0で無効 |
| `queue_max_length` | 0 | サーバーごとの待機メッセージ上限。0で無制限 |
| `queue_overflow` | drop_oldest | 上限超過時に `drop_oldest`=古いものを捨てる / `drop_newest`=新しいものを捨てる |
| `dictionary_refresh_interval` | 600 | 辞書をDBから読み直す間隔（秒） |
| `dictionary_ahocorasick_threshold` | 2000 | 辞書の語数がこれ以上なら Aho-Corasick で置換（`pip install pyahocorasick` が必要。未導入なら通常の方式） |

`synthesis_concurrency` は、Engine のスレッド数を使い切っている場合は 1 が最も速く1文目を返せます（並列にしても合計時間はほぼ変わらず、1文目が遅れるだけ）。

### VOICEVOX Engine の設定
- CPU 版を常駐させ、`VV_CPU_NUM_THREADS`（`--cpu_num_threads` と同じ）を **サーバーのコア数以内** にしてください。コア数を超えると遅くなります。
- `--load_all_models` は付けないでください。使われた話者のモデルだけが読み込まれ、メモリを節約できます（話者を増やすほど使用メモリが増えます）。
- Bot は Engine のユーザー辞書を使わないため、`VV_DISABLE_MUTABLE_API=1` で辞書・設定を変更する API を無効にしています。
- 読みの挙動は Engine のバージョンで変わるため、バージョンを固定しています（英単語の読みは 0.26.2 で確認）。

同梱の [docker-compose.yml](docker-compose.yml) にはこれらを設定済みです。**Engine を別のホストや別の compose で動かしている場合** は、そちらで次のように設定してください。

```yaml
  voicevox:
    image: voicevox/voicevox_engine:cpu-0.26.2
    environment:
      VV_CPU_NUM_THREADS: "2"        # サーバーのコア数以内
      VV_DISABLE_MUTABLE_API: "1"
    healthcheck:                     # イメージに curl が無いため bash で確認する
      test: ["CMD", "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/50021 && printf 'GET /version HTTP/1.0\\r\\n\\r\\n' >&3 && head -1 <&3 | grep -q ' 200 '"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 60s
```
`docker run` の場合は `docker run -d -e VV_CPU_NUM_THREADS=2 -e VV_DISABLE_MUTABLE_API=1 -p 50021:50021 voicevox/voicevox_engine:cpu-0.26.2` です。

### ログ
**動作ログ**（起動・接続・読み上げ・エラーなど）は標準出力に出ます。
```bash
docker compose logs -f bot            # 追いかけて表示
docker compose logs --since 1h bot    # 直近1時間
```
同梱の compose では、Docker 側のログが肥大化しないように 10MB×5ファイルまでに制限しています。

**操作ログ**は「いつ・誰が・何をしたか」を日ごとのファイルに保存します。compose ではホストの `./logs/activity-YYYY-MM-DD.log` に出力され、`retention_days`（既定30日）より古いファイルは自動で削除されます。1行1件のタブ区切りです。

```
日時                        操作     サーバー名(ID)      チャンネル名(ID)   表示名(@ユーザー名, ID)            詳細          本文
2026-11-27 21:04:13+09:00  tts      雑談鯖(1234…)      読み上げ(5678…)    まさと(@masato, 9012…)            speaker=3     こんにちは
2026-11-27 21:04:20+09:00  command  雑談鯖(1234…)      読み上げ(5678…)    まさと(@masato, 9012…)            /dictionary add key=鯖 value=さば
2026-11-27 21:05:02+09:00  stop     雑談鯖(1234…)      読み上げ(5678…)    まさと(@masato, 9012…)            -
```
- 操作の種類: `tts`（メッセージの読み上げ）、`read`（`/read`）、`stop`（`s`）、`command`（その他のコマンド。引数も記録）
- 本文を残したくない場合は `logging.activity_log.include_text: false` にしてください（`tts` と `/read` の本文を記録しなくなります）。
- compose を使わずに `docker run` する場合は、`-v $(pwd)/logs:/app/logs` でマウントしないとコンテナの削除とともに消えます。

設定は `config.yml` の `logging:` です（[config.yml.example](config.yml.example) を参照）。日時は `timezone`（既定 `Asia/Tokyo`）で記録します。

### 応答時間の計測
Discord に接続せず、Engine に実際に合成させて「再生開始までの時間」を変更前の方式と比較できます。
```bash
python scripts/bench_latency.py --url http://<EngineのURL>:50021
```

### テスト
```bash
python -m unittest discover -s tests -t .
```

# ライセンス表記

本BotはVOICEVOXの以下の話者を利用しています。

- VOICEVOX: 四国めたん
- VOICEVOX: ずんだもん
- VOICEVOX: 春日部つむぎ
- VOICEVOX: 雨晴はう
- VOICEVOX: 波音リツ
- VOICEVOX: 玄野武宏
- VOICEVOX: 白上虎太郎
- VOICEVOX: 青山龍星
- VOICEVOX: 冥鳴ひまり
- VOICEVOX: 九州そら
- VOICEVOX: もち子さん
- VOICEVOX: 剣崎雌雄
- VOICEVOX: WhiteCUL
- VOICEVOX: 後鬼
- VOICEVOX: No.7
- VOICEVOX: ちび式じい
- VOICEVOX: 櫻歌ミコ
- VOICEVOX: 小夜/SAYO
- VOICEVOX: ナースロボ＿タイプＴ
- VOICEVOX: †聖騎士 紅桜†
- VOICEVOX: 雀松朱司
- VOICEVOX: 麒ヶ島宗麟
- VOICEVOX: 春歌ナナ
- VOICEVOX: 猫使アル
- VOICEVOX: 猫使ビィ
- VOICEVOX: 中国うさぎ
- VOICEVOX: 栗田まろん
- VOICEVOX: あいえるたん
- VOICEVOX: 満別花丸
- VOICEVOX: 琴詠ニア
- VOICEVOX: Voidoll
- VOICEVOX: ぞん子
- VOICEVOX: 中部つるぎ

各話者の利用条件はVOICEVOX公式サイトをご確認ください。
