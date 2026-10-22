PY ?= .venv/bin/python
CHATSHERPA ?= .venv/bin/chatsherpa
PROVIDER ?= chatgpt
CDP ?= http://localhost:9222
PROMPT ?= Reply with exactly: ok
SERVER ?= root@95.182.94.71
SSH_PORT ?= 2112
SSH_PROXY ?= nc -X 5 -x 127.0.0.1:10808 %h %p

.PHONY: help setup browsers test check providers doctor serve login grab ask sessions deploy clean

help:
	@echo "make setup                 create .venv and install everything (dev, server, crypto, telegram)"
	@echo "make browsers              download Chromium for Playwright (needed for ask, test, login)"
	@echo "make test                  run the unit tests (no browser, no network)"
	@echo "make check                 test + compile check"
	@echo "make doctor                environment diagnostics"
	@echo "make providers             list providers"
	@echo "make login PROVIDER=chatgpt       headful login, saves a session"
	@echo "make grab PROVIDER=gemini CDP=http://localhost:9222   grab from your Edge/Chrome"
	@echo "make ask PROVIDER=chatgpt PROMPT='hello'              send a prompt"
	@echo "make sessions              list stored sessions"
	@echo "make serve                 run the login server locally on 127.0.0.1:8091"
	@echo "make deploy SERVER=root@95.182.94.71 SSH_PORT=2112 SSH_PROXY='nc -X 5 -x 127.0.0.1:10808 %h %p'"

setup:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev,server,crypto,telegram]"
	@if [ ! -f .env ]; then cp .env.example .env && echo "created .env from .env.example"; fi

browsers:
	$(PY) -m playwright install chromium

test:
	$(PY) -m pytest -q

check: test
	$(PY) -m compileall -q chatsherpa examples tests
	@echo "check passed"

providers:
	$(CHATSHERPA) providers

doctor:
	$(CHATSHERPA) doctor

login:
	$(CHATSHERPA) login $(PROVIDER)

grab:
	$(CHATSHERPA) grab $(PROVIDER) --cdp $(CDP) $(if $(TOKEN),--token $(TOKEN) --server $(SERVER),)

ask:
	$(CHATSHERPA) ask $(PROVIDER) "$(PROMPT)"

sessions:
	$(CHATSHERPA) sessions

serve:
	$(CHATSHERPA) serve

deploy:
	SERVER=$(SERVER) SSH_PORT=$(SSH_PORT) SSH_PROXY='$(SSH_PROXY)' ./deploy/deploy.sh $(ARGS)

clean:
	rm -rf .pytest_cache build dist *.egg-info
