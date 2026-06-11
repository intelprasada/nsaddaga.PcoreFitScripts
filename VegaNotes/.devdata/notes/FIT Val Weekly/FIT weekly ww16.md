# FIT Val weekly ww16

#opens
	How often are we syncing main branch into MT1 and vice versa(TD and BU) - Supposed to be daily

	ww19:
		GFC also needs code review for TI

#project gfc
	!task #id T-5GSQFQ IDQ coverage unmapped items to be reviwed #eta ww20  @Namratha #priority P0 #status done
	!task #id T-HA0CP2 IFU coverage unmapped items to be reviwed #eta ww20  @Sachin   #priority P0 #status done
	!task #id T-XB2SXT IDQ coverage unhit items to be reviwed #eta ww20     @Namratha #priority P0 #status done
	!task #id T-AR27H5 IFU coverage unhit items to be reviwed #eta ww20     @Sachin   #priority P0 #status done

@aboli
	#project gfc
		!task #id T-DVND79 MCA – Disable IDQ proof assertion failing during XLAT Error #priority P1 #eta 2026-W18 #status done
			RTL and FV IDQ assertions failing in XLAT Error tests ok
			#note TI done in GFC all steppings and JNC
			#note GFC a0 TI id: 29171
			#note HSD: 14027763585
			!AR #id T-8XTQ99 why failing now #eta WW17.5 #status done
				#note was hiding behind another bucket
	#project jnc
		!task #id T-S3YSN7 Review assertions that have not reached a proven status #status done #eta 2026-06-12
			#note 4/6 converged. need to review the code myself
			#update 2/4 assertions fixed, review pending
		!task #id T-ARYRJN Assumptions review updates model TI #status done
		!task #id T-63C696 Review starvation covers. #status in-progress 
			!AR #id T-TC4QCP converted cover into assertion - found an RTL bug 
		!task #id T-AX24MR RTL bug fix model has FV failures #status blocked
			#note test note
			#note R_STSR_IDQ_ACC_PRE_ALLOC_OVERFLOW -
			#note Apar will review the wave to confirm my rootcause and add more available credits once he is free. the speculative path for reserving DSB credits from stsriq is short of 3 credits in worse case scenario where idq does not read out for a long time without any stalls
			!AR #id T-H66QC7 2 FV fixes #status done
			!AR #id T-B45J4P 1 Jasper failure - details to be added #status done
			!AR #id T-WKYEQQ Followup with Apar about the fix once he is free @aboli
		!task #id T-CV97FD Val Plan development and review #eta 2026-05-28 #status done #priority P0
			!AR #id T-VG12T9 SMT related COI, assertions #status done #eta 2026-05-22
			!AR #id T-Y2A944 SEC for STSR? #status done
		!task #id T-TAAGK0 STSR bug fix done in GFC to be done in JNC - IQ bypass CB added #priority low #eta 2026-06-19
		!task #id T-9VNZDM Added new assertion on outputs with no assertions in GFC  #eta 2026-06-24 
			!AR #id T-H0NNND 25 new assertions added - #status wip
				#note AI tool took wrong pipe stages for assertion coding - need to understand the source 
				#note All assertions are passing
		!task #id T-PA0TBS ARs from reviews to be completed #eta 2026-06-14 
		!task #id T-FA6GDA Reviewing unconverged covers #eta 2026-05-29 #status done
			#note all were IDQ credit related. Real IDQ full is
			#note IDQ size - capsule size -1.
			#note the covers needed to reflect that
			!AR #id T-ED9XED 0/6 Done #status done
