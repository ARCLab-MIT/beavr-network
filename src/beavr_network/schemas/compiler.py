import subprocess
from pathlib import Path


def compile_schemas():
    """Compile all FlatBuffer schemas in the package."""
    # Find the directory where this script is located
    schema_dir = Path(__file__).parent / "flatbuffers"

    if not schema_dir.exists():
        print(f"Error: Schema directory not found at {schema_dir}")
        return

    fbs_files = list(schema_dir.glob("*.fbs"))
    if not fbs_files:
        print(f"No .fbs files found in {schema_dir}")
        return

    print(f"Compiling {len(fbs_files)} schema(s) in {schema_dir}...")

    for fbs in fbs_files:
        cmd = ["flatc", "--python", "--gen-object-api", "-o", str(schema_dir), str(fbs)]
        try:
            subprocess.run(cmd, check=True)
            print(f"  Successfully compiled {fbs.name}")
        except subprocess.CalledProcessError as e:
            print(f"  Error compiling {fbs.name}: {e}")
        except FileNotFoundError:
            print(
                "Error: 'flatc' command not found. Please install FlatBuffers compiler."
            )
            break


if __name__ == "__main__":
    compile_schemas()
