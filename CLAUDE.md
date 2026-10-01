# Inventory Planner (simulated manufacturer)

## What this project is
An inventory and demand tool for a small manufacturer of magnetic inspection tools. Real company data is not available, so it runs on a SIMULATED company (src/generate_sample_data.py) modeled on a small Indian manufacturer. Never present its numbers as real company results.

I'm building it as a portfolio project for Operations Analyst, Supply Chain Analyst and Business Analyst roles. I'm learning Python, SQL and Git while I build, so teaching me matters as much as the code.

## The user
- A small manufacturer's owner. Not technical. Uses a phone or laptop.
- The tool must be simple enough to understand in 30 seconds with no explanation.

## Features
1. Reorder alerts with safety stock (demand and lead-time variation) and EOQ.
2. Sales trend and ABC analysis.
3. Demand forecast with backtest.
4. Idle and overstocked items.
5. Supplier scorecard and inventory turnover.
6. Service-level what-if simulation.
7. Real-data case study (UCI Online Retail II): cleaning, ABC-XYZ, forecast comparison, returns.

## Tech stack
- Python, pandas for cleaning and analysis
- SQLite databases (`data/inventory.db` simulated, `data/real/retail.db` real)
- SciPy for the simulation, pytest for tests
- Streamlit for the web app
- Git and GitHub for version control

## How to work with me
- Plan first. Before writing code, explain the approach in plain English and wait for my OK.
- Work in small steps. One piece at a time, then stop so I can test it.
- Explain every step. After writing code, explain what it does and why you chose that approach, in simple terms. I'm a beginner in Python and Git.
- SQL: the first version's queries were written for me. From now on, I write new queries myself; you review them, point out mistakes, and explain how to fix them. Quiz me on the existing queries in `sql/` when I ask.
- Remind me to commit to Git after each working step, with a clear commit message.
- When something breaks, find the root cause and explain it. No quick patches I don't understand.
- Show money in both ₹ and USD (use a single exchange-rate setting in one place).

## Data rules (important)
- Any real company data is confidential. It lives only in `data/`, which is never committed.
- `data/` must be in `.gitignore`. Never commit real data to GitHub.
- For the public repo, use a sample dataset in `sample_data/` with customer names and prices changed.

## Folder structure
```
inventory-planner/
├── CLAUDE.md
├── README.md
├── BUILD_LOG.md        # my notes: decisions made and why
├── data/               # real data, never committed
│   └── raw/
├── sample_data/        # anonymized data for the public repo
├── sql/                # my SQL queries
├── src/                # cleaning, loading, forecasting code
├── tests/              # pytest checks for formulas and cleaning
├── docs/               # case study and screenshots
└── app.py              # Streamlit app
```

## Later ideas (not for the first version)
- AI reading of messy purchase orders and invoices
- Weekly auto-email report to the owner