@Namratha
	!task #id T-39NW4C SEC IDQ weekly failures to debug #status done
		!AR #id T-1MW9C8 fe::Formal::Assert(sec)::cex::idq.idimmCM*H::gfc-a0 #status done
		!AR #id T-42QB7M fe::Formal::Assert(sec)::cex::idq.IDBiqWrEnBranchEventM*H::gfc-a0" #status done
			due to Jasper tool issue
	!task #id T-MZ0P9M Prepare Presentation on IDQ Arch #status done
		!AR #id T-FVJTR5 IDQ arch - IDQ high level proof stucture #status done
		!AR #id T-NG1CV5 MRN deep dive #eta ww17.3 #status done
	!task #id T-C0WWN0 Cover buckets debug 
		!AR #id T-MJ453V 10 covers unreachable #eta ww19 
	!task #id T-800631 JNC bucket debug #status done
		!AR #id T-W7M4KZ MRN counter bucket debug  #status done
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
		!task #id T-3W3QRW IDQ capsule chk #status done 
	#project gfc
		!task #id T-A33BGA Enabling Swpf For the fsm #eta ww16  #status done
		!task #id T-ZRGPFZ MCA/Parity assertions for GFC #status blocked by HSD approval 
		!task #id T-C08TYC PID deallocation check assertion added to fpv and run in simulations - level0 
			220/240 failing
			#eta ww17
		!task #id T-Z8SMQB snp bit being set in wait for compl only state 
			RTL is resetting the bit on allocaiton, so the assertion is not checking anything.
			!AR #id T-7SMA8D review the snp bit functionality - set and reset conditions should have checks 

@Kelsey
	#project jnc
		!task #id T-WAHSH6 JNC work on moving complex nukes from all thread to thread specific #status done
			#eta ww17
		!task #id T-1Q99SZ JNC CTE ready for all state transitions mentioned in UCODE HAS #status done #eta W21

@Ragavi
	#project jnc
		!task #id T-CS2X6C JNC bucket debug #status done
			!AR #id T-PC96Y9 ITLB cache miss bucket debug #status done
			!AR #id T-V5ZQCR ITLB MT1 enablement 3 issues #status done
			!AR #id T-RXKF7Q ITLB msid mini EID: 1696677 , TXTE_MSG_FILL_BUFFER_OUT_FillBufUBit_mismatch #status done
				#eta ww17
				#note fix in GFC, waiting for sync
			!AR #id T-M2TRKN TXTE_MSG_TLB_LOOKUP_OUT_ItCacheableM122H_mismatch @Ragavi #status done
		!task #id T-3CF48M ITLB preloader SMT coding and smart preloader #status in-progress
			#note smart preloader coding done. Testing in progress(Need to sync up with Husam to get shadow array fixes).
			!AR #id T-WZW8XS smt coding #status done
			!AR #id T-022MGA smart preloader #eta ww18.4 #status blocked
				#note shadow arrays fixes required to TI
			!AR #id T-E32SWW randomizing TIDs in preload #eta ww21
		!task #id T-A7KC90 CB for tid based flush feature #status done 
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
		!task #id T-4PPA0R Signal refactoring for iq_tid_CM106L #status done
		!task #id T-WCF4H6 updating coverage for fe_idq_tlm_cov.e for SMT #status wip #eta WW25 #priority P0
			!AR #id T-PZQ4CK updated code to handle threads @Gautham #status done
			!AR #id T-4SPN38 add missing signals to packet @Gautham #status done
			!AR #id T-ZVSM2W verify cover groups collected @Gautham #status done
			!AR #id T-VFRC9F turn in without thread-aware 148h signals @Gautham #status done
			!AR #id T-P51GT4 investigate 148H signals @Gautham #status done
			!AR #id T-Q1C4MS update coverage to handle 148h signals @Gautham #status done
			!AR #id T-5A2J1D debugging why more failing tests @gajith #status done
			!AR #id T-5YB2ZX verify lsd coverage w MT1 / MT0 comparison @gajith #status in-progress
			!AR #id T-7FZYNJ debug missing coverage scores for MT0 @gajith #status in-progress
		!task #id T-D3K2BP predq tracker #status done #eta ww18
			!AR #id T-S3B7ZW created yaml packet, tlm infrastructure and tracker @Gautham #status done
			!AR #id T-D6JB94 verified cycle accuracy of packet signals @Gautham #status done
			!AR #id T-RZBY17 review and update with corrected signals and columns @Gautham #status done
			!AR #id T-R6GWND Code review/ TI @Gautham #status done
			!AR #id T-8P8RPW core txte failed with merge from master. debug @Gautham #status done
		!task #id T-VREJ1D updating coverage for fe_ifu_tlm_cov.e for SMT #status done 
			!AR #id T-4FEBGG verify covers being hit for smt_core_gating.list @Gautham #status done
			!AR #id T-31QEAV coded thread scoping @Gautham #status done
			!AR #id T-GKDB9P verify if threads are being differentiated with core_gaitng.list @Gautham #status done
			!AR #id T-Q1S4R7 revise val plan with thread-aware signals @Gautham #status done
			!AR #id T-XTFDVA verify rtl signal names from .vs file @Gautham #status done
			!AR #id T-1ZDG4T add additional coverage for SMT @Gautham #status done
			!AR #id T-YNRAYB update logging for signals to verify thread usage @Gautham #status done
			!AR #id T-8AVCBP Pseudocode for cross thread coverage @Gautham #status done
			!AR #id T-1FNXBN Create Val Plan with Sachin @gajith #status done
			!AR #id T-XPPG48 Update Val Plan with Sachin @gajith #status done
			!AR #id T-DSEQ5Z Update Val Plan @gajith #status done
			!AR #id T-X23YZD test @gajith #status done
			!AR #id T-AJTE2P port all valid signals and add instrumental signals for valids that are not vectorized @gajith #status done
			!AR #id T-3Q8T7K verify agent and packet thr_id matching @gajith #status done
		!task #id T-TSRF81 IDQ Ramp up #status done 
			!AR #id T-Q2F4T2 LSD, BIQ checker #eta ww18 #status done 
			!AR #id T-44JP6F create presentation @Gautham #status done 
