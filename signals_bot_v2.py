s bot v2 · PY
"""
BOT DE SIGNAUX V2 : ELLIOTT + FIBONACCI (OTE) + ICT + RISQUE "BANQUE"
=====================================================================
Données Yahoo Finance (gratuit). Alertes sur Telegram, ordre passé à la main
dans MetaTrader 5.
 
LOGIQUE
-------
1) ELLIOTT / FIBONACCI (graphique 1 h, ta méthode)
   - On détecte les vagues avec un zigzag basé sur l'ATR.
   - Jambe de référence (vague A, W...) puis retracement (vague B, X...).
   - Entrée : le retracement touche la zone 0,62 / 0,705 / 0,79 de la jambe.
   - Stop : au bas de la jambe (au sommet pour les ventes, en miroir).
   - Objectifs : extensions 1,272 / 1,414 / 1,618 de la jambe, projetées
     depuis la fin du retracement (vague C, Y ou Z).
   NB : 0,62-0,79 avec le point idéal à 0,705 est exactement la zone OTE
   (Optimal Trade Entry) de l'ICT.
 
2) ICT (graphique 15 min), confirmation de l'entrée
   - Liquidity sweep : chasse des stops sous PDL (plus bas de la veille),
     bas de la session Asie ou swing low (sommets en miroir).
   - MSS / CHoCH : cassure de structure après le point bas.
   - FVG (Fair Value Gap) et Order Block retestés.
   - Kill zones (Londres, New York), heure de New York.
   - Midnight Open : achat sous l'ouverture de minuit NY (discount),
     vente au-dessus (premium).
 
3) RISQUE INSTITUTIONNEL
   - Taille de position à risque fixe, modulée par la conviction (score).
   - Plafond de marge, plafond de risque total par scan (heat),
     limite d'exposition au USD (pas 3 positions "short USD" en même temps).
   - Sorties partielles : 50 % TP1 (puis stop à l'entrée), 30 % TP2, 20 % TP3.
   - Filtre de coûts (spread + commission) et de ratio gain/risque minimum.
 
UTILISATION
    python signals_bot_v2.py          -> analyse réelle (+ Telegram si configuré)
    python signals_bot_v2.py --demo   -> test avec données synthétiques
    python signals_bot_v2.py --force  -> envoie d'abord un message de test Telegram
 
Variables d'environnement (optionnelles) :
    CAPITAL (défaut 1000), LEVERAGE (défaut 100), TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID, STOP_MODE ("leg" = bas de la jambe [défaut],
    "ict" = sous le point bas du retracement)
 
AVERTISSEMENT : ce bot n'a pas été backtesté et ne garantit aucun gain.
"""
 
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
 
import numpy as np
import pandas as pd
 
try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:                                   # pas de base de fuseaux horaires
    NY = timezone(timedelta(hours=-5))
 
