import os
import logging
from flask import Flask, current_app, render_template, request, redirect, url_for, jsonify, session, flash, abort
from flask_socketio import SocketIO
from functools import wraps
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from peewee import fn, PostgresqlDatabase, SqliteDatabase
from models import ChatMessage, Etablissement, User
import smtplib
from email.mime.text import MIMEText
from database import db
from flask_cors import CORS
# un identifiant propre pour chaque établissement
import re
import unicodedata
# fin
from liste_des_etablissements import DEFAULT_ETABLISSEMENTS

#=================  Fin des importations  ===================

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
        db.create_tables([Etablissement, User, ChatMessage], safe=True)
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
        "user_id": msg.user.id if msg.user else None,
        "is_admin": msg.user.is_admin if msg.user else False,
        "is_owner": msg.user.is_owner if msg.user else False,
    }

def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    try:
        return User.get_by_id(user_id)
    except User.DoesNotExist:
        session.pop("user_id", None)
        return None

def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapper

def generate_verification_code():
    """Retourne un code numérique à 5 chiffres."""
    import random
    return f"{random.randint(0, 99999):05d}"

def send_verification_email(recipient_email, code):
    """
    Envoie un email contenant le code de vérification.
    Utilise les variables SMTP_* déjà prévues dans Render/.env.
    """
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    from_email = os.getenv("FROM_EMAIL", smtp_user).strip()

    if not smtp_host or not smtp_user or not smtp_password or not from_email:
        raise RuntimeError("Configuration SMTP incomplète.")

    subject = "Code de vérification - Merci_..."
    body = f"""Bonjour,
                Votre code de vérification est : {code}
                Entrez ce code sur la page de vérification pour activer votre compte.
                Merci_...
            """
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = recipient_email

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

def seed_special_users():
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")

    owner_email = os.getenv("OWNER_EMAIL")
    owner_password = os.getenv("OWNER_PASSWORD")

    if admin_email and admin_password:
        user, created = User.get_or_create(
            email=admin_email.strip().lower(),
            defaults={
                "prenom": "Admin",
                "password_hash": generate_password_hash(admin_password),
                "is_verified": True,
                "is_admin": True,
                "is_owner": False,
            },
        )
        if not created:
            user.prenom = user.prenom or "Admin"
            user.password_hash = generate_password_hash(admin_password)
            user.is_verified = True
            user.is_admin = True
            user.is_owner = False
            user.save()

    if owner_email and owner_password:
        user, created = User.get_or_create(
            email=owner_email.strip().lower(),
            defaults={
                "prenom": "Propriétaire",
                "password_hash": generate_password_hash(owner_password),
                "is_verified": True,
                "is_admin": True,
                "is_owner": True,
            },
        )
        if not created:
            user.prenom = user.prenom or "Propriétaire"
            user.password_hash = generate_password_hash(owner_password)
            user.is_verified = True
            user.is_admin = True
            user.is_owner = True
            user.save()

def seed_etablissements():
    for item in DEFAULT_ETABLISSEMENTS:
        nom = item["nom"].strip()
        pays = item["pays"].strip()
        school_type = item["type"].strip()

        existing = Etablissement.select().where(Etablissement.nom == nom).first()
        if existing:
            continue

        Etablissement.create(
            code=generate_school_code(nom),
            nom=nom,
            pays=pays,
            type=school_type,
            is_validated=True,
        )

# ================= Début des identifiants pour les établissements
def slugify_text(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[\s_-]+", " ", value).strip()
    return value


def generate_school_code(name):
    ignored = {
        "de", "du", "des", "la", "le", "les", "a", "au", "aux",
        "of", "the", "and", "et", "d", "l"
    }

    cleaned = slugify_text(name)
    words = [w for w in cleaned.split() if w and w not in ignored]

    if not words:
        return "etablissement"

    code = "".join(w[:2] for w in words)[:20]
    if not code:
        code = cleaned.replace(" ", "")[:20] or "etablissement"

    base_code = code
    i = 2
    while Etablissement.select().where(Etablissement.code == code).exists():
        code = f"{base_code}{i}"
        i += 1

    return code
# ================= Fin des identifiants pour les établissements

@app.context_processor
def inject_current_user():
    return {"current_user": get_current_user()}

# ==================  Appels de quelques fonctions utilitaires ==========

init_schema_once()
seed_special_users()
seed_etablissements()

# =============================================================== 

@app.route("/", methods=["GET"])
def index():
    etablissement = normalize_etablissement(request.args.get("etablissement"))

    query = ChatMessage.select()
    if etablissement:
        query = query.where(ChatMessage.etablissement_scolaire == etablissement)

    messages = query.order_by(ChatMessage.created_at.desc())
    return render_template(
        "index_backend.html",
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
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "Connexion requise."}), 401

    if not current_user.is_verified:
        return jsonify({"ok": False, "error": "Compte non vérifié."}), 403

    data = request.get_json(silent=True) or {}
    programme = (data.get("programme") or "").strip()
    commentaire = (data.get("commentaire") or "").strip()

    if not commentaire:
        return jsonify({"ok": False, "error": "Le message est requis."}), 400

    if not current_user.etablissement_scolaire:
        return jsonify({
            "ok": False,
            "error": "Complétez votre profil avant de publier."
        }), 400

    msg = ChatMessage.create(
        user=current_user,
        prenom=current_user.prenom[:50],
        programme=programme[:120] if programme else "",
        etablissement_scolaire=current_user.etablissement_scolaire,
        commentaire=commentaire[:2000],
    )

    enforce_cap()
    payload = build_message_payload(msg)
    socketio.emit("chat:new", payload)
    return jsonify({"ok": True, "message": payload})


