# 📊 Options Trading Journal

A production-ready trading journal for option sellers and multi-leg strategy traders on NSE/BSE.  
Built for Zerodha users. Deployable on Streamlit Community Cloud.

---

## ✨ Features

| Feature | Description |
|---|---|
| **PDF Parsing** | Automatically extracts fills from Zerodha contract note PDFs |
| **Strategy Reconstruction** | Clusters fills into Iron Condors, Calendars, Straddles, and 15+ other strategies |
| **Lifecycle Tracking** | Entry → Adjustments → Partial exits → Final exit |
| **Accurate P&L** | Brokerage, STT, ETC, SEBI, Stamp Duty, GST — all calculated correctly |
| **Dashboards** | Equity curve, monthly P&L, drawdown, strategy breakdown, win rate heatmap |
| **AI Coach** | Rule-based insights always on; Claude API optional for deeper coaching |
| **DB Export/Import** | One-click backup and restore (critical for Streamlit Cloud deployments) |

---

## 🚀 Quick Start (Local)

```bash
# Clone the repo
git clone https://github.com/your-username/trading-journal.git
cd trading-journal

# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## ☁️ Deploy on Streamlit Community Cloud

1. Push this repo to GitHub (make sure `trading_journal.db` is in `.gitignore` — it already is).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**.
3. Connect your GitHub repo, set **Main file path** to `app.py`.
4. Click **Deploy**.

### ⚠️ Storage on Streamlit Cloud

Streamlit Cloud has an **ephemeral filesystem** — your database resets on every redeploy.

**Workaround (built-in):**
- Use the **⬇️ Export Database** button on the home page to download your `.db` file before redeploying.
- After redeploying, use **⬆️ Import Database** to restore it.

**Permanent solution (upgrade path):**  
Replace SQLite with [Supabase](https://supabase.com) (free PostgreSQL):
1. Create a Supabase project → copy the connection string.
2. Add it to Streamlit secrets: `[connections.supabase] url = "..."`
3. Replace `sqlite3` calls in `db/database.py` with `psycopg2` / `sqlalchemy`.
   The schema and query interfaces are identical.

---

## 📂 Project Structure

```
trading-journal/
├── app.py                  # Home page + DB management
├── requirements.txt
├── config.py               # Charge rates, strategy names, colours
├── db/
│   └── database.py         # SQLite schema + all CRUD operations
├── core/
│   ├── parser.py           # Zerodha PDF parser
│   ├── clustering.py       # Strategy detection + lifecycle tracking
│   ├── pnl.py              # P&L + charge calculation
│   ├── analytics.py        # Performance metrics + behavioural detection
│   └── ai_coach.py         # Rule-based insights + Claude API
└── pages/
    ├── 1_Upload.py         # Import contract notes
    ├── 2_Trade_Journal.py  # Strategy lifecycle view
    ├── 3_Dashboard.py      # Charts and analytics
    └── 4_AI_Coach.py       # Coaching and insights
```

---

## 🧠 Strategy Detection

The clustering engine recognises 20+ strategies:

| Single Leg | Two Legs | Multi-Leg |
|---|---|---|
| Long/Short Call | Bull/Bear Call Spread | Iron Condor |
| Long/Short Put | Bull/Bear Put Spread | Iron Fly |
| | Straddle / Strangle | Butterfly |
| | Calendar / Ratio Calendar | Jade Lizard |
| | Diagonal / Ratio Spread | Custom Structure |

**How it works:**
1. Sorts all fills by timestamp.
2. Groups fills within a 10-minute window per underlying → candidate entry cluster.
3. Checks if any new fill closes an existing open leg → EXIT or ADJUSTMENT.
4. Classifies the leg structure (strike relationship, qty ratios, expiry count) → strategy type + confidence score.

---

## 💰 Charge Calculation (Zerodha, FY 2024-25)

| Component | Rate |
|---|---|
| Brokerage | ₹20 per order (flat) |
| STT | 0.0625% on sell-side premium |
| Exchange Charges (NSE) | 0.053% on premium turnover |
| Exchange Charges (BSE) | 0.05% on premium turnover |
| SEBI | ₹10 per crore (0.000001%) |
| Stamp Duty | 0.003% on buy-side turnover |
| GST | 18% on (Brokerage + ETC + SEBI) |

---

## 🤖 AI Coach Setup

1. Get an API key from [console.anthropic.com](https://console.anthropic.com).
2. Paste it into the **API Key** field on the AI Coach page sidebar.
3. The key is used only for that session and never stored to disk.

To pre-configure on Streamlit Cloud, add to your app's secrets:
```toml
# .streamlit/secrets.toml  (do NOT commit this file)
ANTHROPIC_API_KEY = "sk-ant-..."
```

Then in `pages/4_AI_Coach.py`, replace the `text_input` default with:
```python
api_key = st.text_input(...) or st.secrets.get("ANTHROPIC_API_KEY", "")
```

---

## 📋 Supported Zerodha Contract Note Formats

| Format | Example |
|---|---|
| Standard (with spaces) | `NIFTY 27 JUN 24 25000.00 CE` |
| Compact (no spaces) | `NIFTY27JUN2425000CE` |
| NSE weekly compact | `NIFTY2461225000CE` |
| Stock options | `RELIANCE 27 JUN 2024 3000.00 CE` |
| SENSEX (BSE) | `SENSEX 28 JUN 24 76000.00 CE` |

If your contract note fails to parse, open an issue with a redacted sample.

---

## 🛠 Configuration

All charge rates, cluster windows, and strategy labels are in `config.py`.  
Change `CLUSTER_WINDOW_MINUTES` (default: 10) if your multi-leg entries span a wider time window.

---

## 📄 License

MIT — use freely, contribute welcome.
