import os
import logging
from flask import Flask, current_app, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO
from peewee import fn, PostgresqlDatabase, SqliteDatabase
from models import ChatMessage
from database import db
from flask_cors import CORS

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

socketio = SocketIO(app, cors_allowed_origins="*")

PAGES_ORIGINS = [
    "https://joel3500.github.io",
    "https://joel3500.github.io/Merci_UQAC",
]

CORS(
    app,
    resources={
        r"/api/*": {"origins": PAGES_ORIGINS},
        r"/post": {"origins": PAGES_ORIGINS},
        r"/debug/*": {"origins": PAGES_ORIGINS},
    },
    supports_credentials=False,
    allow_headers=["Content-Type"],
    methods=["GET", "POST", "OPTIONS"],
)

MAX_MESSAGES = 100
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("merci_uqac")
IS_PROD = os.getenv("APP_ENV") == "production" or os.getenv("RENDER") == "true"


def get_table_columns(table_name):
    """Retourne les noms de colonnes d'une table, autant pour SQLite que PostgreSQL."""
    if isinstance(db, SqliteDatabase):
        rows = db.execute_sql(f"PRAGMA table_info({table_name});").fetchall()
        return {row[1] for row in rows}

    if isinstance(db, PostgresqlDatabase):
        rows = db.execute_sql(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s;
            """,
            (table_name,),
        ).fetchall()
        return {row[0] for row in rows}

    return set()


def ensure_chatmessage_schema():
    with db:
        db.create_tables([ChatMessage], safe=True)
        columns = get_table_columns("chatmessage")

        if "etablissement_scolaire" not in columns:
            db.execute_sql(
                "ALTER TABLE chatmessage ADD COLUMN etablissement_scolaire VARCHAR(180);"
            )
            log.info("[DB] colonne etablissement_scolaire ajoutée")


def init_schema_once():
    try:
        ensure_chatmessage_schema()
        log.info("[DB] schema OK")
    except Exception as e:
        log.exception("[DB] init schema failed: %s", e)


init_schema_once()


def enforce_cap(max_rows=MAX_MESSAGES):
    """Garde au plus max_rows messages en supprimant les plus anciens."""
    with db.atomic():
        total = ChatMessage.select(fn.COUNT(ChatMessage.id)).scalar() or 0
        if total > max_rows:
            excess = total - max_rows
            old_ids = (
                ChatMessage.select(ChatMessage.id)
                .order_by(ChatMessage.created_at.asc())
                .limit(excess)
            )
            ChatMessage.delete().where(ChatMessage.id.in_(old_ids)).execute()


def build_images_list():
    return [
        f"carousel/{name}"
        for name in sorted(os.listdir(os.path.join(app.static_folder, "carousel")))
        if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]


def normalize_etablissement(raw_value):
    return (raw_value or "").strip()[:180]


def build_message_payload(msg):
    return {
        "prenom": msg.prenom,
        "programme": msg.programme,
        "etablissement_scolaire": msg.etablissement_scolaire or "",
        "commentaire": msg.commentaire,
        "created_at": msg.created_at.isoformat() + "Z",
    }


@app.route("/", methods=["GET"])
def index():
    etablissement = normalize_etablissement(request.args.get("etablissement"))

    query = ChatMessage.select()
    if etablissement:
        query = query.where(ChatMessage.etablissement_scolaire == etablissement)

    messages = query.order_by(ChatMessage.created_at.desc())
    return render_template(
        "index.html",
        messages=messages,
        images=build_images_list(),
        filtre_etablissement=etablissement,
    )

@app.route("/but_du_site", methods=["GET"])
def but_du_site():
    return render_template("but_du_site.html")

@app.get("/api/health")
def api_health():
    try:
        db.connect(reuse_if_open=True)
        db.execute_sql("SELECT 1;").fetchone()
        total = ChatMessage.select(fn.COUNT(ChatMessage.id)).scalar() or 0
        return jsonify({"ok": True, "rows": total}), 200
    except Exception as e:
        current_app.logger.exception("health error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/chat")
def api_chat_get():
    etablissement = normalize_etablissement(request.args.get("etablissement"))
    query = ChatMessage.select()
    if etablissement:
        query = query.where(ChatMessage.etablissement_scolaire == etablissement)

    messages = query.order_by(ChatMessage.created_at.desc()).limit(MAX_MESSAGES)
    return jsonify([build_message_payload(m) for m in messages])


@app.post("/api/chat")
def api_chat_post():
    data = request.get_json(silent=True) or {}
    prenom = (data.get("prenom") or "").strip()
    programme = (data.get("programme") or "").strip()
    etablissement_scolaire = normalize_etablissement(data.get("etablissement_scolaire"))
    commentaire = (data.get("commentaire") or "").strip()

    if not prenom or not programme or not etablissement_scolaire or not commentaire:
        return jsonify({"ok": False, "error": "Tous les champs sont requis."}), 400

    msg = ChatMessage.create(
        prenom=prenom[:50],
        programme=programme[:120],
        etablissement_scolaire=etablissement_scolaire,
        commentaire=commentaire[:2000],
    )
    enforce_cap()

    payload = build_message_payload(msg)
    socketio.emit("chat:new", payload)
    return jsonify({"ok": True, "message": payload})


@app.post("/post")
def post_form():
    prenom = (request.form.get("prenom") or "").strip()
    programme = (request.form.get("programme") or "").strip()
    etablissement_scolaire = normalize_etablissement(request.form.get("etablissement_scolaire"))
    commentaire = (request.form.get("commentaire") or "").strip()

    if prenom and programme and etablissement_scolaire and commentaire:
        msg = ChatMessage.create(
            prenom=prenom[:50],
            programme=programme[:120],
            etablissement_scolaire=etablissement_scolaire,
            commentaire=commentaire[:2000],
        )
        enforce_cap()
        socketio.emit("chat:new", build_message_payload(msg))
    return redirect(url_for("index"))


@app.get("/debug/db")
def debug_db():
    backend = type(db).__name__
    try:
        if isinstance(db, PostgresqlDatabase):
            kind = "postgresql"
            name = db.database
            host = getattr(db, "host", None)
        elif isinstance(db, SqliteDatabase):
            kind = "sqlite"
            name = db.database
            host = None
        else:
            kind = type(db).__name__
            name = getattr(db, "database", None)
            host = None

        version = db.execute_sql("select version()").fetchone()[0]
        total = ChatMessage.select(fn.COUNT(ChatMessage.id)).scalar() or 0
        return jsonify({
            "ok": True,
            "env": "production" if IS_PROD else "development",
            "backend": kind,
            "database": name,
            "host": host,
            "version": version,
            "rows": total,
        })
    except Exception as e:
        log.exception("[DEBUG/DB] error: %s", e)
        return jsonify({"ok": False, "backend": backend, "error": str(e)}), 500


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
