# FIT Val weekly ww24

#opens
	How often are we syncing main branch into MT1 and vice versa(TD and BU) - Supposed to be daily

	ww24:
		GFC also needs code review for TI

#project gfc
@aboli
	#project gfc
	#project jnc
		!task #id T-63C696 Review starvation covers. #status in-progress 
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
	!task #id T-800631 JNC bucket debug #status in-progress
		!AR #id T-W7M4KZ MRN counter bucket debug  #status done
		!AR #id T-82SWCP fe::Formal::Assert::cex::idq.fv_idq_mrn.T_FPV_FIT_ID_MRN_AssertStoreAllMrnUpdatesCorrectly::jnc-a0 @njammala #status in-progress
		!AR #id T-0WK980 fe::Formal::Assert(sec)::cex::idq.IDSilentUBranchM*H::jnc-a0 @njammala
	!task #id T-1APKMK IDQ formal plan review #eta ww21 @nchatla

@Muana
	#project jnc
		!task #id T-8V3204 IFU ramp up #status in-progress
			!AR #id T-5DY4HK Study in use bit and array concept
			!AR #id T-XHZD85 Study MITE reduction penalty feature
			!AR #id T-1RMTZT iTLB #eta ww18
		!task #id T-SYNYEP Use GHCP to study fv_VIdPid_chk #eta ww19 #status in-progress
			!AR #id T-J0DHG8 Convert to SMT #eta ww19
	#project gfc
		!task #id T-0C8D86 bucket debug #status in-progress
			!AR #id T-V8XDP2 fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenAndTknErr::gfc-a0  #status wip
			!AR #id T-191DAC fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenErr::gfc-a0  #status wip #eta ww18

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
			!AR #id T-022MGA smart preloader #eta ww18.4 #status blocked
				#note shadow arrays fixes required to TI
			!AR #id T-E32SWW randomizing TIDs in preload #eta ww21
		!task #id T-EATM79 CR for forced partition CTE support #eta ww19 
			!AR #id T-C2RA2C CTE coding 
			!AR #id T-RBTWDT Integration 
		!task #id T-NWXAKW Validation plan for SMT ITLB #eta ww21 #priority high 
			!AR #id T-RDJYHX Internal review for val plan #eta ww20 
		!task #id T-2953KW Snoop injector  SMT coding #eta ww19 #status in-progress
			!AR #id T-XN2WQY further breakdown of tasks #eta ww18.2 #status done
			!AR #id T-6XJAS5 Coding done #eta ww18 #status done
			!AR #id T-R4HWHC Integration done #eta ww19 #status in-progress

@Gautham
	#project jnc
		!task #id T-WCF4H6 updating coverage for fe_idq_tlm_cov.e for SMT #status wip #eta WW25 #priority P0
			!AR #id T-PZQ4CK updated code to handle threads @Gautham #status done
			!AR #id T-4SPN38 add missing signals to packet @Gautham #status done
			!AR #id T-ZVSM2W verify cover groups collected @Gautham #status done
			!AR #id T-VFRC9F turn in without thread-aware 148h signals @Gautham #status done
			!AR #id T-P51GT4 investigate 148H signals @Gautham #status done
			!AR #id T-Q1C4MS update coverage to handle 148h signals @Gautham #status done
			!AR #id T-5A2J1D debugging why more failing tests @gajith #status done
			!AR #id T-5YB2ZX verify lsd coverage w MT1 / MT0 comparison @gajith #status done
			!AR #id T-7FZYNJ debug missing coverage scores for MT0 @gajith #status done
@Yongxi
	#project csk
@Niharika
	#project jnc
		!task #id T-BZWKMW IDQ val plan review #eta ww21
		!task #id T-ZXGRPX ITLB formal val plan review #eta ww21

@Edwin
	#project jnc
		!task #id T-R19WPR IFU formal plan review #eta ww21
        
@Sachin
	#project jnc
		!task #id T-5R1062 IFU val plan review #eta ww21 #status in-progress

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


!task #id T-EZA452 ThreadMode/ThreadOperate logging @gajith

!task #id T-NCY35D MS Ramp @abolisaw #status in-progress #eta 2026-06-12

!task #id T-78A0MT MS SMT coding @abolisaw #status in-progress #eta 2026-06-12 #priority P0
	#note 9 files done, 4 files left as discussed with chen, more file to take after that

!task #id T-JVQYB7 JNC - CTE Infra Valplan @khbyers #priority P0 #eta ww24.3 #status done


!task #id T-DDPYQY ww24_JNC_Bucket_Debug @khbyers #eta 2026-ww23 #status done
	!AR #id T-C6MFMS macrofusion bucket, root cause CTE issue on listening to external snoops @khbyers #status done
	!AR #id T-D74VDF Try to create temp workaround for broadcast in CTE until RTL is coded @khbyers #status done
	!AR #id T-RJEB3Z UOP Clip mismatch @khbyers #status done
	!AR #id T-ZS3AM2 Thread Hang, Long MS stall on T1 causing hang on T0 @khbyers #status done

!task #id T-Z3Z4VD SEC Proof for STSR @abolisaw #eta 2026-06-26

!task #id T-DB7HX1 WW24 GFC - A0 Bucket Debug @khbyers #status done #eta WW24.5
	#note Husam added injections code 1.5months back, likely CTE issue
	!AR #id T-YW62FY MCA Agent Injecting IFU Long Stall @khbyers #status done

!task #id T-5WGW2J WW24_JNC_Bucket_Debug @khbyers #status in-progress #eta WW24.5
	!AR #id T-0JK45W DSB Thread hang due to non-threaded dsbq ctl sync signal @khbyers #status done
	!AR #id T-XNRPX1 branch skid issue with nuke BPU assertion @khbyers #status done
	!AR #id T-YTVX7Y DSBQ Full Stall @khbyers

!task #id T-N3BSET Create Thread Mode Log @khbyers #status in-progress #eta WW24.5

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
	!AR #id T-13PHJZ Update query to allow for both FM/IDC bucket owners @gajith
	!AR #id T-E8SXVZ script to parse csv and reassign bucket owners @gajith

!task #id T-MW39KD GFC Bucket Debug @njammala #eta WW25.2
	!AR #id T-0ADNY5 fe::Assert::T_IDQ_RAT_FPV_CTE_legal_lreg7_1::msid_idq_fv_IDQ_RAT_intf_chk_inst_idq_rat_lreg_constraints_assume_uop_loop @njammala
	!AR #id T-PFGANQ assert_IDQ_DT_no_garbage_out_to_ooo_mite_BiqId @njammala
