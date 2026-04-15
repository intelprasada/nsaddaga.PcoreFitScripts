#!/bin/bash
cd /nfs/site/home/nsaddaga/nsaddaga_wa/MT1_enab3
exec python3 scripts/interfacespec/run_fe_msid_pipeline_with_cross_cluster.py --skip-hierarchy
