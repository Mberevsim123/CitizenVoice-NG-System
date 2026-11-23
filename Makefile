PY ?= python3
PORT ?= 8000

.PHONY: install data train finetune seed run serve test functional all

install:            ## install runtime + test dependencies
	$(PY) -m pip install -r requirements-dev.txt

data:               ## build train / dev / test corpora
	$(PY) app/dataset.py

train:              ## baseline classical models + reports/metrics.json
	$(PY) -m app.train

finetune:           ## 30-minute fine-tuning (MINUTES=5 for a quick run)
	$(PY) -m app.finetune --minutes $(or $(MINUTES),30)

seed:               ## fresh demo database
	$(PY) -m app.seed

run:                ## start the web app (development) on http://localhost:$(PORT)
	$(PY) -m uvicorn app.main:app --port $(PORT)

test:               ## automated test suite (in-process, temporary database)
	$(PY) -m pytest -q

functional:         ## 15 functional test cases against a running server on a fresh seed
	$(PY) functional_tests.py http://localhost:$(PORT)

all: data train finetune seed test

serve:              ## start the web app the way production does (reads PORT / HOST)
	$(PY) -m app.serve
