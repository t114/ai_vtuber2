import os
import sys
import logging
import yaml

# logging setup
logging.getLogger().setLevel(logging.WARNING)
logger = logging.getLogger("chat_poster")
logger.setLevel(logging.INFO)

# Config reading
try:
    with open('ruri_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except Exception:
    config = {}

# Google API Client imports with dynamic fallbacks
google_api_available = False
try:
    from googleapiclient.discovery import build
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    google_api_available = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
except ImportError:
    pass

SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

# グローバル状態
youtube_client = None
active_live_chat_id = None
is_posting_enabled = False

def init_youtube_client(video_id=None):
    """YouTube APIクライアントを初期化し、OAuth認証を行う。"""
    global youtube_client, active_live_chat_id, is_posting_enabled
    
    chat_cfg = config.get("youtube_chat", {})
    if not chat_cfg.get("enabled", True):
        print("[💬 Chat Poster] YouTubeチャット投稿機能は設定で無効化されているし！")
        is_posting_enabled = False
        return False

    if not google_api_available:
        print("[⚠️ Chat Poster] Google API ライブラリ(google-api-python-client)がインストールされていないため、モックモードで動作するし！")
        is_posting_enabled = False
        return False

    client_secrets_file = chat_cfg.get("client_secrets_file", "client_secrets.json")
    token_file = chat_cfg.get("token_file", "youtube_token.json")

    if not os.path.exists(client_secrets_file):
        alt_secrets_file = "client_secret.json" if client_secrets_file == "client_secrets.json" else "client_secrets.json"
        if os.path.exists(alt_secrets_file):
            client_secrets_file = alt_secrets_file
        else:
            print(
                f"[⚠️ Chat Poster] クライアントシークレットファイル '{client_secrets_file}' が見つからないし！\n"
                "※ 現時点ではモック（コンソールログ出力）として動作するし！"
            )
            is_posting_enabled = False
            return False

    creds = None
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception as e:
            print(f"[⚠️ Chat Poster] トークンファイルの読み込み中にエラーが発生したし: {e}")

    try:
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("[📡 Chat Poster] YouTube API of アクセストークンを更新中...")
                creds.refresh(Request())
            else:
                print("[📡 Chat Poster] 新規OAuth認証フローを開始するし！ブラウザが起動したらログインして許可してね。")
                flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
                try:
                    creds = flow.run_local_server(port=0, open_browser=True)
                except Exception:
                    print(f"[⚠️ Chat Poster] デスクトップブラウザの自動起動に失敗したし。コンソールに認証URLを表示して手動認証を行います。")
                    creds = flow.run_local_server(port=0, open_browser=False)
            
            with open(token_file, 'w') as token:
                token.write(creds.to_json())
            print("[✨ Chat Poster] YouTube認証トークンを保存したし！")

        youtube_client = build('youtube', 'v3', credentials=creds)
        is_posting_enabled = True
        print("[✨ Chat Poster] YouTube APIクライアントの作成に成功したし！")
        
        if video_id:
            resolve_live_chat_id(video_id)
            
        return True
    except Exception as e:
        print(f"[⚠️ Chat Poster] YouTube認証/初期化に失敗したし。モックモードで動作します: {e}")
        is_posting_enabled = False
        return False

def resolve_live_chat_id(video_id):
    """video_idから対応するアクティブなliveChatIdを取得する"""
    global youtube_client, active_live_chat_id
    if not youtube_client:
        return None
        
    try:
        print(f"[📡 Chat Poster] 配信動画ID: {video_id} から liveChatId を検索中...")
        response = youtube_client.videos().list(
            part="liveStreamingDetails",
            id=video_id
        ).execute()
        
        items = response.get("items", [])
        if items:
            details = items[0].get("liveStreamingDetails", {})
            active_live_chat_id = details.get("activeLiveChatId")
            if active_live_chat_id:
                print(f"[✨ Chat Poster] liveChatIdを取得できたし！ ID: {active_live_chat_id}")
            else:
                print(f"[⚠️ Chat Poster] 指定された動画ID {video_id} のライブチャットはアクティブではないか、既に終了しているし！")
        else:
            print(f"[⚠️ Chat Poster] 指定された動画ID {video_id} が見つからないし！")
    except Exception as e:
        print(f"[⚠️ Chat Poster] liveChatIdの取得に失敗したし: {e}")
        active_live_chat_id = None

def post_message_to_chat(message):
    """YouTube Live Chatにメッセージを送信する。"""
    global youtube_client, active_live_chat_id, is_posting_enabled
    
    if is_posting_enabled and youtube_client and active_live_chat_id:
        try:
            youtube_client.liveChatMessages().insert(
                part="snippet",
                body={
                    "snippet": {
                        "liveChatId": active_live_chat_id,
                        "type": "textMessageEvent",
                        "textMessageDetails": {
                            "messageText": message
                        }
                    }
                }
            ).execute()
            print(f"[💬 Chat Poster (Live)] チャット欄に投稿したし: {message}")
            return True
        except Exception as e:
            print(f"[⚠️ Chat Poster] チャット投稿中にエラーが発生したし: {e}")
            print(f"[💬 Chat Poster (Fallback Log)] {message}")
            return False
    else:
        print(f"[💬 Chat Poster (Mock Log)] 配信チャットに投稿したし: {message}")
        return True

def post_article_url_to_chat(title, url):
    """現在喋っている記事のURLとタイトルをチャットに投稿する"""
    if not url:
        return False
    message = f"「{title}」のニュースについて話してるし！この記事読んでみて？ URL: {url}"
    return post_message_to_chat(message)
