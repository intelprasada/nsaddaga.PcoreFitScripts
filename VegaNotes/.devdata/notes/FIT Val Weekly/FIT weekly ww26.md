# FIT Val weekly ww26

#opens

	#passdowns Send calendar invites for all OOO communications to both FM and IDC teams.
	#passdowns Send email reply for any tasks/debugs assigned to you before EOD

#project gfc
@aboli
	#project gfc
	#project jnc
		!task #id T-63C696 Review starvation covers. #status done 
			!AR #id T-TC4QCP converted cover into assertion - found an RTL bug 
		!task #id T-AX24MR RTL bug fix model has FV failures #status blocked
			#note test note
			#note R_STSR_IDQ_ACC_PRE_ALLOC_OVERFLOW -
			#note Apar will review the wave to confirm my rootcause and add more available credits once he is free. the speculative path for reserving DSB credits from stsriq is short of 3 credits in worse case scenario where idq does not read out for a long time without any stalls
			!AR #id T-H66QC7 2 FV fixes #status done
			!AR #id T-B45J4P 1 Jasper failure - details to be added #status done
			!AR #id T-WKYEQQ Followup with Apar about the fix once he is free @aboli
		!task #id T-TAAGK0 STSR bug fix done in GFC to be done in JNC - IQ bypass CB added #priority low #eta 2026-06-19
		!task #id T-9VNZDM Added new assertion on outputs with no assertions in GFC  #eta 2026-06-24 
			!AR #id T-H0NNND 25 new assertions added - #status wip
				#note AI tool took wrong pipe stages for assertion coding - need to understand the source 
				#note All assertions are passing
		!task #id T-PA0TBS ARs from reviews to be completed #eta 2026-06-14 
@Namratha
	!task #id T-C0WWN0 Cover buckets debug 
		!AR #id T-MJ453V 10 covers unreachable #eta ww19 #status done
		!AR #id T-C447ZR fe::Formal::Cover::unreachable::idq.fv_idq_uopcod_model.dsb_uopcod_assume*.T_FPV_IDQ_assume_uopcod_dsb_HLEStart_hint.precondition*extend::gfc-a0 @njammala
		!AR #id T-B29C8G fe::Formal::Cover::unreachable::idq.fv_idq_uopcod_model.dsb_uopcod_assume*.T_FPV_IDQ_assume_uopcod_dsb_no_HLE_Begin_End_uops.precondition*extend::gfc-a0 @njammala
		!AR #id T-0933YS fe::Formal::Cover::unreachable::idq.fv_idq_uopcod_model.mite_uopcod_assume*.T_FPV_IDQ_assume_uopcod_mite_HLEEnd_hint.precondition*extend::gfc-a0 @njammala
		!AR #id T-S9HD6K fe::Formal::Cover::unreachable::idq.fv_idq_uopcod_model.mite_uopcod_assume*.T_FPV_IDQ_assume_uopcod_mite_HLEStart_hint.precondition*extend::gfc-a0 @njammala
	!task #id T-800631 JNC bucket debug #status in-progress
		#note https://intel.sharepoint.com/:x:/r/sites/C2DGFormalVerification/Shared%20Documents/P-Core%20FV/FIT%20FV%20Ops/JNC/JNC%20FV%20Buckets.xlsx?d=wddb2d8cdbf4f47a387265cec09307d9a&csf=1&web=1&e=XiBb1u
		!AR #id T-W7M4KZ MRN counter bucket debug  #status done
		!AR #id T-82SWCP fe::Formal::Assert::cex::idq.fv_idq_mrn.T_FPV_FIT_ID_MRN_AssertStoreAllMrnUpdatesCorrectly::jnc-a0 @njammala #status in-progress
		!AR #id T-0WK980 fe::Formal::Assert(sec)::cex::idq.IDSilentUBranchM*H::jnc-a0 @njammala #status done
		!AR #id T-8BHYQJ BIQChecks Enable in FV @njammala #status in-progress
		!AR #id T-0HWZ1G CORE_ST_ONLY Files clean up @njammala #status done
	!task #id T-1APKMK IDQ formal plan review #eta ww21 @nchatla