@app.get("/api/etablissements")
def api_etablissements():
    items = (
        Etablissement
        .select()
        .where(Etablissement.is_validated == True)
        .order_by(Etablissement.nom.asc())
    )

    return jsonify([
        {
            "id": e.id,
            "code": e.code,
            "nom": e.nom,
            "pays": e.pays,
            "type": e.type,
        }
        for e in items
    ])

@app.post("/api/etablissements")
@login_required
def api_add_etablissement():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "Connexion requise."}), 401

    data = request.get_json(silent=True) or {}

    nom = (data.get("nom") or "").strip()
    pays = (data.get("pays") or "").strip()
    school_type = (data.get("type") or "").strip()

    if not nom or not pays or not school_type:
        return jsonify({"ok": False, "error": "Nom, pays et type sont requis."}), 400

    existing = Etablissement.select().where(Etablissement.nom == nom).first()
    if existing:
        return jsonify({
            "ok": True,
            "already_exists": True,
            "etablissement": {
                "id": existing.id,
                "code": existing.code,
                "nom": existing.nom,
                "pays": existing.pays,
                "type": existing.type,
            }
        })

    code = generate_school_code(nom)

    school = Etablissement.create(
        code=code,
        nom=nom[:255],
        pays=pays[:120],
        type=school_type[:80],
        is_validated=True,
    )

    return jsonify({
        "ok": True,
        "already_exists": False,
        "etablissement": {
            "id": school.id,
            "code": school.code,
            "nom": school.nom,
            "pays": school.pays,
            "type": school.type,
        }
    }), 201


@app.post("/post")
@login_required
def post_form():
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    if not current_user.is_verified:
        flash("Votre compte doit être vérifié avant de publier.", "error")
        return redirect(url_for("verify_account", email=current_user.email))

    programme = (request.form.get("programme") or "").strip()
    etablissement_scolaire = normalize_etablissement(
        request.form.get("etablissement_scolaire") or current_user.etablissement_scolaire
    )
    commentaire = (request.form.get("commentaire") or "").strip()

    if programme and etablissement_scolaire and commentaire:
        msg = ChatMessage.create(
            user=current_user,
            prenom=current_user.prenom[:50],
            programme=programme[:120],
            etablissement_scolaire=etablissement_scolaire,
            commentaire=commentaire[:2000],
        )
        enforce_cap()
        socketio.emit("chat:new", build_message_payload(msg))
        flash("Message publié avec succès.", "success")
    else:
        flash("Tous les champs sont requis.", "error")

    return redirect(url_for("index"))


