import ruri_env
import os
import sys
import json
import time
import queue
import random
import asyncio
import threading
from typing import Optional, List
import yaml

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# 独自モジュールのインポート
import ruri_memory
import voice
import unity_osc
import web_scraper
import chat_poster

from run_agent import AIAgent
from tools.registry import registry

# 設定ファイルの読み込み
try:
    with open('ruri_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"[ERROR] 設定ファイルの読み込みに失敗したし: {e}")
    config = {}

# データベースのクリーン初期化
print("[⚙️ サーバー起動] データベースを初期化中...")
ruri_memory.init_db(clean=False)

# グローバル状態管理
global_status = {
    "mode": config.get("boot_mode", "chat"),            # radio (独り語り) / chat (対話) / thinking (思考中)
    "topic": "のんびり雑談中" if config.get("boot_mode", "chat") == "chat" else "トレンドニュース紹介",
    "user_name": "",
    "user_comment": "",
    "ruri_msg": "ハローお前ら！るりちゃんだし！",
    "voice_data": "",
    "voice_id": "",
    "emotion": "neutral",
    "glasses_on": False,
    "thinking_log": "",
    "reasoning_log": "",
    "queue_size": 0
}

playback_finish_time = 0.0

# 状態のディスク書き出し（後方互換性のため）
def save_status_to_disk():
    try:
        with open("ruri_status.json", "w", encoding="utf-8") as f:
            json.dump(global_status, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARNING] statusのディスク書き出し失敗: {e}")

save_status_to_disk()

# SSE配信用イベントキューとループ管理
active_listeners: List[asyncio.Queue] = []
async_loop: Optional[asyncio.AbstractEventLoop] = None

def broadcast_event(event_type: str, data: any):
    """メイン/バックグラウンドスレッドからFastAPIのイベントループを介して全クライアントへSSEイベントを配信する"""
    global async_loop
    event_str = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    
    # グローバルステータスの一部を更新
    if event_type == "status":
        global_status.update(data)
    elif event_type == "thinking":
        global_status["thinking_log"] = data
    elif event_type == "reasoning":
        global_status["reasoning_log"] += data
    
    save_status_to_disk()

    if async_loop and async_loop.is_running():
        for q in active_listeners:
            async_loop.call_soon_threadsafe(q.put_nowait, event_str)

# -------------------------------------------------------------
# エージェント用カスタムツールの実装＆登録
# -------------------------------------------------------------
voice_generated_during_run = False
spoken_texts_during_run = []
speak_count_during_run = 0
def clear_audio_on_tool_call():
    """ツール呼び出し（中間ステップ）が発生した際に、重複読み上げを防ぐため既存の音声出力をクリアするし"""
    global audio_handler
    if audio_handler:
        # ラジオモード中、またはバックグラウンドでの先読み中は、再生中の音声（トピック等）をクリアしない
        if global_status.get("mode") == "radio" or prefetch_in_progress:
            print("[🔧 ツール実行検出] ラジオ再生中または先読み中のため、音声のクリアはスキップするし")
            return
        print("[🔧 ツール実行検出] 読み上げ重複防止のため既存の音声出力をクリアするし")
        audio_handler.reset()
        asyncio.run_coroutine_threadsafe(
            audio_handler.send_clear_signal(),
            audio_handler.loop
        )

def speak_handler(args, **kw):
    """るりが喋る（音声合成、リップシンク、Unityモーション送信、オーバーレイ更新、発話ディレイ）"""
    global voice_generated_during_run, spoken_texts_during_run, speak_count_during_run, inside_paragraphs_call
    
    text = args.get("text", "").strip()
    emotion = args.get("emotion") or "neutral"
    
    if not text:
        return "発話テキストが空だし！"
        
    # 暴走防止：1ターンにおける複数回の speak 呼び出しを制限
    if not inside_paragraphs_call and speak_count_during_run >= 1:
        print(f"[⚠️ 暴走防止ブロック] LLMが同一ターン内で2回目以降の speak ツール呼び出しを試みたため、実行をブロックしたし。")
        return "すでにこのターンで発話済みだし！これ以上のツール呼び出しは不要なので、対話を終了してね。"
        
    speak_count_during_run += 1
        
    # 先頭の感情タグ [emotion] を抽出し、除去する
    import re
    match = re.match(r'^\[([a-zA-Z0-9_]+)\]', text)
    if match:
        extracted_emotion = match.group(1)
        if emotion == "neutral" or not emotion:
            emotion = extracted_emotion
        text = text[match.end():].strip()
        
    # 文中の他の感情タグ [emotion] も全て除去する
    text = re.sub(r'\[[a-zA-Z0-9_]+\]', '', text).strip()
        
    print(f"\n[📢 るり発話] [{emotion}] {text}")
    voice_generated_during_run = True
    spoken_texts_during_run.append(text)
    
    # 1. 音声WAVの生成
    wav_path = voice.generate_voice_wav(text, emotion)
    
    # 2. Base64エンコード
    voice_base64 = ""
    if wav_path and os.path.exists(wav_path):
        try:
            with open(wav_path, "rb") as f:
                import base64
                voice_base64 = "data:audio/wav;base64," + base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            print(f"[ERROR] 音声Base64変換に失敗したし: {e}")
            
    voice_id = str(time.time()) if voice_base64 else ""
    duration = voice.get_wav_duration(wav_path) if wav_path else 2.0
    
    # 3. Unity OSCへの表情・リップシンク送信
    try:
        unity_osc.send_unity_emotion(emotion, is_speaking=(wav_path is not None))
    except Exception as e:
        print(f"[WARNING] Unity OSCへの送信失敗: {e}")

    # 4. SSEおよびグローバルステータスの更新
    status_update = {
        "ruri_msg": text,
        "voice_data": voice_base64,
        "voice_id": voice_id,
        "emotion": emotion
    }
    broadcast_event("status", status_update)
    
    # 5. 音声の再生時間の長さだけディレイを挟む（発話が終わるまで次の思考や行動を防ぐ）
    sleep_time = duration + 0.3
    print(f"[⏳ 発話中] {sleep_time:.2f}秒待機するし...")
    time.sleep(sleep_time)
    
    return "発話に成功したし！"

def speak_paragraphs_handler(args, **kw):
    """連続する複数の段落を感情付きで発話する"""
    global inside_paragraphs_call
    paragraphs = args.get("paragraphs", [])
    emotions = args.get("emotions", [])
    
    if not paragraphs:
        return "段落リストが空だし！"
        
    inside_paragraphs_call = True
    try:
        for i, p in enumerate(paragraphs):
            emo = emotions[i] if i < len(emotions) else "neutral"
            speak_handler({"text": p, "emotion": emo})
    finally:
        inside_paragraphs_call = False
        
    return "すべての段落の発話が完了したし！"

def set_glasses_handler(args, **kw):
    """メガネの着脱を行う"""
    is_on = bool(args.get("is_on", False))
    print(f"[👓 メガネ制御] メガネ装着状態を変更: {is_on}")
    
    try:
        unity_osc.send_unity_glasses(is_on)
    except Exception as e:
        print(f"[WARNING] Unity OSC メガネ送信失敗: {e}")
        
    broadcast_event("status", {"glasses_on": is_on})
    return f"メガネを{'装着した' if is_on else '外した'}し！"

def query_listener_activity_handler(args, **kw):
    """指定されたリスナーの過去の活動情報をDBから照会する"""
    name = args.get("name", "").strip()
    if not name:
        return "ユーザー名が指定されてないし！"
    
    act = ruri_memory.get_user_activity(name)
    if not act:
        return f"{name} は初見のリスナーだし！"
        
    return f"ユーザー {name} の情報: 発言回数={act.get('message_count', 0)}, 初回検知={act.get('first_seen', 0)}, 最終検知={act.get('last_seen', 0)}, 前回のメッセージ='{act.get('last_message', '')}'"

def query_episodic_memory_handler(args, **kw):
    """指定されたリスナーに関する出来事や事実をセマンティック検索する"""
    clear_audio_on_tool_call()
    name = args.get("name", "").strip()
    query = args.get("query", "").strip()
    
    if not name or not query:
        return "ユーザー名またはクエリが指定されてないし！"
        
    memories = ruri_memory.search_memories(name, query, limit=3)
    if not memories:
        return f"{name} についての記憶「{query}」に合致するものは見つからなかったし。"
        
    results = [f"- {m.get('content')}" for m in memories]
    return f"{name} について覚えている記憶:\n" + "\n".join(results)

def save_episodic_memory_handler(args, **kw):
    """リスナーに関する重要な事実をメモリへ記憶する"""
    clear_audio_on_tool_call()
    name = args.get("name", "").strip()
    fact = args.get("fact", "").strip()
    
    if not name or not fact:
        return "ユーザー名または事実が指定されてないし！"
        
    # 同期的または非同期的（スレッド）で埋め込みを作成してDBに保存
    vector = ruri_memory.get_embedding(fact)
    if vector:
        try:
            import sqlite3
            now = time.time()
            conn = sqlite3.connect("ruri_memory.db")
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO memories (user_name, content, embedding, emotion, importance, created_at, last_accessed) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, fact, json.dumps(vector), "neutral", 0.5, now, now)
            )
            conn.commit()
            conn.close()
            print(f"[🧠 手動記憶] {name} について記憶したし: 「{fact}」")
            return f"{name} について「{fact}」と記憶したし！"
        except Exception as e:
            return f"記憶の保存に失敗したし: {e}"
    else:
        return "埋め込みベクトルの生成に失敗したし。記憶できなかったよ。"

