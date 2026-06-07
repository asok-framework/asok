import os
import subprocess
import sys

# Add the workspace root to sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_dir)

from asok.cli.tools import assets_install  # noqa: E402


def compile_assets():
    print("Ensuring esbuild is installed...")
    esbuild_path = assets_install(root_dir, verbose=True)

    dirs_to_compile = [
        os.path.join(root_dir, "asok", "core", "assets"),
        os.path.join(root_dir, "asok", "admin", "static"),
        os.path.join(root_dir, "asok", "toolbar", "static"),
        os.path.join(root_dir, "asok", "api", "static"),
    ]

    for asset_dir in dirs_to_compile:
        if not os.path.exists(asset_dir):
            print(f"Skipping missing directory: {asset_dir}")
            continue

        print(f"Compiling assets in: {asset_dir}")
        for filename in os.listdir(asset_dir):
            if (filename.endswith(".js") or filename.endswith(".css")) and not (
                filename.endswith(".min.js") or filename.endswith(".min.css")
            ):
                src_path = os.path.join(asset_dir, filename)
                base, ext = os.path.splitext(filename)
                dest_path = os.path.join(asset_dir, f"{base}.min{ext}")

                print(f"  {filename} -> {base}.min{ext}...")

                res = subprocess.run(
                    [
                        esbuild_path,
                        src_path,
                        "--minify",
                        f"--outfile={dest_path}",
                        "--allow-overwrite",
                    ],
                    capture_output=True,
                )

                if res.returncode != 0:
                    print(f"Error minifying {filename}: {res.stderr.decode()}")
                    sys.exit(1)

    print("Asset compilation complete.")


if __name__ == "__main__":
    compile_assets()
