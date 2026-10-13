"""STEDS Web (Streamlit): password, upload a STEDS backup zip, search everything.

Run locally (PowerShell, from C:\\stedsweb):
    $env:APP_PASSWORD="test123"
    .\\.venv\\Scripts\\python.exe -m streamlit run app.py
"""
import hmac
import os
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from importer import ImportFailed, import_zip

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "steds.db"

st.set_page_config(page_title="STEDS Web", layout="wide")


# ---------- Login ----------

def require_password():
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        if os.environ.get("RENDER"):
            st.error("APP_PASSWORD n'est pas configuré sur le serveur.")
            st.stop()
        expected = "admin"  # local testing only

    if st.session_state.get("auth"):
        return

    st.title("STEDS Web")
    with st.form("login"):
        typed = st.text_input("Mot de passe", type="password")
        submitted = st.form_submit_button("Entrer")
    if submitted:
        if hmac.compare_digest(typed.encode(), expected.encode()):
            st.session_state["auth"] = True
            st.rerun()
        st.error("Mot de passe incorrect.")
    st.stop()


# ---------- Database helpers ----------

def query(sql, params=()):
    """Run a read-only query; returns (dataframe, error message or None)."""
    con = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)
    try:
        return pd.read_sql_query(sql, con, params=params), None
    except Exception as e:  # missing table or column in an unusual backup
        return pd.DataFrame(), str(e)
    finally:
        con.close()


def load_status():
    if not DB_PATH.exists():
        return None
    meta, err = query("SELECT key, value FROM _meta")
    if err:
        return None
    tables, _ = query('SELECT label AS "Table", name AS "Fichier", rows AS "Lignes" FROM _tables ORDER BY rowid')
    last, _ = query("SELECT MAX(cdat) AS d FROM comrep")
    return {
        "meta": dict(zip(meta["key"], meta["value"])),
        "tables": tables,
        "last_date": None if last.empty else last.iloc[0]["d"],
    }


# ---------- Upload ----------

def upload_panel():
    st.header("Données")
    uploaded = st.file_uploader("Zip de sauvegarde STEDS", type=["zip"])
    if uploaded is not None:
        key = (uploaded.name, uploaded.size)
        if st.session_state.get("imported") != key:
            with st.spinner("Import en cours…"):
                fd, tmp_path = tempfile.mkstemp(suffix=".zip", dir=DATA_DIR)
                try:
                    with os.fdopen(fd, "wb") as out:
                        out.write(uploaded.getbuffer())
                    import_zip(tmp_path, DB_PATH, uploaded.name)
                    st.session_state["import_error"] = None
                except ImportFailed as e:
                    st.session_state["import_error"] = str(e)
                finally:
                    os.remove(tmp_path)
            st.session_state["imported"] = key

    if st.session_state.get("import_error"):
        st.error(st.session_state["import_error"])

    status = load_status()
    if status:
        st.caption(
            f"Fichier : {status['meta'].get('source', '?')}  \n"
            f"Importé le : {status['meta'].get('imported_at', '?')} (UTC)  \n"
            f"Dernier dossier saisi : {status['last_date'] or '?'}"
        )
        with st.expander("Tables importées"):
            st.dataframe(status["tables"], hide_index=True)
    return status


# ---------- Search ----------

def show(tab, df, err):
    with tab:
        if err:
            st.warning(f"Recherche impossible sur cette table : {err}")
        elif df.empty:
            st.caption("Aucun résultat.")
        else:
            st.dataframe(df, hide_index=True)


def search_page():
    st.title("Recherche")
    q = st.text_input(
        "Rechercher",
        placeholder="Nom du client, n° de dossier, facture, BL, référence ou n° de série…",
        label_visibility="collapsed",
    ).strip()
    if len(q) < 2:
        st.caption("Tapez au moins 2 caractères. Un nom de client affiche ses dossiers, BL et factures.")
        return

    like = f"%{q}%"

    clients = query(
        """SELECT clcod AS "Code", clrais AS "Client", clville AS "Ville",
                  cltel AS "Tél", clgsm AS "GSM", cltva AS "TVA"
           FROM fclt
           WHERE clrais LIKE ? OR clcod LIKE ? OR clctva LIKE ?
           ORDER BY clrais LIMIT 100""",
        (like, like, like),
    )
    dossiers = query(
        """SELECT c.cn AS "Dossier", c.cdat AS "Date", f.clrais AS "Client",
                  c.ccarte AS "Pièce", c.cref AS "Réf.", c.cnser AS "N° série",
                  c.ctyp AS "Type", c.cnbon AS "BL", c.cnfact AS "Facture"
           FROM comrep c LEFT JOIN fclt f ON f.clcod = c.ccl
           WHERE c.cn LIKE ? OR c.cref LIKE ? OR c.cnser LIKE ? OR c.ccarte LIKE ? OR f.clrais LIKE ?
           ORDER BY c.cdat DESC LIMIT 100""",
        (like,) * 5,
    )
    factures = query(
        """SELECT fa.fn AS "Facture", fa.fdat AS "Date", f.clrais AS "Client",
                  (SELECT SUM(cp.cpdeb) FROM fcpcl cp
                   WHERE cp.cpcl = fa.fcl AND cp.cpfact = fa.fcpfact AND cp.cpnat = 'F') AS "TTC",
                  fa.fmrg AS "Règlement"
           FROM fact fa LEFT JOIN fclt f ON f.clcod = fa.fcl
           WHERE fa.fn LIKE ? OR f.clrais LIKE ?
           ORDER BY fa.fdat DESC LIMIT 100""",
        (like, like),
    )
    bons = query(
        """SELECT b.bn AS "BL", b.bdat AS "Date", f.clrais AS "Client", b.bfn AS "Facture"
           FROM bon b LEFT JOIN fclt f ON f.clcod = b.bcl
           WHERE b.bn LIKE ? OR f.clrais LIKE ?
           ORDER BY b.bdat DESC LIMIT 100""",
        (like, like),
    )

    results = [("Clients", clients), ("Dossiers", dossiers), ("Factures", factures), ("BL", bons)]
    tabs = st.tabs([f"{name} ({len(df)})" for name, (df, _) in results])
    for tab, (_, (df, err)) in zip(tabs, results):
        show(tab, df, err)
    st.caption("100 résultats maximum par onglet. TTC = montant inscrit au compte client.")


# ---------- Page ----------

require_password()
with st.sidebar:
    status = upload_panel()
if status is None:
    st.title("STEDS Web")
    st.info("Chargez le zip de sauvegarde STEDS dans le panneau de gauche pour commencer.")
else:
    search_page()