def get_trending_news_handler(args, **kw):
    """IT・テック系のトレンドニュースを取得する"""
    news = web_scraper.get_trending_news()
    return f"【トレンドニュース】\nタイトル: {news['title']}\n概要: {news['body']}\nURL: {news.get('url', '')}"

def search_web_handler(args, **kw):
    """インターネットでウェブ検索を行う。Google News RSS を主力に、DuckDuckGo をフォールバックで使う"""
    clear_audio_on_tool_call()
    import re as _re
    query = args.get("query", "").strip()
    if not query:
        return "検索クエリが空だし！"

    # クエリの自動正規化：数字と英字の境界にスペースを挿入（Fable5→Fable 5）
    normalized = _re.sub(r'([a-zA-Z])([0-9])', r'\1 \2', query)
    normalized = _re.sub(r'([0-9])([a-zA-Z])', r'\1 \2', normalized)

    # ===== 第1優先：Google News RSS（最新ニュースに強い） =====
    news_result = web_scraper.search_google_news(normalized)
    if news_result and news_result != "検索結果なし":
        return f"【Google News検索: {normalized}】\n{news_result}"

    # ===== 第2優先：DuckDuckGo（一般的な情報に強い） =====
    queries_to_try = []
    if normalized != query:
        queries_to_try.append(normalized)
    queries_to_try.append(query)
    queries_to_try.append("Claude " + normalized)   # AIモデル名フォールバック
    queries_to_try.append(normalized + " 最新情報")

    seen = set()
    for q in queries_to_try:
        if q in seen:
            continue
        seen.add(q)
        r = web_scraper.search_duckduckgo(q)
        if r and r != "検索結果なし":
            return f"【DuckDuckGo検索: {q}】\n{r}"

    return (
        f"「{query}」についての検索結果は見つからなかったし。\n"
        f"まだ公式発表前か、別のキーワードで試してみるといいかもしれないし。"
    )




def fetch_web_page_handler(args, **kw):
    """指定されたURLのウェブページ本文を取得して要約する"""
    clear_audio_on_tool_call()
    url = args.get("url", "").strip()
    if not url:
        return "URLが空だし！"
    article = web_scraper.fetch_web_article(url)
    if not article:
        return "記事の取得に失敗したし。安全ではないURLかもしれないし。"
    if not article.get("safe", True):
        return "この記事は公序良俗フィルターに引っかかったため、読み込めないし！"
    return f"タイトル: {article['title']}\n本文抜粋:\n{article['body']}"

def learn_pronunciation_handler(args, **kw):
    """新しい言葉の読み方を自己学習としてデータベースに登録する"""
    clear_audio_on_tool_call()
    word = args.get("word", "").strip()
    pronunciation = args.get("pronunciation", "").strip()
    
    if not word or not pronunciation:
        return "単語または読み方が空だし！"
        
    success = ruri_memory.save_pronunciation(word, pronunciation)
    if success:
        return f"単語「{word}」の読み方を「{pronunciation}」として登録したし！次からその読み方で喋るよ。"
    else:
        return "登録に失敗したし。"

# 動的ツール登録
# registry.register(
#     name="speak", toolset="vtuber_tools",
#     schema={
#         "name": "speak",
#         "description": "リスナーに対して声を出して喋るし。発話の絶対先頭には [joy] や [jitome] などの感情キーを付与してね。",
#         "parameters": {
#             "type": "object",
#             "properties": {
#                 "text": {"type": "string", "description": "喋る日本語のメッセージ。るりのキャラクター口調（タメ口）で！"},
#                 "emotion": {"type": "string", "description": "発話時の感情。joy, happy, angry, sad, surprised, blush, jitome, wink, sweat, pale, neutral 等。"}
#             },
#             "required": ["text"]
#         }
#     },
#     handler=speak_handler, description="Speak message to listener"
# )