@Muana
	#project jnc
	#project gfc
@Kushwanth
	#project jnc
		!task #id T-N1KE10 IDQ Ramp up #eta ww17 
			!AR #id T-B93VSH issue checker review done 
	#project gfc
		!task #id T-ZRGPFZ MCA/Parity assertions for GFC #status blocked by HSD approval 
		!task #id T-C08TYC PID deallocation check assertion added to fpv and run in simulations - level0 
			220/240 failing
			#eta ww17
		!task #id T-Z8SMQB snp bit being set in wait for compl only state 
			RTL is resetting the bit on allocaiton, so the assertion is not checking anything.
			!AR #id T-7SMA8D review the snp bit functionality - set and reset conditions should have checks 

@Kelsey
	#project jnc
@Ragavi
	#project jnc
		!task #id T-3CF48M ITLB preloader SMT coding and smart preloader #status in-progress
			#note smart preloader coding done. Testing in progress(Need to sync up with Husam to get shadow array fixes).
			!AR #id T-WZW8XS smt coding #status done
			!AR #id T-022MGA smart preloader #eta ww18.4 #status in-progress
				#note shadow arrays fixes required to TI
			!AR #id T-E32SWW randomizing TIDs in preload #eta ww21
		!task #id T-EATM79 CR for forced partition CTE support #eta WW25 #status in-progress
			!AR #id T-C2RA2C CTE coding #status done
			!AR #id T-RBTWDT Integration 
		!task #id T-NWXAKW Validation plan for SMT ITLB #eta ww26 #priority high #status in-progress
			!AR #id T-RDJYHX Internal review for val plan #eta ww20 
		!task #id T-2953KW Snoop injector  SMT coding #eta ww19 #status done
			!AR #id T-XN2WQY further breakdown of tasks #eta ww18.2 #status done
			!AR #id T-6XJAS5 Coding done #eta ww18 #status done
			!AR #id T-R4HWHC Integration done #eta ww19 #status in-progress

@Gautham
	#project jnc
@Yongxi
	#project csk
@Niharika
	#project jnc
		!task #id T-BZWKMW IDQ val plan review #eta ww21
		!task #id T-ZXGRPX ITLB formal val plan review #eta ww21

@Edwin
	#project jnc
		!task #id T-R19WPR IFU formal plan review #eta ww26
        
@Sachin
	#project jnc
		!task #id T-5R1062 IFU val plan review #eta ww25.5 #status in-progress #priority P0

!task #id T-P7QS48 Full Code coverage GFC @Gautham #status in-progress #eta 2026-ww19
	#note Required changes:
	#note - in commands provided, switch OOO to FE
	#note - simgress command update:
	#note simregress -dut fe -cost_source ooo -reg_type debug_regression -l $MODEL_ROOT/core/ooo/reglist/gfc_weekly_regression.list -trex -cfg_sw COVERAGE=COLLECT -cfg_sw- -trex- -collect_coverage -trex -ms -vcs -cm fsm+assert+branch+line+tgl+cond -vcs- -ms- -vms_args -project gfc -stepping gfc-a0 -super_cluster ip -cluster ooo -te_platform sim -ind_scope fe_cc -vms_args- -trex- &
	!AR #id T-FJ0M34 make required changes based off doc/email @Gautham #status done
	!AR #id T-G9W18Y run coverage @Gautham #status done
	!AR #id T-B28GKB run full regression suite @Gautham #status in-progress
	!AR #id T-90JRHP Build and send model to Aya @gajith #status done

!task #id T-X1NSHZ Debug Apar Clock gating model fails @abolisaw #status in-progress #eta 2026-06-05
	#note Debugged, needs an FV fix
	#note sent 4 fixes to apar: added modelling for fall of LSD stall to remove false scenarios and disable assertions failing during LSD stall.
	#note 5 failures fixed TI done and accepted
	!AR #id T-07X13K fix 13 failures @abolisaw


