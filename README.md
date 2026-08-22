# 🤖 AI VTuber 「るり (Ruri)」 システム構成 & 使用マニュアル

**自作サーバーに住み着いた、ちょっと生意気な管理AI VTuber「るり (Ruri)」を動かすためのシステムリポジトリです。**
LLMと音声合成、Unity OSC、YouTubeチャット連携などを統合した、低遅延ストリーミング配信システムです。

---

## 🛠️ システム概要とデータフロー

本システムは、LLMのリアルタイムなテキストストリーム出力を文末ごとにチャンク化し、非同期で音声合成を行いながら、WebSocket経由でOBSおよびUnity/VTube Studio等と連携するアーキテクチャを採用しています。

```
[ユーザーのコメント] ──> 【LLM（Ollama / Gemini）】 (ストリーミング応答)
                              │
                              ▼
                        【テキスト分割バッファ】 (「。」や「！」等で分割)
                              │
                              ▼
                  【音声合成エンジン (非同期)】 (VOICEVOX / Style-Bert-VITS2)
                     /        |        \
                    /         |         \
                   ▼          ▼          ▼
           【WebSocket】  【OSC送信】 【YouTube投稿】 (自動リプライ等)
                 │            │
                 ▼            ▼
         【OBSブラウザ】 【Unity / VTS】 (リップシンク・表情・モーション)
```

---

## 📋 必要な環境 (Prerequisites)

- **OS**: Linux (推奨)
- **Python**: 3.10+ (仮想環境 `.venv` 内で稼働)
- **Docker**: VOICEVOX Engine をコンテナで動かす場合に必要
- **外部サービス・アプリケーション**:
  - **Ollama**: `http://localhost:11434` で起動（LLM用）
  - **Style-Bert-VITS2**: `http://localhost:5000` で起動（高音質音声合成用）
  - **Unity (OSC接続)**: アバターのリップシンク、表情・モーション送信用
  - **Stable Diffusion WebUI Forge**: 夢日記生成時の画像生成用

---

## 🎙️ Style-Bert-VITS2 の連携詳細

るりの声を合成するために **Style-Bert-VITS2** を使用しています。

### 1. 起動と配置
- **配置パス**: `/home/reppu/Style-Bert-VITS2` に配置されている前提です。
- **自動起動**: `ruri_config.yaml` の `bert_vits2.enabled` が `true` の場合、`make start` を実行すると自動的にバックグラウンドで `server_fastapi.py` がポート `5000` で起動します。
- **手動起動**:
  ```bash
  cd /home/reppu/Style-Bert-VITS2 && ./venv/bin/python3 server_fastapi.py
  ```

### 2. 感情スタイルマッピング
るりの発話の感情タグ（例: `[joy]`, `[angry]`）に応じて、Style-Bert-VITS2 の `style` を切り替えて感情豊かに喋らせます。
マッピングは `ruri_config.yaml` の `bert_vits2.emotion_styles` にて変更可能です：
- `joy` / `happy` ──> `"Joy"`
- `angry` / `angry2` ──> `"Anger"`
- `sad` / `pale` ──> `"Sadness"`
- `blush` / `perori` ──> `"Lovey-dovey"`
- `sleep` / `gonyogonyo` ──> `"Whisper"`

---

## 🎮 Unity / VTube Studio (OSC連携) の詳細

るりのアバター表情やアニメーション（モーション）の制御は、**OSC (Open Sound Control) プロトコル**を利用して Unity 等にリアルタイム送信されます。

### 1. 接続設定
- **IP / ポート**: デフォルトで Unity が動いている端末の IP アドレス（例: `192.168.1.6`）とポート `39540` へUDPパケットを送信します。
- 送信の有効/無効は `ruri_config.yaml` の `unity.send_expression` および `send_motion` で制御します。

### 2. 送信アドレスとパラメータ
* **モーション送信 (`/avatar/motion`)**
  - アドレス: `/avatar/motion`
  - データ型: `String`
  - るりが話し始めると、`talking_motions`（`Talking1`, `Talking2`, `Talking3` など）からランダムに選ばれたモーション名が送信されます。
* **表情送信 (`/avatar/expression`)**
  - アドレス: `/avatar/expression`
  - データ型: `String`
  - 発話感情（例: `joy`, `angry`, `surprised`）に応じた Unity 側の Expression 名（例: `Joy`, `Angry`, `Surprised`）が送信されます。
* **メガネ制御 (Gemini使用時など)**
  - 表情パラメータやメガネオン/オフコマンド（`/avatar/expression` に `glasses_on` / `glasses_off` など）を送信し、るりちゃんのメガネの着脱をダイナミックに制御します。

