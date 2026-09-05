# Trinetra — Fraud Fund Flow Tracer

**Real-Time Identification of Fraud-Linked Cryptocurrency Exchanges from Victim-Reported Wallet Addresses**

Smart India Hackathon 2026 | Problem Statement 26183 | Team 38, BPIT (GGSIPU)

---

## 1. What This Project Does (In Plain English)

When someone gets scammed and sends cryptocurrency (USDT) to a fraudster's wallet,
the money doesn't stay in one place. It gets moved through several other wallets
("mule wallets") within minutes, specifically to make it hard to trace — before
finally landing in a real cryptocurrency exchange, where it gets converted to cash.

**Trinetra automates the process of following that money.** You give it one
wallet address (the one the victim reported), and it:

1. Follows the money forward, hop by hop, through every wallet it passed through
2. Figures out — using behavior, not a lookup table — which of those wallets look
   like mules, which look like mixers (money-laundering services), and which one
   is the final exchange
3. Shows you the whole journey as an interactive graph
4. Gives you a confidence score for each guess, instead of pretending to be certain
5. Generates a ready-to-send "freeze notice" PDF for law enforcement / the exchange

**What Trinetra does NOT do:** it doesn't reveal anyone's real name. Blockchain
wallets have no identity attached to them by design. Only the very last hop — a
regulated exchange — has real KYC identity behind it, and even that identity is
only accessible to the exchange itself or law enforcement with a subpoena.
Trinetra's job ends at generating the lead; it does not do the un-masking itself.

---

## 2. Why This Is Hard (The Core Problem)

There is no public database anywhere that says "this wallet address belongs to
Binance" or "this wallet address is a known scammer." Everything has to be
figured out from **behavior alone** — how wallets receive and forward money.

This is exactly the same challenge that companies like Chainalysis solve
commercially (at a price no student team can afford), so Trinetra rebuilds a
simplified, transparent version of the same idea:

- A wallet that receives money from **many different people** but forwards it
  to **only one place** is probably a deposit address for an exchange.
- A wallet that receives from many people **and** sends to many people, with no
  single dominant destination, is probably a mixer (a money-laundering service).
- A wallet that forwards 95% of what it received to one place, while skimming a
  small percentage elsewhere, is doing a "peel chain" — a classic laundering trick.

---

## 3. The Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.11+ | One language for the whole team, no context-switching |
| Backend | FastAPI (planned) | Simple, auto-documented API |
| Dashboard | Streamlit | Full interactive UI in pure Python, no JavaScript needed |
| Blockchain data source | TronGrid API + TronScan API | Free, official TRON blockchain data |
| Graph engine | Custom Python tree structure (no database) | In-memory is enough for hackathon scale |
| Graph visualization | vis-network.js (via Streamlit HTML embed) | Interactive, glowing neon-style fund flow graph |
| ML (optional layer) | scikit-learn (Random Forest) | Explainable, fast to train, doesn't need a GPU |
| PDF generation | ReportLab | Generates the law-enforcement-style freeze notice |
| Storage | CSV / JSON files (no database server) | Zero setup cost, fully portable |

**Why TRON and USDT specifically, not Bitcoin or Ethereum?**
UNODC (United Nations) and Chainalysis both report that USDT on TRON is the
primary settlement rail for organized fraud (like pig-butchering scams) in this
region — because it has near-zero fees, 3-second block times, and stable value.
This isn't a shortcut; it's where the actual crime happens.

---

## 4. Where the Data Comes From

**Everything comes from the public TRON blockchain — nothing is invented.**

1. **TronGrid API** (`api.trongrid.io`) — the primary source. Given a wallet
   address, it returns every USDT (TRC-20 token) transaction that address has
   ever sent or received: amount, timestamp, and a unique transaction hash.
