"""Build agent/unclefarmer-agent.jar from agent/src.

    python agent/build_agent.py              the jar the game loads (close every game first)
    python agent/build_agent.py <jar path>   a jar somewhere else (build_exe.py: the release's own)

Needs a JDK 8 or newer: javac is taken from JAVA_HOME, else from PATH. The jar is compiled for
Java 8, the version the game runs on.
"""

import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
BUILD = os.path.join(HERE, "build")
JAR = os.path.join(HERE, "unclefarmer-agent.jar")
PREMAIN = "unclefarmer.agent.BotAgent"


def javac() -> str:
    home = os.environ.get("JAVA_HOME")
    if home:
        exe = os.path.join(home, "bin", "javac.exe" if os.name == "nt" else "javac")
        if os.path.exists(exe):
            return exe
    exe = shutil.which("javac")
    if not exe:
        sys.exit("javac not found: install a JDK and set JAVA_HOME, or put javac on PATH")
    return exe


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    jar_path = os.path.abspath(argv[0]) if argv else JAR
    # the jar's folder may not exist yet (a clean CI runner's temp folder): make it
    os.makedirs(os.path.dirname(jar_path), exist_ok=True)
    shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(BUILD)
    sources = [os.path.join(folder, n) for folder, _d, names in os.walk(SRC) for n in names if n.endswith(".java")]
    # --release 8: the game's own Java. UTF-8: the sources hold non-ASCII character literals.
    subprocess.check_call([javac(), "--release", "8", "-encoding", "UTF-8", "-Xlint:all", "-Xlint:-options", "-Werror", "-d", BUILD] + sources)
    manifest = ("Manifest-Version: 1.0\r\nPremain-Class: %s\r\nCan-Redefine-Classes: true\r\n"
                "Can-Retransform-Classes: true\r\n\r\n" % PREMAIN)
    with zipfile.ZipFile(jar_path, "w", zipfile.ZIP_DEFLATED) as jar:
        jar.writestr("META-INF/MANIFEST.MF", manifest)
        for folder, _d, names in os.walk(BUILD):
            for n in sorted(names):
                full = os.path.join(folder, n)
                jar.write(full, os.path.relpath(full, BUILD).replace(os.sep, "/"))
    shutil.rmtree(BUILD, ignore_errors=True)
    print("built %s (%d bytes)" % (jar_path, os.path.getsize(jar_path)))


if __name__ == "__main__":
    main()
