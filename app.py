"""
Serveur backend — Système de comptage SCET-Tunisie
Déployé sur Render.com (gratuit)

Reçoit les données des agents et les stocke.
L'interface admin PC s'y connecte pour récupérer les données.
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import sqlite3
import json
import os
import hashlib
import hmac
from datetime import datetime

app = Flask(__name__)
CORS(app, origins="*")   # Autoriser l'app GitHub Pages et l'admin PC

# ─── Base de données (Render fournit un disque persistant) ────────────────────
DB_PATH = os.environ.get("DB_PATH", "comptage_serveur.db")

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c

def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS missions (
            code        TEXT PRIMARY KEY,    -- ex: RN7-2024 (généré par admin)
            nom         TEXT NOT NULL,
            config_json TEXT NOT NULL,        -- toute la config (postes, cats, etc.)
            cle_hash    TEXT NOT NULL,        -- hash de la clé secrète
            cree_le     TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code_mission TEXT NOT NULL,
            poste_id    TEXT,
            poste_nom   TEXT,
            sens        INTEGER DEFAULT 1,
            sens_label  TEXT,
            agent_nom   TEXT,
            date_jour   TEXT,
            debut       TEXT,
            synchro_le  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS clics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL,
            cat_id      TEXT NOT NULL,
            horodatage  TEXT NOT NULL,
            annule      INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL,
            nom         TEXT,
            data_b64    TEXT NOT NULL,
            recue_le    TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_clics_session  ON clics(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_code  ON sessions(code_mission);
        """)
    print("✅ DB initialisée")

init_db()


# ══════════════════════════════════════════════════════════════════════════════
#   UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

def hash_cle(cle):
    return hashlib.sha256(cle.encode()).hexdigest()

def verifier_cle(code, cle_fournie):
    with conn() as c:
        m = c.execute("SELECT cle_hash FROM missions WHERE code=?", (code,)).fetchone()
    if not m:
        return False
    return hmac.compare_digest(m["cle_hash"], hash_cle(cle_fournie))


# ══════════════════════════════════════════════════════════════════════════════
#   API — ADMIN PC
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/admin/publier", methods=["POST"])
def admin_publier():
    """
    L'admin PC envoie la configuration d'une mission.
    La mission devient accessible aux agents via son code.
    """
    data = request.json
    code      = data.get("code", "").upper().strip()
    nom       = data.get("nom", "")
    cle       = data.get("cle", "")
    config    = data.get("config", {})

    if not code or not nom or not cle:
        return jsonify({"ok": False, "message": "code, nom et cle requis"}), 400

    if len(cle) < 6:
        return jsonify({"ok": False, "message": "Clé trop courte (min 6 caractères)"}), 400

    with conn() as c:
        c.execute("""
            INSERT INTO missions (code, nom, config_json, cle_hash)
            VALUES (?,?,?,?)
            ON CONFLICT(code) DO UPDATE SET
              nom=excluded.nom,
              config_json=excluded.config_json,
              cle_hash=excluded.cle_hash
        """, (code, nom, json.dumps(config, ensure_ascii=False), hash_cle(cle)))

    return jsonify({"ok": True, "message": f"Mission '{code}' publiée", "code": code})


@app.route("/api/admin/donnees/<code>", methods=["GET"])
def admin_donnees(code):
    """L'admin récupère toutes les données synchronisées d'une mission."""
    cle = request.headers.get("X-Cle", "")
    if not verifier_cle(code, cle):
        return jsonify({"ok": False, "message": "Code ou clé invalide"}), 403

    with conn() as c:
        sessions = c.execute(
            "SELECT * FROM sessions WHERE code_mission=? ORDER BY date_jour, poste_nom",
            (code,)
        ).fetchall()

        resultat = []
        for sess in sessions:
            clics = c.execute(
                "SELECT cat_id, horodatage, annule FROM clics WHERE session_id=?",
                (sess["id"],)
            ).fetchall()
            nb_photos = c.execute(
                "SELECT COUNT(*) as n FROM photos WHERE session_id=?",
                (sess["id"],)
            ).fetchone()["n"]

            resultat.append({
                "session": dict(sess),
                "clics":   [dict(c_) for c_ in clics],
                "nb_photos": nb_photos,
            })

    return jsonify({"ok": True, "sessions": resultat})


