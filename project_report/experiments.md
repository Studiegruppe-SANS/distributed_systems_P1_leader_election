# 5 Experiments

This section compares the original and the improved Bully Algorithm through concrete experiments.
The goal is to quantify the difference between the two versions on three points: whether the
correct leader is elected, how many messages the election costs, and how long it takes. The
experiments also test whether the measured message counts match the worst-case expressions
derived in Section 2, (1) for the original version and (2) for the improved version.

## 5.1 Experimental setup

All experiments were run on the deployment shown in Figure 1: a local Kubernetes cluster created
with kind, consisting of one control-plane node and two worker nodes. Each process P_i is a pod
named bully-i running the same Docker image. The version is selected with an environment variable,
so both algorithms run on identical infrastructure, with the same node classes and the same
timeouts. Table 1 lists the parameters.

**Table 1: Parameters used in all experiments.**

| Parameter | Value |
|---|---|
| T_ok (wait for OK after ELECTION) | 3 s |
| Wait for COORDINATOR after OK | 5 s |
| Heartbeat interval / heartbeat timeout | 2 s / 5 s |
| Priority delay step Δ (improved version only) | 4 s |
| Number of processes n | 5, 10, 15, 20 |
| Repetitions per scenario, version and n | 3 (144 runs in total) |

The experiments are run from the host by the script `docker/experiment.py`, which controls the
cluster through kubectl. For every run it resets the message counters of all processes, stages the
scenario, waits until all live processes agree on a leader, and then collects the state and the
counters from every process.

To be able to crash any process, and not only the one with the highest ID, every process exposes
the test endpoints `/crash` and `/recover`. A crashed process ignores all incoming messages, sends
nothing and stops its timers, so to the other processes it is indistinguishable from a crashed
process. When it recovers, it restarts with an empty state and starts an election. This follows the
procedure Recovery in [1, pp. 55–56], where a recovering process resets its state and calls
Election. In the failover and worst-case scenarios, the coordinator's pod is
instead removed from the cluster.

## 5.2 Scenarios

Each scenario starts from a stable system where P_{n−1} is the coordinator, except the start-up
scenario, where no coordinator exists yet. Table 2 describes the six scenarios.

**Table 2: Experiment scenarios.**

| Scenario | What happens | Expected leader |
|---|---|---|
| Start-up | All n processes start at the same time | P_{n−1} |
| Failover | The coordinator crashes, and all processes detect it through the heartbeat timeout | P_{n−2} |
| Worst case | The coordinator crashes, and only the lowest process P_0 starts the election | P_{n−2} |
| Non-coordinator crash | A random process that is not the coordinator crashes | P_{n−1} (no election) |
| Two highest crash | P_{n−1} and P_{n−2} crash at the same time | P_{n−3} |
| Coordinator recovery | The coordinator crashes, a new one is elected, and the old coordinator recovers | P_{n−1} |

The worst-case scenario is the situation behind (1) and (2): the lowest process initiates the
election. The failover scenario is the more common situation, where all processes notice the
failure at roughly the same time. This is the case the priority delay of the improved version is
designed for.

## 5.3 Metrics

- **Election outcome:** after each run, all live processes must agree on the same coordinator, and
  this must be the highest live process.
- **Message count:** the sum of ELECTION, OK and COORDINATOR messages sent by all processes during
  the run. HEARTBEAT is used for failure detection and is not part of the election, so it is not
  counted. A message sent to a crashed process is counted, since it was sent.
- **Election time:** the time from the first process starting an election until the last live
  process knows the new coordinator. The time it takes to detect the failure (the heartbeat
  timeout) is not included, since it is the same for both versions.

## 5.4 Results

### 5.4.1 Election outcome

In all 144 runs, every live process agreed on the expected coordinator. This holds for both versions,
in all six scenarios and for all values of n. The improvements in Section 2.2 therefore change the
cost of an election, but not its result.

### 5.4.2 Message complexity

Figure 3 shows the number of election messages as a function of n for each scenario, and Table 3
gives the mean values.

*Figure 3: Number of election messages as a function of n (mean of 3 runs, min–max shaded). The
original version is the thick blue line, and the improved version the thin orange line. In the
worst-case panel, the dashed lines show (1) and (2).* (`results/plots/messages_vs_n.png`)

**Table 3: Mean number of election messages (original / improved).**

| Scenario | n = 5 | n = 10 | n = 15 | n = 20 | Reduction at n = 20 |
|---|---|---|---|---|---|
| Start-up | 97 / 27 | 643 / 102 | 1613 / 239 | 3009 / 406 | 87 % |
| Failover | 20 / 5 | 90 / 10 | 210 / 15 | 380 / 20 | 95 % |
| Worst case | 20 / 12 | 90 / 27 | 210 / 42 | 380 / 57 | 85 % |
| Two highest crash | 16 / 6 | 81 / 11 | 196 / 16 | 361 / 21 | 94 % |
| Non-coordinator crash | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | – |
| Coordinator recovery | 4 / 4 | 9 / 9 | 14 / 14 | 19 / 19 | 0 % |

**Worst case.** The measured counts match (1) and (2) exactly for all four values of n: the
original version sent n(n − 1) messages (20, 90, 210, 380), and the improved version 3(n − 1)
(12, 27, 42, 57). At n = 5, the original version's 20 messages split into 10 ELECTION, 6 OK and
4 COORDINATOR, the three terms of (1). The improved version's 12 messages split into 4 ELECTION,
3 OK and 5 COORDINATOR (1 nomination and 4 broadcast), the four terms of (2). The difference comes from the cascade: in the original
version, n − 1 processes started an election, while in the improved version only P_0 did.