# =============================== CONFIG ===============================
CAPITAL = float(os.getenv("CAPITAL", "1000"))
LEVERAGE = float(os.getenv("LEVERAGE", "100"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
STOP_MODE = os.getenv("STOP_MODE", "leg").strip().lower()
 
INTERVALLE = "15m"
PERIODE = "30d"
FRAICHEUR_MAX_MIN = 45
WEEKEND_OFF = True
 
# --- Elliott / Fibonacci (graphique 1 h) ---
ZZ_MULT = 2.0              # un retournement = 2 x ATR(1h) pour confirmer un pivot
MIN_LEG_ATR = 3.0          # jambe de référence >= 3 x ATR(1h)
RB_MIN, RB_MAX = 0.58, 0.86  # le retracement doit avoir touché la zone 0,62-0,79 (avec tolérance)
RC_MIN = 0.40              # le prix n'a pas déjà rebondi au-delà de 40 % de retracement
FIB_ENTREE = (0.62, 0.705, 0.79)
FIB_TP = (1.272, 1.414, 1.618)
STOP_BUFFER_ATR = 0.10     # petite marge au-delà du stop (chasse aux stops)
MIN_RR = 1.0               # ratio gain/risque minimum vers TP1
 
# --- ICT (graphique 15 min) ---
N_ICT = 20                 # fenêtre (bougies 15 min) pour trouver le point bas du retracement
KILL_ZONES = [("Londres", 2.0, 5.0), ("New York AM", 7.0, 10.0),
              ("Silver Bullet", 10.0, 11.0), ("New York PM", 13.5, 16.0)]  # heure de New York
 
# --- Score de confluence ---
SCORE_MIN = 6.0
 
# --- Risque (style desk institutionnel) ---
RISQUE_PAR_TRADE = 0.01     # 1 % du capital au maximum (modulé par la conviction)
RISQUE_TOTAL_MAX = 0.03     # 3 % de risque cumulé maximum par scan
MAX_SIGNAUX = 3
MAX_PAR_COTE_USD = 2        # max 2 signaux avec la même exposition au dollar
MARGE_MAX_PCT = 0.20
LOT_MIN, LOT_PAS = 0.01, 0.01
 
INSTRUMENTS = {
    "EURUSD": dict(ticker="EURUSD=X", contract=100000, base_usd=False, conv="usd", cost=0.00015, pip=0.0001),
    "GBPUSD": dict(ticker="GBPUSD=X", contract=100000, base_usd=False, conv="usd", cost=0.00020, pip=0.0001),
    "AUDUSD": dict(ticker="AUDUSD=X", contract=100000, base_usd=False, conv="usd", cost=0.00025, pip=0.0001),
    "NZDUSD": dict(ticker="NZDUSD=X", contract=100000, base_usd=False, conv="usd", cost=0.00030, pip=0.0001),
    "USDJPY": dict(ticker="JPY=X",    contract=100000, base_usd=True,  conv="div", cost=0.00015, pip=0.01),
    "USDCAD": dict(ticker="CAD=X",    contract=100000, base_usd=True,  conv="div", cost=0.00025, pip=0.0001),
    "USDCHF": dict(ticker="CHF=X",    contract=100000, base_usd=True,  conv="div", cost=0.00025, pip=0.0001),
    "XAUUSD": dict(ticker="GC=F",     contract=100,    base_usd=False, conv="usd", cost=0.00040, pip=0.1),
    "BTCUSD": dict(ticker="BTC-USD",  contract=1,      base_usd=False, conv="usd", cost=0.00080, pip=1.0),
}
# ======================================================================
 
 
# ----------------------------- OUTILS -----------------------------
def atr(df, n=14):
    c = df["close"]
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - c.shift()).abs(),
                    (df["low"] - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()
 
 
def flip(df):
    """Miroir des prix : permet d'utiliser la logique d'achat pour les ventes."""
    return pd.DataFrame({"open": -df["open"], "high": -df["low"],
                         "low": -df["high"], "close": -df["close"],
                         "volume": df["volume"]}, index=df.index)
 
 
def fractals(h, l, n=2):
    sh, sl = [], []
    for i in range(n, len(h) - n):
        if h[i] > h[i - n:i].max() and h[i] >= h[i + 1:i + n + 1].max():
            sh.append(i)
        if l[i] < l[i - n:i].min() and l[i] <= l[i + 1:i + n + 1].min():
            sl.append(i)
    return sh, sl
 
 
# ----------------------------- ELLIOTT / FIBONACCI -----------------------------
def zigzag(h, l, a, mult):
    """Pivots confirmés : retournement d'au moins mult x ATR."""
    n = len(h)
    piv, d, hi_i, lo_i = [], 0, 0, 0
    for i in range(1, n):
        thr = mult * a[i]
        if not thr > 0:
            continue
        if d == 0:
            if h[i] > h[hi_i]:
                hi_i = i
            if l[i] < l[lo_i]:
                lo_i = i
            if h[hi_i] - l[lo_i] >= thr:
                if lo_i < hi_i:
                    piv.append(("L", lo_i, float(l[lo_i])))
                    d = 1
                else:
                    piv.append(("H", hi_i, float(h[hi_i])))
                    d = -1
        elif d == 1:
            if h[i] > h[hi_i]:
                hi_i = i
            elif h[hi_i] - l[i] >= thr:
                piv.append(("H", hi_i, float(h[hi_i])))
                d, lo_i = -1, i
        else:
            if l[i] < l[lo_i]:
                lo_i = i
            elif h[i] - l[lo_i] >= thr:
                piv.append(("L", lo_i, float(l[lo_i])))
                d, hi_i = 1, i
    return piv
 
 
def structure_elliott(htf, close):
    """Cherche : jambe de référence + retracement dans la zone 0,62-0,79.
 
    Retourne un dict (sens, S=début de jambe, E=fin de jambe, Bx=fin du
    retracement, leg, rb, rc, label...) ou None.
    """
    h, l, a = htf["high"].values, htf["low"].values, htf["atr"].values
    piv = zigzag(h, l, a, ZZ_MULT)
    if len(piv) < 3 or not a[-1] > 0:
        return None
    sizes = [abs(piv[q + 1][2] - piv[q][2]) for q in range(len(piv) - 1)]
 
    for e in (len(piv) - 2, len(piv) - 1):          # retracement déjà confirmé, ou en cours
        if e < 1:
            continue
        tE, iE, E = piv[e]
        S = piv[e - 1][2]
        sens = 1 if tE == "H" else -1
        leg = sens * (E - S)
        if leg < MIN_LEG_ATR * a[-1]:
            continue
        if iE + 1 >= len(h):
            continue
        if e == len(piv) - 1:
            Bx = float(l[iE + 1:].min()) if sens == 1 else float(h[iE + 1:].max())
        else:
            Bx = piv[e + 1][2]
        depasse = (h[iE + 1:].max() > E) if sens == 1 else (l[iE + 1:].min() < E)
        if depasse:                                  # la jambe a été prolongée : pas un retracement
            continue
        rb = sens * (E - Bx) / leg                   # profondeur maximale du retracement
        rc = sens * (E - close) / leg                # profondeur actuelle
        if not (RB_MIN <= rb <= RB_MAX and RC_MIN <= rc <= rb + 0.05):
            continue
 
        # étiquette indicative : combien de jambes depuis la jambe dominante
        ref = e - 1
        debut = max(0, ref - 9)
        D = debut + int(np.argmax(sizes[debut:ref + 1]))
        m = (ref - D) + 1
        if m <= 2:
            label, v_entree, v_cible = "ABC", "B", "C"
        elif m <= 5:
            label, v_entree, v_cible = "WXY", "X", "Y"
        else:
            label, v_entree, v_cible = "WXYXZ", "X", "Z"
        return dict(sens=sens, S=S, E=E, Bx=Bx, leg=leg, rb=rb, rc=rc, atr_htf=float(a[-1]),
                    label=label, v_entree=v_entree, v_cible=v_cible,
                    sweet=abs(rb - FIB_ENTREE[1]) <= 0.04)
    return None
 
 
# ----------------------------- ICT (logique d'achat) -----------------------------
def ict_achat(df, atr15):
    """Confluences ICT côté achat. Pour les ventes, on passe le DataFrame en miroir."""
    h, l, c, o = (df[x].values for x in ("high", "low", "close", "open"))
    n = len(df)
    res = dict(sweep=None, mss=False, fvg=False, ob=False, kz=None, minuit=False, bas=None)
    if n < N_ICT + 10:
        return res
    ny = df.index.tz_convert(NY)
    dates = np.array(ny.date)
    heures = np.array(ny.hour) + np.array(ny.minute) / 60.0
    jours = sorted(set(dates))
    k0 = n - N_ICT
    k = k0 + int(np.argmin(l[k0:]))                  # point bas du retracement
    res["bas"] = float(l[k])
    sh, sl = fractals(h, l)
 
    # 1) liquidité sous le marché (PDL, bas d'Asie, swing lows)
    niveaux = []
    if len(jours) >= 2:
        veille = dates == jours[-2]
        if veille.any():
            niveaux.append(("PDL (plus bas de la veille)", float(l[veille].min())))
        asie = veille & (heures >= 20)
        if asie.any():
            niveaux.append(("bas de la session Asie", float(l[asie].min())))
    for i in [x for x in sl if x < k][-8:]:
        niveaux.append(("swing low", float(l[i])))
    for j in range(max(k - 1, 0), min(k + 2, n)):
        for nom, niv in niveaux:
            if l[j] < niv and c[j] > niv:
                res["sweep"] = nom
                break
        if res["sweep"]:
            break
 
    # 2) MSS / CHoCH : clôture au-dessus du dernier sommet précédant le point bas
    hs = [x for x in sh if x < k]
    if hs and k < n - 1:
        res["mss"] = bool((c[k + 1:] > h[hs[-1]]).any())
 
    # 3) FVG haussier retesté
    for i in range(max(2, k), n - 1):
        if l[i] > h[i - 2] and (l[i] - h[i - 2]) >= 0.1 * atr15:
            bas, haut = h[i - 2], l[i]
            if l[i + 1:].min() <= haut and c[i + 1:].min() >= bas and l[-8:].min() <= haut and c[-1] >= bas:
                res["fvg"] = True
                break
 
    # 4) Order Block haussier retesté (dernière bougie baissière avant le déplacement)
    for j in range(k + 1, n - 1):
        if c[j] - o[j] >= atr15:
            p = next((q for q in range(j - 1, k - 1, -1) if c[q] < o[q]), None)
            if p is None:
                continue
            if l[j + 1:].min() <= h[p] and c[j + 1:].min() >= l[p] and l[-8:].min() <= h[p] and c[-1] >= l[p]:
                res["ob"] = True
                break
 
    # 5) kill zone (heure de New York, à la clôture de la dernière bougie)
    hnow = (heures[-1] + 0.25) % 24
    for nom, a_, b_ in KILL_ZONES:
        if a_ <= hnow < b_:
            res["kz"] = nom
            break
 
    # 6) Midnight Open : achat sous l'ouverture de minuit NY (discount)
    jour = dates == dates[-1]
    res["minuit"] = bool(c[-1] < o[jour][0])
    return res
 
 
# ----------------------------- ANALYSE D'UN MARCHÉ -----------------------------
def analyser(nom, cfg, ltf):
    """ltf = bougies 15 min clôturées. Retourne (signal | None, raison)."""
    ltf = ltf.copy()
    ltf["atr"] = atr(ltf)
    htf = ltf.resample("1h").agg(dict(open="first", high="max", low="min",
                                      close="last", volume="sum")).dropna(subset=["close"])
    if len(htf) < 60:
        return None, "historique 1 h insuffisant"
    htf["atr"] = atr(htf)
    close, atr15 = float(ltf["close"].iloc[-1]), float(ltf["atr"].iloc[-1])
 
    ell = structure_elliott(htf, close)
    if ell is None:
        return None, "pas de retracement Fibonacci 0,62-0,79 exploitable"
    sens = ell["sens"]
 
    ict = ict_achat(ltf if sens > 0 else flip(ltf), atr15)
    if ict["bas"] is None:
        return None, "historique 15 min insuffisant"
    if abs(sens * ict["bas"] - ell["Bx"]) > 0.6 * ell["atr_htf"]:
        return None, "fin du retracement hors de la fenêtre ICT"
 
    # --- score de confluence ---
    pts, conf = 3.0, [f"Fibonacci : retracement {ell['rb']:.3f} de la jambe"]
    if ell["sweet"]:
        pts += 1.0
        conf.append("point idéal OTE 0,705")
    if ict["sweep"]:
        pts += 2.0
        conf.append(f"liquidity sweep : {ict['sweep']}")
    if ict["mss"]:
        pts += 2.0
        conf.append("MSS / CHoCH confirmé")
    if ict["fvg"]:
        pts += 1.5
        conf.append("FVG retesté")
    if ict["ob"]:
        pts += 1.5
        conf.append("Order Block retesté")
    if ict["kz"]:
        pts += 1.0
        conf.append(f"kill zone : {ict['kz']}")
    if ict["minuit"]:
        pts += 1.0
        conf.append("discount vs Midnight Open" if sens > 0 else "premium vs Midnight Open")
    ema50, ema200 = htf["close"].ewm(span=50, adjust=False).mean().iloc[-1], \
        htf["close"].ewm(span=200, adjust=False).mean().iloc[-1]
    if (ema50 > ema200) == (sens > 0) and len(htf) >= 200:
        pts += 1.0
        conf.append("biais 1 h aligné (EMA 50/200)")
 
    if not (ict["mss"] or (ict["sweep"] and (ict["fvg"] or ict["ob"]))):
        return None, "pas de déclencheur ICT (MSS, ou sweep + FVG/OB)"
    if pts < SCORE_MIN:
        return None, f"score insuffisant ({pts:.1f})"
 
    # --- niveaux ---
    buf = STOP_BUFFER_ATR * ell["atr_htf"]
    stop = (ell["S"] - sens * buf) if STOP_MODE != "ict" else (ell["Bx"] - sens * buf)
    risque = sens * (close - stop)
    if risque <= 0:
        return None, "prix déjà au-delà du stop"
    tps = [ell["Bx"] + sens * k * ell["leg"] for k in FIB_TP]
    rr = [sens * (tp - close) / risque for tp in tps]
    if rr[0] < MIN_RR:
        return None, f"ratio gain/risque trop faible ({rr[0]:.2f})"
    if risque < 3 * cfg["cost"] * close:
        return None, "stop trop serré par rapport aux coûts"
 
    # --- taille de position ---
    conv = 1.0 if cfg["conv"] == "usd" else 1.0 / close
    perte_lot = risque * cfg["contract"] * conv
    risque_pct = RISQUE_PAR_TRADE * (0.6 + 0.4 * min(pts / 10.0, 1.0))
    lots = CAPITAL * risque_pct / perte_lot
    notionnel = cfg["contract"] * (1.0 if cfg["base_usd"] else close)
    lots = min(lots, CAPITAL * MARGE_MAX_PCT / (notionnel / LEVERAGE))
    lots = math.floor(lots / LOT_PAS + 1e-9) * LOT_PAS
    if lots < LOT_MIN:
        return None, "capital insuffisant pour le lot minimum avec ces limites"
    lots = round(lots, 2)
 
    return dict(
        nom=nom, sens=sens, prix=close, stop=stop, tps=tps, rr=rr, risque=risque,
        pips=risque / cfg["pip"], lots=lots, risque_usd=lots * perte_lot, score=pts, conf=conf,
        ell=ell, signature=(sens, round(ell["S"], 5), round(ell["E"], 5)),
    ), "OK"
 
 
# ----------------------------- DONNÉES -----------------------------
def telecharger(cfg):
    import yfinance as yf
    df = yf.download(cfg["ticker"], period=PERIODE, interval=INTERVALLE,
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df
 
 
def donnees_demo(i):
    """Marché synthétique : jambe, retracement ~70 %, chasse de liquidité, rebond.
    Les marchés impairs sont en miroir (scénario de vente)."""
    rng = np.random.default_rng(100 + i)
    segments = [(400, 0.0), (100, 0.012), (50, -0.0085), (6, -0.0006), (22, 0.0025)]
    deriv = np.concatenate([np.full(n, tot / n) for n, tot in segments])
    rets = deriv + rng.normal(0, 0.00025, len(deriv))
    close = 1.10 + np.cumsum(rets)
    if i % 2 == 1:
        close = 2 * 1.10 - close
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.00012, len(close)))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.00012, len(close)))
    fin = pd.Timestamp.now(tz="UTC").floor("15min")
    idx = pd.date_range(end=fin, periods=len(close), freq="15min")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.integers(100, 1000, len(close))}, index=idx)
 
 
