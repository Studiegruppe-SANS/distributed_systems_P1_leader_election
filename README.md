# Group 8: P1 (Leader Election)

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
[1] Hector Garcia-Molina. “Elections in a Distributed Computing System”. In: IEEE Transactions on Computers C-31.1 (1982), pp. 48–59.