2. **TronScan API** (`apilist.tronscanapi.com`) — used to check whether a
   specific address has a known public label (e.g., "this is a Binance hot
   wallet"). This is the only source of confirmed, ground-truth labels.
3. **Chainabuse** (run by TRM Labs, not Chainalysis) — a free, community-reported
   database of addresses associated with scams.

None of these APIs give you a "download everything" button. You always start
from one address, and the system discovers the next addresses to check on its
own, based on where that address sent money.

---

## 5. How the Data Is Stored

```
trinetra/
├── data/
│   ├── raw/
│   │   ├── trongrid_transactions/     ← one JSON file per address (untouched, for audit)
│   │   └── all_transactions.csv        ← every transaction, flattened into one table
│   ├── processed/
│   │   ├── address_features.csv        ← computed stats per address (see Section 7)
│   │   └── trace_report.json           ← final structured report per case
│   ├── labels/
│   │   └── address_labels.csv          ← known + heuristic-guessed labels per address
│   └── external/
│       └── known_exchanges.csv         ← confirmed exchange/scam labels from TronScan/Chainabuse
```

The rule followed throughout: **raw data is never modified.** Every step reads
from one stage and writes a new file to the next stage, so you can always trace
back exactly where a number came from.

---

## 6. How the Tracing Algorithm Works (Step by Step)

### Step 1 — Start from one address
You give Trinetra a single wallet address (the one a victim reported).

### Step 2 — Fetch and clean the data
Raw blockchain data has two quirks that will silently break your math if you
don't handle them:
- USDT amounts on TRON use **6 decimal places**, not the raw integer the API
  returns (so `1000000` actually means `1.00` USDT)
- Timestamps come back in **milliseconds**, not seconds

### Step 3 — Follow the money forward, branch by branch
Real fraud doesn't move in a straight line — one wallet often splits money into
several directions ("fan-out"). So instead of following just one path, Trinetra
builds a **tree**: every wallet that received a meaningful share of the money
becomes a new branch to explore.

At every branch, the system logs one of four outcomes, and — critically — **why**:

| Status | Meaning |
|---|---|
| `followed` | This branch carried enough value to be worth tracing further |
| `pruned` | This branch carried too little value (below 5% of the parent's total), or the compute budget ran out — logged with the exact reason, never silently dropped |
| `terminus` | This wallet matches the behavioral pattern of an exchange deposit address — tracing stops here |
| `dead_end` | No further transactions found, or the wallet shows mixer-like behavior — tracing stops here |

### Step 4 — Classify each wallet using two behavioral heuristics

**The Deposit-Address Heuristic** (how an exchange wallet is recognized):
A real exchange gives each customer their own personal deposit address, and
periodically "sweeps" all of them into one central hot wallet. This creates an
unavoidable fingerprint: **many-in, one-out.** The algorithm checks two things:
- **alpha:** does the amount going out match the amount that came in almost
  exactly? (For USDT, this should be an exact match, since network fees are
  paid in a separate token, not deducted from the transfer itself.)
- **tau:** did the forward happen within a reasonable time window (not
  years later)?

These two checks produce a single confidence score between 0 and 1 (called
**kappa**) — the system never claims 100% certainty, only a scored belief.

**The Pattern Detector** (how mixers, peel chains, and smurfing are recognized):

| Pattern | What it looks like on-chain |
|---|---|
| Mixer | Many senders AND many receivers, with no single dominant destination |
| Peel chain | One dominant transfer (>85% of value) plus one small "peeled" amount |
| Smurfing | Many small, nearly identical amounts sent out at once |
| Fan-out / layering | Money split across several wallets in similar proportions |

### Step 5 — Summarize the whole case
Once the tree is fully built, the system answers the two questions that matter
most to an investigator:
- **How much of the money actually reached a confirmed exchange?** (a percentage)
- **How much is stuck at a dead end (like a mixer), or still mid-chain?**

### Step 6 — Generate the evidence report and freeze notice
The final output is a structured report containing every hop's transaction hash
(so anyone can independently re-verify it on the public blockchain), the
confidence scores, and — for every high-confidence exchange found — a
ready-to-send PDF preservation/freeze notice.

**Important honesty principle carried through the whole system:** a deposit
address identifies an *account*, not a *person*. Final identification of a real
human always requires the exchange's own KYC records or a law enforcement
subpoena — Trinetra generates the lead, not the accusation.

---

## 7. The Optional Machine Learning Layer

Because there is no public dataset anywhere that labels wallets as "exchange"
or "mule," a normal supervised ML model has nothing to learn from out of the
box. Trinetra solves this using a technique called **weak supervision**:

1. The two heuristics above (deposit-address detector, pattern detector) run
   first and produce their own labels.
2. Those labels — plus a small number of externally *confirmed* labels from
   TronScan/Chainabuse — become the training data for a Random Forest
   classifier.
3. The ML model is never allowed to override the heuristic. If they agree, the
   combined confidence goes up. If they disagree, the heuristic's answer is
   kept, and the disagreement is flagged for manual review instead of being
   silently resolved.

**Features the ML model looks at, per address:**
`in_degree`, `out_degree`, `total_in_value`, `total_out_value`,
`avg_forward_delay_sec`, `forward_ratio`, `dominant_destination_share`,
`deposit_heuristic_score`, `mixer_pattern_score`, `address_age_days`.

---

## 8. Full Pipeline — How to Run Everything, In Order

```powershell
# 1. Fetch blockchain data starting from a victim-reported address
python -m src.ingestion.fetch_trongrid

# 2. Compute behavioral features for every address discovered
python -m src.features.feature_engineering

# 3. Pull known exchange/scam labels from TronScan and Chainabuse
python -m src.ingestion.fetch_labels

# 4. Assign a label to every address (known label, or heuristic guess)
python -m src.labeling.generate_weak_labels

# 5. (Optional) Train the ML model on the labeled data
python -m src.model.train

# 6. Run the full multi-branch trace as a tree
python -m src.tracing.tree_trace

# 7. Generate the structured JSON evidence report
python -m src.reporting.generate_report

# 8. Generate the PDF freeze notice(s)
python -m src.reporting.freeze_notice

# 9. Launch the interactive dashboard (does steps 1-8 live, from the browser)
streamlit run src/app/dashboard.py
```

---

## 9. Project Folder Structure

```
trinetra/
├── .env                              ← API keys (never committed to Git)
├── data/                             ← see Section 5
├── models/
│   └── deposit_classifier_v1.pkl     ← saved trained ML model
├── src/
│   ├── ingestion/
│   │   ├── fetch_trongrid.py         ← pulls transaction data from TronGrid
│   │   └── fetch_labels.py           ← pulls known labels from TronScan/Chainabuse
│   ├── heuristics/
│   │   ├── deposit_heuristic.py      ← the alpha/tau/kappa exchange-detection logic
│   │   └── pattern_detection.py      ← mixer/peel-chain/smurfing detection
│   ├── features/
│   │   └── feature_engineering.py    ← turns raw transactions into per-address stats
│   ├── labeling/
│   │   └── generate_weak_labels.py   ← assigns a label to every address
│   ├── model/
│   │   ├── train.py                  ← trains the Random Forest classifier
│   │   ├── evaluate.py               ← re-evaluates a saved model
│   │   ├── predict.py                ← runs the trained model on a new address
│   │   └── hybrid_classify.py        ← combines heuristic + ML into one final answer
│   ├── tracing/
│   │   ├── tree_trace.py             ← the core multi-branch tracing algorithm
│   │   ├── visualise_tree.py         ← full interactive graph (shows every branch)
│   │   └── visualise_neon_graph.py   ← clean, styled graph (Victim/Suspect/Relay/Exchange/Mixer)
│   ├── reporting/
│   │   ├── generate_report.py        ← builds the structured JSON case report
│   │   └── freeze_notice.py          ← generates the PDF freeze notice
│   └── app/
│       └── dashboard.py              ← the full Streamlit web dashboard
└── requirements.txt
```

---

## 10. Key Design Decisions (and Why)

| Decision | Reasoning |
|---|---|
| TRON/USDT only for v1 | This is empirically where this category of fraud settles, per UNODC and Chainalysis research — not a shortcut |
| Behavioral fingerprinting instead of a lookup database | No public database of exchange addresses exists anywhere, for anyone |
| Every confidence score is explicit, never hidden | Overclaiming certainty is worse than admitting a limitation |
| Pruned branches are logged with a reason, never silently dropped | An investigator should be able to see exactly what was and wasn't checked, and why |
| ML never overrides the heuristic | The heuristic is deterministic and explainable; ML can corroborate it but disagreements go to human review |
| The report never names a person, only an address | Real identity attribution requires a subpoena or exchange KYC — this tool generates leads, not accusations |

---

## 11. Known Limitations (Stated Honestly)

- **Cross-chain movement is not tracked.** If funds are bridged from TRON to
  another blockchain, there is no on-chain proof linking the two sides — this
  is a known limitation even for industry-leading commercial tools.
- **Mixers are detected, not de-anonymized.** The system flags mixer-like
  behavior as a trace boundary rather than attempting an unreliable "guess" at
  where the money went afterward.
- **Recall is expected to be moderate, not perfect.** Even Chainalysis, under
  independent peer review, shows real-world accuracy ranging from about 25% to
  95% depending on the case — incomplete coverage is the honest state of the
  art in this field, not a flaw unique to this project.

---

## 12. Setup Instructions

```powershell
# 1. Install dependencies
pip install requests pandas python-dotenv numpy scikit-learn joblib reportlab streamlit plotly pyvis

# 2. Create a .env file in the project root with:
TRONGRID_API_KEY=your_key_here
TRONSCAN_API_KEY=your_key_here          # optional
CHAINABUSE_API_KEY=your_key_here        # optional

# 3. Get your free TronGrid API key at:
https://www.trongrid.io/dashboard/keys

# 4. Run any module using the -m flag from the project root, e.g.:
python -m src.ingestion.fetch_trongrid

# 5. Launch the dashboard:
streamlit run src/app/dashboard.py
```