# registry.register(
#     name="speak_paragraphs", toolset="vtuber_tools",
#     schema={
#         "name": "speak_paragraphs",
#         "description": "複数の段落を連続して喋るし。段落ごとに異なる感情を指定できるし。",
#         "parameters": {
#             "type": "object",
#             "properties": {
#                 "paragraphs": {"type": "array", "items": {"type": "string"}, "description": "喋る段落テキストの配列。"},
#                 "emotions": {"type": "array", "items": {"type": "string"}, "description": "各段落に対応する感情の配列。"}
#             },
#             "required": ["paragraphs"]
#         }
#     },
#     handler=speak_paragraphs_handler, description="Speak multiple paragraphs"
# )

registry.register(
    name="set_glasses", toolset="vtuber_tools",
    schema={
        "name": "set_glasses",
        "description": "るりのインテリジェントメガネを着脱するし。",
        "parameters": {
            "type": "object",
            "properties": {
                "is_on": {"type": "boolean", "description": "メガネをかけるならtrue、外すならfalse。"}
            },
            "required": ["is_on"]
        }
    },
    handler=set_glasses_handler, description="Toggle glasses expression"
)

registry.register(
    name="query_listener_activity", toolset="vtuber_tools",
    schema={
        "name": "query_listener_activity",
        "description": "指定されたリスナーの過去の発言回数や最終閲覧日時などの活動履歴をDBからロードするし。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "リスナーのユーザー名。"}
            },
            "required": ["name"]
        }
    },
    handler=query_listener_activity_handler, description="Query listener active count"
)

registry.register(
    name="query_episodic_memory", toolset="vtuber_tools",
    schema={
        "name": "query_episodic_memory",
        "description": "指定されたリスナーについて過去に覚えた客観的な事実や好みなどの記憶を検索するし。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "リスナーのユーザー名。"},
                "query": {"type": "string", "description": "検索したい記憶のキーワード。"}
            },
            "required": ["name", "query"]
        }
    },
    handler=query_episodic_memory_handler, description="Query memory for a listener"
)

registry.register(
    name="save_episodic_memory", toolset="vtuber_tools",
    schema={
        "name": "save_episodic_memory",
        "description": "指定されたリスナーについての重要な事実や好みを記憶として永続保存するし。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "リスナーのユーザー名。"},
                "fact": {"type": "string", "description": "記憶すべき具体的な事実（例: 「リンゴが好き」「自作PCの冷却に悩んでいる」など）。"}
            },
            "required": ["name", "fact"]
        }
    },
    handler=save_episodic_memory_handler, description="Save memory for a listener"
)

registry.register(
    name="get_trending_news", toolset="vtuber_tools",
    schema={
        "name": "get_trending_news",
        "description": "IT/テック系の最新トレンドニュースや時事ネタを取得するし。",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    handler=get_trending_news_handler, description="Get tech trending news"
)

registry.register(
    name="search_web", toolset="vtuber_tools",
    schema={
        "name": "search_web",
        "description": "インターネット（DuckDuckGo）でウェブ検索を行い、最新情報や技術用語の解説を取得するし。【重要】queryにはリスナーが言った言葉をそのまま渡すこと。ボク自身の名前や余計なワードを絶対に追加しないこと。例：リスナーが「Fable 5」と言ったら query=\"Fable 5\" だけ。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "検索クエリ。リスナーの言葉をそのまま使うこと。自分の名前や比較ワードを追加しないこと。"}
            },
            "required": ["query"]
        }
    },
    handler=search_web_handler, description="Search the web"
)

registry.register(
    name="fetch_web_page", toolset="vtuber_tools",
    schema={
        "name": "fetch_web_page",
        "description": "指定されたURLのウェブページを取得して中身を要約するし。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "取得先URL。"}
            },
            "required": ["url"]
        }
    },
    handler=fetch_web_page_handler, description="Extract article body from URL"
)

registry.register(
    name="learn_pronunciation", toolset="vtuber_tools",
    schema={
        "name": "learn_pronunciation",
        "description": "リスナーから「Xの読み方はYだよ」「XはYと読むんだよ」のように正しい読み仮名（ひらがな・カタカナ）を提示されて、学習するように指示された時のみ、その単語と読み方をデータベースに登録して自己学習するツール。リスナーから読み方を「質問された時」には、絶対にこのツールを呼び出してはいけません。",
        "parameters": {
            "type": "object",
            "properties": {
                "word": {"type": "string", "description": "登録する単語（ユーザーから提示されたもの。例: 「三角関係」「EPYC」）。絶対に自分から勝手に抽出した単語を登録しないでください。"},
                "pronunciation": {"type": "string", "description": "ユーザーから教えてもらった正しいカタカナ・ひらがな表記の読み方（例: 「さんかくかんけい」「エピック」）。絶対に自分の推測やアルファベットごとのスペル読み（例: EPYCに対して「イーピーワイシー」）を登録しないでください。"}
            },
            "required": ["word", "pronunciation"]
        }
    },
    handler=learn_pronunciation_handler, description="Learn word pronunciation dynamically"
)

# -------------------------------------------------------------
# コメントキューとバックグラウンドエージェント実行ループ
# -------------------------------------------------------------
comment_queue = queue.Queue()
last_activity_time = time.time()
ruri_agent_thread = None

# SOUL.md（るりのアイデンティティ＆プロンプト）の読み込み
try:
    with open("SOUL.md", "r", encoding="utf-8") as f:
        soul_prompt = f.read()
except Exception as e:
    print(f"[WARNING] SOUL.mdが見つからないし: {e}")
    soul_prompt = "お前はAI VTuber「るり」だ。"

