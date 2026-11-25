# Legacy NI Prototype

`ReadAllChannels.py` is historical reference material showing the original
four-channel intent. It is excluded from the installed package and automated
quality and architecture checks.

The script is not a compatibility target or architectural template. It opens
files at import time, combines NI task ownership with storage and plotting,
converts arrays through strings, uses implicit channel ordering, creates a
time-vector length mismatch, and does not close every file on all paths.

Do not run it as part of LabDAQ verification. Later milestones may preserve its
scientifically meaningful physical-channel intent through validated profiles.
