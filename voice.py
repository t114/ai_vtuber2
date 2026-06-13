import requests
import urllib.parse
import os
import time
import re
import yaml
import wave
import subprocess
import json

# 設定ファイルの読み込み
try:
    with open('ruri_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except Exception:
    config = {}

vits_config = config.get("vits", {}) or {}
VITS_API_URL = vits_config.get("api_url", "http://localhost:5001")
DEFAULT_SPEAKER_ID = vits_config.get("speaker_id", 0)

# 感情とVITSモデルパラメータのマッピング
EMOTION_PARAMS = {
    "neutral": {"sdp_ratio": 0.2, "noise": 0.6, "noisew": 0.8, "length": 1.0},
    "joy": {"sdp_ratio": 0.3, "noise": 0.5, "noisew": 0.7, "length": 0.95},
    "happy": {"sdp_ratio": 0.3, "noise": 0.5, "noisew": 0.7, "length": 0.92},
    "angry": {"sdp_ratio": 0.4, "noise": 0.7, "noisew": 0.9, "length": 0.85},
    "angry2": {"sdp_ratio": 0.5, "noise": 0.8, "noisew": 0.95, "length": 0.8},
    "sad": {"sdp_ratio": 0.2, "noise": 0.6, "noisew": 0.8, "length": 1.2},
    "surprised": {"sdp_ratio": 0.3, "noise": 0.6, "noisew": 0.85, "length": 0.9},
    "blush": {"sdp_ratio": 0.25, "noise": 0.5, "noisew": 0.75, "length": 1.05},
    "jitome": {"sdp_ratio": 0.15, "noise": 0.65, "noisew": 0.8, "length": 1.1},
    "wink": {"sdp_ratio": 0.25, "noise": 0.55, "noisew": 0.75, "length": 0.98},
    "sleep": {"sdp_ratio": 0.1, "noise": 0.6, "noisew": 0.8, "length": 1.3},
    "sweat": {"sdp_ratio": 0.3, "noise": 0.65, "noisew": 0.85, "length": 1.0},
    "pale": {"sdp_ratio": 0.2, "noise": 0.7, "noisew": 0.9, "length": 1.15},
    "gonyogonyo": {"sdp_ratio": 0.15, "noise": 0.5, "noisew": 0.7, "length": 1.25},
    "cat_mouth": {"sdp_ratio": 0.25, "noise": 0.5, "noisew": 0.7, "length": 0.95},
    "perori": {"sdp_ratio": 0.25, "noise": 0.5, "noisew": 0.7, "length": 0.95},
    "heart": {"sdp_ratio": 0.3, "noise": 0.45, "noisew": 0.65, "length": 1.05},
    "star": {"sdp_ratio": 0.35, "noise": 0.5, "noisew": 0.7, "length": 0.9},
    "note": {"sdp_ratio": 0.25, "noise": 0.5, "noisew": 0.7, "length": 0.95},
    "question": {"sdp_ratio": 0.2, "noise": 0.6, "noisew": 0.8, "length": 1.05},
    "exclamation": {"sdp_ratio": 0.35, "noise": 0.6, "noisew": 0.85, "length": 0.88},
    "zzz": {"sdp_ratio": 0.1, "noise": 0.6, "noisew": 0.8, "length": 1.35}
}

# 基本的なカタカナ読み・記号除去のルール（ルビ置換）
RUBY_RULES = {
    r'\bAI\b': 'エーアイ',
    r'\bVTuber\b': 'ブイチューバー',
    r'\bVTube\b': 'ブイチューブ',
    r'\bLLM\b': 'エルエルエム',
    r'\bOS\b': 'オーエス',
    r'\bCPU\b': 'シーピーユー',
    r'\bRAM\b': 'ラム',
    r'\bGPU\b': 'ジーピーユー',
    r'\bSSD\b': 'エスエスディー',
    r'\bHDD\b': 'ハードディスク',
    r'\bRTX\b': 'アールティーエックス',
    r'\bAMD\b': 'エーエムディー',
    r'\bEPYC\b': 'エピック',
    r'\bDDR5\b': 'ディーディーアールファイブ',
    r'\bDDR4\b': 'ディーディーアールフォー',
    r'\bDDR3\b': 'ディーディーアールスリー',
    r'\bNVMe\b': 'エヌブイエムイー',
    r'\bRAID\b': 'レイド',
    r'\bPCIe\b': 'ピーシーアイイー',
    r'\bGen5\b': 'ジェンファイブ',
    r'\bGen4\b': 'ジェンフォー',
    r'\bTB\b': 'テラバイト',
    r'\bGB\b': 'ギガバイト',
    r'\bGentoo\b': 'ジェンツー',
    r'\bLinux\b': 'リナックス',
    r'\bOSC\b': 'オーエスシー',
    r'\bPC\b': 'ピーシー',
    r'\bssd\b': 'エスエスディー'
}

def get_kana_pronunciation_via_llm(text):
    """入力文の漢字や英単語をすべて正しい発音（カタカナ）に変換したテキストを得る"""
    if not re.search(r'[a-zA-Z]', text):
        return text
        
    # システムプロンプト定義
    system_prompt = (
        "お前は超優秀な音声合成用（TTS）の日本語読み仮名変換システムだ。入力された日本語テキストの「漢字」「英単語」をすべて、日本語の正しい発音通りの「ひらがな」または「カタカナ」に変換してください。\n\n"
        "【絶対厳守ルール】\n"
        "- キー（変換対象）と値（カタカナ読み）のみのフラットなJSON形式で出力してください。\n"
        "- 余計な会話文や説明、「以下が返答です」などの前置きは一切出力しないでください。\n"
        "- カタカナ変換できない文字（漢字や英字など）を、値に含めないでください。\n\n"
        "【出力形式例】\n"
        "{\n"
        "  \"CPU\": \"シーピーユー\",\n"
        "  \"Gentoo\": \"ジェンツー\"\n"
        "}\n\n"
        "【対象テキスト】\n"
        f"{text}"
    )

    llm_provider = config.get("llm_provider", "ollama").lower()
    reply = ""
    
    try:
        use_gemini = (llm_provider == "gemini")
        
        if not use_gemini:
            # Ollamaで試みるが、タイムアウトは3秒にする
            ollama_config = config.get("ollama", {}) or {}
            model_name = ollama_config.get("model", "qwen3:14b")
            api_url = ollama_config.get("api_url", "http://localhost:11434/v1")
            if "/v1" in api_url:
                api_url = api_url.split("/v1")[0]
            api_url = f"{api_url.rstrip('/')}/api/chat"
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 250
                }
            }
            try:
                res = requests.post(api_url, json=payload, timeout=3.0)
                res.raise_for_status()
                reply = res.json()['message']['content'].strip()
            except Exception as ollama_err:
                print(f"[WARNING] Ollamaでの読み仮名取得に失敗したため、Geminiへのフォールバックを試みるし: {ollama_err}")
                use_gemini = True

        if use_gemini:
            gemini_cfg = config.get("gemini", {}) or {}
            model_name = gemini_cfg.get("model", "gemini-1.5-flash")
            api_key = gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or ""
            base_url = gemini_cfg.get("api_url", "https://generativelanguage.googleapis.com/v1beta/openai/")
            if not base_url.endswith("/"):
                base_url += "/"
            api_url = f"{base_url}chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                "temperature": 0.0,
            }
            res = requests.post(api_url, json=payload, headers=headers, timeout=3.0)
            res.raise_for_status()
            reply = res.json()['choices'][0]['message']['content'].strip()

        # JSONをパースして置換する
        if "{" in reply and "}" in reply:
            reply = reply[reply.find("{"):reply.rfind("}")+1]
        
        mapping = json.loads(reply)
        if isinstance(mapping, dict):
            replaced = text
            import ruri_memory
            for k, val in mapping.items():
                if not re.match(r'^[a-zA-Z0-9\s().+\-_]+$', k):
                    continue
                replaced = re.sub(rf'\b{re.escape(k)}\b', val, replaced, flags=re.IGNORECASE)
                replaced = replaced.replace(k, val)
                
                # 自動的にデータベースに登録して学習する
                try:
                    ruri_memory.save_pronunciation(k, val)
                except Exception as db_err:
                    print(f"[WARNING] 自動発音学習DB保存に失敗したし: {db_err}")
            
            print(f"[🧠 LLM読み仮名置換＆自動学習成功] 元: '{text}' -> 先: '{replaced}'")
            return replaced
    except Exception as e:
        print(f"[WARNING] LLMでの読み置換に失敗したし(ローカル辞書フォールバック): {e}")
        
    return text