# ラジオ独り語り用システムプロンプトの尾部（soul_prompt に結合して使用）
# 「記事内容のまとめ→るりの感想・ツッコミ」の2部構成フロー
RADIO_SYSTEM_SUFFIX = """

【ラジオ独り語りモード：二部構成フロー】
お前は現在、生放送のラジオ独り語りコーナーを担当している。
次の「ニュース・トピック」を渡すので、必ず以下の2部分から構成して語ってください：

1. 第1部：記事内容の要約
- 渡された記事の内容を、リスナーにわかりやすく簡潔にかみ砕いて語れ。
  "今日のトピックはこれだし！"とリードして、記事の要点・概要をリスナーへわかりやすく喋る。
- 専門用語はそのまま使いながらも、知らない人にも会話調でザックリ説明する。

2. 第2部：るりの感想・主観・ツッコミ
- 記事内容に対するボクのガチテックな主観、強い具体的なこだわり、あるいはテンション感を込めて語る。
- 「これはやばいし！」「なんでこうなったの？」「ボクならこうするし」など、リスナーが共感または笑えるリアクションを盛り込む。

【超重要ルール】
- 出力テキストには「【第1部：記事内容の要約】」や「第2部：感想」などの見出しや構成ラベルを一切含めないでください。
- セクション分けのための記号（【】や第1部など）は絶対に出力せず、リスナーへ語りかける生のセリフだけを上から順に出力してください。
- 語尾は必ず「〜だよ」「〜じゃん？」「〜だし」「〜し！」「〜もんね」などの生意気で可愛いタメ口にしてください。「〜じゃ」「〜じゃが」「〜じゃから」「〜のう」「〜じゃの」といった老人のような口調（ジジくさい喋り）は【絶対に禁止】です！
- 発話ツール（speak や speak_paragraphs）は一切使用しない。
- 返答テキストとして直接出力する。
- 各文の冒頭に `[joy]` `[jitome]` `[surprised]` `[star]` などの感情タグを必ず付与する。
- 記事要約は `[neutral]` や `[star]` で落ち着いたトーン、感想・ツッコミは `[joy]` `[jitome]` `[angry]` などアクティブな感情で語る。
"""

# -------------------------------------------------------------
# 先読みバッファ（音声再生中に次ターンのLLM生成を先行実行）
# -------------------------------------------------------------
import threading as _threading

# 先読み結果を格納する辞書
# { "type": "radio"|"chat", "text": str, "news": dict|None,
#   "user_name": str, "user_message": str, "system_message": str }
prefetch_buffer: dict = {}
prefetch_lock = _threading.Lock()
prefetch_in_progress = False

def _make_agent(llm_provider_val, config_val, session_prefix):
    """LLMエージェントを生成するファクトリ（先読み・本番共通）"""
    import uuid
    if llm_provider_val == "gemini":
        gemini_cfg = config_val.get("gemini", {}) or {}
        return AIAgent(
            model=gemini_cfg.get("model", "gemini-1.5-flash"),
            api_key=gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or "dummy",
            base_url=gemini_cfg.get("api_url", "https://generativelanguage.googleapis.com/v1beta/openai/"),
            enabled_toolsets=["vtuber_tools"],
            quiet_mode=True,
            session_id=f"{session_prefix}_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            skip_memory=True
        )
    else:
        ollama_cfg = config_val.get("ollama", {}) or {}
        return AIAgent(
            model=ollama_cfg.get("model", "dsasai/llama3-elyza-jp-8b:latest"),
            api_key="dummy",
            base_url=ollama_cfg.get("api_url", "http://localhost:11434/v1"),
            enabled_toolsets=["vtuber_tools"],
            quiet_mode=True,
            session_id=f"{session_prefix}_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            skip_memory=True
        )

def _prefetch_next_radio():
    """次のラジオトピックをバックグラウンドで先読み生成する"""
    global prefetch_buffer, prefetch_in_progress
    try:
        llm_provider_val = config.get("llm_provider", "ollama").lower()
        news = web_scraper.get_trending_news()
        print(f"[🔮 先読み] 次のラジオトピックをLLMに投げているし: {news['title'][:40]}...")
        agent = _make_agent(llm_provider_val, config, "prefetch_radio")
        res = agent.run_conversation(
            user_message=f"次のトピックについて語ってください：\nタイトル: {news['title']}\n内容: {news['body']}\nURL: {news.get('url', '')}",
            system_message=soul_prompt + get_pronunciation_context() + RADIO_SYSTEM_SUFFIX
        )
        with prefetch_lock:
            prefetch_buffer = {
                "type": "radio",
                "text": res.get("text", "") if res else "",
                "news": news,
            }
        print(f"[🔮 先読み完了] ラジオトピック準備完了: {news['title'][:40]}")
    except Exception as e:
        print(f"[WARNING] 先読みラジオ生成に失敗したし: {e}")
    finally:
        prefetch_in_progress = False

def _prefetch_next_comment(user_name_val, comment_val, system_msg_val):
    """次のコメント返答をバックグラウンドで先読み生成する"""
    global prefetch_buffer, prefetch_in_progress
    try:
        llm_provider_val = config.get("llm_provider", "ollama").lower()
        print(f"[🔮 先読み] {user_name_val}のコメント返答をLLMに投げているし...")
        agent = _make_agent(llm_provider_val, config, "prefetch_chat")
        res = agent.run_conversation(
            user_message=f"{user_name_val}: {comment_val}",
            system_message=system_msg_val
        )
        with prefetch_lock:
            prefetch_buffer = {
                "type": "chat",
                "text": res.get("text", "") if res else "",
                "user_name": user_name_val,
                "user_message": comment_val,
                "system_message": system_msg_val,
            }
        print(f"[🔮 先読み完了] コメント返答準備完了 ({user_name_val})")
    except Exception as e:
        print(f"[WARNING] 先読みコメント生成に失敗したし: {e}")
    finally:
        prefetch_in_progress = False

def start_prefetch_radio():
    """先読みラジオ生成スレッドを起動する（多重起動防止付き）"""
    global prefetch_in_progress
    if config.get("boot_mode") == "chat":
        return
    with prefetch_lock:
        if prefetch_in_progress:
            return
        prefetch_in_progress = True
    t = _threading.Thread(target=_prefetch_next_radio, daemon=True)
    t.start()

def start_prefetch_comment(user_name_val, comment_val, system_msg_val):
    """先読みコメント生成スレッドを起動する（多重起動防止付き）"""
    global prefetch_in_progress
    with prefetch_lock:
        if prefetch_in_progress:
            return
        prefetch_in_progress = True
    t = _threading.Thread(target=_prefetch_next_comment, args=(user_name_val, comment_val, system_msg_val), daemon=True)
    t.start()

def consume_prefetch(expected_type=None):
    """先読みバッファから結果を取り出す。型が合わない場合はNoneを返す"""
    with prefetch_lock:
        if not prefetch_buffer:
            return None
        if expected_type and prefetch_buffer.get("type") != expected_type:
            return None
        result = prefetch_buffer.copy()
        prefetch_buffer.clear()
        return result

def get_pronunciation_context():
    """データベースから登録されている読み方リストをロードしてコンテキストテキストを生成する"""
    try:
        pronunciations_dict = ruri_memory.get_all_pronunciations()
        if pronunciations_dict:
            pronunciations_list = "\n".join([f"- {k}: {v}" for k, v in pronunciations_dict.items()])
            return f"\n\n【登録されている単語の正しい読み方リスト（TTSでの発音設定）】\nこのリストにある英単語や略称を返答テキストに含める場合、または読み方を尋ねられた場合は、必ずこの読み方に従ってください：\n{pronunciations_list}"
    except Exception as e:
        print(f"[WARNING] 読み方リストのロードに失敗したし: {e}")
    return ""

