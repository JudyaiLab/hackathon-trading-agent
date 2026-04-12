.PHONY: help install test status scan dry-run run reset card register show validate validate-json verify reputation clean service-install

help: ## Show available commands
	@echo "JudyAI WaveRider — AI Trading Agent"
	@echo ""
	@echo "Quick Start:"
	@echo "  make install      Install dependencies"
	@echo "  make test         Run 93 tests (59 unit + 21 integration + 13 integrity)"
	@echo "  make status       Show agent status and paper balance"
	@echo "  make scan         Run single scan (test signals)"
	@echo "  make dry-run      See signals without executing"
	@echo "  make run          Start main loop (4H scan + 5min monitor)"
	@echo "  make validate     Validation audit report (for judges)"
	@echo "  make validate-json  Audit report as JSON"
	@echo "  make verify       Verify Merkle integrity of validation artifacts"
	@echo "  make reputation   Show reputation score breakdown (zero-base formula)"
	@echo "  make card         Generate ERC-8004 Agent Card"
	@echo "  make show         Show Agent Card JSON"
	@echo "  make register     Register on Sepolia testnet"
	@echo "  make reset        Reset paper balance to \$$100,000"
	@echo "  make clean        Remove caches and logs"

install:
	pip install -r requirements.txt

test:
	python3 -m pytest tests/ -v --tb=short

status:
	python3 agent.py --status

scan:
	python3 agent.py --single-scan

dry-run:
	python3 agent.py --dry-run

run:
	python3 agent.py

reset:
	python3 agent.py --reset

card:
	python3 erc8004.py --generate-card

register:
	python3 erc8004.py --register

show:
	python3 erc8004.py --show

validate:
	python3 validate.py

validate-json:
	python3 validate.py --json

verify:
	python3 merkle.py

reputation:
	python3 calc_reputation.py

service-install: ## Install and enable systemd service
	sudo cp hackathon-agent.service /etc/systemd/system/hackathon-agent.service
	sudo systemctl daemon-reload
	sudo systemctl enable hackathon-agent.service
	@echo "Service installed. Start with: sudo systemctl start hackathon-agent"

clean:
	rm -rf __pycache__ tests/__pycache__ .pytest_cache logs/*.log
