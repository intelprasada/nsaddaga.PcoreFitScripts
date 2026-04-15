#!/bin/bash
set -e
cd /nfs/site/home/nsaddaga/nsaddaga_wa/MT1_enab3

python3 scripts/interfacespec/unsupervised_signal_classifier.py \
    --io-csv target/fe/gen/InterfaceSpecAgent/fe_pipeline_20260315_160321/ifu_drilldown/interface_spec/00_ifu_top_interface_from_drilldown.csv \
    --out-dir target/fe/gen/InterfaceSpecAgent/fe_pipeline_20260315_160321/ifu_drilldown/ml_clustering \
    --module ifu \
    --ae-epochs 150 \
    --ae-bottleneck 32 \
    --hdbscan-min-cluster 8 \
    --hdbscan-min-samples 5

echo "DONE"
