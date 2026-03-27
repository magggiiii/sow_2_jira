# SOW-to-Jira Automation POC: Quick Start Guide

Welcome to the SOW-to-Jira Automation pipeline. This tool uses Large Language Models (LLMs) to automatically parse PDF Statements of Work (SOWs), extract actionable work items, present them for human review, and push the approved tasks directly into Jira Cloud.

This guide will walk you through the end-to-end process of running the demo.

---

## 1. Prerequisites

Before starting, ensure your environment is configured correctly. You will need a `.env` file in the root directory (you can copy `.env.example` to `.env` to get started).

The `.env` file must contain your active credentials:
- **Bifrost API Keys:** `BIFROST_BASE_URL` and `BIFROST_API_KEY` (to route LLM requests).
- **Z.ai GLM Key:** `ZAI_API_KEY` (if using the primary cloud model).
- **Jira Cloud Credentials:** `JIRA_SERVER`, `JIRA_EMAIL`, and `JIRA_API_TOKEN` (so the pipeline can create tickets).

*Note: Make sure your `data/` folder contains a sample PDF to run the test against. (e.g., `data/sample.pdf`).*

---

## 2. Step One: Run the Extraction Pipeline

The first half of the process is running the intelligent extraction pipeline. This reads the PDF, splits it into semantic chunks, extracts the tasks, merges duplicates, and looks for missing items.

Run the interactive wizard from your terminal:
```bash
python main.py
```

### The Wizard will ask you for:
1. **Path to SOW PDF:** Enter the path to your test document (e.g., `data/sample.pdf`).
2. **LLM Mode:** 
   - Choose `1` for the API (GLM-4 via Bifrost) — *Recommended for highest quality*.
   - Choose `2` for Local (Ollama) — *Requires Ollama and `qwen2.5:7b` to be running locally*.
3. **Jira Hierarchy:** 
   - `1` (Flat): Everything is created as a standalone Task.
   - `2` (Epic > Task): SOW sections become Epics, extracted items become Tasks inside those Epics.
   - `3` (Story > Sub-task): SOW sections become Stories, items become Sub-tasks.
4. **Jira Project Key:** The key for the Jira project where tickets will be created (e.g., `PROJ`).

**What happens next?**
The pipeline will run through 6 automated steps: parsing the PDF, building a document index, extracting raw tasks, managing cross-chunk task states, deduplicating with local embeddings, and running a gap-recovery pass. 

When it finishes, it saves all the data to `data/pipeline_output.json`.

---

## 3. Step Two: Review and Edit in the UI

Once the extraction is complete, you (or a Project Manager/Business Analyst) must review the LLM's output to ensure accuracy before anything is sent to Jira.

Launch the review dashboard:
```bash
python -m streamlit run ui/app.py
```
*(This will automatically open a new tab in your web browser).*

### Using the Dashboard:
- **Left Sidebar:** Shows a summary of total tasks, pending reviews, approvals, and rejections. You can also use the toggles to filter the view (e.g., hide approved tasks) or add a manual task if the LLM missed one entirely.
- **Task Cards:** The main window shows every extracted task.
  - Expand a task to edit its Title, Description, Acceptance Criteria, etc. Changes are saved automatically as you type.
  - Notice the **Source** link at the top of each card—this tells you exactly which page and section of the SOW this task was extracted from, along with the LLM's confidence score.
  - Look out for tags like `NO_ACCEPTANCE_CRITERIA` or `AMBIGUOUS_SCOPE`. These indicate items that require special human attention.
- **Approve or Reject:** Click the `✅ Approve` or `❌ Reject` buttons on the right side of each card to finalize the task.

---

## 4. Step Three: Push to Jira

Once you are satisfied with the extracted items and have Approved the ones you want to keep, scroll to the very bottom of the Streamlit dashboard.

1. Click the **"🚀 Push X Tasks to Jira"** button.
2. The UI will display a loading spinner while the `JiraClient` creates the issues via the Atlassian REST API.
3. **Results Table:** When finished, a table will appear showing the status of every push attempt. Successful pushes will include a clickable URL directly to the newly created Jira ticket!

---

## Troubleshooting & Advanced Tips

- **Re-running without re-indexing:** If you just want to test extraction tweaks without waiting for the document to be parsed and mapped again, you can pass the skip flag: `python main.py --skip-indexing` (Note: This requires a previous successful run to have cached the tree in `data/document_tree.json`).
- **Epic Duplication:** Be aware that running the pipeline multiple times and pushing to Jira in `Epic > Task` or `Story > Sub-task` mode will create *new* Epics/Stories each time. The POC does not search Jira for pre-existing Epics.
- **Audit Logs:** Every single action taken by the pipeline, the LLMs, and the Jira Client is logged locally. If something goes wrong, you can query `data/audit.db` using sqlite3 to see exactly what happened and why.