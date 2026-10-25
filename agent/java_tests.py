"""Compile the agent with its tests and run them: AgentTests, then the packet fuzzer.

    python agent/java_tests.py

Compiled into a temporary folder: the jar the game loads (agent/unclefarmer-agent.jar) is never
touched, so this is safe while a game is running. check.py runs it whenever a JDK is installed.
"""

import os
import shutil
import subprocess
import sys
import tempfile

from fuzz_packets import SRC, TEST, tool

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(TEST, "contract.txt")


def java_files(folder):
    return [os.path.join(d, n) for d, _dirs, names in os.walk(folder) for n in names if n.endswith(".java")]


def main():
    build = tempfile.mkdtemp(prefix="unclefarmer-agent-tests-")
    try:
        subprocess.check_call([tool("javac"), "--release", "8", "-encoding", "UTF-8", "-Xlint:-options", "-d", build]
                              + java_files(SRC) + java_files(TEST))
        java = [tool("java"), "-cp", build]
        subprocess.check_call(java + ["unclefarmer.agent.AgentTests", CONTRACT])
        subprocess.check_call(java + ["unclefarmer.agent.PacketFuzz", "0x5EED", "100000"])
    except subprocess.CalledProcessError as e:
        return e.returncode or 1
    finally:
        shutil.rmtree(build, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