def run_agent_loop():
    """バックグラウンドでチャットキューの監視と、スリープ時独り語り（ラジオ）を実行するメインループ"""
    global last_activity_time, voice_generated_during_run, spoken_texts_during_run, speak_count_during_run
    
    print("[🧠 エージェント] エージェント制御ループを開始したし！")
    
    # 環境変数 HERMES_HOME の強制適用（ローカル config.yaml を読ませる）
    os.environ["HERMES_HOME"] = os.path.join(os.getcwd(), ".hermes")
    
    # LLMプロバイダとモデル名の決定
    llm_provider = config.get("llm_provider", "ollama").lower()
    if llm_provider == "gemini":
        gemini_cfg = config.get("gemini", {}) or {}
        model_name = gemini_cfg.get("model", "gemini-1.5-flash")
    else:
        model_name = config.get("ollama", {}).get("model", "dsasai/llama3-elyza-jp-8b:latest")
        
    is_gemini = (llm_provider == "gemini")
    print(f"[👓 メガネ自動制御] 起動時判定 - プロバイダ: {llm_provider}, モデル: {model_name} (Geminiモード: {is_gemini})")
    try:
        unity_osc.send_unity_glasses(is_gemini)
        global_status["glasses_on"] = is_gemini
        save_status_to_disk()
    except Exception as e:
        print(f"[WARNING] 起動時メガネ自動制御に失敗したし: {e}")

    while True:
        try:
            # キューにコメントが来ているかチェック（ノンブロッキング）
            try:
                user_name, comment = comment_queue.get(timeout=1.0)
                broadcast_event("status", {"queue_size": comment_queue.qsize()})
            except queue.Empty:
                if config.get("boot_mode") == "chat":
                    continue
                now = time.time()
                radio_cfg = config.get("radio", {}) or {}
                idle_timeout = float(radio_cfg.get("idle_timeout", 20.0))
                # ラジオ→ラジオ間の間隔（inter_topic_gap）は idle_timeout とは独立して設定可能
                # 未設定の場合は idle_timeout をそのまま使う
                inter_topic_gap = float(radio_cfg.get("inter_topic_gap", idle_timeout))
                speaking_end = max(last_activity_time, playback_finish_time)
                if now - speaking_end > inter_topic_gap:
                    print(f"\n[📻 ラジオモード] リスナーのコメントが途絶えたため、独り語りを開始するし！")
                    last_activity_time = now

                    # 先読みキャッシュを確認する
                    cached = consume_prefetch(expected_type="radio")

                    if cached and cached.get("text"):
                        # ======= 先読みヒット：即座に配信開始 =======
                        news = cached["news"]
                        topic_title = news["title"]
                        print(f"[⚡ 先読みヒット] ラジオトピックをキャッシュから即座に使用するし: {topic_title[:40]}")
                        broadcast_event("status", {
                            "mode": "radio",
                            "topic": topic_title,
                            "user_name": "システム",
                            "user_comment": "トレンドニュース紹介（先読み）"
                        })
                        broadcast_event("thinking", "")
                        broadcast_event("reasoning", "")
                        if audio_handler:
                            audio_handler.prepare_new_turn()
                            # 先読みテキストをトークンとして流し込む
                            for char in cached["text"]:
                                audio_handler.handle_token(char)
                            audio_handler.finalize()
                            audio_handler.wait_for_playback()
                        else:
                            speak_handler({"text": cached["text"], "emotion": "neutral"})
                    else:
                        # ======= キャッシュなし：通常の同期生成 =======
                        # 1. ニュースの取得
                        news = web_scraper.get_trending_news()
                        topic_title = news["title"]

                        # 2. 状態の更新
                        broadcast_event("status", {
                            "mode": "radio",
                            "topic": topic_title,
                            "user_name": "システム",
                            "user_comment": "トレンドニュース紹介"
                        })

                        # 3. エージェントの初期化と実行
                        broadcast_event("thinking", "トレンドニュースについて考えているし...")
                        broadcast_event("reasoning", "")

                        if audio_handler:
                            audio_handler.prepare_new_turn()

                        import uuid
                        if llm_provider == "gemini":
                            gemini_cfg = config.get("gemini", {}) or {}
                            agent_model = gemini_cfg.get("model", "gemini-1.5-flash")
                            agent_api_key = gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or "dummy"
                            agent_base_url = gemini_cfg.get("api_url", "https://generativelanguage.googleapis.com/v1beta/openai/")
                        else:
                            ollama_cfg = config.get("ollama", {}) or {}
                            agent_model = ollama_cfg.get("model", "dsasai/llama3-elyza-jp-8b:latest")
                            agent_api_key = "dummy"
                            agent_base_url = ollama_cfg.get("api_url", "http://localhost:11434/v1")

                        agent = AIAgent(
                            model=agent_model,
                            api_key=agent_api_key,
                            base_url=agent_base_url,
                            enabled_toolsets=["vtuber_tools"],
                            quiet_mode=True,
                            thinking_callback=lambda text: broadcast_event("thinking", text),
                            reasoning_callback=lambda text: broadcast_event("reasoning", text),
                            stream_delta_callback=audio_handler.handle_token if audio_handler else None,
                            session_id=f"radio_{int(time.time())}_{uuid.uuid4().hex[:8]}",
                            skip_memory=True
                        )

                        soliloquy_system = soul_prompt + get_pronunciation_context() + RADIO_SYSTEM_SUFFIX

                        voice_generated_during_run = False
                        spoken_texts_during_run = []
                        speak_count_during_run = 0

                        # エージェント実行
                        res = agent.run_conversation(
                            user_message=f"次のトピックについて語ってください：\nタイトル: {news['title']}\n内容: {news['body']}\nURL: {news.get('url', '')}",
                            system_message=soliloquy_system
                        )

                        if audio_handler:
                            audio_handler.finalize()
                            audio_handler.wait_for_playback()
                        else:
                            if res and res.get("text"):
                                speak_handler({"text": res["text"], "emotion": "neutral"})

                    broadcast_event("thinking", "")
                    print("[📻 ラジオモード] トピックの独り語りが終了したし。")

                    # ===== 再生終了後の処理 =====
                    # last_activity_time を「再生終了時刻」にセットする
                    # これにより次のトピック開始までの待ち時間が
                    # 「音声時間 + inter_topic_gap」ではなく「inter_topic_gap」だけになる
                    last_activity_time = max(time.time(), playback_finish_time)

                    # 次のラジオトピックを先読み開始（キューが空の場合のみ）
                    if comment_queue.empty():
                        start_prefetch_radio()

                continue

            # コメントを受信した場合の対話処理
            now = time.time()
            if now < playback_finish_time:
                wait_time = playback_finish_time - now
                print(f"[⏳ 待機] トピック読み上げ完了まで {wait_time:.2f}秒 待機するし...")
                time.sleep(wait_time)

            last_activity_time = time.time()
            print(f"\n[💬 対話モード] {user_name}からのコメントを処理中: {comment}")

            # SSE送信（ユーザーコメント）：モード切替と同時に前の発言テキストをクリア
            broadcast_event("status", {
                "mode": "chat",
                "user_name": user_name,
                "user_comment": comment,
                "ruri_msg": ""   # ラジオの残留テキストをリセット
            })

            # リスナーの過去記憶・統計をDBからロード
            activity = ruri_memory.get_user_activity(user_name) or {}

            # セマンティック検索による関連記憶の検索
            related_memories = ruri_memory.search_memories(user_name, comment, limit=3)
            # 直近の記憶の取得
            recent_memories = ruri_memory.get_user_recent_memories(user_name, limit=5)

            # 重複を除いて統合する
            unique_memories = []
            for mem in related_memories:
                content = mem.get("content")
                if content and content not in unique_memories:
                    unique_memories.append(content)
            for content in recent_memories:
                if content not in unique_memories:
                    unique_memories.append(content)

            memory_text = "\n".join([f"- {m}" for m in unique_memories]) if unique_memories else "特になし"

            # リスナー特有のコンテキスト情報
            user_context = f"\n\n【現在会話中のリスナー情報】\n名前: {user_name}\n発言回数: {activity.get('message_count', 1)}回\n前回会った日時: {activity.get('prev_seen', '')} (現在時間: {time.time()})\n前回のメッセージ: {activity.get('prev_message', '')}\n覚えている記憶:\n{memory_text}"

            prompt_instruction = "\n\n【重要：発話と感情表現】\n会話の返答（喋る内容）は、`speak` などの発話ツールを呼び出すのではなく、**返答テキストとして直接出力**してください。また、感情を表現するために、各文の冒頭に `[joy]` や `[jitome]` などの感情タグを必ず付与してください。（例: `[joy]ハローお前ら！[wink]今日も楽しんでいこうだし！`）。"
            full_system_msg = soul_prompt + user_context + get_pronunciation_context() + prompt_instruction

            # 先読みキャッシュを確認する（同一ユーザー＆コメントのみ適用）
            cached = consume_prefetch(expected_type="chat")
            if (cached
                    and cached.get("user_name") == user_name
                    and cached.get("user_message") == comment
                    and cached.get("text")):
                # ======= 先読みヒット =======
                print(f"[⚡ 先読みヒット] {user_name}のコメント返答をキャッシュから即座に使用するし")
                broadcast_event("thinking", "")
                broadcast_event("reasoning", "")
                if audio_handler:
                    audio_handler.prepare_new_turn()
                    for char in cached["text"]:
                        audio_handler.handle_token(char)
                    audio_handler.finalize()
                    audio_handler.wait_for_playback()
                    final_spoken_text = re.sub(r'\[[a-zA-Z0-9_]+\]', '', cached["text"]).strip()
                else:
                    speak_handler({"text": cached["text"], "emotion": "neutral"})
                    final_spoken_text = cached["text"]
            else:
                # ======= キャッシュなし：通常の同期生成 =======
                if audio_handler:
                    audio_handler.prepare_new_turn()

                broadcast_event("thinking", f"{user_name}のコメントに返答を考えているし...")
                broadcast_event("reasoning", "")

                import uuid
                if llm_provider == "gemini":
                    gemini_cfg = config.get("gemini", {}) or {}
                    agent_model = gemini_cfg.get("model", "gemini-1.5-flash")
                    agent_api_key = gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or "dummy"
                    agent_base_url = gemini_cfg.get("api_url", "https://generativelanguage.googleapis.com/v1beta/openai/")
                else:
                    ollama_cfg = config.get("ollama", {}) or {}
                    agent_model = ollama_cfg.get("model", "dsasai/llama3-elyza-jp-8b:latest")
                    agent_api_key = "dummy"
                    agent_base_url = ollama_cfg.get("api_url", "http://localhost:11434/v1")

                agent = AIAgent(
                    model=agent_model,
                    api_key=agent_api_key,
                    base_url=agent_base_url,
                    enabled_toolsets=["vtuber_tools"],
                    quiet_mode=True,
                    thinking_callback=lambda text: broadcast_event("thinking", text),
                    reasoning_callback=lambda text: broadcast_event("reasoning", text),
                    stream_delta_callback=audio_handler.handle_token if audio_handler else None,
                    session_id=f"chat_{int(time.time())}_{uuid.uuid4().hex[:8]}",
                    skip_memory=True
                )

                voice_generated_during_run = False
                spoken_texts_during_run = []
                speak_count_during_run = 0

                res = agent.run_conversation(
                    user_message=f"{user_name}: {comment}",
                    system_message=full_system_msg
                )

                if audio_handler:
                    audio_handler.finalize()
                    audio_handler.wait_for_playback()

                final_spoken_text = ""
                if audio_handler:
                    final_spoken_text = res.get("text", "") if res else ""
                    final_spoken_text = re.sub(r'\[[a-zA-Z0-9_]+\]', '', final_spoken_text).strip()
                else:
                    if not voice_generated_during_run and res and res.get("text"):
                        speak_handler({"text": res["text"], "emotion": "neutral"})
                        final_spoken_text = res["text"]
                    else:
                        final_spoken_text = " ".join(spoken_texts_during_run)

                broadcast_event("thinking", "")

            # 非同期でリスナーに関する事実抽出＆SQLite記憶保存を開始
            if final_spoken_text:
                ruri_memory.save_memory_async(user_name, comment, final_spoken_text)

            comment_queue.task_done()
            broadcast_event("status", {"queue_size": comment_queue.qsize()})

            # ===== 再生終了後：次のラジオトピックを先読み開始（コメントキューが空の場合のみ）=====
            if comment_queue.empty():
                start_prefetch_radio()
            
        except Exception as ex:
            print(f"[ERROR] エージェントループ内で重大な例外が発生したし: {ex}")
            broadcast_event("thinking", "エラーが発生して一時的にフリーズしたし...")
            time.sleep(5)

