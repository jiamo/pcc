# Goal ledger archive

This directory preserves the pre-M0 startup ledgers as read-only history.
Neither archived file is an executable task queue. Active work comes only from
`docs/goal/task-board.yaml`; the compact startup state is generated from that
board and `docs/goal/head-truth-manifest.json`.

Do not append new work logs here. Use a finite task row, an evidence file, or a
routed investigation.
