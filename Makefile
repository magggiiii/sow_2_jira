.PHONY: help venv install clean run ui ui-dev ui-build verify

# Default python command to use inside the venv
PYTHON = venv/bin/python
PIP = venv/bin/pip
UVICORN = venv/bin/uvicorn

help:
	@echo "SOW-to-Jira Automation Makefile"
	@echo "-------------------------------"
	@echo "make venv    - Create a Python virtual environment"
	@echo "make install - Install all dependencies into the virtual environment"
	@echo "make run     - Run the pipeline extraction (main.py)"
	@echo "make ui      - Launch the FastAPI review UI"
	@echo "make clean   - Remove the virtual environment and cached data/logs"
	@echo "make verify  - Run a quick import check to ensure dependencies are installed"

venv:
	python3 -m venv venv
	@echo "Virtual environment created at ./venv"
	@echo "Run 'source venv/bin/activate' to activate it in your shell."

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	npm install
	@echo "Dependencies installed successfully (Python venv + Node/Vite frontend)."

run:
	$(PYTHON) main.py

ui: ui-build
	$(UVICORN) ui.server:app --reload --port 8000

ui-build:
	npm run build

# HMR dev: Vite (:5173, proxies /api -> :8000) + the API server. Open :5173.
ui-dev:
	$(UVICORN) ui.server:app --reload --port 8000 & npm run dev

verify:
	$(PYTHON) -c "import opendataloader_pdf, fastapi, uvicorn, jira, pageindex, sentence_transformers, pydantic, openai; print('All imports successful!')"

clean:
	rm -rf venv
	rm -rf data/*.db
	rm -rf data/*.json
	rm -rf data/parser_output
	rm -rf __pycache__
	rm -rf */__pycache__
	rm -rf */*/__pycache__
	@echo "Cleaned up virtual environment and runtime data files."