def replace_pronunciation(text):
    """登録された単語の読み方を優先適用し、さらに基本的なルビ置換ルール（RUBY_RULES）を適用する"""
    # 1. データベースから動的な読み仮名を適用
    try:
        import ruri_memory
        db_dict = ruri_memory.get_all_pronunciations()
        for word, pron in db_dict.items():
            # word が英数字だけなら単語境界 \b を付与、日本語文字を含むならそのまま置換
            if re.match(r'^[a-zA-Z0-9\s()+\-_]+$', word):
                pattern = rf'\b{re.escape(word)}\b'
            else:
                pattern = re.escape(word)
            text = re.sub(pattern, pron, text, flags=re.IGNORECASE)
    except Exception as e:
        print(f"[WARNING] 動的発音辞書の適用に失敗したし: {e}")

    # 2. 静的な基本変換
    pronunciation_dict = {
        r'\banthropic\b': 'アンスロピック',
        r'\bclaude\b': 'クロード',
        r'\bmythos\b': 'ミソス',
        r'\bfable\b': 'フェーブル',
        r'\bsiiibo\b': 'シーボ',
        r'\belevenlabs\b': 'イレブンラボ',
        r'\bmusic\b': 'ミュージック',
        r'\bbtc\b': 'ビーティーシー',
        r'\bbert\b': 'バート',
        r'\bvits2\b': 'ビッツツー',
        r'\bacer\b': 'エイサー',
        r'\basus\b': 'エイスース',
        r'\bgigabyte\b': 'ギガバイト',
        r'\bmsi\b': 'エムエスアイ',
        r'\bintel\b': 'インテル',
        r'\bamd\b': 'エーエムディー',
        r'\bnvidia\b': 'エヌビディア',
        r'\bgeforce\b': 'ジーフォース',
        r'\bradeon\b': 'ラデオン',
        r'\bryzen\b': 'ライゼン',
        r'\bepyc\b': 'エピック',
        r'\bgentoo\b': 'ジェンツー',
        r'\blinux\b': 'リナックス',
        r'\bwindows\b': 'ウィンドウズ',
        r'\bmacbook\b': 'マックブック',
        r'\bchrome\b': 'クローム',
        r'\bvisa\b': 'ビザ',
        r'\bmastercard\b': 'マスターカード',
        r'\bllm\b': 'エルエルエム',
        r'\bfmri\b': 'エフエムアールアイ',
        r'\bplos\b': 'プロス',
        r'\bplos\s+one\b': 'プロスワン',
        r'\bapi\b': 'エーピーアイ',
        r'\bsns\b': 'エスエヌエス',
        r'\bai\b': 'エーアイ',
        r'\bos\b': 'オーエス',
        r'\bpc\b': 'ピーシー',
        r'\bssd\b': 'エスエスディー'
    }
    
    # RUBY_RULES の適用
    for k, v in RUBY_RULES.items():
        text = re.sub(k, v, text)
        
    # 英単語置換の適用
    for k, v in pronunciation_dict.items():
        text = re.sub(k, v, text, flags=re.IGNORECASE)
        
    return text

