# FurniScope Sales Forecast V4 assets

This directory makes the FurniScope repository independently deployable.

- `state/`: active daily/weekly feature frames, trained XGBoost + LightGBM model
  bundles, label encoders, and state metadata used by online inference.
- `training_data/`: full order and inventory sources required to rebuild state.
- `append_samples/`: incremental order/inventory fixture and its manifest.
- `docs/`: V4 design/training details and operator guide.
- `MODEL_CARD.json`: active weight timestamps, feature counts, delivered backtest
  metrics, data-quality effects, and limitations.
- `MANIFEST.sha256`: checksums captured when the assets were imported.

The API never accepts a model or pickle path from an HTTP client. Only the
server-configured files in this trusted deployment directory are loaded.

Verify the copied assets from this directory:

```bash
sha256sum -c MANIFEST.sha256
```

The active state reports data through `2026-06-30` and contains 1,204 SKUs.
The historical `../model` V1-V3 backups from the source workspace are not part
of this deployment because Sales Forecast V4 does not load them.

The binary assets are covered by the repository `.gitattributes` and must be
committed/pulled with Git LFS. A deployment checkout that contains small LFS
pointer text instead of the real binaries will fail the checksum and readiness
checks.
