#!/bin/bash
# Waits for the verifier to exit, then builds the SFT set. Detached so memory reaping can't stop it.
cd /c/Users/ankit/hacktober-2026/hsn
while tasklist //FI "PID eq $(cat data/synth/verify.pid)" | grep -q python; do sleep 30; done
.venv/Scripts/python -I scripts/build_sft.py > data/sft_build.log 2>&1
echo DONE >> data/sft_build.log
