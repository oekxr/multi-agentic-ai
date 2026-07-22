"""
Web App: OAuth Login + Pilih Video (Sesi A)

Alur:
    1. User buka halaman utama -> tombol "Login with Google"
    2. Redirect ke Google, user approve akses
    3. Google redirect balik ke /oauth2callback dengan authorization code
    4. Kita tukar code jadi credentials, ambil info channel YouTube user
    5. Simpan credentials ke database (per-user, lewat user_store.py)
    6. Tampilkan halaman /videos: daftar video VIDEO_LOOKBACK_DAYS hari
       terakhir dari channel user, dengan tombol aktifkan/nonaktifkan bot

CATATAN: Sesi A ini BELUM tersambung ke sistem moderasi (Scout/Classifier/
Moderator). Itu tahap Sesi B setelah alur login + pilih video ini terbukti
jalan dengan benar.
"""

import json
import os
from datetime import datetime, timedelta, timezone

# HANYA untuk development lokal: OAuth library menolak koneksi HTTP secara
# default (demi keamanan). Kita jalankan HTTP di localhost saat testing,
# jadi izinkan khusus kondisi ini -- TIDAK PERNAH aktif kalau environment
# variable RENDER ada (artinya sedang jalan di server Render yang sudah
# pasti pakai HTTPS).
if not os.environ.get("RENDER"):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from flask import Flask, redirect, render_template_string, request, session, url_for
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

import user_store
import overseer
import orchestrator
import activity_log
import ui_layout

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CLIENT_SECRET_WEB_FILE = "client_secret_web.json"

# Rentang waktu video yang ditampilkan -- ganti angka ini kalau mau ubah
# dari "1 bulan terakhir" ke rentang lain.
VIDEO_LOOKBACK_DAYS = 30


def _bootstrap_client_secret_from_env() -> None:
    """
    client_secret_web.json sengaja di-gitignore (rahasia), jadi tidak ikut
    ter-push ke GitHub -- artinya server deployment (Render) tidak akan
    punya file ini secara default. Fungsi ini membuatnya dari environment
    variable CLIENT_SECRET_WEB_JSON_BASE64 (isi file di-encode base64),
    supaya Flow.from_client_secrets_file() tetap bisa membaca filenya.

    Kalau file sudah ada di disk (kasus development lokal), tidak melakukan
    apa-apa.
    """
    import base64

    if os.path.exists(CLIENT_SECRET_WEB_FILE):
        return

    encoded = os.environ.get("CLIENT_SECRET_WEB_JSON_BASE64")
    if not encoded:
        return  # biarkan error asli muncul kalau memang dibutuhkan tapi tidak ada

    decoded = base64.b64decode(encoded).decode("utf-8")
    with open(CLIENT_SECRET_WEB_FILE, "w") as f:
        f.write(decoded)
    print("[Startup] client_secret_web.json dibuat dari environment variable.")


_bootstrap_client_secret_from_env()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-JANGAN-dipakai-di-production")

if os.environ.get("RENDER"):
    # Render (dan platform serupa) menghentikan HTTPS di proxy mereka, lalu
    # meneruskan request ke aplikasi kita sebagai HTTP biasa di jaringan
    # internal. Tanpa ProxyFix, Flask salah mengira request.url berskema
    # http://, padahal aslinya https:// -- ini yang menyebabkan
    # InsecureTransportError saat fetch_token, meskipun koneksi sungguhan
    # dari browser sudah HTTPS. ProxyFix membaca header X-Forwarded-Proto
    # dari proxy untuk mengoreksi ini.
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.register_blueprint(overseer.bp, url_prefix="/overseer")


def _redirect_uri() -> str:
    """
    Redirect URI untuk OAuth callback. Di Render, lebih aman pakai
    environment variable OAUTH_REDIRECT_URI eksplisit (karena Render ada
    di belakang proxy, url_for kadang salah deteksi http vs https).
    Untuk development lokal, fallback ke deteksi otomatis dari request.
    """
    configured = os.environ.get("OAUTH_REDIRECT_URI")
    if configured:
        return configured
    return url_for("oauth2callback", _external=True)


@app.route("/")
def index():
    if "channel_id" in session:
        return redirect(url_for("videos"))
    return render_template_string(LOGIN_TEMPLATE)


