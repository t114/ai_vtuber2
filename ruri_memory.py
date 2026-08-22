import ruri_env
import sqlite3
import json
import requests
import time
import threading
import yaml
import os

# 設定ファイルの読み込み
try:
    with open('ruri_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except Exception:
    config = {}

ollama_config = config.get("ollama", {}) or {}
api_url_raw = ollama_config.get("api_url", "http://localhost:11434/v1")

# Extract the base host (e.g. http://localhost:11434)
base_host = api_url_raw
if "/v1" in base_host:
    base_host = base_host.split("/v1")[0]
elif "/api/chat" in base_host:
    base_host = base_host.split("/api/chat")[0]
base_host = base_host.rstrip("/")

OLLAMA_CHAT_URL = f"{base_host}/api/chat"
OLLAMA_EMBED_URL = f"{base_host}/api/embed"
MODEL_NAME = ollama_config.get("model", "qwen3:14b")

def cosine_similarity(v1, v2):
    """2つのベクトルのコサイン類似度を計算する"""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_a = sum(a * a for a in v1) ** 0.5
    norm_b = sum(b * b for b in v2) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)

def init_db(clean=False):
    """データベースとテーブルの初期化。clean=Trueの場合は既存DBを削除してクリーンな状態で起動する。"""
    db_path = "ruri_memory.db"
    if clean and os.path.exists(db_path):
        try:
            os.remove(db_path)
            print("[💾 データベース] 既存のデータベースを削除し、クリーンな状態で再生成するし！")
        except Exception as e:
            print(f"[WARNING] データベースファイルの削除に失敗したし: {e}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # memories テーブルの作成
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT,
            content TEXT,
            embedding TEXT,
            emotion TEXT,
            importance REAL,
            created_at REAL,
            last_accessed REAL,
            access_count INTEGER DEFAULT 0,
            category TEXT DEFAULT 'episodic'
        )
        """)
        
        # memory_links テーブルの作成
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_links (
            source_id INTEGER,
            target_id INTEGER,
            similarity REAL,
            PRIMARY KEY (source_id, target_id)
        )
        """)
        
        # user_activities テーブルの作成（初見判定用）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_activities (
            user_name TEXT PRIMARY KEY,
            first_seen REAL,
            last_seen REAL,
            prev_seen REAL,
            message_count INTEGER DEFAULT 1,
            last_message TEXT,
            prev_message TEXT
        )
        """)
        
        # pronunciations テーブルの作成（発音置換用）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pronunciations (
            word TEXT PRIMARY KEY,
            pronunciation TEXT,
            created_at REAL
        )
        """)
        
        conn.commit()
        conn.close()
        print("[💾 データベース] ruri_memory.db の初期化に成功したし！")
    except Exception as e:
        print(f"[ERROR] データベースの初期化に失敗したし: {e}")

def get_embedding(text):
    """設定されたLLMプロバイダ(Ollama/Gemini)に合わせて埋め込みベクトルを取得する"""
    llm_provider = config.get("llm_provider", "ollama").lower()
    if llm_provider == "gemini":
        try:
            gemini_cfg = config.get("gemini", {}) or {}
            api_key = gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or ""
            # Gemini Embedding API
            url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
            payload = {
                "model": "models/text-embedding-004",
                "content": {
                    "parts": [{"text": text}]
                }
            }
            res = requests.post(url, json=payload, timeout=15)
            res.raise_for_status()
            data = res.json()
            values = data.get("embedding", {}).get("values")
            if values:
                return values
        except Exception as e:
            print(f"[WARNING] Gemini埋め込みベクトルの生成に失敗したし: {e}")
        return None
    else:
        try:
            payload = {
                "model": "nomic-embed-text",
                "input": text
            }
            res = requests.post(OLLAMA_EMBED_URL, json=payload, timeout=15)
            res.raise_for_status()
            data = res.json()
            embeddings = data.get("embeddings")
            if embeddings and len(embeddings) > 0:
                return embeddings[0]
        except Exception as e:
            print(f"[WARNING] Ollama埋め込みヹクトルの生成に失敗したし: {e}")
        return None

