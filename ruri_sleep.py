import sqlite3
import json
import requests
import time
import yaml
import sys
import os
from collections import defaultdict
from ruri_memory import init_db, get_embedding, cosine_similarity

# 設定ファイルの読み込み
try:
    with open('ruri_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except Exception:
    config = {}

def llm_chat(prompt, system_prompt=None, temperature=0.7, max_tokens=1024):
    """AIAgent を生成して会話を実行し、結果のテキストを返す"""
    from run_agent import AIAgent
    import uuid
    
    llm_provider = config.get("llm_provider", "ollama").lower()
    
    if llm_provider == "gemini":
        gemini_cfg = config.get("gemini", {}) or {}
        agent = AIAgent(
            model=gemini_cfg.get("model", "gemini-1.5-flash"),
            api_key=gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or "dummy",
            base_url=gemini_cfg.get("api_url", "https://generativelanguage.googleapis.com/v1beta/openai/"),
            enabled_toolsets=[], # 睡眠時は外部ツールは使用しない
            quiet_mode=True,
            session_id=f"sleep_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            skip_memory=True
        )
    else:
        ollama_config = config.get("ollama", {}) or {}
        agent = AIAgent(
            model=ollama_config.get("model", "qwen3:14b"),
            api_key="dummy",
            base_url=ollama_config.get("api_url", "http://localhost:11434/v1"),
            enabled_toolsets=[],
            quiet_mode=True,
            session_id=f"sleep_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            skip_memory=True
        )
        
    try:
        res = agent.run_conversation(
            user_message=prompt,
            system_message=system_prompt
        )
        return res.get("text", "").strip()
    except Exception as e:
        print(f"[WARNING] AIAgentによる対話生成に失敗したし: {e}")
        raise e

def run_dream_phase():
    """夢フェーズ: ランダムな記憶からるりの夢のつぶやきを生成する"""
    print("\n" + "="*50)
    print(" ░▒▓ フェーズ 1: 入眠・夢 (Dream Phase) ▓▒░")
    print("="*50)
    
    try:
        conn = sqlite3.connect("ruri_memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM memories WHERE category != 'schema' ORDER BY RANDOM() LIMIT 5")
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"[ERROR] 記憶の読み込みに失敗したし: {e}")
        return

    if not rows:
        print("  「まだ何も思い出がないし…ぐーぐー…💤」")
        return

    memories_text = "\n".join([f"- {row['content']}" for row in rows])
    
    prompt = (
        "お前は大人気AI VTuber「るり」だ。るりは現在眠っており、夢を見ている。\n"
        "以下のいくつかの断片的な記憶から、るりが見ている奇妙で面白い夢のつぶやき（3〜5文程度）を、るりの口調（一人称は「ボク」、語尾は「〜だし」「〜し！」など、リスナーを「お前ら」と呼ぶ）で日本語で作成してください。\n\n"
        "【記憶の断片】\n"
        f"{memories_text}\n\n"
        "余計な説明や解説、前置きは一切出力せず、夢のつぶやきのみを出力してください。"
    )

    try:
        print("  [💤] るりが夢の世界に入り込んでるし...")
        dream_content = llm_chat(prompt, temperature=0.7, max_tokens=1024)
        print("\n" + "~"*40)
        print(f"  {dream_content}")
        print("~"*40)
        
        # 画像・動画生成用プロンプトの生成
        prompt_gen_prompt = (
            "お前は超優秀な画像生成・動画生成AI（Stable Diffusion XL / Animagine XL 等）のプロンプトデザイナーだ。以下の「夢の内容」から、その情景を表現する高品質で詳細な英語の画像生成用プロンプト（ポジティブプロンプト）を作成してください。\n\n"
            "【プロンプト作成ルール】\n"
            "1. 出力はすべて英語の「カンマ区切りのタグ形式（Danbooruタグ形式）」で出力してください。流暢な英文（自然言語）は使用しないでください。\n"
            "2. プロンプトは必ず以下の画質・画風を規定するキーワードから開始してください：\n"
            "   `masterpiece, best quality, anime artwork, anime style, key visual, `\n"
            "3. 主人公は「AI VTuberのるり（Ruri）」です。彼女の特徴を表すタグ `1girl, solo, ruri, cyber girl, blue hair, glowing futuristic outfit` を含めてください。\n"
            "4. 夢の内容に「火花」「電気」「光」「風」「炎」などの強い視覚的エフェクトや動きがある場合、そのエフェクトを表現するタグ（例: `(flying bright orange electrical sparks:1.5)`, `(crackling electricity:1.3)` など）を、画質キーワードの直後（主人公のタグよりも前）の最前列に配置してください。背景と同化しないよう必要に応じて色や強さを指定してください。\n"
            "5. 夢の内容に登場する「主要なオブジェクト、背景、状況」（例：空に浮かぶ本、本に書かれたC言語の文字、コンピュータサーバー、コード画面など）を省略せず、すべて英語のタグに変換してキャラクタータグの後に含めてください（例: `floating books, c programming books, computer servers` など）。\n"
            "6. 余計な説明、前置き、解説、見出しなどは一切出力せず、カンマ区切りの英語 of タグリストのみを1行で出力してください。\n\n"
            f"【夢の内容】:\n{dream_content}"
        )
        
        raw_prompt = llm_chat(prompt_gen_prompt, temperature=0.5, max_tokens=1024)
        
        # 不要な前置き文のクリーニング
        lines = [l.strip() for l in raw_prompt.split("\n") if l.strip()]
        clean_lines = []
        for line in lines:
            lowered = line.lower()
            if any(x in lowered for x in ["here is", "this is", "generation prompt:", "prompt for", "prompt:", "please note", "designed to"]):
                continue
            clean_lines.append(line)
        
        image_prompt = "\n".join(clean_lines).strip().strip('"').strip("'").strip()
        
        # Animagine XL 向けの標準ネガティブプロンプト
        DEFAULT_NEGATIVE_PROMPT = (
            "(worst quality, low quality:1.4), lowres, bad anatomy, bad hands, text, error, "
            "missing fingers, extra digit, fewer digits, cropped, normal quality, jpeg artifacts, "
            "signature, watermark, username, blurry, artist name, photo, photorealistic, "
            "watercolor, oil painting, sketch, 3d, rendering"
        )
        
        print(f"\n  [🎨 画像生成用プロンプト]")
        print(f"  Positive: {image_prompt}")
        print(f"  Negative: {DEFAULT_NEGATIVE_PROMPT}")
        
        # 夢日記ファイル (dream_journal.txt) に記録
        try:
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open("dream_journal.txt", "a", encoding="utf-8") as dj:
                dj.write(f"=== 夢日記 ({timestamp}) ===\n")
                dj.write(f"【夢の内容】\n{dream_content}\n\n")
                dj.write(f"【画像生成用プロンプト】\n")
                dj.write(f"Positive: {image_prompt}\n")
                dj.write(f"Negative: {DEFAULT_NEGATIVE_PROMPT}\n")
                dj.write("="*50 + "\n\n")
            print("\n  ✓ 夢の内容とプロンプトを dream_journal.txt に保存したし！")
        except Exception as e:
            print(f"\n  [WARNING] 夢日記の書き込みに失敗したし: {e}")
            
    except Exception as e:
        print(f"[WARNING] 夢の生成に失敗したし: {e}")