!task #id T-EZA452 ThreadMode/ThreadOperate logging @gajith

!task #id T-NCY35D MS Ramp @abolisaw #status in-progress #eta 2026-06-12

!task #id T-78A0MT MS SMT coding @abolisaw #status in-progress #eta 2026-06-12 #priority P0
	#note 9 files done, 4 files left as discussed with chen, more file to take after that

!task #id T-Z3Z4VD SEC Proof for STSR @abolisaw #eta 2026-06-26

!task #id T-5WGW2J ww26_JNC_Bucket_Debug @khbyers #status in-progress #eta WW24.5
	!AR #id T-0JK45W DSB Thread hang due to non-threaded dsbq ctl sync signal @khbyers #status done
	!AR #id T-XNRPX1 branch skid issue with nuke BPU assertion @khbyers #status done
	!AR #id T-YTVX7Y DSBQ Full Stall @khbyers #status in-progress
	!AR #id T-N9YPVR MJEU SAR violation @khbyers #status in-progress
	!AR #id T-0W7ZZR RAStall Stuck High, ROBFull @khbyers #status in-progress
	!AR #id T-YXPN95 minTe thread hang @khbyers #status done

!task #id T-1QHX29 IDQ Ramp plan @gajith @Kushwanth #status in-progress #eta WW 25
	#note develop plan of ramp up for DV:
	#note - Understand reference models for LSD
	#note - look at the idq write side (nearly done)
	#note - look at the idq read side (need to start)
	#note - Look into the LSD and TBIQ LSD related checkers
	#note - understand inputs, outputs and transformations and how data is being fed into ref models
	#note - Checker EID re-evaluations:
	#note - Take some solved buckets with failing EIDs (preferably from each checker we have discussed) that Niharika has solved
	#note - Reverse engineer/ understand how to conclusions were made by our ownselves #status done
	#note - Present waveforms review and process of solving checkers #status in-progress
	!AR #id T-MV861K present write side checker/ref model @gajith #status in-progress
	!AR #id T-VSJRCG Study read packets/ ref model idq lsd read side @gajith
	!AR #id T-XCJR1S present lsd read side ref model/ checker @gajith
	!AR #id T-FBDJ3N ask for debug buckets/ go through one with niharika @gajith
	!AR #id T-VXJ5HE present debug bucket @gajith #priority P2

!task #id T-XZ5H94 Bucket automation/ updates @gajith #status in-progress
	!AR #id T-13PHJZ Update query to allow for both FM/IDC bucket owners @gajith #status done
	!AR #id T-E8SXVZ script to parse csv and reassign bucket owners @gajith #status done


!task #id T-7G2A2V fe_ifu_tlm is under ST_TE? #priority P0 #eta ww25 @sbhattad

!task #id T-CSEMPN updating BP LSD coverage @gajith #status in-progress
	!AR #id T-YS4XC4 update coverage for smt @gajith #status done
	!AR #id T-RG66EC Review the code with team @gajith

!task #id T-5HXD31 Bucket Debug #priority P0 #eta fe::Formal::Assert::cex::bpu.fv_bpsyncs_bpu_intf_chk.T_FPV_FE_BPSYNCS_Assume_TakenOfstCmp::gfc-a0

!task #id T-XXTHFF GFC_B0 Bucket debugs @njammala #status in-progress
	!AR #id T-QS8KJD fe::Formal::Assert::cex::idq.fv_IDQ_RAT_intf_chk.idq_git_assume_uop_GatherScatter_window*.T_IDQ_RAT_FPV_MRN_Legal_Ld_mrn::gfc-a0 @njammala #status in-progress
	!AR #id T-PW75NW fe::Formal::Assert(sec)::cex::idq.idimmCM*H::gfc-a0 @njammala #status in-progress
	!AR #id T-4W769S fe::Formal::Assert(sec)::cex::idq.IDBiqWrEnBranchEventM*H::gfc-a0 @njammala #status in-progress

