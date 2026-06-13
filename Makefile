.PHONY: help run start-voicevox stop-voicevox status start stop sleep logs start-sd stop-sd

HERMES_HOME_PATH := /home/reppu/workspace/ai_vtuber2/.hermes

help:
	@echo "=== Ruri AI VTuber 2 Execution Manager ==="
	@echo "Available commands:"
	@echo "  make start          - Start VOICEVOX or Style-Bert-VITS2 (based on config) and launch Ruri"
	@echo "  make stop           - Stop Ruri backend and speech engines (VOICEVOX, Style-Bert-VITS2)"
	@echo "  make run            - Run Ruri FastAPI Server directly"
	@echo "  make start-voicevox - Run VOICEVOX Engine in Docker (background)"
	@echo "  make stop-voicevox  - Stop VOICEVOX Engine Docker container"
	@echo "  make start-sd       - Start Stable Diffusion Forge (background)"
	@echo "  make stop-sd        - Stop Stable Diffusion Forge"
	@echo "  make status         - Check status of VOICEVOX and Ollama services"
	@echo "  make sleep          - Run memory consolidation (sleep) routine for Ruri"
	@echo "  make logs           - Show and follow Ruri backend logs"

run:
	@echo "Starting Ruri FastAPI Backend..."
	@HERMES_HOME=$(HERMES_HOME_PATH) .venv/bin/python3 vtuber_server.py

sleep:
	@echo "Starting Ruri Memory Consolidation..."
	@.venv/bin/python3 ruri_sleep.py

start-sd:
	@PID=$$(lsof -t -i:7860 2>/dev/null || true); \
	if [ -n "$$PID" ]; then \
		echo "Stable Diffusion Forge is already running on port 7860 (PID: $$PID)."; \
	else \
		echo "Starting Stable Diffusion Forge in background..."; \
		cd /home/reppu/stable-diffusion-webui-forge && ./start_forge.sh >sd.log 2>&1 & \
		echo "Launched! You can watch logs using 'tail -f /home/reppu/stable-diffusion-webui-forge/sd.log'."; \
	fi

stop-sd:
	@PID=$$(lsof -t -i:7860 2>/dev/null || true); \
	if [ -n "$$PID" ]; then \
		echo "Stopping Stable Diffusion Forge (PID: $$PID)..."; \
		kill -9 $$PID 2>/dev/null || true; \
		sleep 1; \
	else \
		PID2=$$(pgrep -f "[w]ebui.py" || true); \
		if [ -n "$$PID2" ]; then \
			echo "Stopping Stable Diffusion Forge zombie process (PID: $$PID2)..."; \
			kill -9 $$PID2 2>/dev/null || true; \
		else \
			echo "Stable Diffusion Forge is not running."; \
		fi \
	fi

start-voicevox:
	@if docker ps -a --format '{{.Names}}' | grep -Eq "^voicevox-engine$$"; then \
		echo "VOICEVOX container already exists. Starting it..."; \
		docker start voicevox-engine; \
	else \
		echo "Creating and running new VOICEVOX container..."; \
		docker run -d --name voicevox-engine -p 127.0.0.1:50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest; \
	fi

stop-voicevox:
	@echo "Stopping VOICEVOX container..."
	@docker stop voicevox-engine || true

status:
	@echo "=== Service Status ==="
	@echo -n "VOICEVOX Engine (Port 50021): "
	@curl -s --connect-timeout 2 http://localhost:50021/version >/dev/null && echo "Running" || echo "Stopped"
	@echo -n "Ollama (Port 11434):          "
	@curl -s --connect-timeout 2 http://localhost:11434/api/tags >/dev/null && echo "Running" || echo "Stopped"
	@echo -n "Docker Container Status:      "
	@docker ps -a -f name=voicevox-engine --format "{{.Status}}" || echo "Not Found"
start:
	@PID=$$(lsof -t -i:8000 2>/dev/null || pgrep -f "vtuber_server.py" | grep -v "$$$$" || true); \
	if [ -n "$$PID" ]; then \
		echo "Stopping existing Ruri backend process (PID: $$PID)..."; \
		kill -9 $$PID 2>/dev/null || true; \
		sleep 1; \
	fi;
	@BERT_ENABLED=$$(.venv/bin/python3 -c "import yaml; config=yaml.safe_load(open('ruri_config.yaml')); print('true' if config.get('bert_vits2', {}).get('enabled', False) else 'false')"); \
	VOICEVOX_ENABLED=$$(.venv/bin/python3 -c "import yaml; config=yaml.safe_load(open('ruri_config.yaml')); print('true' if config.get('voicevox', {}).get('enabled', False) else 'false')"); \
	if [ "$$BERT_ENABLED" = "true" ]; then \
		if ! curl -s --connect-timeout 2 http://localhost:5000/models/info >/dev/null; then \
			echo "Starting Style-Bert-VITS2 API server..."; \
			cd /home/reppu/Style-Bert-VITS2 && ./venv/bin/python3 server_fastapi.py >server.log 2>&1 & \
			echo "Waiting for Style-Bert-VITS2 to be ready..."; \
			until curl -s --connect-timeout 2 http://localhost:5000/models/info >/dev/null; do sleep 1; done; \
		fi; \
		echo "Style-Bert-VITS2 is ready!"; \
	fi; \
	if [ "$$VOICEVOX_ENABLED" = "true" ]; then \
		if ! curl -s --connect-timeout 2 http://localhost:50021/version >/dev/null; then \
			$(MAKE) start-voicevox; \
			echo "Waiting for VOICEVOX to be ready..."; \
			until curl -s --connect-timeout 2 http://localhost:50021/version >/dev/null; do sleep 1; done; \
		fi; \
		echo "VOICEVOX is ready!"; \
	fi; \
	echo "Launching Ruri FastAPI Backend in background..."
	@PYTHONUNBUFFERED=1 HERMES_HOME=$(HERMES_HOME_PATH) .venv/bin/python3 -u vtuber_server.py >server.log 2>&1 &
	@echo "Ruri backend started! You can now watch the logs using 'make logs'."

logs:
	@if [ -f server.log ]; then \
		tail -n 100 -f server.log; \
	else \
		echo "server.log not found. Please run the server first."; \
	fi

stop:
	@echo "Stopping Ruri backend and engines..."
	@PID=$$(lsof -t -i:8000 2>/dev/null || pgrep -f "vtuber_server.py" | grep -v "$$$$" || true); \
	if [ -n "$$PID" ]; then \
		echo "Stopping Ruri backend process (PID: $$PID)..."; \
		kill -9 $$PID 2>/dev/null || true; \
	else \
		echo "Ruri backend is not running."; \
	fi
	@PID_BERT=$$(lsof -t -i:5000 2>/dev/null || pgrep -f "server_fastapi.py" | grep -v "$$$$" || true); \
	if [ -n "$$PID_BERT" ]; then \
		echo "Stopping Style-Bert-VITS2 API server (PID: $$PID_BERT)..."; \
		kill -9 $$PID_BERT 2>/dev/null || true; \
	else \
		echo "Style-Bert-VITS2 is not running."; \
	fi
	@$(MAKE) stop-voicevox