def run_replay_decay_phase():
    """リプレイ＆減衰フェーズ: 忘却曲線の適用と、コサイン類似度に基づく連想リンクの再構築"""
    print("\n" + "="*50)
    print(" ░▒▓ フェーズ 2: 忘却と連想リンク再構築 (Replay & Decay) ▓▒░")
    print("="*50)
    
    now = time.time()
    forgotten_count = 0
    links_count = 0

    try:
        conn = sqlite3.connect("ruri_memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. 記憶の読み込みと時間減衰
        cursor.execute("SELECT * FROM memories")
        memories = [dict(row) for row in cursor.fetchall()]
        
        active_memories = []
        for mem in memories:
            days_elapsed = (now - mem['created_at']) / 86400.0
            # 時間減衰（半減期14日）
            freshness = 2.0 ** (-days_elapsed / 14.0)
            
            # 想起されておらず、かつ時間経過で極めて希薄になった記憶の刈り込み
            # （例: 新鮮度 * 重要度 が 0.1 未満、かつ想起回数が 0）
            if freshness * mem['importance'] < 0.1 and mem['access_count'] == 0 and mem['category'] != 'schema':
                cursor.execute("DELETE FROM memories WHERE id = ?", (mem['id'],))
                print(f"  [忘却] ID #{mem['id']}: 「{mem['content']}」を忘れたし。")
                forgotten_count += 1
            else:
                active_memories.append(mem)
                
        # 2. リンクテーブルのクリアと再構築
        cursor.execute("DELETE FROM memory_links")
        
        # エピソード記憶（スキーマ以外）の間でリンクを計算
        episodic_memories = [m for m in active_memories if m['category'] != 'schema']
        
        for i in range(len(episodic_memories)):
            for j in range(i + 1, len(episodic_memories)):
                m1 = episodic_memories[i]
                m2 = episodic_memories[j]
                
                try:
                    vec1 = json.loads(m1['embedding'])
                    vec2 = json.loads(m2['embedding'])
                except Exception:
                    continue
                
                sim = cosine_similarity(vec1, vec2)
                
                # 類似度 0.75 以上の場合は連想リンクを構築
                if sim >= 0.75:
                    cursor.execute(
                        "INSERT INTO memory_links (source_id, target_id, similarity) VALUES (?, ?, ?)",
                        (m1['id'], m2['id'], sim)
                    )
                    cursor.execute(
                        "INSERT INTO memory_links (source_id, target_id, similarity) VALUES (?, ?, ?)",
                        (m2['id'], m1['id'], sim)
                    )
                    links_count += 2
                    
        conn.commit()
        conn.close()
        print(f"  ✓ リプレイ完了: {links_count // 2}個の連想リンクを再構築、{forgotten_count}件を自動忘却したし！")
    except Exception as e:
        print(f"[ERROR] リプレイフェーズ中にエラーが発生したし: {e}")

def run_consolidation_phase():
    """マージフェーズ: 類似度0.90以上の極めて類似度の高いエピソード記憶ペアを統合する"""
    print("\n" + "="*50)
    print(" ░▒▓ フェーズ 3: 記憶の統合 (Consolidation) ▓▒░")
    print("="*50)

    try:
        conn = sqlite3.connect("ruri_memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 類似度0.90以上の非スキーマ記憶ペアを取得
        cursor.execute("""
            SELECT ml.source_id, ml.target_id, ml.similarity,
                   m1.content as content1, m1.user_name as user1, m1.importance as imp1, m1.access_count as acc1, m1.created_at as time1,
                   m2.content as content2, m2.user_name as user2, m2.importance as imp2, m2.access_count as acc2, m2.created_at as time2
            FROM memory_links ml
            JOIN memories m1 ON ml.source_id = m1.id
            JOIN memories m2 ON ml.target_id = m2.id
            WHERE ml.similarity >= 0.90 AND ml.source_id < ml.target_id
              AND m1.category != 'schema' AND m2.category != 'schema'
        """)
        links = [dict(row) for row in cursor.fetchall()]
        conn.close()
    except Exception as e:
        print(f"[ERROR] マージ候補の取得に失敗したし: {e}")
        return

    if not links:
        print("  統合可能な酷似した記憶は見つからなかったし。")
        return

    # 類似度の高い順にソート
    links.sort(key=lambda x: x['similarity'], reverse=True)
    merged_ids = set()

    for link in links:
        s_id, t_id = link['source_id'], link['target_id']
        if s_id in merged_ids or t_id in merged_ids:
            continue
            
        # 同一人物の事実のみマージ対象とする
        if link['user1'] != link['user2']:
            continue
            
        print(f"  [統合候補] 類似度 {link['similarity']:.3f}: ID #{s_id} と ID #{t_id} を統合中...")
        
        prompt = (
            "お前はAI VTuber「るり」の記憶整理システムだ。以下の同一人物に関する2つの事実を、内容や人物情報を一切損なわずに、1つの簡潔で客観的な事実として日本語でマージ（統合）してください。\n\n"
            f"事実1: {link['content1']}\n"
            f"事実2: {link['content2']}\n\n"
            "余計な説明や解説、前置きは一切出力せず、マージ後の事実のみを一行で出力してください。"
        )

        try:
            merged_content = llm_chat(prompt, temperature=0.0, max_tokens=1024)
            merged_content = merged_content.strip('"').strip("'").strip()
            
            if len(merged_content) > 5:
                # 埋め込みベクトルの生成
                merged_vector = get_embedding(merged_content)
                if merged_vector:
                    conn = sqlite3.connect("ruri_memory.db")
                    cursor = conn.cursor()
                    
                    # 新しい統合記憶の挿入
                    new_importance = max(link['imp1'], link['imp2'])
                    new_access = link['acc1'] + link['acc2']
                    new_time = max(link['time1'], link['time2'])
                    
                    cursor.execute(
                        "INSERT INTO memories (user_name, content, embedding, emotion, importance, created_at, last_accessed, access_count, category) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (link['user1'], merged_content, json.dumps(merged_vector), "neutral", new_importance, new_time, new_time, new_access, "episodic")
                    )
                    
                    # 古い記憶の削除
                    cursor.execute("DELETE FROM memories WHERE id = ?", (s_id,))
                    cursor.execute("DELETE FROM memories WHERE id = ?", (t_id,))
                    
                    # 関連リンクの削除
                    cursor.execute("DELETE FROM memory_links WHERE source_id = ? OR target_id = ?", (s_id, s_id))
                    cursor.execute("DELETE FROM memory_links WHERE source_id = ? OR target_id = ?", (t_id, t_id))
                    
                    conn.commit()
                    conn.close()
                    
                    merged_ids.add(s_id)
                    merged_ids.add(t_id)
                    print(f"  ✓ 統合成功: ID #{s_id} と #{t_id} -> 「{merged_content}」")
        except Exception as e:
            print(f"  [WARNING] 記憶の統合プロセス中に失敗したし: {e}")

def find_cliques(graph):
    """Bron-Kerbosch アルゴリズムでサイズ3以上の極大クリーク（相互に全結合したクラスター）を探索"""
    cliques = []
    
    def r_bron_kerbosch(r, p, x):
        if not p and not x:
            if len(r) >= 3:
                cliques.append(r)
            return
        for v in list(p):
            r_bron_kerbosch(
                r | {v},
                p & graph[v],
                x & graph[v]
            )
            p.remove(v)
            x.add(v)
            
    p = set(graph.keys())
    r_bron_kerbosch(set(), p, set())
    return cliques

def run_schema_phase():
    """スキーマ生成フェーズ: 3つ以上の関連記憶クラスタを検出し、抽象スキーマを生成する"""
    print("\n" + "="*50)
    print(" ░▒▓ フェーズ 4: スキーマ（抽象概念）生成 (Schema Generation) ▓▒░")
    print("="*50)

    try:
        conn = sqlite3.connect("ruri_memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 非スキーマの記憶情報を取得
        cursor.execute("SELECT id, content FROM memories WHERE category != 'schema'")
        mem_rows = cursor.fetchall()
        mem_dict = {row['id']: row['content'] for row in mem_rows}
        
        # リンク関係の取得
        cursor.execute("SELECT source_id, target_id FROM memory_links")
        link_rows = cursor.fetchall()
        
        # 既存スキーマ記憶の取得（重複生成防止用）
        cursor.execute("SELECT content, embedding FROM memories WHERE category = 'schema'")
        existing_schemas = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
    except Exception as e:
        print(f"[ERROR] 記憶の取得に失敗したし: {e}")
        return

    # 隣接リスト（グラフ）の構築
    graph = defaultdict(set)
    for row in link_rows:
        s_id, t_id = row['source_id'], row['target_id']
        if s_id in mem_dict and t_id in mem_dict:
            graph[s_id].add(t_id)
            graph[t_id].add(s_id)

    cliques = find_cliques(graph)
    if not cliques:
        print("  条件を満たす相互接続された記憶クラスタは見つからなかったし。")
        return

    # 重複やサブセットとなるクリークの簡易フィルタリング
    cliques.sort(key=len, reverse=True)
    processed_mem_ids = set()
    schemas_created = 0

    for clique in cliques:
        # 既にスキーマ化された記憶の割合が多い場合はスキップ（独立したクラスタを優先）
        overlap = clique.intersection(processed_mem_ids)
        if len(overlap) / len(clique) >= 0.5:
            continue
            
        clique_contents = [mem_dict[c_id] for c_id in clique]
        facts_text = "\n".join([f"- {content}" for content in clique_contents])
        
        print(f"  [クラスタ検出] 記憶 IDs {list(clique)} からスキーマを検討中...")
        
        prompt = (
            "お前はAI VTuber「るり」の記憶整理システムだ。以下の関連する複数の事実リストから、共通するテーマ、関心事、人物の特徴などの背景を読み解き、客観的な抽象知識（スキーマ）を日本語で1文に要約してください。\n\n"
            "【事実リスト】\n"
            f"{facts_text}\n\n"
            "【出力例】\n"
            "[スキーマ] ユーザー「たろう」はプログラミング学習中で、特にC言語のポインタについて集中的に練習している。\n\n"
            "余計な説明や解説、前置きは一切出力せず、[スキーマ] で始まる要約文のみを一行で出力してください。"
        )

        try:
            schema_content = llm_chat(prompt, temperature=0.2, max_tokens=1024)
            schema_content = schema_content.strip('"').strip("'").strip()
            
            if not schema_content.startswith("[スキーマ]"):
                schema_content = f"[スキーマ] {schema_content}"
                
            # 重複スキーマチェック
            schema_vector = get_embedding(schema_content)
            if not schema_vector:
                continue
                
            is_duplicate = False
            for ex in existing_schemas:
                try:
                    ex_vector = json.loads(ex['embedding'])
                    if cosine_similarity(schema_vector, ex_vector) >= 0.85:
                        is_duplicate = True
                        break
                except Exception:
                    continue
            
            if is_duplicate:
                print("  [スキップ] 既に酷似したスキーマが存在するし。")
                continue

            # スキーマをデータベースに保存
            conn = sqlite3.connect("ruri_memory.db")
            cursor = conn.cursor()
            now = time.time()
            cursor.execute(
                "INSERT INTO memories (user_name, content, embedding, emotion, importance, created_at, last_accessed, access_count, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("system", schema_content, json.dumps(schema_vector), "neutral", 0.8, now, now, 0, "schema")
            )
            conn.commit()
            conn.close()
            
            processed_mem_ids.update(clique)
            schemas_created += 1
            print(f"  ✓ スキーマ作成成功: 「{schema_content}」")
            
        except Exception as e:
            print(f"  [WARNING] スキーマ生成プロセス中に失敗したし: {e}")

    print(f"  ✓ スキーマ生成完了: {schemas_created}件のスキーマ知識を作成したし！")

if __name__ == "__main__":
    init_db()  # データベース初期化とマイグレーションの実行
    print("\n💤 るりが睡眠状態に入ったし。記憶の整理（クリーンアップと統合）を開始するし！")
    run_dream_phase()
    run_decay_count = run_replay_decay_phase()
    run_consolidation_phase()
    run_schema_phase()
    print("\n🌅 るりが起床したし！脳内がすっきり整理されて準備万端だし！\n")