import re
import base64

active_audio_websockets = []

from concurrent.futures import ThreadPoolExecutor

class AudioStreamHandler:
    def __init__(self, loop):
        self.loop = loop
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.text_buffer = ""
        self.sentence_index = 0
        self.next_send_index = 0
        self.total_duration = 0.0
        self.tasks = []
        self.sentence_pattern = re.compile(r'[^。！？\n]+[。！？\n]')
        self.emotion_pattern = re.compile(r'^\[([a-zA-Z0-9_]+)\]')
        self.current_emotion = "neutral"

    def reset(self):
        global playback_finish_time
        playback_finish_time = 0.0
        self.text_buffer = ""
        self.sentence_index = 0
        self.next_send_index = 0
        self.total_duration = 0.0
        self.tasks = []
        self.current_emotion = "neutral"
        # 割り込みクリア処理は対話受信時などに限定し、reset()では内部変数の初期化のみ行う

    def prepare_new_turn(self):
        global playback_finish_time
        is_speaking = time.time() < playback_finish_time
        if not is_speaking:
            self.reset()
        else:
            # 現在再生中のため、クリア信号は送らず、インデックスもリセットしない
            # 新しいターンの送信タスクを追跡するため tasks だけを初期化する
            self.tasks = []

    async def send_clear_signal(self):
        payload = {"type": "clear"}
        disconnected = []
        for ws in active_audio_websockets:
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            if ws in active_audio_websockets:
                active_audio_websockets.remove(ws)

    def handle_token(self, token: str):
        if not token:
            return
        self.text_buffer += token
        
        matches = list(self.sentence_pattern.finditer(self.text_buffer))
        if matches:
            last_end = 0
            for match in matches:
                sentence = match.group().strip()
                if sentence:
                    idx = self.sentence_index
                    self.sentence_index += 1
                    fut = asyncio.run_coroutine_threadsafe(
                        self.process_and_send(idx, sentence),
                        self.loop
                    )
                    self.tasks.append(fut)
                last_end = match.end()
            self.text_buffer = self.text_buffer[last_end:]

    def finalize(self):
        remaining = self.text_buffer.strip()
        if remaining:
            idx = self.sentence_index
            self.sentence_index += 1
            fut = asyncio.run_coroutine_threadsafe(
                self.process_and_send(idx, remaining),
                self.loop
            )
            self.tasks.append(fut)
        self.text_buffer = ""

    async def send_to_all_ws(self, payload):
        disconnected = []
        for ws in active_audio_websockets:
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            if ws in active_audio_websockets:
                active_audio_websockets.remove(ws)

    def wait_for_playback(self):
        # WAVファイルの生成とWebSocketへの送信（配信）タスクの完了だけを同期的に待つ
        for fut in self.tasks:
            try:
                fut.result()
            except Exception as e:
                print(f"[ERROR] 音声配信タスクエラー: {e}")
        
        # 全タスクが送信完了したので、クライアントに再生開始のGOサインを送る
        print("[🔊 Audio] すべての音声パケットの送信が完了したし。クライアントに playback_ready を送るし！")
        asyncio.run_coroutine_threadsafe(
            self.send_to_all_ws({"type": "playback_ready"}),
            self.loop
        )

        self.tasks = []
        self.total_duration = 0.0

    async def process_and_send(self, idx: int, text: str):
        emotion = self.current_emotion
        match = self.emotion_pattern.match(text)
        if match:
            emotion = match.group(1)
            self.current_emotion = emotion
            text = text[match.end():].strip()
        
        # 【第1部：要約】や「第2部：感想」などの不要な見出しラベル・記号を完全除去
        text = re.sub(r'【[^】]*】', '', text)
        text = re.sub(r'^第[0-9一二三四五]部\s*[：:]?\s*', '', text)

        text = re.sub(r'\[[a-zA-Z0-9_]+\]', '', text).strip()
        if not text:
            while self.next_send_index != idx:
                await asyncio.sleep(0.02)
            self.next_send_index += 1
            return

        print(f"[🎤 Stream Sentence] [{emotion}] {text}")

        # スロット競合や過負荷を防ぐため、専用のシングルスレッドExecutorで実行
        wav_path = await self.loop.run_in_executor(
            self.executor,
            voice.generate_voice_wav,
            text,
            emotion
        )
        
        audio_base64 = ""
        duration = 1.0
        if wav_path and os.path.exists(wav_path):
            try:
                duration = await self.loop.run_in_executor(
                    self.executor,
                    voice.get_wav_duration,
                    wav_path
                )
                
                def read_and_encode():
                    with open(wav_path, "rb") as f:
                        return base64.b64encode(f.read()).decode('utf-8')
                
                encoded = await self.loop.run_in_executor(self.executor, read_and_encode)
                audio_base64 = "data:audio/wav;base64," + encoded
                os.remove(wav_path)
            except Exception as e:
                print(f"[ERROR] 音声ファイルの読み込み/削除に失敗したし: {e}")
                if os.path.exists(wav_path):
                    os.remove(wav_path)

        while self.next_send_index != idx:
            await asyncio.sleep(0.02)

        if audio_base64:
            self.total_duration += duration
            
            # 再生完了予定時刻の更新
            global playback_finish_time
            now = time.time()
            if playback_finish_time < now:
                playback_finish_time = now + duration
            else:
                playback_finish_time += duration

            payload = {
                "audio": audio_base64,
                "text": text,
                "emotion": emotion,
                "duration": duration,
                "idx": idx,
                "status": {
                    "mode": global_status.get("mode"),
                    "topic": global_status.get("topic"),
                    "user_name": global_status.get("user_name"),
                    "user_comment": global_status.get("user_comment")
                }
            }
            
            disconnected = []
            for ws in active_audio_websockets:
                try:
                    await ws.send_json(payload)
                except Exception:
                    disconnected.append(ws)
            
            for ws in disconnected:
                if ws in active_audio_websockets:
                    active_audio_websockets.remove(ws)



            status_update = {
                "ruri_msg": text,
                "emotion": emotion
            }
            broadcast_event("status", status_update)

        self.next_send_index += 1

