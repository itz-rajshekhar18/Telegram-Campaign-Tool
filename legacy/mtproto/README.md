# Previous MTProto worker tests

`campaign_worker_tests.py` preserves the tests introduced by remote commit `24382ec3d8b55181202fb55e2876dbb35febc213`. They target the retired user-account worker in `app/telegram_manager.py`, not the current Bot API Campaign Studio. Restore that Git revision in a separate checkout to execute the historical suite.

The current application uses `app/studio.py` and the isolated workflow checks in `scripts/check_backend.py`, `scripts/check_connection.py`, and `scripts/check_reports.py`. JavaScript behavior checks live alongside those scripts. This directory is excluded from deployment.
