#!/bin/bash
# Get the directory where the script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "Compiling FlatBuffer schemas in $DIR..."
flatc --python --gen-object-api -o . ./teleop.fbs
echo "Done."