@app.route("/login")
def login():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_WEB_FILE, scopes=SCOPES, redirect_uri=_redirect_uri()
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["oauth_state"] = state
    session["code_verifier"] = flow.code_verifier
    return redirect(auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    state = session.get("oauth_state")
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_WEB_FILE, scopes=SCOPES, state=state, redirect_uri=_redirect_uri()
    )
    flow.code_verifier = session.get("code_verifier")
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    youtube = build("youtube", "v3", credentials=credentials)
    channel_response = youtube.channels().list(part="snippet,contentDetails", mine=True).execute()

    items = channel_response.get("items", [])
    if not items:
        return "Tidak ditemukan channel YouTube untuk akun Google ini.", 400

    channel = items[0]
    channel_id = channel["id"]
    channel_title = channel["snippet"]["title"]
    uploads_playlist_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    user_store.save_user(
        channel_id=channel_id,
        channel_title=channel_title,
        uploads_playlist_id=uploads_playlist_id,
        credentials_json=credentials.to_json(),
    )

    session["channel_id"] = channel_id
    session["channel_title"] = channel_title
    return redirect(url_for("videos"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/videos")
def videos():
    channel_id = session.get("channel_id")
    if not channel_id:
        return redirect(url_for("index"))

    user = user_store.get_user(channel_id)
    if not user:
        session.clear()
        return redirect(url_for("index"))

    video_list = _fetch_recent_videos(user)
    active_ids = user_store.get_active_video_ids(channel_id)

    return render_template_string(
        VIDEOS_TEMPLATE,
        channel_title=session.get("channel_title"),
        videos=video_list,
        active_ids=active_ids,
        lookback_days=VIDEO_LOOKBACK_DAYS,
        scout_interval=orchestrator.SCOUT_POLL_INTERVAL_SECONDS,
    )


@app.route("/api/activity")
def api_activity():
    channel_id = session.get("channel_id")
    if not channel_id:
        return {"events": []}, 401
    events = activity_log.get_recent_events(channel_id, limit=30)
    return {"events": events}


@app.route("/api/activity/dismiss/<int:event_id>", methods=["POST"])
def api_activity_dismiss(event_id):
    channel_id = session.get("channel_id")
    if not channel_id:
        return {"ok": False}, 401
    activity_log.dismiss_event(channel_id, event_id)
    return {"ok": True}


@app.route("/activate/<video_id>", methods=["POST"])
def activate(video_id):
    channel_id = session.get("channel_id")
    if not channel_id:
        return redirect(url_for("index"))
    title = request.form.get("title", "")
    user_store.activate_video(channel_id, video_id, title)
    return redirect(url_for("videos"))


@app.route("/deactivate/<video_id>", methods=["POST"])
def deactivate(video_id):
    channel_id = session.get("channel_id")
    if not channel_id:
        return redirect(url_for("index"))
    user_store.deactivate_video(channel_id, video_id)
    return redirect(url_for("videos"))


@app.route("/settings")
def settings_page():
    channel_id = session.get("channel_id")
    if not channel_id:
        return redirect(url_for("index"))

    current = user_store.get_settings(channel_id)
    return render_template_string(
        SETTINGS_TEMPLATE,
        sensitive_words=current["sensitive_words"],
        ambiguous_template=current["ambiguous_template"],
    )


@app.route("/settings", methods=["POST"])
def save_settings_route():
    channel_id = session.get("channel_id")
    if not channel_id:
        return redirect(url_for("index"))

    raw_words = request.form.get("sensitive_words", "")
    # Pisah per baris, buang baris kosong dan spasi berlebih
    words = [w.strip() for w in raw_words.splitlines() if w.strip()]

    template = request.form.get("ambiguous_template", "").strip()
    if not template:
        template = user_store.DEFAULT_AMBIGUOUS_TEMPLATE

    user_store.save_settings(channel_id, words, template)
    return redirect(url_for("settings_page"))


def _fetch_recent_videos(user: dict) -> list:
    """
    Ambil video dari uploads playlist channel user, filter yang
    diupload dalam VIDEO_LOOKBACK_DAYS hari terakhir.

    Dibatasi maksimal 3 halaman (150 video) supaya kuota API terkendali --
    playlistItems.list cuma 1 unit kuota per panggilan, jadi ini murah.
    """
    creds = Credentials.from_authorized_user_info(json.loads(user["credentials_json"]), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        user_store.update_credentials(user["channel_id"], creds.to_json())

    youtube = build("youtube", "v3", credentials=creds)
    cutoff = datetime.now(timezone.utc) - timedelta(days=VIDEO_LOOKBACK_DAYS)

    results = []
    next_page_token = None
    max_pages = 3

    for _ in range(max_pages):
        response = youtube.playlistItems().list(
            part="snippet",
            playlistId=user["uploads_playlist_id"],
            maxResults=50,
            pageToken=next_page_token,
        ).execute()

        for item in response.get("items", []):
            published_at_str = item["snippet"]["publishedAt"]
            published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
            if published_at >= cutoff:
                thumbnails = item["snippet"].get("thumbnails", {})
                thumbnail_url = (thumbnails.get("medium") or thumbnails.get("default") or {}).get("url", "")
                results.append(
                    {
                        "video_id": item["snippet"]["resourceId"]["videoId"],
                        "title": item["snippet"]["title"],
                        "published_at": published_at_str,
                        "thumbnail": thumbnail_url,
                    }
                )

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    results.sort(key=lambda v: v["published_at"], reverse=True)
    return results


LOGIN_TEMPLATE = """
<!doctype html>
<html>
<head>
<title>Multiagent Comment Moderator</title>
<style>
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    display: flex; align-items: center; justify-content: center;
    height: 100vh; margin: 0; background: #F8FAFC;
  }
  .card {
    background: white; padding: 2.5rem 3rem; border-radius: 14px;
    border: 1px solid #E2E8F0; text-align: center; max-width: 360px;
  }
  .card h1 { font-size: 1.3rem; margin: 0 0 0.5rem; color: #1E293B; }
  .card p { color: #64748B; font-size: 0.9rem; margin: 0 0 1.5rem; }
  a.button {
    display: inline-flex; align-items: center; gap: 0.5rem;
    padding: 0.75rem 1.6rem; background: #4F46E5; color: white;
    text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 0.9rem;
  }
  a.button:hover { background: #4338CA; }
</style>
</head>
<body>
<div class="card">
<h1>Multiagent Comment Moderator</h1>
<p>Login dengan akun Google (channel YouTube) untuk mulai.</p>
<a class="button" href="/login">Login with Google</a>
</div>
</body>
</html>
"""

SETTINGS_TEMPLATE = """
<!doctype html>
<html>
<head>
<title>Pengaturan - Comment Moderator</title>
<style>
""" + ui_layout.BASE_STYLE + """
  .settings-form {
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 1.5rem; max-width: 600px;
  }
  .field { margin-bottom: 1.4rem; }
  .field label { display: block; font-weight: 600; font-size: 0.88rem; margin-bottom: 0.4rem; }
  .field .hint { font-size: 0.78rem; color: var(--text-muted); margin-bottom: 0.6rem; line-height: 1.4; }
  textarea, input[type=text] {
    width: 100%; padding: 0.7rem 0.85rem; border: 1px solid var(--border); border-radius: 8px;
    font-size: 0.87rem; font-family: inherit; resize: vertical;
  }
  textarea { min-height: 120px; }
  .save-btn {
    background: var(--primary); color: white; border: none; padding: 0.65rem 1.4rem;
    border-radius: 8px; font-weight: 600; font-size: 0.87rem; cursor: pointer;
  }
  .word-count { font-size: 0.75rem; color: var(--text-muted); margin-top: 0.3rem; }
</style>
</head>
<body>
<div class="app-shell">
""" + ui_layout.sidebar_html("settings") + """
  <div class="main">
    <div class="topbar">
      <h1>Pengaturan</h1>
      <p>Kustomisasi kata sensitif dan template balasan</p>
    </div>
    <div class="page-content">
      <form class="settings-form" method="post" action="/settings">
        <div class="field">
          <label>Kata/Frasa Sensitif Custom</label>
          <p class="hint">
            Satu kata atau frasa per baris. Komentar yang mengandung salah satu
            dari ini akan dinilai lebih ketat oleh bot (cenderung dianggap
            spam/hate dengan confidence tinggi).
          </p>
          <textarea name="sensitive_words" placeholder="contoh:&#10;judi online&#10;pinjol ilegal&#10;kata kasar tertentu">{{ sensitive_words | join('\\n') }}</textarea>
        </div>

        <div class="field">
          <label>Template Balasan untuk Komentar Ambigu</label>
          <p class="hint">
            Dipakai otomatis saat bot tidak yakin kategori komentar setelah
            negosiasi (bukan pakai LLM, supaya konsisten dan aman).
          </p>
          <input type="text" name="ambiguous_template" value="{{ ambiguous_template }}">
        </div>

        <button class="save-btn" type="submit">Simpan Pengaturan</button>
      </form>
    </div>
  </div>
</div>
<script>
""" + ui_layout.SIDEBAR_SCRIPT + """
</script>
</body>
</html>
"""

VIDEOS_TEMPLATE = """
<!doctype html>
<html>
<head>
<title>Beranda - Comment Moderator</title>
<style>
""" + ui_layout.BASE_STYLE + """
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 1rem; }
  .content-grid { display: grid; grid-template-columns: 1fr 320px; gap: 1.5rem; align-items: start; }
  .card {
    background: var(--card-bg); border-radius: 10px; overflow: hidden;
    border: 1px solid var(--border); transition: box-shadow 0.15s;
  }
  .card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.06); }
  .card img { width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; }
  .card-body { padding: 0.85rem; }
  .card-body h3 { font-size: 0.88rem; margin: 0 0 0.6rem; line-height: 1.3; min-height: 2.3em; }
  button {
    padding: 0.5rem 1rem; border: none; border-radius: 6px; cursor: pointer;
    width: 100%; font-weight: 600; font-size: 0.85rem;
  }
  .activate { background: var(--primary); color: white; }
  .deactivate { background: var(--danger-bg); color: var(--danger); }
  .badge {
    display: inline-block; background: var(--success-bg); color: var(--success);
    font-size: 0.7rem; font-weight: 600; padding: 0.25rem 0.6rem; border-radius: 20px; margin-bottom: 0.6rem;
  }
  .panel-title { font-size: 0.95rem; margin: 0 0 0.2rem; }
  .live-dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--success); margin-right: 0.4rem; animation: pulse 1.5s infinite;
  }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .panel-subtitle { font-size: 0.78rem; color: var(--text-muted); margin: 0 0 0.8rem; }
  .log-entry {
    position: relative; padding: 0.6rem 1.6rem 0.6rem 0.7rem; border-radius: 8px;
    margin-bottom: 0.5rem; font-size: 0.8rem;
  }
  .log-entry .log-time { font-size: 0.7rem; opacity: 0.7; display: block; margin-bottom: 0.2rem; }
  .log-entry .log-video { font-weight: 600; font-size: 0.72rem; display: block; margin-bottom: 0.15rem; }
  .log-info { background: var(--info-bg); color: var(--text); }
  .log-success { background: var(--success-bg); color: var(--success); }
  .log-warning { background: var(--warning-bg); color: var(--warning); }
  .log-danger { background: var(--danger-bg); color: var(--danger); }
  .log-dismiss {
    position: absolute; top: 0.4rem; right: 0.5rem; cursor: pointer;
    font-size: 0.9rem; opacity: 0.6; line-height: 1;
  }
  .log-dismiss:hover { opacity: 1; }
</style>
</head>
<body>
<div class="app-shell">
""" + ui_layout.sidebar_html("beranda") + """
  <div class="main">
    <div class="topbar">
      <h1>{{ channel_title }}</h1>
      <p>Video {{ lookback_days }} hari terakhir</p>
    </div>

    <div class="page-content">
      <div class="content-grid">
        <div class="scroll-box">
          {% if videos %}
          <div class="grid">
          {% for v in videos %}
            <div class="card">
              <img src="{{ v.thumbnail }}" alt="">
              <div class="card-body">
                <h3>{{ v.title }}</h3>
                {% if v.video_id in active_ids %}
                  <span class="badge">&#9679; Bot Aktif</span>
                  <form method="post" action="/deactivate/{{ v.video_id }}">
                    <button class="deactivate" type="submit">Nonaktifkan Bot</button>
                  </form>
                {% else %}
                  <form method="post" action="/activate/{{ v.video_id }}">
                    <input type="hidden" name="title" value="{{ v.title }}">
                    <button class="activate" type="submit">Aktifkan Bot</button>
                  </form>
                {% endif %}
              </div>
            </div>
          {% endfor %}
          </div>
          {% else %}
          <p class="empty-state">Tidak ada video dalam {{ lookback_days }} hari terakhir.</p>
          {% endif %}
        </div>

        <div class="scroll-box">
          <h2 class="panel-title"><span class="live-dot"></span>Status Bot</h2>
          <p class="panel-subtitle">Bot memeriksa komentar baru tiap {{ scout_interval }} detik</p>
          <div id="log-container">
            <p class="empty-state" id="log-empty">Belum ada aktivitas.</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
""" + ui_layout.SIDEBAR_SCRIPT + """

var logEntries = {};

function escapeHtml(str) {
  var div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

function renderLog() {
  var container = document.getElementById('log-container');
  var keys = Object.keys(logEntries).filter(function(k) { return !logEntries[k].dismissed; });
  keys.sort(function(a, b) { return logEntries[b].data.id - logEntries[a].data.id; });

  if (keys.length === 0) {
    container.innerHTML = '<p class="empty-state" id="log-empty">Belum ada aktivitas.</p>';
    return;
  }

  container.innerHTML = keys.map(function(k) {
    var e = logEntries[k].data;
    var time = new Date(e.timestamp).toLocaleTimeString('id-ID');
    var level = ['info','success','warning','danger'].indexOf(e.level) !== -1 ? e.level : 'info';
    var dismissBtn = (level === 'success')
      ? '<span class="log-dismiss" data-dismiss-key="' + k + '">&times;</span>'
      : '';
    return '<div class="log-entry log-' + level + '">' + dismissBtn +
           '<span class="log-time">' + escapeHtml(time) + '</span>' +
           '<span class="log-video">' + escapeHtml(e.video_title) + '</span>' +
           escapeHtml(e.message) + '</div>';
  }).join('');

  // Event delegation lewat data-attribute, BUKAN inline onclick dengan teks
  // dinamis -- karena teks balasan dari LLM bisa mengandung tanda kutip yang
  // merusak atribut onclick kalau ditempel langsung sebagai string.
  container.querySelectorAll('[data-dismiss-key]').forEach(function(el) {
    el.addEventListener('click', function() {
      dismissLog(el.getAttribute('data-dismiss-key'));
    });
  });
}

async function dismissLog(key) {
  var entry = logEntries[key];
  if (!entry) return;

  // Optimistic UI: sembunyikan langsung di browser tanpa nunggu respons
  // server, biar terasa instan.
  entry.dismissed = true;
  renderLog();

  // Beri tahu SERVER juga event mana yang sudah di-dismiss -- supaya
  // statusnya tetap tersimpan meskipun halaman di-refresh atau user
  // pindah halaman lalu kembali lagi (server yang jadi sumber kebenaran,
  // bukan cuma memori browser yang hilang tiap reload).
  try {
    await fetch('/api/activity/dismiss/' + entry.data.id, { method: 'POST' });
  } catch (err) {
    console.error('Gagal menyimpan status dismiss ke server:', err);
  }
}

async function refreshActivityLog() {
  try {
    var res = await fetch('/api/activity');
    var data = await res.json();
    (data.events || []).forEach(function(e) {
      var key = 'log' + e.id;
      if (logEntries[key]) return; // sudah pernah diterima, jangan dobel

      logEntries[key] = { data: e, dismissed: false };

      if (e.message.indexOf('belum ada komentar baru') !== -1) {
        (function(k) {
          setTimeout(function() {
            if (logEntries[k]) dismissLog(k);
          }, 30000);
        })(key);
      }
    });
    renderLog();
  } catch (err) {
    console.error('Gagal ambil log aktivitas:', err);
  }
}
refreshActivityLog();
setInterval(refreshActivityLog, 5000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    user_store.init_db()

    # PENTING: Flask debug mode menjalankan proses DUA KALI (auto-reloader) --
    # tanpa pengaman ini, orchestrator (Scout + negosiasi + aksi) akan start
    # dobel, berpotensi memproses komentar yang sama dua kali. WERKZEUG_RUN_MAIN
    # cuma "true" di proses worker sungguhan, bukan di proses reloader awal.
    debug_mode = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    is_reloader_subprocess = os.environ.get("WERKZEUG_RUN_MAIN") == "true"

    if not debug_mode or is_reloader_subprocess:
        orchestrator.start_orchestrator()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug_mode)