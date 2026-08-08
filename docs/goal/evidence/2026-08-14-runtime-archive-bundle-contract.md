# Runtime archive bundle contract evidence — 2026-08-14

Mode: host-side artifact/provenance/cache/compiler contract; no production
archive publication and no pcc1 build.

The following fail-fast serial group completed with 131 passed:

- runtime archive provenance;
- cache and package consumers;
- compiler archive isolation;
- direct Make concurrent publication;
- head-truth manifest observations.

The C archive path rejects archives without real members and inventories that
do not exactly match actual defined `Py*`/`_Py*` symbols. The pcc-Python path
verifies schema-v2 archive, receipt and inventory binding. This evidence does
not include a frozen-source production archive rebuild, native-extension final
link, or sequential pcc1 -> pcc2 -> pcc3 proof.