!task #id T-9DSSTX MS EMU debugs @abolisaw #status in-progress #eta WW25.5
	#note core_emu::Assert::T_FPV_MS_noPendingUnstallonEom::icore_par_msid_msid_ms_fv_ms_global_inst::gfc-a0
	#note core_emu::Assert::T_FPV_MS_MSuromNNUIP_equal_target_of_branch::icore_par_msid_msid_ms_fv_ms_global_inst::gfc-a0
	!AR #id T-J00YA2 Test and TI Fix suggested by Edwin in GFC @abolisaw #status done
	!AR #id T-7WWR6A reproduce the emu failure 2 in FV @abolisaw #status in-progress

!task #id T-6Z48Q7 iTLB coverage - thread scope @rnagarat #eta WW26

!task #id T-YWG1CV DSB flush modelling in iTLB @rnagarat #eta WW25 #status in-progress

!task #id T-S3HAWY JNC debugs @rnagarat #eta ww25 #status in-progress
	!AR #id T-VF1HV4 TXTE_MSG_TLB_LOOKUP_OUT_SmVictimWayM122H_old_mismatch @rnagarat #status done
	!AR #id T-N0NM4P PREFQ_pop_from_empty_prefq @rnagarat #status done
	!AR #id T-N7BX30 R_ITLB_SmHit_Mutex @rnagarat #status done
	!AR #id T-5VWWP8 PREFQ_pop_from_empty_prefq (rtl bug) @rnagarat #status done
	!AR #id T-VDR5HH EID::1696676::TXTE_MSG_TLB_LOOKUP_OUT_ItCacheableM122H_mismatch @rnagarat

!task #id T-1NW5RS MSID JNC SMT Critical violations model from Michal FV failures to be fixed #priority P0 #eta ww25.5 @abolisaw
	#note nfs/site/disks/mnemirov_wa/SMT_Cleanup_ww26.4/regression/fe/fe_fv_level0.list/

#project gfc 
!task #id T-HJ5GFM fe::EID::1687794::actual_sb_way_is_different_than_exp,::gfc-a0	@sbhattad	Known RTL
!task #id T-TWJ0PZ fe::EID::1688704::TXTE_MSG_CSP_LOOKUP_OUT_SbAllocReq_mismatch ::gfc-a0	@sbhattad	Known RTL
!task #id T-1KK4GX fe::EID::1688704::TXTE_MSG_CSP_LOOKUP_OUT_SwPrefReqValid_mismatch ::gfc-a0	@sbhattad	Known RTL
!task #id T-Z66K31 fe::EID::212406::BPU_LSD_CHECKER:ABORT_M155H_MARKERS_INCONSISTENT::gfc-a0	@nchatla	Known RTL
!task #id T-X94PF3 fe::Assert::::gfc-a0	@nchatla	active
!task #id T-B311AW fe::Assert::T_FPV_FE_BPU_BAC_INTF_BPU_Assume_BAC_Assert_IFixedByBacUpdateM110H_underflow::fe_bpu_fv_bpu_bac_intf_chk_inst::gfc-a0	@mkasongo	active
!task #id T-6H85C6 fe::Assert::T_FPV_FE_BPU_COMMON_Assume_RoNukeT_M907H_BpRSMOClearVM803H_Mutex::fe_bpu_fv_fe_bpu_common_chk::gfc-a0	@mkasongo	active
!task #id T-096KXX fe::Assert::T_FPV_FE_IFU_ISSUEUOP_FSM_Assume_IfClearStartM123H_NO_PMH_RESPONSE::fe_ifu_fv_ifu_global_inst::gfc-a0	@efmendez	active
!task #id T-87ZD81 fe::Assert::T_IDQ_RAT_FPV_Relamination_correctness_stimuli_1::msid_idq_fv_IDQ_RAT_intf_chk_inst_idq_rat_assume_uop_loop_genblk::gfc-a0	@nchatla	active
!task #id T-NCWBS1 Review AI Val plan from Daher @abolisaw #eta ww26.3
	!AR #id T-M831YD communicate the eta to Daher