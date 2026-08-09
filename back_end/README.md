# NBFC Financial AI Suite - FastAPI REST Backend

FastAPI backend server exposing **7 AI Financial Agents** to frontend applications (React, Next.js, Vue, Angular, Mobile).

---

## 🚀 Quick Start for Fullstack Developers

### 1. Install Dependencies
```bash
pip install -r back_end/requirements.txt
```

### 2. Environment Setup
Make sure `.env` exists in the repository root (`/`) with your Groq API key:
```env
GROQ_API_KEYS=your_groq_api_key_here
GROQ_MODEL=llama-3.1-8b-instant
```

### 3. Launch Server
From the project root directory:
```bash
uvicorn back_end.main:app --reload --port 8000
```

The server will start at `http://localhost:8000`.

---

## 📑 Interactive Documentation

Once the server is running, access:
- **Swagger Interactive UI**: `http://localhost:8000/docs`
- **ReDoc Interactive UI**: `http://localhost:8000/redoc`

---

## 🔌 API Endpoints Reference

### System & Health
- `GET /api/v1/health` -> System health & list of available playbooks

### 7 Financial AI Agents
- `POST /api/v1/agents/reconciliation/run` -> 3-way Reconciliation Agent
- `POST /api/v1/agents/cashflow/run` -> 13-Week Cashflow Forecasting Agent
- `POST /api/v1/agents/cof/run` -> Cost of Funds (CoF) Optimization Agent
- `POST /api/v1/agents/expense-anomalies/run` -> Expense Anomaly Detection Agent
- `POST /api/v1/agents/npa-forecast/run` -> NPA & Provisioning Forecast Agent
- `POST /api/v1/agents/investor-report/run` -> Investor & Board Reporting Agent (Pass `{ "tone": "board_formal", "audience": "board" }`)
- `POST /api/v1/agents/fund-raising/run` -> Fund Raising Document & Data Pack Assembly Agent

### Run History & Audit Trail
- `GET /api/v1/history` -> List past runs across agents
- `GET /api/v1/history/{run_id}` -> Get detailed JSON output for a specific run ID

---

## 💡 Frontend Integration Example (Fetch / Axios)

```javascript
// Example: Triggering the Cashflow Forecasting Agent
const response = await fetch('http://localhost:8000/api/v1/agents/cashflow/run', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' }
});

const data = await response.json();
console.log('Run ID:', data.run_id);
console.log('Summary:', data.data.summary);
console.log('AI Insights:', data.data.ai_insights);
```