def search_memories(user_name, query_text, limit=3):
    """
    クエリテキストとセマンティックに類似する過去の記憶を検索する。
    発言ユーザー名の一致、新しさ（Freshness）、想起回数を考慮したスコアリングを行う。
    """
    query_vector = get_embedding(query_text)
    if not query_vector:
        return []

    try:
        conn = sqlite3.connect("ruri_memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM memories")
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"[ERROR] 記憶の検索中にデータベース読み込み失敗: {e}")
        return []

    scored_memories = []
    now = time.time()

    for row in rows:
        try:
            mem_vector = json.loads(row["embedding"])
        except Exception:
            continue

        # コサイン類似度計算
        similarity = cosine_similarity(query_vector, mem_vector)

        # 時間減衰（半減期14日）
        days_elapsed = (now - row["created_at"]) / 86400.0
        freshness = (2.0 ** (-days_elapsed / 14.0)) * 0.3 + 0.7  # 0.7〜1.0

        # 特定ユーザーに対する一致ボーナス
        user_boost = 1.35 if row["user_name"] == user_name else 1.0

        # アクセス頻度（想起されやすさ）のボーナス
        freq_boost = min(1.2, 1.0 + row["access_count"] * 0.03)

        # 総合スコア
        score = similarity * freshness * user_boost * freq_boost

        # コサイン類似度が一定以上（例えば0.35以上）のもののみ有効とする
        if similarity >= 0.35:
            scored_memories.append((row, score))

    # スコア順にソートして上位limit件を取得
    scored_memories.sort(key=lambda x: x[1], reverse=True)
    top_results = scored_memories[:limit]

    # 想起された記憶の統計情報を更新
    if top_results:
        try:
            conn = sqlite3.connect("ruri_memory.db")
            cursor = conn.cursor()
            for row, _ in top_results:
                cursor.execute(
                    "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                    (now, row["id"])
                )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[WARNING] 記憶のアクセス統計の更新に失敗したし: {e}")

    return [dict(row) for row, _ in top_results]

def save_memory_worker(user_name, user_comment, ruri_response):
    """
    AIAgentを用いて会話から覚えるべき事実を抽出し、
    save_episodic_memory ツールを自律的に呼び出してSQLiteに保存する。
    """
    from run_agent import AIAgent
    import uuid
    
    # 覚えるべき事実の抽出とツールの使用を指示するプロンプト
    system_prompt = (
        "お前はAI VTuber「るり」の記憶整理システム（Backman）だ。\n"
        "直前の会話ログを分析し、リスナーに関する新しい客観的な事実（好み、予定、趣味、特徴など）があれば、`save_episodic_memory` ツールを呼び出して記憶してください。\n"
        "もし覚えるべき新しい事実がない場合、または単なる挨拶や挨拶の返しである場合は、ツールを呼び出さずに「何も記憶しませんでした」とだけ出力して終了してください。\n\n"
        "【ツール呼び出しの引数ルール】\n"
        f"- name: 会話しているリスナーの名前（今回は '{user_name}'）\n"
        "- fact: 抽出したリスナーに関する簡潔で客観的な事実（例: 「自作PCの冷却に悩んでいる」）\n\n"
        "【注意】るり（自分）に関する情報や、単なる挨拶などは記憶しないでください。"
    )
    
    user_message = (
        f"以下の会話から、リスナーに関する覚えるべき事実を抽出して記憶してください。\n\n"
        f"リスナー: {user_name}\n"
        f"コメント: {user_comment}\n"
        f"るりの返答: {ruri_response}"
    )

    llm_provider = config.get("llm_provider", "ollama").lower()
    
    if llm_provider == "gemini":
        gemini_cfg = config.get("gemini", {}) or {}
        agent = AIAgent(
            model=gemini_cfg.get("model", "gemini-1.5-flash"),
            api_key=gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or "dummy",
            base_url=gemini_cfg.get("api_url", "https://generativelanguage.googleapis.com/v1beta/openai/"),
            enabled_toolsets=["vtuber_tools"],
            quiet_mode=True,
            session_id=f"memory_save_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            skip_memory=True
        )
    else:
        ollama_config = config.get("ollama", {}) or {}
        agent = AIAgent(
            model=ollama_config.get("model", "qwen3:14b"),
            api_key="dummy",
            base_url=ollama_config.get("api_url", "http://localhost:11434/v1"),
            enabled_toolsets=["vtuber_tools"],
            quiet_mode=True,
            session_id=f"memory_save_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            skip_memory=True
        )
        
    try:
        print(f"[🧠 エージェント記憶整理] {user_name}との会話から記憶すべき事実があるかエージェントが判断中...")
        res = agent.run_conversation(
            user_message=user_message,
            system_message=system_prompt
        )
        print(f"[🧠 エージェント記憶整理完了] エージェントの返答: {res.get('text', '').strip()}")
    except Exception as e:
        print(f"[WARNING] エージェントによる記憶の自動整理に失敗したし: {e}")