# ----------------------------- TELEGRAM -----------------------------
def formater(s):
    e = s["ell"]
    sens = "🟢 ACHAT" if s["sens"] > 0 else "🔴 VENTE"
    dec = 2 if s["prix"] > 50 else 5
    tp = "\n".join(f"TP{k + 1} (ext. {FIB_TP[k]}) : {s['tps'][k]:.{dec}f}  (RR {s['rr'][k]:.1f})"
                   for k in range(3))
    return (
        f"{sens} {s['nom']}  |  Score {s['score']:.1f}\n"
        f"Elliott : vague {e['v_entree']} de {e['label']} → cible vague {e['v_cible']}\n"
        f"Entrée ≈ {s['prix']:.{dec}f}\n"
        f"Stop loss : {s['stop']:.{dec}f} (≈ {s['pips']:.0f} pips)\n"
        f"{tp}\n"
        f"Lot conseillé : {s['lots']:.2f} (risque ≈ {s['risque_usd']:.2f} $)\n"
        f"Sorties : 50 % TP1 (puis stop à l'entrée), 30 % TP2, 20 % TP3\n"
        f"Confluences : " + " ; ".join(s["conf"]) + "\n"
        f"⚠️ Prix indicatif (Yahoo) : vérifiez le prix de votre courtier et gardez "
        f"les mêmes distances pour le stop et les objectifs."
    )
 
 
