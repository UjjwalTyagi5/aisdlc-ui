# Deployment Agentic AI (Gemini 2.5 Flash)

### 🛠 How to Run:

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set your Google API key:
```bash
export GOOGLE_API_KEY="your_gemini_api_key"
```

3. Run the agent:
```bash
python deployment_agent.py
```

4. When prompted, type:
```
Can you evaluate my_code.py using test_case.xlsx
```

### 📁 Outputs:
- Adds a column called "Deployment Evaluation" to `deployment_evaluation.xlsx` based on Gemini feedback.

---

