import random
import re
import requests
import html as html_parser
import urllib.request
import xml.etree.ElementTree as ET
from ddgs import DDGS
import yaml

# 設定ファイルの読み込み
try:
    with open('ruri_config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
except Exception:
    config = {}

def is_safe_article(title, body):
    """記事のタイトルと本文が公序良俗に反していないか簡易的なNGワードチェックを行う。"""
    text = (title + "\n" + body).lower()
    ng_words = [
        "ポルノ", "アダルト", "エロ", "r18", "r-18", "猟奇", "自殺", "殺害", "ドラッグ", "覚醒剤", 
        "大麻", "詐欺", "死体", "暴行", "虐待", "ヘイト", "グロ", "殺人", "レイプ", "強姦", 
        "売春", "コカイン", "麻薬", "テロ", "爆破", "暗殺", "児童ポルノ", "グロテスク"
    ]
    for word in ng_words:
        if word in text:
            print(f"[WARNING] 公序良俗フィルターに抵触したし: キーワード「{word}」を検出。")
            return False
    return True

def fetch_web_article(url):
    """指定されたURLからWeb記事を取得し、要約・パースする。SSRF対策を含む。"""
    if not (url.startswith("http://") or url.startswith("https://")):
        return None
        
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RuriVTuber/1.0"}
        
        # プライベートホスト制限 (SSRF対策)
        parsed_url = re.search(r"https?://([^/]+)", url)
        if parsed_url:
            host = parsed_url.group(1).lower()
            if any(h in host for h in ["localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172.16.", "169.254."]):
                print(f"[WARNING] 安全のため、プライベートIPへのアクセスはブロックしたし: {host}")
                return None
                
        # メモリ制限のため最大500KBまで
        res = requests.get(url, headers=headers, timeout=5, stream=True)
        res.raise_for_status()
        
        content_length = res.headers.get('content-length')
        if content_length and int(content_length) > 1024 * 1024:
            print("[WARNING] 記事のサイズが大きすぎるし！")
            return None
            
        html_bytes = b""
        max_bytes = 500 * 1024 # 500KB
        bytes_read = 0
        for chunk in res.iter_content(chunk_size=4096):
            if chunk:
                html_bytes += chunk
                bytes_read += len(chunk)
                if bytes_read > max_bytes:
                    break
                    
        encoding = res.encoding
        if not encoding or encoding.lower() == 'iso-8859-1':
            try:
                temp_head = html_bytes[:10240].decode('ascii', errors='ignore')
                meta_match = re.search(r'<meta[^>]+charset=["\']?([a-zA-Z0-9_-]+)', temp_head, re.IGNORECASE)
                if meta_match:
                    encoding = meta_match.group(1)
                else:
                    encoding = res.apparent_encoding
            except Exception:
                encoding = 'utf-8'
                
        if not encoding:
            encoding = 'utf-8'
            
        try:
            html = html_bytes.decode(encoding, errors='replace')
        except Exception:
            html = html_bytes.decode('utf-8', errors='replace')
            
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else "謎のネット記事"
        title = re.sub(r"\s+", " ", title)
        title = re.sub(r"<[^>]*>", "", title)
        title = html_parser.unescape(title)
        
        paragraphs = re.findall(r"<p>(.*?)</p>", html, re.DOTALL)
        body_texts = []
        for p in paragraphs:
            p_clean = re.sub(r"<[^>]*>", "", p).strip()
            p_clean = re.sub(r"\s+", " ", p_clean)
            if len(p_clean) > 20:
                body_texts.append(p_clean)
            if len(body_texts) >= 5:
                break
                
        body = "\n".join(body_texts)
        if len(body) > 1000:
            body = body[:1000] + "..."
            
        if not body:
            desc_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html, re.IGNORECASE | re.DOTALL)
            if not desc_match:
                desc_match = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']', html, re.IGNORECASE | re.DOTALL)
            if desc_match:
                body = re.sub(r"<[^>]*>", "", desc_match.group(1).strip())
            else:
                body = "記事の本文を読み取れなかったし。お前ら代わりに解説して？"
                
        body = html_parser.unescape(body)
                
        if not is_safe_article(title, body):
            return {"title": title, "body": body, "safe": False}
            
        return {"title": title, "body": body, "safe": True}
    except Exception as e:
        print(f"[WARNING] 記事の取得に失敗: {e}")
        return None

