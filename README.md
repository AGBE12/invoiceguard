# InvoiceGuard API & Frontend

Application SaaS de **facturation pour indépendants et entrepreneurs** (Mali / zone UEMOA, devise FCFA).
Backend **FastAPI** (factures multi-lignes, clients, authentification JWT, paiements **Stripe carte**
+ **CinetPay Mobile Money** Orange / Wave / Moov) + génération de **PDF** (WeasyPrint / reportlab)
et une interface de test légère en HTML/JS (`index.html`).

---

## ⚠️ ATTENTION — ENVIRONNEMENT PYTHON

> Le **Python système** de ce Mac (`/usr/bin/python`) est en **version 2.7**, une version **obsolète**
> (fin de support définitive en 2020). Ce code **exige Python 3.8+** et **ne fonctionne PAS** sous Python 2.7.

Le venv du projet (`./.venv`) est basé sur **Python 3.11** et contient **toutes les dépendances** nécessaires.

### 🚫 Règle stricte

**Ne JAMAIS utiliser les commandes `python` ou `pip` globales.**

- La commande `python` renvoie vers **Python 2.7.16** → syntaxe invalide / plantages garantis.
  (Ex. `python -m py_compile ...` ou `python app.py` échoueront bêtement.)
- La commande `pip` globale peut polluer le Python du système et ne pointe pas vers nos dépendances.

Toujours utiliser **le venv local** :

```bash
./.venv/bin/python ...            # utilisation directe (sans activation)
```

…ou **activer** l'environnement virtuel au préalable :

```bash
source .venv/bin/activate         # puis `python` = .venv Python 3.11
```

---

## 🚀 Lancement en local (commandes exactes)

### 1️⃣ Activer le venv

```bash
source .venv/bin/activate
```

Après activation, les commandes `python` / `pip` / `uvicorn` ci-dessous pointent bien vers le projet.

### 2️⃣ Lancer le backend (FastAPI / Uvicorn)

```bash
uvicorn app.main:app --reload --port 8000
```

Documentation interactive (Swagger) disponible sur : <http://localhost:8000/docs>

### 3️⃣ Lancer l'interface frontend (serveur statique)

```bash
python3 -m http.server 5500
```

Interface de test accessible sur : <http://localhost:5500>

> **Remarque :** `python3` renvoie au Python 3.8 du système, ce qui est **sans danger ici** : cette
> commande ne fait que servir des fichiers statiques et **n'exécute aucun code backend dépendant**.
> La règle stricte « ne pas utiliser `python` / `pip` » concerne le code du projet et les dépendances.
> En cas de doute, privilégiez le serveur statique depuis le moteur Python du venv :
> `./.venv/bin/python -m http.server 5500`.

---

## 🧪 Vérification rapide

Commandes à exécuter dans le venv (compilation sans exécution) :

```bash
./.venv/bin/python -m py_compile app/main.py app/routers/*.py app/services/*.py
```

> **Astuce :** si `python -m py_compile` (sans venv) affiche `SyntaxError` sur de simples annotations de
> type, c'est le symptôme classique du **Python 2.7 système** — activez le venv et recommencez.

---

## 📁 Structure clé

```
app/
  main.py                 # point d'entrée FastAPI (enregistre auth, clients, invoices, billing, stripe)
  config.py               # configuration (.env) — Stripe, CinetPay, SMTP…
  models/  schemas/       # SQLAlchemy + Pydantic (Invoice, InvoiceItem, Client, User…)
  routers/                # auth.py, clients.py, invoices.py, billing.py, stripe.py
  services/               # pdf_generator, stripe_service, mobile_money_service
  templates/              # invoice_template.html (rendu PDF WeasyPrint)
index.html                # interface web de test
requirements.txt          # dépendances Python (installées dans .venv)
```

## ⚙️ Configuration requise

Copiez `.env.example` vers `.env` puis renseignez (au minimum pour les paiements) :
`STRIPE_SECRET_KEY`, `CINETPAY_API_KEY`, `CINETPAY_SITE_ID`, `BACKEND_URL`…

Étant donné la **règle stricte** ci-dessus, installez/mettez à jour les dépendances **uniquement** via :
```bash
source .venv/bin/activate
pip install -r requirements.txt
```