**Failover.** This is where the priority delay has its effect. In the original version, all
processes detect the failure, and the run costs n(n − 1) messages, the same as the worst case.
In the improved version, the cost is exactly n messages (5, 10, 15, 20) and only one election is
started. The highest live process P_{n−2} has rivals = 0 and starts immediately. It sends one
ELECTION to the crashed coordinator, receives no OK, and broadcasts COORDINATOR to the other
n − 1 processes. This broadcast reaches the lower processes before their delays expire, so they
never start an election. This is the best case described in Section 2.2.

**Two highest crash.** The original version sent (n − 1)² messages (16, 81, 196, 361). The
improved version sent n + 1 (6, 11, 16, 21): P_{n−3} sends ELECTION to the two crashed processes,
receives no OK, and broadcasts COORDINATOR to the other n − 1 processes.

**Start-up.** As stated in Section 1, start-up is the only case where the improved version keeps
the quadratic complexity. At start-up there is no coordinator, so the priority delay does not
apply, and every process starts its own election. Each P_i sends ELECTION to the n − 1 − i higher
processes and receives an OK from each of them. Together with the final broadcast, this gives
n(n − 1)/2 + n(n − 1)/2 + (n − 1) = n² − 1 messages, plus a few nominations, which matches the
measurements (e.g. 399–413 at n = 20). The original version is still far more expensive (1869–3802
at n = 20), because every process that receives an ELECTION from a lower process starts yet
another election. Start-up is also the only scenario where the count varied between runs, since
it depends on the order in which the pods start.

**Where the improvements do not apply.** When a process that is not the coordinator crashes, no
election is needed, and both versions send 0 messages. When the old coordinator recovers, it is
the highest process and declares itself coordinator directly. This costs n − 1 COORDINATOR messages
in both versions. The improvements in Section 2.2 only matter when a lower process has to find
the new coordinator.

### 5.4.3 Election time

Figure 4 shows the election time as a function of n, and Table 4 gives the mean values.

*Figure 4: Election time as a function of n (mean of 3 runs, min–max shaded).*
(`results/plots/time_vs_n.png`)

**Table 4: Mean election time in seconds (original / improved).**

| Scenario | n = 5 | n = 10 | n = 15 | n = 20 |
|---|---|---|---|---|
| Start-up | 0.33 / 3.36 | 0.23 / 3.80 | 0.57 / 3.61 | 0.65 / 3.15 |
| Failover | 3.07 / 3.09 | 3.09 / 3.14 | 3.08 / 3.15 | 3.10 / 3.11 |
| Worst case | 3.09 / 3.10 | 3.14 / 3.10 | 3.10 / 3.13 | 3.13 / 3.07 |
| Two highest crash | 3.05 / 3.07 | 3.06 / 3.41 | 3.06 / 3.14 | 3.09 / 3.13 |
| Coordinator recovery | 0.00 / 0.01 | 0.03 / 0.04 | 0.01 / 0.01 | 0.10 / 0.10 |

After a crash, both versions elect a new coordinator in about 3.1 s, independent of n. The time is
dominated by T_ok = 3 s: the process that becomes coordinator must wait T_ok to be sure that no
higher process is alive. The network and the processing only add around 0.1 s. When the old
coordinator recovers, the election is almost instant in both versions, since the recovering
process is the highest and does not have to wait for anyone.

At start-up, the improved version is about 3 s slower than the original. The pods do not start at
exactly the same moment, so a process that starts late begins its election after the coordinator
has already announced itself. In the original version, the higher processes respond by starting
their own elections, and the coordinator announces itself again within milliseconds, at the cost
of many messages. In the improved version, the higher processes only answer OK, so the late
process waits T_ok before it nominates the highest responder. The spread at n = 10 (0.5–6.2 s)
reflects differences in start order between runs.

The improved version thus trades a little time for a large reduction in messages. After a crash,
the election time is the same for both versions, while at start-up it is slower by roughly T_ok.

### 5.4.4 Space complexity

Space was not measured, so we argue it analytically. Both versions store the IDs of the other
processes, which is O(n), and a constant amount of state: the current state, the coordinator ID
and the deadlines. The improved version also stores the candidate set C, which holds at most
n − 1 IDs. Both versions therefore use O(n) memory per process.

## 5.5 Limitations

- All pods run on the same physical machine. There is almost no network latency, and the worker
  nodes cannot fail independently of each other. Latency on a real network would increase the
  election time, but not the number of messages.
- Most crashes are simulated with `/crash`, where the process stops participating but keeps
  running. Only in the failover and worst-case scenarios is the pod actually removed.
- Each configuration was run 3 times. All scenarios except start-up gave identical message counts
  in every run, so for those the variation is zero.
- The improved version depends on well-chosen timeouts. Δ (4 s) must be larger than T_ok (3 s),
  so that a higher process can win and announce itself before a lower process starts its own
  election.

## 5.6 Summary

Both versions elected the correct coordinator in all 144 runs. The measured worst-case message
counts match (1) and (2) exactly, and confirm that the improved version reduces the worst case from
O(n²) to O(n). In the common failover case, the priority delay reduces the cost further, to exactly
n messages. Overall, the reduction is 85–95 % at n = 20 in all scenarios that require an election.
Start-up remains quadratic for the improved version, as expected, but is still far cheaper than in
the original version. The price is time: at start-up, the improved version is about T_ok slower,
while after a crash both versions take about 3.1 s.