def get_trending_news():
    """RSSフィードから最新のIT/テック系ニュースを1つ取得する。"""
    rss_urls = [
        "https://news.yahoo.co.jp/rss/categories/it.xml", # Yahoo! IT
        "https://rss.itmedia.co.jp/rss/2.0/itmedia_all.xml", # ITmedia 全体
        "https://forest.watch.impress.co.jp/data/rss/1.0/forest/feed.rdf", # 窓の杜
        "https://gizmodo.jp/index.xml" # ギズモード
    ]
    
    random_urls = list(rss_urls)
    random.shuffle(random_urls)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    for url in random_urls:
        print(f"[📰 ニュース収集中...] RSSフィードから取得を試行中: {url}")
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as response:
                xml_data = response.read()
                
            root = ET.fromstring(xml_data)
            items = []
            
            for item in root.findall(".//item"):
                title_node = item.find("title")
                desc_node = item.find("description")
                link_node = item.find("link")
                if title_node is not None and title_node.text:
                    body = desc_node.text if desc_node is not None and desc_node.text else title_node.text
                    body = re.sub(r'<[^>]*>', '', body).strip()
                    link_url = link_node.text.strip() if link_node is not None and link_node.text else None
                    items.append({"title": title_node.text.strip(), "body": body, "url": link_url})
                    
            if not items:
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                for entry in root.findall(".//atom:entry", ns):
                    title_node = entry.find("atom:title", ns)
                    summary_node = entry.find("atom:summary", ns)
                    content_node = entry.find("atom:content", ns)
                    if title_node is not None and title_node.text:
                        desc_node = summary_node if summary_node is not None else content_node
                        body = desc_node.text if desc_node is not None and desc_node.text else title_node.text
                        body = re.sub(r'<[^>]*>', '', body).strip()
                        
                        link_nodes = entry.findall("atom:link", ns)
                        link_url = None
                        for l in link_nodes:
                            if l.attrib.get('rel') == 'alternate' or not l.attrib.get('rel'):
                                link_url = l.attrib.get('href')
                                break
                        if not link_url and len(link_nodes) > 0:
                            link_url = link_nodes[0].attrib.get('href')
                            
                        items.append({"title": title_node.text.strip(), "body": body, "url": link_url})
                        
            if items:
                valid_items = [
                    it for it in items 
                    if not it['title'].startswith(("PR:", "【PR】", "広告:"))
                ]
                if not valid_items:
                    valid_items = items
                    
                chosen = random.choice(valid_items)
                print(f"[✨ ニュース取得成功] タイトル: {chosen['title']}")
                return chosen
                
        except Exception as e:
            print(f"[WARNING] RSS取得失敗 ({url}): {e}")
            
    # フォールバックリスト
    fallbacks = [
        {"title": "自作サーバーの電気代が高すぎる件", "body": "最近電気代が上がっていて、大容量メモリのモンスターサーバーを維持するのが本当に大変。電気代削減のためにファン回転数を落とすべきか悩み中。"},
        {"title": "液体窒素によるCPU極冷オーバークロック世界記録更新のニュース", "body": "世界的な自作PCチームが、CPUを液体窒素でマイナス190度まで冷却し、驚異のクロック周波数9.1GHzを達成してギネス記録を塗り替える。"},
        {"title": "猫がサーバーケースの排気熱で温まり排気口をふさぐ熱暴走バグ多発中", "body": "自宅に自作サーバーを持つエンジニアの間で、冬場に猫がファン排気口の上で寝てしまい、内部温度が100度に達してCPUが保護機能でシャットダウンする熱暴走が頻発。"},
        {"title": "押入れを完全防音サーバー室に改造したら熱気がサウナ化した件", "body": "サーバーの騒音対策として押入れの中に防音シートとラックを詰め込んだ結果、熱が全く抜けず内部の温度が65度を突破。押入れのふすまが熱で歪み、ショート寸前になる。"},
        {"title": "グラフィックボードが巨大化しすぎて一般的なPCケースのサイドパネルが閉まらない悲劇", "body": "最新世代のGPUが前代未聞 of 3.5スロット厚、長さ35cmへと大型化。多くの自作PCファンがケースに入り切らず、側板をヤスリで削ったり曲げて無理やり押し込んでいる。"},
    ]
    selected_fallback = random.choice(fallbacks)
    print(f"[🎲 フォールバック適用] テーマ: {selected_fallback['title']}")
    return selected_fallback

def search_google_news(query):
    """クエリに関連するGoogle News RSSから最新ニュースを取得する。
    DuckDuckGoより最新情報に強く、公式APIで無料・無制限。"""
    print(f"[🔍 Google News検索中...] クエリ: {query}")
    try:
        import urllib.parse as _up
        rss_url = f"https://news.google.com/rss/search?q={_up.quote(query)}&hl=ja&gl=JP&ceid=JP:ja"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RuriVTuber/1.0"}
        res = requests.get(rss_url, headers=headers, timeout=8)
        res.raise_for_status()
        root = ET.fromstring(res.content)
        items = root.findall(".//item")
        if not items:
            return "検索結果なし"
        summary_items = []
        for item in items[:5]:
            title_node = item.find("title")
            link_node = item.find("link")
            source_node = item.find("{https://news.google.com/rss}source")
            pub_node = item.find("pubDate")
            title = title_node.text.strip() if title_node is not None and title_node.text else ""
            link = link_node.text.strip() if link_node is not None and link_node.text else ""
            source = source_node.text.strip() if source_node is not None and source_node.text else ""
            pub = pub_node.text.strip() if pub_node is not None and pub_node.text else ""
            if title:
                entry = f"タイトル: {title}"
                if source:
                    entry += f" ({source})"
                if pub:
                    entry += f"\n発表日: {pub[:22]}"
                if link:
                    entry += f"\nURL: {link}"
                summary_items.append(entry)
        return "\n\n".join(summary_items) if summary_items else "検索結果なし"
    except Exception as e:
        print(f"[ERROR] Google News検索に失敗したし: {e}")
        return "検索結果なし"

def search_duckduckgo(query):
    """リスナーからの検索クエリをDuckDuckGoで検索し、上位結果を返す。
    クエリ文字列はそのまま使用し、改変しない。"""
    print(f"[🔍 ネット検索実行中...] クエリ: {query}")
    try:
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(query, max_results=5)]
            if results:
                summary_items = []
                for r in results:
                    url_part = f"\nURL: {r.get('href', '')}" if r.get('href') else ""
                    summary_items.append(f"タイトル: {r['title']}\n概要: {r['body']}{url_part}")
                return "\n\n".join(summary_items)
    except Exception as e:
        print(f"[ERROR] ネット検索に失敗したし: {e}")
    return "検索結果なし"