def generate_voice_wav(text, emotion="neutral"):
    """音声ファイルを生成し、一時ファイル名（パス）を返す。
    VOICEVOX または Style-Bert-VITS2 の有効な方を使用する。
    """
    # 感情タグ [emotion] などの大括弧で囲まれた英数字タグを丸ごと除去
    clean_text = re.sub(r'\[[a-zA-Z0-9_]+\]', '', text)
    # HTML/XMLタグの除去
    clean_text = re.sub(r'<[^>]*>', '', clean_text)
    clean_text = re.sub(r'[\(\)（）「」『』\[\]【】\*\-\_\~\!\?]', ' ', clean_text)
    clean_text = clean_text.strip()
    
    # 読み仮名置換を適用
    clean_text = replace_pronunciation(clean_text)
    clean_text = get_kana_pronunciation_via_llm(clean_text)
    
    if not clean_text:
        return None

    # Style-Bert-VITS2 が有効な場合
    bert_config = config.get("bert_vits2", {})
    if bert_config.get("enabled", False):
        return generate_bert_vits2_wav(clean_text, emotion)

    # VOICEVOX が有効な場合
    vv_config = config.get("voicevox", {})
    if vv_config.get("enabled", False):
        return generate_voicevox_wav(clean_text, emotion)

    return None

