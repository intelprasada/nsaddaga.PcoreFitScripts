# GFCCoverage

!task #id T-VSZ9QB IDQ uncovered_checker_coverage @Niharika
	!AR #id T-PJBZPD EID_461765	 #file idq_issue_chk.e	idq_issue	@Niharika	#note The predicate is unreachable through ucode flow. Reviewed with Ayal and he confirmed that we won't be able to reach it in ucode flow because there's no uop that jumps to that label. Okay to ignore.	#status Done
	!AR #id T-NXGSG7 EID_351214	 #file fe_idq_chk.e	fe_idq	@Niharika	 #note a common checker - we have that EID in uopchecker too.. will check with AYal to see if we are hitting that. If we are hitting this in uopchecker, it's okay to waive 	#status wip In Progress

!task #id T-EYCEPJ uncovered_functional coverage @Niharika
	!AR #id T-MEQFCA lsd_23317_R2I_ronuke	                           #file   	fe_bpu_cov_data_s_tlm.e	@Niharika	FE_Legacy_COV	1.1.6.11 - lsd_tbiq_fsm_state_change_cov_data_s	50%
	!AR #id T-AGBXHM lsd_23319_R2I_ronuke	                            #file  	fe_bpu_cov_data_s_tlm.e	@Niharika	FE_Legacy_COV	1.1.6.11 - lsd_tbiq_fsm_state_change_cov_data_s	50%
	!AR #id T-7EQBMT lsd_jeclear		                                 #file   fe_bpu_cov_data_s_tlm.e	@Niharika	FE_Legacy_COV	1.1.6.12 - lsd_tbiq_fsm_state_cov_data_s	83.33%
	!AR #id T-26ZNFZ bpurepair_tbiqid0_and_biqrepairtbiqid_max		    #file    fe_bpu_lsd_cov_tlm.e	@Niharika	FE_Legacy_COV	1.1.10.16 - bpurepair_tbiqid0_and_biqrepairtbiqid_max	50%
	!AR #id T-4X6G67 isb_clear_vir_val_per_vid		                     #file   fe_ifu_cov_tlm.e	@Sachin	ISB_re_Use_Vplan	1.1.1.2.1 - isb_clear_cov_event	88.89%
	!AR #id T-MX2KNV isbclear_phy_val_per_vid		                     #file   fe_ifu_cov_tlm.e	@Sachin	ISB_re_Use_Vplan	1.1.1.2.1 - isb_clear_cov_event	88.89%
	!AR #id T-04V4DS cross__biqisFull__biqisFull_comparisonsettolsdstart__idlsdstall__biqthreshold_cfg	#file	fe_lsd_endtoend_cov.e	@Niharika	FE_Legacy_COV	1.1.11.6 - cross__biqisFull__biqisFull_comparisonsettolsdstart__idlsdstall__biqthreshold_cfg	62.5%
	!AR #id T-NQWZS8 cross__biqisFull__biqthreshold_cfg__lsdjeclearretired	#file	fe_lsd_endtoend_cov.e	@Niharika	FE_Legacy_COV	1.1.11.7 - cross__biqisFull__biqthreshold_cfg__lsdjeclearretired	87.5%
	!AR #id T-WV4KJJ DSBBuild_IFU_set0_msb_requests_from_different_IFU_sets_ways	#status done redefinition		@Sachin	GFC_IC_128K_feature	1.1.1.13.1 - DSBBuild_IFU_set0_msb_requests_from_different_IFU_sets_ways	0%
	!AR #id T-50ZQ8A DSBBuild_IFU_set0_msb_requests_from_different_IFU_sets_ways	#status done redefinition		@Sachin	GFC_IC_128K_feature	1.1.1.16.1 - DSBBuild_IFU_set0_msb_requests_from_different_IFU_sets_ways	0%
	!AR #id T-VE59Q3 DSBBuild_IFU_set0_msb_requests_from_different_IFU_sets_ways_#status done 1	redefinition		@Sachin	GFC_IC_128K_feature	1.1.1.13.5 - DSBBuild_IFU_set0_msb_requests_from_different_IFU_sets_ways_1	0%
	!AR #id T-PKP92D DSBBuild_IFU_set1_msb_extra_bits	redefinition	#status done 	@Sachin	GFC_IC_128K_feature	1.1.1.13.2 - DSBBuild_IFU_set1_msb_extra_bits	0%
	!AR #id T-891A89 DSBBuild_IFU_set2_msb_extra_bits	redefinition	#status done 	@Sachin	GFC_IC_128K_feature	1.1.1.13.3 - DSBBuild_IFU_set2_msb_extra_bits	0%
	!AR #id T-0A3AZD DSBBuild_IFU_set3_msb_extra_bits	redefinition	#status done 	@Sachin	GFC_IC_128K_feature	1.1.1.13.4 - DSBBuild_IFU_set3_msb_extra_bits	0%
	!AR #id T-3Z2T9F DSBBuild_IFU_snoop_hit0_requests_from_different_IFU_ways	redefinition	#status done 	@Sachin	GFC_IC_128K_feature	1.1.1.14.1 - DSBBuild_IFU_snoop_hit0_requests_from_different_IFU_ways	0%
	!AR #id T-9FNKFH DSBBuild_IFU_snoop_hit1_requests_from_different_IFU_sets	redefinition	#status done 	@Sachin	GFC_IC_128K_feature	1.1.1.13.7 - DSBBuild_IFU_snoop_hit1_requests_from_different_IFU_sets	0%
	!AR #id T-DH0C5Q DSBBuild_IFU_snoop_hit1_requests_from_different_IFU_ways	redefinition	#status done 	@Sachin	GFC_IC_128K_feature	1.1.1.13.6 - DSBBuild_IFU_snoop_hit1_requests_from_different_IFU_ways	0%
	!AR #id T-F2FNY8 DSBBuild_IFU_snoop_hit1_requests_from_different_IFU_ways	redefinition	#status done 	@Sachin	GFC_IC_128K_feature	1.1.1.14.2 - DSBBuild_IFU_snoop_hit1_requests_from_different_IFU_ways	0%
	!AR #id T-GSZ3NM DSBBuild_IFU_snoop_hit2_requests_from_different_IFU_sets	redefinition	#status done 	@Sachin	GFC_IC_128K_feature	1.1.1.13.8 - DSBBuild_IFU_snoop_hit2_requests_from_different_IFU_sets	0%
	!AR #id T-RA9XCW DSBBuild_IFU_snoop_hit2_requests_from_different_IFU_ways	redefinition	#status done 	@Sachin	GFC_IC_128K_feature	1.1.1.14.3 - DSBBuild_IFU_snoop_hit2_requests_from_different_IFU_ways	0%
	!AR #id T-1B9QFB DSBBuild_IFU_snoop_hit3_requests_from_different_IFU_sets	redefinition		#status done @Sachin	GFC_IC_128K_feature	1.1.1.13.9 - DSBBuild_IFU_snoop_hit3_requests_from_different_IFU_sets	0%
	!AR #id T-4QF20F DSBBuild_IFU_snoop_hit3_requests_from_different_IFU_ways	redefinition		#status done @Sachin	GFC_IC_128K_feature	1.1.1.14.4 - DSBBuild_IFU_snoop_hit3_requests_from_different_IFU_ways	0%

