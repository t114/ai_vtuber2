import re
import random
from pythonosc import udp_client
import yaml

# Config reading
try:
    with open('ruri_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except Exception:
    config = {}

unity_config = config.get("unity", {}) or {}
enabled = unity_config.get("enabled", False)
ip = unity_config.get("ip", "192.168.1.6")
port = unity_config.get("port", 39540)
motion_address = unity_config.get("motion_address", "/avatar/motion")
expression_address = unity_config.get("expression_address", "/avatar/expression")
talking_motions = unity_config.get("talking_motions", ["Talking1", "Talking2", "Talking3"])
emotions_map = unity_config.get("emotions", {}) or {}
send_expression = unity_config.get("send_expression", True)
send_motion = unity_config.get("send_motion", True)

client = None

def validate_ip(ip_str):
    """Validate IPv4 address format."""
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip_str):
        return False
    parts = ip_str.split('.')
    return all(0 <= int(part) <= 255 for part in parts)

def validate_port(port_val):
    """Validate port range (1-65535)."""
    try:
        p = int(port_val)
        return 1 <= p <= 65535
    except (TypeError, ValueError):
        return False

# Initialize Client
if enabled:
    if not validate_ip(ip):
        print(f"[OSC ERROR] Invalid Unity IP address configured: {ip}. Unity OSC disabled.")
        enabled = False
    elif not validate_port(port):
        print(f"[OSC ERROR] Invalid Unity Port configured: {port}. Unity OSC disabled.")
        enabled = False
    else:
        try:
            client = udp_client.SimpleUDPClient(ip, int(port))
            print(f"[OSC] Initialized Unity OSC Client pointing to {ip}:{port}")
        except Exception as e:
            print(f"[OSC ERROR] Failed to initialize Unity OSC Client: {e}. Unity OSC disabled.")
            enabled = False

# 直前に送信した感情・モーションを記憶して重複送信をスキップする
_last_sent_expression = None
_last_sent_motion = None

def send_unity_motion(motion_name):
    """Send a raw motion name command directly to Unity OSC."""
    global enabled, client, send_motion, _last_sent_motion
    if not enabled or not client or not send_motion:
        return
    
    # Input validation: Motion name must be alphanumeric with dashes/underscores
    if not re.match(r"^[a-zA-Z0-9_-]+$", motion_name):
        print(f"[OSC WARNING] Rejected unsafe motion name input: '{motion_name}'")
        return

    # 直前と同じモーションは送らない（重複排除）
    if motion_name == _last_sent_motion:
        return
    _last_sent_motion = motion_name

    try:
        client.send_message(motion_address, motion_name)
        print(f"[OSC] Sent motion command to Unity: {motion_address} -> {motion_name}")
    except Exception as e:
        print(f"[OSC ERROR] Failed to send motion command to Unity: {e}")

def send_unity_expression_only(emotion_name):
    """感情に対応するExpressionだけをUnityに送る（motion は送らない）。
    再生キューに溜まった各文の感情は、実際の再生開始時(play_start)にまとめて送るため、
    WAV生成時点では expression だけ更新しておく用途で使う。"""
    global enabled, client, send_expression, _last_sent_expression
    if not enabled or not client or not send_expression:
        return

    mapped_expression = emotions_map.get(emotion_name) or emotion_name or "Neutral"

    if not re.match(r"^[a-zA-Z0-9_-]+$", mapped_expression):
        print(f"[OSC WARNING] Rejected unsafe expression name: '{mapped_expression}'")
        return

    # 直前と同じ expression は送らない
    if mapped_expression == _last_sent_expression:
        return
    _last_sent_expression = mapped_expression

    try:
        client.send_message(expression_address, mapped_expression)
        print(f"[OSC] Sent expression command to Unity: {expression_address} -> {mapped_expression}")
    except Exception as e:
        print(f"[OSC ERROR] Failed to send expression command to Unity: {e}")

def send_unity_emotion(emotion_name, is_speaking=False):
    """Map a standard emotion to its configured Unity expression+motion and send both.
    音声再生の瞬間（play_start）に呼ばれることを想定。expression と motion を両方送る。"""
    global enabled, client, send_expression, _last_sent_expression, _last_sent_motion
    if not enabled or not client:
        return

    # 1. Expression を送信（重複スキップ付き）
    if send_expression:
        mapped_expression = emotions_map.get(emotion_name) or emotion_name or "Neutral"

        if not re.match(r"^[a-zA-Z0-9_-]+$", mapped_expression):
            print(f"[OSC WARNING] Rejected unsafe expression name input: '{mapped_expression}'")
            return

        if mapped_expression != _last_sent_expression:
            _last_sent_expression = mapped_expression
            try:
                client.send_message(expression_address, mapped_expression)
                print(f"[OSC] Sent expression command to Unity: {expression_address} -> {mapped_expression}")
            except Exception as e:
                print(f"[OSC ERROR] Failed to send expression command to Unity: {e}")

    # 2. Motion を送信（重複スキップ付き）
    mapped_motion = emotions_map.get(emotion_name) or emotion_name or "Neutral"

    if mapped_motion == "Neutral":
        selected_motion = random.choice(talking_motions) if (is_speaking and talking_motions) else "Neutral"
    else:
        selected_motion = mapped_motion

    send_unity_motion(selected_motion)

def send_unity_glasses(is_on):
    """OSCの/avatar/expressionに対してメガネ用のExpression名を送信し、
    さらに/avatar/glassesに対して"true"または"false"の文字列を送信する"""
    global enabled, client, expression_address
    if not enabled or not client:
        return
        
    gemini_config = config.get("gemini", {}) or {}
    if is_on:
        expr_name = gemini_config.get("glasses_osc_on_expression", "glasses_on")
        glasses_val = "true"
    else:
        expr_name = gemini_config.get("glasses_osc_off_expression", "glasses_off")
        glasses_val = "false"
        
    try:
        # 1. 従来の /avatar/expression への送信
        if re.match(r"^[a-zA-Z0-9_-]+$", expr_name):
            client.send_message(expression_address, expr_name)
            print(f"[OSC] Sent glasses expression command to Unity: {expression_address} -> {expr_name}")
            
        # 2. 新しい /avatar/glasses への "true" / "false" 送信
        client.send_message("/avatar/glasses", glasses_val)
        print(f"[OSC] Sent glasses status to Unity: /avatar/glasses -> {glasses_val}")
    except Exception as e:
        print(f"[OSC ERROR] Failed to send glasses command to Unity: {e}")