def envoyer_telegram(texte):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("Telegram non configuré (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID manquants).")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": texte}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20)
        return True
    except Exception as e:
        print(f"Erreur Telegram : {e}")
        return False
 
 
# ----------------------------- PROGRAMME -----------------------------
def main():
    demo, force = "--demo" in sys.argv, "--force" in sys.argv
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC] V2 Elliott+ICT | {len(INSTRUMENTS)} marchés "
          f"| capital={CAPITAL:.0f} $ | {'DÉMO' if demo else 'réel'}")
    if force:
        envoyer_telegram("✅ Test : le bot V2 (Elliott + ICT) est bien connecté à Telegram.")
    if not demo and WEEKEND_OFF and datetime.now(NY).weekday() >= 5:
        print("Week-end : pas de scan.")
        return
 
    candidats = []
    for i, (nom, cfg) in enumerate(INSTRUMENTS.items()):
        try:
            df = donnees_demo(i) if demo else telecharger(cfg)
            if df is None or len(df) < 300:
                print(f"{nom}: données insuffisantes")
                continue
            if not demo:
                df = df.iloc[:-1]                                   # bougie clôturée uniquement
                age = (datetime.now(timezone.utc) - df.index[-1].tz_convert("UTC")).total_seconds() / 60
                if age > FRAICHEUR_MAX_MIN + 15:
                    print(f"{nom}: marché fermé ou données trop anciennes ({age:.0f} min)")
                    continue
            sig, raison = analyser(nom, cfg, df)
            if sig is None:
                print(f"{nom}: pas de trade ({raison})")
                continue
            if not demo:                                             # anti-doublon sans mémoire
                prec, _ = analyser(nom, cfg, df.iloc[:-1])
                if prec is not None and prec["signature"] == sig["signature"]:
                    print(f"{nom}: signal déjà envoyé précédemment")
                    continue
            candidats.append((sig, cfg))
        except Exception as e:
            print(f"{nom}: erreur ({e})")
 
    # --- filtres de portefeuille : score, risque total, exposition USD ---
    candidats.sort(key=lambda x: -x[0]["score"])
    cote_usd, risque_total, envoyes = {1: 0, -1: 0}, 0.0, 0
    for sig, cfg in candidats:
        usd = sig["sens"] if cfg["base_usd"] else -sig["sens"]
        if envoyes >= MAX_SIGNAUX:
            print(f"{sig['nom']}: ignoré (maximum de {MAX_SIGNAUX} signaux par scan)")
        elif cote_usd[usd] >= MAX_PAR_COTE_USD:
            print(f"{sig['nom']}: ignoré (trop d'exposition au dollar dans le même sens)")
        elif risque_total + sig["risque_usd"] > RISQUE_TOTAL_MAX * CAPITAL:
            print(f"{sig['nom']}: ignoré (risque total du scan dépassé)")
        else:
            msg = formater(sig)
            print("\n" + msg + "\n")
            envoyer_telegram(msg)
            cote_usd[usd] += 1
            risque_total += sig["risque_usd"]
            envoyes += 1
    print(f"Terminé : {envoyes} signal(s).")
 
 
if __name__ == "__main__":
    main()
 