!task #id T-N9ZB02 unmapped_uncovered_coverage @Niharika
	!AR #id T-K5GQG6 loadtodelonoldcancelled_duetoindircallonyng #file core/fe/cte/fe_bpu/fe_bpu_lsd_tlm	2	#note 	0%	fe_bpu_lsd_tlm_cov.e	@Niharika
	!AR #id T-KTVKFB mrnable_with_ivcdealloc_window	#file core/fe/cte/fe_idq	3	#note 0%	fe_idq_cov.e	@Namratha
	!AR #id T-K7BHZR mrnable_with_ivcdealloc_window_ported	#file core/fe/cte/fe_idq	4	#note 0%	fe_idq_cov.e	@Namratha
	!AR #id T-0ANDZ7 vp_predicted_immf #file /nfs/site/proj/gfc/gfc.models.23/core/core-gfc-b0-master-26ww18a/core/fe/cte/fe_idq	1		50%	fe_idq_cov.e	@Namratha

!task #id T-NGVJSA msid_coverage_gaps @Niharika 
	!AR #id T-TQ8JGA cross__push2_num__mux_src		#file fe_idq_wr_cov.e		IDQ	1.1.9.2 - mm_valid_window_cov_e
	!AR #id T-0RPBP8 push_or_pop_bogus_mrn_dest		#file fe_idq_wr_cov.e		IDQ	1.1.9.3 - emrn_cov_e
	!AR #id T-MW83JR push_or_pop_bogus_mrn_HLE_event		#file fe_idq_wr_cov.e		IDQ	1.1.9.3 - emrn_cov_e
	!AR #id T-CWNNSG cross__dsbcallretpushpop_indi__mitecallretpushpop_indi		#file fe_idq_tlm_cov.e		IDQ	1.1.9.8 - lsdreset_cov_e
	!AR #id T-CDJJDY lsd_end_biq_dsb		#file fe_idq_tlm_cov.e		IDQ	1.1.9.9 - idqbiq_cov_e
	!AR #id T-SN68XP lsd_start_tbiq_dsb		#file fe_idq_tlm_cov.e		IDQ	1.1.9.9 - idqbiq_cov_e
	!AR #id T-F1QY7J cross__num_lsdstarts_dsb__num_lsdends_dsb		#file fe_idq_tlm_cov.e		IDQ	1.1.9.12 - num_lsdstartsends_e
	!AR #id T-FC66YK source		#file fe_idq_lsd_cov.e		IDQ	1.1.9.17 - update_lsd_start_per_uop_e
	!AR #id T-B5RZ8J overflo_ptr_position_bank0		#file fe_idq_lsd_cov.e		IDQ	1.1.9.21 - lsd_overflow_e
	!AR #id T-S2SF0B single_cyc_lsd		#file fe_idq_lsd_cov.e		IDQ	1.1.9.23 - idq_lsd_fsm_transition_e
	!AR #id T-5FT2CR idq_lsd_single_cyc_SthenE		#file fe_idq_lsd_cov.e		IDQ	1.1.9.23 - idq_lsd_fsm_transition_e
	!AR #id T-J7J0C9 lsd_SthenE_unroll_cycle		#file fe_idq_lsd_cov.e		IDQ	1.1.9.23 - idq_lsd_fsm_transition_e
	!AR #id T-MQZ33A idq_lsd_clear		#file fe_idq_lsd_cov.e		IDQ	1.1.9.23 - idq_lsd_fsm_transition_e
	!AR #id T-50VVHD faketaken_ms_restart		#file fe_idq_cov.e		IDQ	1.1.9.25 - idq_alloc_uop_e
	!AR #id T-8QZP9Y lsdstart_w_ft_dsb		#file fe_idq_wr_cov.e		IDQ	1.1.9.27 - uop_indications_cov_e
	!AR #id T-Y6319M lvp_ppx_hint		#file fe_idq_wr_cov.e		IDQ	1.1.9.27 - uop_indications_cov_e
	!AR #id T-51Z4BW greedy_read_parital_window		#file fe_idq_cov.e		IDQ	1.1.9.28 - greedy_idq_cov_e
	!AR #id T-VNZCCZ cross__Slot0Only_0__lam2_1__lam2_2		#file fe_idq_cov.e		IDQ	1.1.9.29 - idq_read_window_e
	!AR #id T-41AJRA cross__lam2_0__lam2_1		#file fe_idq_cov.e		IDQ	1.1.9.29 - idq_read_window_e
	!AR #id T-HCQYQT INT_attributes		#file fe_idq_cov.e		IDQ	1.1.9.30 - idq_write_uop_e
	!AR #id T-X9F3FZ MXM_attributes		#file fe_idq_cov.e		IDQ	1.1.9.30 - idq_write_uop_e
	!AR #id T-1GHJGC VEC_attributes		#file fe_idq_cov.e		IDQ	1.1.9.30 - idq_write_uop_e
	!AR #id T-B6GAAN num_std_with_push2		#file fe_idq_cov.e		IDQ	1.1.9.31 - alloc_window_cov_e
	!AR #id T-2QPAGT push_or_pop_bogus_mrn_dest		#file fe_idq_wr_cov.e		IDQ	1.1.9.61 - push_or_pop_bogus_mrn_dest
	!AR #id T-D0KEFA push_or_pop_bogus_mrn_HLE_event		#file fe_idq_wr_cov.e		IDQ	1.1.9.62 - push_or_pop_bogus_mrn_HLE_event
	!AR #id T-10X1WG pp2_ud		#file fe_idq_wr_cov.e		IDQ	1.1.9.64 - pp2_ud
	!AR #id T-943R66 num_std_with_push2		#file fe_idq_cov.e		IDQ	1.1.9.65 - num_std_with_push2
	!AR #id T-RV1PBC num_std_with_push2		#file fe_idq_cov.e		IDQ	1.1.9.69 - num_std_with_push2_1