@app.route("/signup/", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html")

    prenom = (request.form.get("prenom") or "").strip()
    nom = (request.form.get("nom") or "").strip()
    ville = (request.form.get("ville") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    mot_de_passe = (request.form.get("mot_de_passe") or "").strip()
    etablissement_scolaire = normalize_etablissement(
        request.form.get("etablissement_scolaire")
    )

    if not prenom or not nom or not ville or not email or not mot_de_passe:
        flash("Tous les champs obligatoires doivent être remplis.", "error")
        return render_template("signup.html"), 400

    if len(mot_de_passe) < 6:
        flash("Le mot de passe doit contenir au moins 6 caractères.", "error")
        return render_template("signup.html"), 400

    existing_user = User.select().where(User.email == email).first()
    if existing_user:
        flash("Un compte existe déjà avec cet email.", "error")
        return render_template("signup.html"), 409

    code = generate_verification_code()

    try:
        user = User.create(
            prenom=prenom[:50],
            nom=nom[:80],
            ville=ville[:120],
            pays=pays[:120],
            type_etablissement=type_etablissement[:80],
            programme=programme[:120],
            email=email,
            password_hash=generate_password_hash(mot_de_passe),
            etablissement_scolaire=etablissement_scolaire or None,
            is_verified=False,
            verification_code=code,
            verification_created_at=datetime.utcnow(),
        )

        send_verification_email(email, code)

        flash("Compte créé. Vérifiez votre email pour activer votre compte.", "success")
        return redirect(url_for("verify_account", email=email))

    except Exception as e:
        log.exception("[SIGNUP] erreur: %s", e)
        flash("Impossible de créer le compte pour le moment.", "error")
        return render_template("signup.html"), 500


@app.route("/verify/", methods=["GET", "POST"])
def verify_account():
    prefill_email = (request.args.get("email") or "").strip().lower()

    if request.method == "GET":
        return render_template("verify_account.html", prefill_email=prefill_email)

    email = (request.form.get("email") or "").strip().lower()
    code = (request.form.get("code") or "").strip()

    if not email or not code:
        flash("Email et code requis.", "error")
        return render_template("verify_account.html", prefill_email=email), 400

    user = User.select().where(User.email == email).first()
    if not user:
        flash("Aucun compte trouvé avec cet email.", "error")
        return render_template("verify_account.html", prefill_email=email), 404

    if user.is_verified:
        flash("Ce compte est déjà vérifié. Vous pouvez vous connecter.", "info")
        return redirect(url_for("login"))

    if not user.verification_code or user.verification_code != code:
        flash("Code de vérification invalide.", "error")
        return render_template("verify_account.html", prefill_email=email), 400

    # Option simple : expiration après 30 minutes
    if user.verification_created_at:
        delta = datetime.utcnow() - user.verification_created_at
        if delta > timedelta(minutes=30):
            flash("Le code a expiré. Recréez un compte ou demandez un nouveau code plus tard.", "error")
            return render_template("verify_account.html", prefill_email=email), 400

    user.is_verified = True
    user.verification_code = None
    user.verification_created_at = None
    user.save()

    session["user_id"] = user.id
    flash("Compte vérifié avec succès. Vous êtes maintenant connecté.", "success")
    return redirect(url_for("index"))


@app.route("/login/", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = (request.form.get("email") or "").strip().lower()
    mot_de_passe = (request.form.get("mot_de_passe") or "").strip()

    if not email or not mot_de_passe:
        flash("Email et mot de passe requis.", "error")
        return render_template("login.html"), 400

    user = User.select().where(User.email == email).first()
    if not user:
        flash("Email ou mot de passe invalide.", "error")
        return render_template("login.html"), 401

    if not check_password_hash(user.password_hash, mot_de_passe):
        flash("Email ou mot de passe invalide.", "error")
        return render_template("login.html"), 401

    if not user.is_verified:
        flash("Votre compte n'est pas encore vérifié.", "error")
        return redirect(url_for("verify_account", email=user.email))

    session["user_id"] = user.id
    flash("Connexion réussie.", "success")

    next_url = request.args.get("next")
    if next_url:
        return redirect(next_url)

    return redirect(url_for("index"))

@app.route("/logout/")
def logout():
    session.pop("user_id", None)
    flash("Déconnexion réussie.", "success")
    return redirect(url_for("index"))


@app.route("/update-profile/", methods=["GET", "POST"])
@login_required
def update_profile():
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    if request.method == "GET":
        return render_template("update_profil.html", user=current_user)

    current_user.prenom = (request.form.get("prenom") or "").strip()[:50]
    current_user.nom = (request.form.get("nom") or "").strip()[:80]
    current_user.ville = (request.form.get("ville") or "").strip()[:120]
    current_user.pays = (request.form.get("pays") or "").strip()[:120]
    current_user.etablissement_scolaire = (request.form.get("etablissement_scolaire") or "").strip()[:180]
    current_user.type_etablissement = (request.form.get("type_etablissement") or "").strip()[:80]
    current_user.programme = (request.form.get("programme") or "").strip()[:120]

    if not current_user.prenom or not current_user.etablissement_scolaire:
        flash("Le prénom et l'établissement sont requis.", "error")
        return render_template("update_profil.html", user=current_user), 400

    current_user.save()
    flash("Profil mis à jour avec succès.", "success")
    return redirect(url_for("index"))


@app.route("/delete-account/", methods=["GET", "POST"])
@login_required
def delete_account():
    current_user = get_current_user()
    if not current_user:
        abort(401)

    if request.method == "GET":
        return render_template("account_delete.html")

    try:
        # On supprime d'abord les messages liés
        ChatMessage.delete().where(ChatMessage.user == current_user).execute()
        current_user.delete_instance()
        session.pop("user_id", None)
        flash("Votre compte a été supprimé.", "success")
        return redirect(url_for("index"))
    except Exception as e:
        log.exception("[DELETE ACCOUNT] erreur: %s", e)
        flash("Impossible de supprimer le compte pour le moment.", "error")
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
