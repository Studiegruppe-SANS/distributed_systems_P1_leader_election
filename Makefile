# Bully leader election — Docker Compose og Kubernetes (kind).
# Se docker/README.md for forklaring.
#
#   make build                         byg imaget bully-app:latest
#   make compose-up ALGO=improved N=5  start N noder med Docker Compose
#   make compose-down
#   make test                          systemtest af begge algoritmer (Docker Compose)
#   make kind-up                       opret kind-klyngen
#   make kind-load                     byg + indlaes imaget i kind (efter hver kodeaendring)
#   make deploy ALGO=classic N=5       (gen)start poddene i Kubernetes
#   make status                        vis tilstand og beskedtal
#   make experiments                   fuld sammenligning -> results/sweep-*.csv
#   make plots                         grafer fra seneste sweep -> results/plots/
#   make kind-down                     slet klyngen

ALGO    ?= classic
N       ?= 5
RUNS    ?= 1
NS      ?= 5 10 15 20
CLUSTER ?= bully
IMAGE   ?= bully-app:latest
PYTHON  ?= python3

.PHONY: build compose-up compose-down test kind-up kind-load deploy status experiments plots kind-down

build:
	docker build -t $(IMAGE) docker/

# --- Docker Compose ----------------------------------------------------
compose-up:
	cd docker && $(PYTHON) gen_compose.py --n $(N)
	cd docker && ALGORITHM=$(ALGO) docker compose up --build -d --wait
	@echo "Noderne svarer paa localhost:8080 ... localhost:$$((8080 + $(N) - 1))"

compose-down:
	cd docker && docker compose down

test:
	$(PYTHON) -m pytest testing/ -v

# --- Kubernetes (kind) -------------------------------------------------
kind-up:
	kind create cluster --name $(CLUSTER) --config docker/k8s/kind-cluster.yaml
	kubectl apply -f docker/k8s/headless-service.yaml

kind-load: build
	kind load docker-image $(IMAGE) --name $(CLUSTER)

deploy:
	kubectl apply -f docker/k8s/headless-service.yaml
	$(PYTHON) docker/experiment.py deploy --algorithm $(ALGO) --n $(N)

status:
	$(PYTHON) docker/experiment.py status

experiments:
	$(PYTHON) docker/experiment.py sweep --runs $(RUNS) --ns $(NS)

plots:
	$(PYTHON) docker/plot_results.py

kind-down:
	kind delete cluster --name $(CLUSTER)