@Yongxi
	#project csk
		!task #id T-JV6MZH MRQ (mop recover queue) preloader/injector #status done
			!AR #id T-AA277Z Enable with genfeed #status done
		!task #id T-MVTK3J SVA debug: btb_update_queue_valid_mismatch #status done 
		!task #id T-7DWZ43 SVA debug: redundancy array multiple hit #status done 
		!task #id T-8QA47X SVA debug: ONEHOT_DECODE_ic_mop_choose_way_mif2h ww15e #status done 
		!task #id T-VXA4K2 SVA debug: ltt_fetch_from_ip_match ww15e #status done 
		!task #id T-0XNJTM Extended run for btb duplicate queue feature with Marty’s new implementation #status done 
		!task #id T-AEGYJY SVA debug: btb round robin check selected cluster for update mismatch when btb update conflict #status done
		!task #id T-NQZ949 SVA debug: btp update queue valid mismatch #status done
		!task #id T-18XD5R SVA debug: mop update data mismatch #status done
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

!task #id T-KP1PRW IDQ Ramp up: LSD checker @Gautham #status done
	!AR #id T-55E9FD review LSD checker related packets @Gautham #status done
	!AR #id T-34T7JK Understand high level of IDQ write side of LSD @Gautham #status done
	!AR #id T-03KG0T present write side lsd @gajith #status done
	!AR #id T-9FB0XQ discuss missing signals w Niharika @gajith #status done



!task #id T-2MWMS4 Removal of incorrect const assume for DisSharedIDQorSMT signal @abolisaw #status done
	#note Removed the const assume to let the proof have mode change capacity as it works in RTL.
	#note Needed to add assumes for IDQ signals connecte dto this signla as there were failures after removing const assume.

!task #id T-HV614G Complete STSR Val Plan review with STSR team @abolisaw #status done

!task #id T-2RBC1Q Fixing FV to reproduce an RTL bug that missed in FV @abolisaw #status done
	#note RTL credit counter was not updating the credits correctly incase of ST-SMT mode change. since the mode change was not supported by FV we did not catch it. Fixed FV an reproduced the failure and reviewed with designer.

!task #id T-TQHRYZ Debug Fv regression fails in Elad's model @abolisaw #status done
	#note DsbqBypassArriveThrM182H was stuck at 1. HSD: 14027888369
	#note FAilure: DsbqNotEmptyOrBypassArriveThrM182H_according_to_active_thread

!task #id T-X1NSHZ Debug Apar Clock gating model fails @abolisaw #status in-progress #eta 2026-06-05
	#note Debugged, needs an FV fix
	#note sent 4 fixes to apar: added modelling for fall of LSD stall to remove false scenarios and disable assertions failing during LSD stall.


!task #id T-EZA452 ThreadMode/ThreadOperate logging @gajith

!task #id T-NCY35D MS Ramp @abolisaw #status in-progress #eta 2026-06-12

!task #id T-78A0MT MS SMT coding @abolisaw #status in-progress #eta 2026-06-12 #priority P0
	#note 9 files done, 4 files left as discussed with chen, more file to take after that