### 3. 音声連動リップシンクの流れ
1. るりが発話ツールを実行すると、バックエンドで音声WAVが生成されます。
2. WAVバイナリデータが WebSocket (`ws://localhost:8000/ws/audio`) 経由でOBS側ブラウザソースに届きます。
3. OBSブラウザ側でデコードされ、再生が開始されるタイミングに合わせて **`play_start`** シグナルがサーバーへ返され、同期して Unity へ OSC 表情コマンドが送信されることで、音と顔の動きがシンクロします。

---

## 🚀 使い方・コマンド一覧 (Makefile)

プロジェクトのルートにある `Makefile` を使うことで、各種サービスの起動や停止、ログ確認を簡単に行うことができます。

### 1. サービスの稼働ステータス確認
```bash
make status
```
VOICEVOX Engine と Ollama の稼働状況、Dockerコンテナの状態を確認します。

### 2. システムの起動・停止
* **すべて起動する** (設定ファイルに従い、Style-Bert-VITS2/VOICEVOX とサーバーを自動でバックグラウンド起動):
  ```bash
  make start
  ```
* **サーバー単体を手動で起動する** (フォアグラウンド実行、開発・デバッグ用):
  ```bash
  make run
  ```
* **システムを停止する** (るり本体、Style-Bert-VITS2、VOICEVOXを一括停止):
  ```bash
  make stop
  ```

### 3. ログの確認
```bash
make logs
```
るり本体のバックエンドサーバーログ (`server.log`) をリアルタイム追跡 (`tail -f`) します。

### 4. 音声合成エンジンの単体操作 (VOICEVOX)
* **起動**: `make start-voicevox` (Dockerで立ち上げます)
* **停止**: `make stop-voicevox`

### 5. 画像生成エンジンの操作 (Stable Diffusion Forge)
* **起動**: `make start-sd` (バックグラウンドで起動し、ログを出力します)
* **停止**: `make stop-sd`

### 6. 記憶整理 (睡眠モード) の実行
るりちゃんが1日の記憶を整理し、夢日記の生成と学習用データベースへの書き込みを行います。
```bash
make sleep
```

---

## ⚙️ 設定ファイルの説明 (`ruri_config.yaml`)

システムの挙動は [ruri_config.yaml](file:///home/reppu/workspace/ai_vtuber2/ruri_config.yaml) で制御します。主な項目は以下の通りです：

| セクション | 項目名 | 説明 |
| :--- | :--- | :--- |
| `boot_mode` | - | 起動モード。`chat` (対話モード) または `radio` (独り語り/雑談モード) |
| `llm_provider` | - | 使用するLLM。`ollama` または `gemini` |
| `voicevox` | `enabled` | VOICEVOX を音声合成として使用する場合は `true` に設定 |
| `bert_vits2` | `enabled` | Style-Bert-VITS2 を音声合成として使用する場合は `true` に設定 |
| `vts` | `enabled` | VTube Studio を連携させて表情制御を行う場合は `true` |
| `unity` | `enabled` | Unity OSC を使ってアバターへ表情やモーションを送信する場合は `true` |
| `youtube_chat`| `enabled` | YouTube ライブのチャット自動巡回・投稿を行う場合は `true` |

---

## 🖥️ 配信・配信管理画面へのアクセス

サーバー起動後、ブラウザで以下のURLにアクセスしてください。

* **コントロールパネル (Dashboard)**
  - `http://localhost:8000/dashboard` (または `http://localhost:8000/`)
  - るりの稼働状態確認、手動でのコメント送信やモーション・表情のテストトリガー、手動スリープ（記憶整理）の実行が可能です。
* **OBSブラウザソース用オーバーレイ**
  - `http://localhost:8000/overlay` (または `http://localhost:8000/comment.html`)
  - OBSの「ブラウザソース」にこのURLを追加することで、るりちゃんのセリフ表示や、WebSocket経由の低遅延リップシンク音声再生が可能です。

---

## 📂 主要ファイルと役割

* **[vtuber_server.py](file:///home/reppu/workspace/ai_vtuber2/vtuber_server.py)**: メインのFastAPIサーバー。ルーティングとエージェントループ。
* **[ruri_sleep.py](file:///home/reppu/workspace/ai_vtuber2/ruri_sleep.py)**: 睡眠モード（記憶整理）実行用スクリプト。
* **[voice.py](file:///home/reppu/workspace/ai_vtuber2/voice.py)**: 音声合成API連携およびテキスト前処理（ルビ振り）。
* **[unity_osc.py](file:///home/reppu/workspace/ai_vtuber2/unity_osc.py)**: Unity向けOSC送信ロジック（表情・リップシンク・モーション）。
* **[ruri_memory.py](file:///home/reppu/workspace/ai_vtuber2/ruri_memory.py)**: 記憶用SQLiteデータベースの操作。
* **[SOUL.md](file:///home/reppu/workspace/ai_vtuber2/SOUL.md)**: るりの性格、口調、キャラクターデザイン設定資料。
* **[static/](file:///home/reppu/workspace/ai_vtuber2/static)**: ダッシュボードおよびオーバーレイ用のHTMLファイル群。
