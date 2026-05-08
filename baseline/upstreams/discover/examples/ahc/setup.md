# AtCoder Heuristic Contest (AHC) Environment

>NOTE: It is very important that to reproduce our results for AHC, you must run on an HPC level set of cpus. See paper appendix for more details.

## Docker
To run this environment, you will need to run your program in the docker container provided by ale-bench.

[yimjk/ale-bench:cpp20-202301](https://hub.docker.com/layers/yimjk/ale-bench/cpp20-202301/images/sha256-946af1b209a84160594e43262b5157aec933938c99e2585b53042cac9bc3f43c)


## Getting Test Cases and Binaries
This code assumes there is a cache of test cases and tester binaries.

To pull the cache, run the following command from the project root:

```bash
bash examples/ahc/get_cache.sh
```

## Running on multinode

Our standard submitit script does not work with different containers. See discover_multinode.sh for a complicated multinode slurm launch of our ahc jobs. Alternatively, you can run without this script.