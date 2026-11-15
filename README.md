# Group 8: P1 (Leader Election) and P1 (Kubernetes)

This repository contains two projects:
1. [Leader Election](#project-1-leader-election): the original and an improved Bully algorithm.
2. [Kubernetes](#project-2-kubernetes): a fortune cookie website hosted by a leader pod elected with the Bully algorithm.

# Project 1: Leader Election

## Relevant code files for this project:
Algorithm code files:
- `distributed_systems_P1_leader_election/docker/bully_node.py`
- `distributed_systems_P1_leader_election/docker/improved_bully_node.py`
- `distributed_systems_P1_leader_election/docker/experiment.py`
- `distributed_systems_P1_leader_election/docker/app.py`

Results:
- `distributed_systems_P1_leader_election/results`

Testing:
- `distributed_systems_P1_leader_election/testing/test_bully_node.py`
- `distributed_systems_P1_leader_election/testing/test_system.py`

Report:
- `distributed_systems_P1_leader_election/project_report/P1_Leader_Election_Group8.pdf`

## Background
This project is about leader election in distributed systems. Many distributed systems require a single process/node to act as a leader of the computational tasks to be carried out. The election is conducted by a so called leader election algorithm.

## Problem formulation
The focus of the present project is to design, implement, verify, compare, and document

- the original Bully Election algorithm [1]
- and an improved version of the original Bully Election algorithm [A].

In the design phase, the algorithms should be fully or partly modeled/designed by UML.

If you have prior experience with Docker and Kubernetes, you may establish a distributed system, by creating virtual nodes by bundling the nodes’ leader election code/functionality in individual Docker containers and use Kubernetes for orchestration.

The implementation should preferably be done in Python, and the verification should cover at least unit-tests (manual or automated) and a system test (for both versions, ie. for both the original Bully Election algorithm and for your improved version).

The improved version of the Bully Election algorithm should be optimized with regard message complexity and optionally time and space complexities.

Both the original and the improved version of the Bully Election algorithm should be proven functional via concrete experiments. The experiments should be comparative and focus on objectively quantifying the differences between the two algorithms in terms of the actual leader election outcome and message complexity and optionally time and space complexities.

As an overall assessment of the Bully Election algorithms, a brief comparison with state of the art within leader election should be conducted.

## Notes
[A] You can find already existing material regarding both the original Bully Election algorithm and improvements on the internet. You are welcome to use this material as long as you do proper referencing.

## References
[1] Hector Garcia-Molina. “Elections in a Distributed Computing System”. In: IEEE Transactions on Computers C-31.1 (1982), pp. 48–59 :)

# Project 2: Kubernetes

## Relevant code files for this project:
Website and pod code files:
- `distributed_systems_P1_leader_election/docker/app.py`
- `distributed_systems_P1_leader_election/docker/fortunes.txt`
- `distributed_systems_P1_leader_election/docker/static/`
- `distributed_systems_P1_leader_election/docker/Dockerfile`

Kubernetes files:
- `distributed_systems_P1_leader_election/docker/k8s/kind-cluster.yaml`
- `distributed_systems_P1_leader_election/docker/k8s/statefulset.yaml`
- `distributed_systems_P1_leader_election/docker/k8s/headless-service.yaml`
- `distributed_systems_P1_leader_election/docker/k8s/web-service.yaml`

Experiments:
- `distributed_systems_P1_leader_election/docker/loadtest.py`
- `distributed_systems_P1_leader_election/docker/failover_test.py`

Results:
- `distributed_systems_P1_leader_election/results`

Report:
- `distributed_systems_P1_leader_election/project_report/P2_Kubernetes/main.tex`

## Background
Much to people's surprise, the fortune cookie is not a Chinese invention, but an American one. Its exact origin is unclear, however, it most likely originated from cookies made by Japanese immigrants to the United States in the late 19th or early 20th century. It is a crisp and sugary cookie wafer with a piece of paper inside, a "fortune", usually an aphorism, or a vague prophecy. It is often served as a dessert in Chinese restaurants in Western countries.

## Problem formulation
In this project you will get a chance to offer people an infinite amount of fortune cookies by just clicking a button. You will use Kubernetes to host a website that can return a fortune cookie upon request. You can store these cookies in memory, in a file or fetch them from the Internet. The website can be a simple HTML page, some CSS styling and a bit of JavaScript. You must also design, implement and use the Bully algorithm to elect a leader pod, that would host the website. Should the leader pod fail, a new must be elected via the algorithm. You are free to design your solution in Kubernetes however you want, but it should at least contain:

- A deployment specification that supports multiple pods.
- An active Bully algorithm that elects a new leader when the current fails.
- A website with some visualization of the cookie text and a way to get more.

Your report should explain your design decisions and how you have implemented your solution. You must also make the following experiments:

- Run a load test towards your Kubernetes cluster and note down the response times.
- Simulate pod failure by shutting down the leader pod and document how the leader election algorithm now elects a new leader. How long does it take to find a new leader? Do the users notice any lag?

## References
[1] Starter code: https://github.com/thecml/bully-in-kubernetes


## Running the simulation
The nodes run in Docker containers, orchestrated with Kubernetes (kind) or Docker Compose. All commands are listed at the top of the `Makefile`.

Fortune cookie website (Project 2):
```
make kind-up
make kind-load
make deploy ALGO=improved N=5
make web-deploy          # the website runs on http://localhost:30080
make loadtest            # load test -> results/loadtest-*.csv
make failover MODE=delete  # delete the leader pod -> results/failover-*.csv
```