def generate_voicevox_wav(clean_text, emotion="neutral"):
    """VOICEVOXを使って音声ファイルを生成し、一時ファイル名（パス）を返す"""
    vv_config = config.get("voicevox", {})
    api_url = vv_config.get("api_url", "http://localhost:50021")
    speaker = vv_config.get("speaker_id", 2)
    
    emotion_speakers = vv_config.get("emotion_speakers", {})
    if emotion in emotion_speakers:
        speaker = emotion_speakers[emotion]
        
    speed_scale = vv_config.get("speed_scale", 1.15)
    
    print(f"[📢 VOICEVOX 音声生成中...] (Style ID: {speaker}) {clean_text}")
    try:
        query_res = requests.post(
            f"{api_url}/audio_query",
            params={"text": clean_text, "speaker": speaker},
            timeout=5
        )
        query_res.raise_for_status()
        query_data = query_res.json()
        query_data["speedScale"] = speed_scale
        
        synth_res = requests.post(
            f"{api_url}/synthesis",
            params={"speaker": speaker},
            json=query_data,
            timeout=15
        )
        synth_res.raise_for_status()
        
        # パスの安全な取り扱い (Path traversal防御)
        import uuid
        unique_filename = f"temp_voice_{uuid.uuid4().hex}.wav"
        wav_path = os.path.join(os.getcwd(), unique_filename)
        with open(wav_path, "wb") as f:
            f.write(synth_res.content)
            
        return wav_path
    except Exception as e:
        print(f"[ERROR] VOICEVOXでの音声生成に失敗したし: {e}")
        return None

def generate_bert_vits2_wav(clean_text, emotion="neutral"):
    """Style-Bert-VITS2を使って音声ファイルを生成し、一時ファイル名（パス）を返す"""
    bert_config = config.get("bert_vits2", {})
    api_url = bert_config.get("api_url", "http://localhost:5000")
    speaker = bert_config.get("speaker_id", 0)
    model_name = bert_config.get("model_name", "")
    length_scale = bert_config.get("length_scale", 1.0)
    sdp_ratio = bert_config.get("sdp_ratio", 0.2)
    
    style = "Neutral"
    emotion_styles = bert_config.get("emotion_styles", {})
    if emotion in emotion_styles:
        style = emotion_styles[emotion]
        
    print(f"[📢 Style-Bert-VITS2 音声生成中...] (Speaker ID: {speaker}, Style: {style}) {clean_text}")
    try:
        params = {
            "text": clean_text,
            "speaker_id": speaker,
            "style": style,
            "length": length_scale,
            "sdp_ratio": sdp_ratio
        }
        if model_name:
            params["model_name"] = model_name
            
        res = requests.get(f"{api_url}/voice", params=params, timeout=30)
        res.raise_for_status()
        
        # パスの安全な取り扱い (Path traversal防御)
        import uuid
        unique_filename = f"temp_voice_{uuid.uuid4().hex}.wav"
        wav_path = os.path.join(os.getcwd(), unique_filename)
        with open(wav_path, "wb") as f:
            f.write(res.content)
            
        return wav_path
    except Exception as e:
        print(f"[ERROR] Style-Bert-VITS2での音声生成に失敗したし: {e}")
        return None

def play_voice_wav(wav_path):
    """音声ファイルをシステムプレイヤーで再生する"""
    if not wav_path or not os.path.exists(wav_path):
        return
        
    # パスがカレントディレクトリ内にあることを保証 (Security Check)
    abs_wav_path = os.path.abspath(wav_path)
    if not abs_wav_path.startswith(os.getcwd()):
        print(f"[OSC WARNING] Rejected unsafe audio path: '{wav_path}'")
        return

    bert_config = config.get("bert_vits2", {})
    vv_config = config.get("voicevox", {})
    
    local_playback = False
    if bert_config.get("enabled", False):
        local_playback = bert_config.get("local_playback", False)
    elif vv_config.get("enabled", False):
        local_playback = vv_config.get("local_playback", False)
        
    if not local_playback:
        return
        
    try:
        subprocess.run(
            ["pw-play", abs_wav_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        try:
            subprocess.run(
                ["aplay", abs_wav_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as play_err:
            print(f"[ERROR] 音声の再生に失敗したし: {play_err}")

def get_wav_duration(wav_path):
    """WAVファイルの再生時間（秒）を取得する"""
    if not wav_path or not os.path.exists(wav_path):
        return 0.0
    try:
        with wave.open(wav_path, 'rb') as f:
            frames = f.getnframes()
            rate = f.getframerate()
            duration = frames / float(rate)
            return duration
    except Exception as e:
        print(f"[WARNING] WAV再生時間の取得に失敗: {e}")
        return 0.0