@app.route("/api/admin/photos/<int:session_id>", methods=["GET"])
def admin_photos(session_id):
    """Récupérer les photos d'une session."""
    with conn() as c:
        photos = c.execute(
            "SELECT nom, data_b64, recue_le FROM photos WHERE session_id=?",
            (session_id,)
        ).fetchall()
    return jsonify({"ok": True, "photos": [dict(p) for p in photos]})


# ══════════════════════════════════════════════════════════════════════════════
#   API — AGENT MOBILE
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/mission/<code>")
def agent_mission(code):
    """
    L'agent entre son code → reçoit toute la config.
    Pas de clé requise ici (le code est partagé via QR).
    La sécurité est assurée par l'obscurité du code.
    """
    with conn() as c:
        m = c.execute(
            "SELECT nom, config_json FROM missions WHERE code=?",
            (code.upper(),)
        ).fetchone()

    if not m:
        return jsonify({"erreur": "Code mission invalide"}), 404

    config = json.loads(m["config_json"])
    config["nom"] = m["nom"]
    return jsonify(config)


@app.route("/api/sync", methods=["POST"])
def agent_sync():
    """Reçoit les clics et photos d'un agent."""
    data    = request.json
    session = data.get("session", {})
    clics   = data.get("clics",   [])
    photos  = data.get("photos",  [])

    if not session or not session.get("code_mission"):
        return jsonify({"ok": False, "message": "Données session manquantes"}), 400

    with conn() as c:
        # Créer la session
        cur = c.execute("""
            INSERT INTO sessions
            (code_mission, poste_id, poste_nom, sens, sens_label,
             agent_nom, date_jour, debut)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            session.get("code_mission"),
            session.get("poste_id"),
            session.get("poste_nom"),
            session.get("sens", 1),
            session.get("sens_label", "Sens 1"),
            session.get("agent_nom", "Agent"),
            session.get("date_jour", datetime.now().strftime("%Y-%m-%d")),
            session.get("debut"),
        ))
        session_id = cur.lastrowid

        # Enregistrer les clics
        for clic in clics:
            c.execute(
                "INSERT INTO clics (session_id, cat_id, horodatage, annule) VALUES (?,?,?,?)",
                (session_id, clic["cat_id"], clic["horodatage"], clic.get("annule", 0))
            )

        # Enregistrer les photos (compressées)
        for photo in photos[:10]:   # max 10 photos par session
            if photo.get("data") and len(photo["data"]) < 2_000_000:  # max 2Mo
                c.execute(
                    "INSERT INTO photos (session_id, nom, data_b64) VALUES (?,?,?)",
                    (session_id, photo.get("nom", "photo.jpg"), photo["data"])
                )

    nb_clics_valides = sum(1 for c in clics if not c.get("annule"))
    return jsonify({
        "ok":      True,
        "message": f"{nb_clics_valides} clics et {len(photos)} photos synchronisés",
        "session_id": session_id,
    })


@app.route("/api/ping")
def ping():
    return jsonify({"ok": True, "ts": datetime.now().isoformat(), "service": "SCET Comptage"})


@app.route("/")
def index():
    return jsonify({
        "service":  "SCET Comptage Backend",
        "version":  "2.0",
        "status":   "ok",
        "endpoints": {
            "ping":            "GET  /api/ping",
            "mission_config":  "GET  /api/mission/<code>",
            "sync_agent":      "POST /api/sync",
            "publier_mission": "POST /api/admin/publier",
            "donnees_admin":   "GET  /api/admin/donnees/<code>",
        }
    })


# ─── Lancement ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