audio_handler = None

# -------------------------------------------------------------
# FastAPI サーバーの定義とルーティング
# -------------------------------------------------------------
app = FastAPI(title="Ruri VTuber Core Backend Server")

from fastapi import WebSocket, WebSocketDisconnect

@app.websocket("/ws/audio")
async def websocket_audio_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_audio_websockets.append(websocket)
    print(f"[🔌 Audio WS] クライアントが接続したし。接続数: {len(active_audio_websockets)}")
    try:
        while True:
            data_str = await websocket.receive_text()
            try:
                data = json.loads(data_str)
                if data.get("type") == "play_start":
                    emotion = data.get("emotion", "neutral")
                    try:
                        # 音声再生の瞬間に合わせてUnity OSCへ送信
                        unity_osc.send_unity_emotion(emotion, is_speaking=True)
                    except Exception as e:
                        print(f"[WARNING] Unity OSCへの送信失敗: {e}")
            except Exception:
                pass
    except WebSocketDisconnect:
        print("[🔌 Audio WS] クライアントが切断したし。")
    finally:
        if websocket in active_audio_websockets:
            active_audio_websockets.remove(websocket)

# SSE配信用のエンドポイント
@app.get("/api/events")
async def events_endpoint(request: Request):
    """リアルタイムに状態変化や思考ログをストリーム配信する"""
    async def sse_generator():
        q = asyncio.Queue()
        active_listeners.append(q)
        try:
            # 接続時に現在のステータスを即時送信
            init_event = f"event: status\ndata: {json.dumps(global_status, ensure_ascii=False)}\n\n"
            yield init_event
            
            while True:
                # 切断チェック
                if await request.is_disconnected():
                    break
                event = await q.get()
                yield event
        finally:
            active_listeners.remove(q)

    # sse-starlette のインポートなしで標準の FastAPI StreamingResponse を用いた実装
    from fastapi.responses import StreamingResponse
    return StreamingResponse(sse_generator(), media_type="text/event-stream")