!task #id T-TJC2BC New Thread Hang assertion @abolisaw #status done
	#note added new assertion, after multiple debugs and reviews, ready to TI

!task #id T-RJ7269 GFC A0 FV Paranoia Tasks @njammala #status done

!task #id T-QPX0D6 JNC - CTE support from fe_test for PEBs init to both threads @khbyers #priority P1 #eta W22 ww22 #status done
	#note #jnc
	#note #jnc

!task #id T-RMQ98B JNC - CTE fix for split alloc stall stress for delta between MT vs ST modes @khbyers #priority P0 #eta W22 ww22 #status done

!task #id T-4CGN9Y JNC - MJEU Issue with JeClear & MoNuke at Same Time @khbyers #priority P0 #eta W22 ww22 #status done


!task #id T-JVQYB7 JNC - CTE Infra Valplan @khbyers #priority P0 #eta ww24.3 #status in-progress

!task #id T-4MRVAX JNC - Sar Violation SMT Bucket @khbyers #priority P0 #status done #eta W21
	#note MJEU needing to support two FFEIP on different threads at same time

!task #id T-PR3BCP JNC - Thread Hang SMT Bucket @khbyers #priority P0 #eta W21 #status done
	#note Root cause issue in MS due to missing threading on pending load loopcnt

!task #id T-JYG7V8 JNC - Uop Checker UIP SMT Bucket @khbyers #priority P0 #eta W21 #status done
	#note Root cause MS issue on missing threading on stallclear

!task #id T-Z0P4P4 JNC - Thread Hang SMT Bucket CTE Issue @khbyers #priority P0 #status done #eta W21
	#note root cause to CTE issue in MJEU logic

!task #id T-P9AYEF GFC - Qa/Qb Immediate Gating Uop Checker Support @khbyers

!task #id T-DVDE14 WW22_JNC_Bucket_Debug @khbyers #status done
	!AR #id T-25RDYP UIP Mismatch, MS threading issue on stallclear @khbyers #status done
	!AR #id T-Q3PT59 ROB_EMU_IFU_ADDRESS, IFU RTL Poison Issue @khbyers #status done
	!AR #id T-QJPAA5 Core Debug BIQID Mismatch Causing Hang @khbyers #status done

!task #id T-DDPYQY WW23_JNC_Bucket_Debug @khbyers #eta 2026-ww23 #status in-progress
	!AR #id T-C6MFMS macrofusion bucket, root cause CTE issue on listening to external snoops @khbyers #status done
	!AR #id T-D74VDF Try to create temp workaround for broadcast in CTE until RTL is coded @khbyers #status done
	!AR #id T-RJEB3Z UOP Clip mismatch @khbyers #status done
	!AR #id T-ZS3AM2 Thread Hang, Long MS stall on T1 causing hang on T0 @khbyers #status in-progress

!task #id T-66WRAH Enable MBB Agent in MT @khbyers #status done

!task #id T-Z3Z4VD SEC Proof for STSR @abolisaw #eta 2026-06-26

!task #id T-W3J83X GFC a0 Paranoia @abolisaw #status in-progress #eta ww24.1
	#note 2 STSR assertions to check, one is in FPV_RESTRICT need to check why and other has a wrong format
	#note 1: this fails a lot in simulation. Edwin suggested it can be removed if assume was not used,I checked and proof is not affected, so can be removed, Chen suggested to run coi_proof too, need to run that and make the final finishes.
	#note 2. STSR_Assume_DSBE_Assert_if_dsFirstVec_than_dsbqtid_and_DSBOwnership
	#note Assume properties should not be in interface files, if needed it should be in a different format. This was flagged for using assume property but when I checked this is assert property itself in interface file. have sent analysis to Daher and chen, waiting to see if anything else was required for this.

!task #id T-DB7HX1 WW24 GFC - A0 Bucket Debug @khbyers #status in-progress #eta WW24.5
	#note Husam added injections code 1.5months back, likely CTE issue
	!AR #id T-YW62FY MCA Agent Injecting IFU Long Stall @khbyers

!task #id T-5WGW2J WW24_JNC_Bucket_Debug @khbyers #status in-progress #eta WW24.5

!task #id T-N3BSET Create Thread Mode Log @khbyers #status in-progress #eta WW24.5