def save_memory_async(user_name, user_comment, ruri_response):
    """非同期で記憶の自動整理スレッドを開始する"""
    t = threading.Thread(
        target=save_memory_worker,
        args=(user_name, user_comment, ruri_response),
        daemon=True
    )
    t.start()

def get_user_any_fact(user_name):
    """データベースから該当ユーザーの最も新しい事実を1件直接取得する。存在しない場合は None を返す。"""
    try:
        conn = sqlite3.connect("ruri_memory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM memories WHERE user_name = ? ORDER BY created_at DESC LIMIT 1", (user_name,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception as e:
        print(f"[WARNING] ユーザーの過去記憶の直接取得に失敗したし: {e}")
    return None

def get_user_recent_memories(user_name, limit=5):
    """データベースから該当ユーザーの最近の記憶を複数件直接取得する。"""
    try:
        conn = sqlite3.connect("ruri_memory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM memories WHERE user_name = ? ORDER BY created_at DESC LIMIT ?", (user_name, limit))
        rows = cursor.fetchall()
        conn.close()
        return [row[0] for row in rows]
    except Exception as e:
        print(f"[WARNING] ユーザーの最近の記憶の取得に失敗したし: {e}")
        return []

def record_user_activity(user_name, message):
    """ユーザーの発言を記録し、発言回数や最終発言日時を更新する"""
    try:
        now = time.time()
        conn = sqlite3.connect("ruri_memory.db")
        cursor = conn.cursor()
        
        # 既存レコードがあるか確認
        cursor.execute("SELECT message_count, last_seen, last_message FROM user_activities WHERE user_name = ?", (user_name,))
        row = cursor.fetchone()
        
        if row:
            # 更新
            count = row[0] + 1
            prev_seen = row[1]
            prev_message = row[2]
            cursor.execute("""
                UPDATE user_activities 
                SET last_seen = ?, prev_seen = ?, message_count = ?, last_message = ?, prev_message = ? 
                WHERE user_name = ?
            """, (now, prev_seen, count, message, prev_message, user_name))
        else:
            # 新規追加（初めての発言）
            cursor.execute("""
                INSERT INTO user_activities (user_name, first_seen, last_seen, prev_seen, message_count, last_message, prev_message)
                VALUES (?, ?, ?, ?, 1, ?, ?)
            """, (user_name, now, now, now, message, ""))
            
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WARNING] ユーザー活動の記録に失敗したし: {e}")

def get_user_activity(user_name):
    """ユーザーの活動情報を取得する。存在しない場合は None を返す。"""
    try:
        conn = sqlite3.connect("ruri_memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_activities WHERE user_name = ?", (user_name,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception as e:
        print(f"[WARNING] ユーザー活動情報の取得に失敗したし: {e}")
    return None
def save_pronunciation(word, pronunciation):
    """単語の正しい発音（読み方）をデータベースに保存する"""
    try:
        conn = sqlite3.connect("ruri_memory.db")
        cursor = conn.cursor()
        now = time.time()
        cursor.execute("""
            INSERT OR REPLACE INTO pronunciations (word, pronunciation, created_at)
            VALUES (?, ?, ?)
        """, (word.strip(), pronunciation.strip(), now))
        conn.commit()
        conn.close()
        print(f"[🧠 発音学習] 単語 '{word}' の読み方を '{pronunciation}' として登録したし！")
        return True
    except Exception as e:
        print(f"[WARNING] 発音登録に失敗したし: {e}")
        return False

def get_all_pronunciations():
    """データベースに登録されているすべての発音（読み方）辞書を取得する"""
    try:
        conn = sqlite3.connect("ruri_memory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT word, pronunciation FROM pronunciations")
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception as e:
        print(f"[WARNING] 発音辞書のロードに失敗したし: {e}")
        return {}

if __name__ == "__main__":
    init_db(clean=True)