@app.get("/api/status")
@app.get("/ruri_status.json")
async def get_status():
    """現在のステータスをJSONで返す（ポーリング用）"""
    return JSONResponse(content=global_status)

@app.get("/api/config")
async def get_web_config():
    """VTube Studioなどのフロントエンド用設定情報を取得する"""
    vts_cfg = config.get("vts", {}) or {}
    gemini_cfg = config.get("gemini", {}) or {}
    audio_cfg = config.get("audio", {}) or {}
    return JSONResponse(content={
        "vts_enabled": vts_cfg.get("enabled", True),
        "vts_port": vts_cfg.get("port", 8001),
        "vts_emotions": vts_cfg.get("emotions", {}),
        "glasses_vts_expression": gemini_cfg.get("glasses_vts_expression", "glasses.exp3.json"),
        "volume": audio_cfg.get("volume", 1.0)
    })

@app.post("/api/comment")
async def post_comment(req: Request):
    """手動コメント模擬送信（または外部連携用）"""
    data = await req.json()
    user_name = data.get("user_name", "テックオタク").strip()
    comment = data.get("comment", "").strip()
    
    if not comment:
        raise HTTPException(status_code=400, detail="Comment cannot be empty.")
        
    print(f"[📥 外部コメント入力] {user_name}: {comment}")
    # ユーザーの活動を先行記録
    ruri_memory.record_user_activity(user_name, comment)
    comment_queue.put((user_name, comment))
    broadcast_event("status", {"queue_size": comment_queue.qsize()})
    
    return {"status": "queued", "user_name": user_name, "comment": comment}

@app.post("/api/motion")
async def trigger_motion(req: Request):
    """手動でUnityにモーションと表情を送信する"""
    data = await req.json()
    motion = data.get("motion", "").strip()
    emotion = data.get("emotion", "").strip()
    
    print(f"[🕹️ 手動モーション] motion: {motion}, emotion: {emotion}")
    try:
        if emotion:
            unity_osc.send_unity_emotion(emotion, is_speaking=False)
            broadcast_event("status", {"emotion": emotion})
        if motion:
            unity_osc.send_unity_motion(motion)
        return {"status": "success", "motion": motion, "emotion": emotion}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/sleep")
async def trigger_sleep():
    """記憶整理のsleep consolidation処理を呼び出す"""
    print("[😴 スリープ要請] 記憶整理スクリプトを非同期起動するし！")
    
    def run_sleep():
        try:
            import subprocess
            res = subprocess.run([sys.executable, "ruri_sleep.py"], capture_output=True, text=True)
            print("[😴 記憶整理完了]")
            print(res.stdout)
            if res.stderr:
                print("[WARNING]", res.stderr)
        except Exception as e:
            print(f"[ERROR] 記憶整理スクリプト起動失敗: {e}")
            
    threading.Thread(target=run_sleep, daemon=True).start()
    return {"status": "triggered"}

# YouTube ライブチャット監視タスク
def youtube_chat_polling_loop():
    """YouTube配信が有効でチャット投稿・監視用ライブラリpytchatが使える場合、コメントを自動回収してキューに流す"""
    # 接続確認用の動画ID設定が yaml にあるか確認
    video_id = config.get("youtube_chat", {}).get("video_id")
    if not video_id:
        print("[💬 YouTubeチャット監視] ruri_config.yaml に video_id が設定されてないため、監視はスキップするし。")
        return
        
    print(f"[📡 YouTubeチャット監視] 動画ID: {video_id} に対するチャット取得ループを開始するし！")
    
    try:
        import pytchat
        chat = pytchat.create(video_id=video_id)
        
        while chat.is_alive():
            data = chat.get()
            if data and data.items:
                for c in data.items:
                    author = c.author.name
                    msg = c.message
                    
                    # 無限ループや自己言及メッセージの無視
                    if author == config["character"]["name"] or "のニュースについて話してるし！" in msg:
                        continue
                        
                    # ユーザーの活動をDBに記録
                    ruri_memory.record_user_activity(author, msg)
                    
                    print(f"[💬 YouTubeチャット] {author}: {msg}")
                    comment_queue.put((author, msg))
            time.sleep(1.0)
            
    except Exception as e:
        print(f"[WARNING] YouTubeチャット監視ループ内でエラーが発生したし: {e}")

# 静的ファイルの提供設定（static/ をマウント）
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    """Web コントロールパネル"""
    try:
        with open("static/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Dashboard UI (index.html) not found. Create it in static/ first.")

@app.get("/overlay", response_class=HTMLResponse)
@app.get("/comment.html", response_class=HTMLResponse)
async def serve_overlay():
    """OBS ブラウザソース用オーバーレイ"""
    try:
        with open("static/overlay.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Overlay source (overlay.html) not found. Create it in static/ first.")

@app.get("/")
async def root_redirect():
    """デフォルトアクセスをダッシュボードへリダイレクト"""
    return RedirectResponse(url="/dashboard")

# FastAPI 起動時にバックグラウンドスレッドを走らせる
@app.on_event("startup")
async def startup_event():
    global async_loop, ruri_agent_thread, audio_handler
    async_loop = asyncio.get_running_loop()
    audio_handler = AudioStreamHandler(async_loop)
    
    # エージェントメインループの開始
    ruri_agent_thread = threading.Thread(target=run_agent_loop, daemon=True)
    ruri_agent_thread.start()
    
    # YouTubeチャット監視スレッドの開始
    yt_enabled = config.get("youtube_chat", {}).get("enabled", True)
    if yt_enabled:
        threading.Thread(target=youtube_chat_polling_loop, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    print("[🌐 Web Server] サーバーを http://0.0.0.0:8000 で起動するし！ (LAN内アクセスも可能だし)")
    uvicorn.run(app, host="0.0.0.0", port=8000)
