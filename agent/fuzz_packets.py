"""Compile and run the Java packet fuzzer.

    python agent/fuzz_packets.py [iterations] [seed]

The agent's parser lives inside the game's JVM, so this is kept out of the Python test suite:
it needs a JDK and is run by the agent CI job after the jar build.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
TEST = os.path.join(HERE, "test")
MAIN = "unclefarmer.agent.PacketFuzz"


def tool(name: str) -> str:
    home = os.environ.get("JAVA_HOME")
    if home:
        exe = os.path.join(home, "bin", name + (".exe" if os.name == "nt" else ""))
        if os.path.exists(exe):
            return exe
    exe = shutil.which(name)
    if not exe:
        sys.exit("%s not found: install a JDK and set JAVA_HOME, or put it on PATH" % name)
    return exe


def main() -> None:
    iterations = sys.argv[1] if len(sys.argv) > 1 else "100000"
    seed = sys.argv[2] if len(sys.argv) > 2 else "0x5EED"
    sources = [os.path.join(folder, n) for folder, _d, names in os.walk(SRC) for n in names if n.endswith(".java")]
    sources += [os.path.join(folder, n) for folder, _d, names in os.walk(TEST) for n in names if n.endswith(".java")]

    build = tempfile.mkdtemp(prefix="unclefarmer-fuzz-")
    try:
        subprocess.check_call([tool("javac"), "--release", "8", "-encoding", "UTF-8", "-Xlint:-options", "-d", build] + sources)
        subprocess.check_call([tool("java"), "-cp", build, MAIN, seed, iterations])
    finally:
        shutil.rmtree(build, ignore_errors=True)


if __name__ == "__main__":
    main()
