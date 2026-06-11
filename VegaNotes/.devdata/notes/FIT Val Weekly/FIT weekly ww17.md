# FIT Val weekly ww17

#opens
	How often are we syncing main branch into MT1 and vice versa(TD and BU) - Supposed to be daily

	ww19:
		GFC also needs code review for TI

#project gfc
@aboli
	#project gfc
	#project jnc
		#task T-63C696 Review starvation covers. #status in-progress
			#AR T-TC4QCP converted cover into assertion - found an RTL bug
		#task T-AX24MR RTL bug fix model has FV failures #status blocked
			#note test note
			#note R_STSR_IDQ_ACC_PRE_ALLOC_OVERFLOW -
			#note Apar will review the wave to confirm my rootcause and add more available credits once he is free. the speculative path for reserving DSB credits from stsriq is short of 3 credits in worse case scenario where idq does not read out for a long time without any stalls
			#AR T-WKYEQQ Followup with Apar about the fix once he is free @aboli
		#task T-TAAGK0 STSR bug fix done in GFC to be done in JNC - IQ bypass CB added #priority low #eta 2026-06-19
		#task T-9VNZDM Added new assertion on outputs with no assertions in GFC #eta 2026-06-24
			#AR T-H0NNND 25 new assertions added - #status wip
				#note AI tool took wrong pipe stages for assertion coding - need to understand the source 
				#note All assertions are passing
		#task T-PA0TBS ARs from reviews to be completed #eta 2026-06-14
@Namratha
	#task T-C0WWN0 Cover buckets debug
		#AR T-MJ453V 10 covers unreachable #eta ww19
	#task T-1APKMK IDQ formal plan review #eta ww21 @nchatla

@Muana
	#project jnc
		#task T-8V3204 IFU ramp up #status in-progress
			#AR T-5DY4HK Study in use bit and array concept
			#AR T-XHZD85 Study MITE reduction penalty feature
			#AR T-1RMTZT iTLB #eta ww18
		#task T-SYNYEP Use GHCP to study fv_VIdPid_chk #eta ww19 #status in-progress
			#AR T-J0DHG8 Convert to SMT #eta ww19
	#project gfc
		#task T-0C8D86 bucket debug #status in-progress
			#AR T-V8XDP2 fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenAndTknErr::gfc-a0 #status wip
			#AR T-191DAC fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenErr::gfc-a0 #status wip #eta ww18

@Kushwanth
	#project jnc
		#task T-N1KE10 IDQ Ramp up #eta ww17
			#AR T-B93VSH issue checker review done
	#project gfc
		#task T-ZRGPFZ MCA/Parity assertions for GFC #status blocked by HSD approval
		#task T-C08TYC PID deallocation check assertion added to fpv and run in simulations - level0
			220/240 failing
			#eta ww17
		#task T-Z8SMQB snp bit being set in wait for compl only state
			RTL is resetting the bit on allocaiton, so the assertion is not checking anything.
			#AR T-7SMA8D review the snp bit functionality - set and reset conditions should have checks

@Kelsey
	#project jnc
@Ragavi
	#project jnc
		#task T-3CF48M ITLB preloader SMT coding and smart preloader #status in-progress
			#note smart preloader coding done. Testing in progress(Need to sync up with Husam to get shadow array fixes).
			#AR T-022MGA smart preloader #eta ww18.4 #status blocked
				#note shadow arrays fixes required to TI
			#AR T-E32SWW randomizing TIDs in preload #eta ww21
		#task T-EATM79 CR for forced partition CTE support #eta ww19
			#AR T-C2RA2C CTE coding
			#AR T-RBTWDT Integration
		#task T-NWXAKW Validation plan for SMT ITLB #eta ww21 #priority high
			#AR T-RDJYHX Internal review for val plan #eta ww20
		#task T-2953KW Snoop injector SMT coding #eta ww19 #status in-progress
			#AR T-R4HWHC Integration done #eta ww19 #status in-progress

@Gautham
	#project jnc
		#task T-WCF4H6 updating coverage for fe_idq_tlm_cov.e for SMT #status wip #eta WW25 #priority P0
			#AR T-5YB2ZX verify lsd coverage w MT1 / MT0 comparison @gajith #status in-progress
			#AR T-7FZYNJ debug missing coverage scores for MT0 @gajith #status in-progress
@Yongxi
	#project csk
@Niharika
	#project jnc
		#task T-BZWKMW IDQ val plan review #eta ww21
		#task T-ZXGRPX ITLB formal val plan review #eta ww21

@Edwin
	#project jnc
		#task T-R19WPR IFU formal plan review #eta ww21
        
@Sachin
	#project jnc
		#task T-5R1062 IFU val plan review #eta ww21 #status in-progress

#task T-P7QS48 Full Code coverage GFC @Gautham #status in-progress #eta 2026-ww19
	#note Required changes:
	#note - in commands provided, switch OOO to FE
	#note - simgress command update:
	#note simregress -dut fe -cost_source ooo -reg_type debug_regression -l $MODEL_ROOT/core/ooo/reglist/gfc_weekly_regression.list -trex -cfg_sw COVERAGE=COLLECT -cfg_sw- -trex- -collect_coverage -trex -ms -vcs -cm fsm+assert+branch+line+tgl+cond -vcs- -ms- -vms_args -project gfc -stepping gfc-a0 -super_cluster ip -cluster ooo -te_platform sim -ind_scope fe_cc -vms_args- -trex- &
	#AR T-B28GKB run full regression suite @Gautham #status in-progress
#task T-X1NSHZ Debug Apar Clock gating model fails @abolisaw #status in-progress #eta 2026-06-05
	#note Debugged, needs an FV fix
	#note sent 4 fixes to apar: added modelling for fall of LSD stall to remove false scenarios and disable assertions failing during LSD stall.


#task T-EZA452 ThreadMode/ThreadOperate logging @gajith

#task T-NCY35D MS Ramp @abolisaw #status in-progress #eta 2026-06-12

#task T-78A0MT MS SMT coding @abolisaw #status in-progress #eta 2026-06-12 #priority P0
	#note 9 files done, 4 files left as discussed with chen, more file to take after that

#task T-JVQYB7 JNC - CTE Infra Valplan @khbyers #priority P0 #eta ww24.3 #status in-progress

#task T-P9AYEF GFC - Qa/Qb Immediate Gating Uop Checker Support @khbyers

#task T-DDPYQY WW23_JNC_Bucket_Debug @khbyers #eta 2026-ww23 #status in-progress
	#AR T-ZS3AM2 Thread Hang, Long MS stall on T1 causing hang on T0 @khbyers #status in-progress

#task T-Z3Z4VD SEC Proof for STSR @abolisaw #eta 2026-06-26

#task T-W3J83X GFC a0 Paranoia @abolisaw #status in-progress #eta ww24.1
	#note 2 STSR assertions to check, one is in FPV_RESTRICT need to check why and other has a wrong format
	#note 1: this fails a lot in simulation. Edwin suggested it can be removed if assume was not used,I checked and proof is not affected, so can be removed, Chen suggested to run coi_proof too, need to run that and make the final finishes.
	#note 2. STSR_Assume_DSBE_Assert_if_dsFirstVec_than_dsbqtid_and_DSBOwnership
	#note Assume properties should not be in interface files, if needed it should be in a different format. This was flagged for using assume property but when I checked this is assert property itself in interface file. have sent analysis to Daher and chen, waiting to see if anything else was required for this.

#task T-DB7HX1 WW24 GFC - A0 Bucket Debug @khbyers #status in-progress #eta WW24.5
	#note Husam added injections code 1.5months back, likely CTE issue
	#AR T-YW62FY MCA Agent Injecting IFU Long Stall @khbyers

#task T-5WGW2J WW24_JNC_Bucket_Debug @khbyers #status in-progress #eta WW24.5

#task T-N3BSET Create Thread Mode Log @khbyers #status in-progress #eta WW24.5

#task T-1QHX29 IDQ Ramp plan @gajith @Kushwanth #status in-progress #eta WW 25
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
	#AR T-MV861K present write side checker/ref model @gajith #status in-progress
	#AR T-VSJRCG Study read packets/ ref model idq lsd read side @gajith
	#AR T-XCJR1S present lsd read side ref model/ checker @gajith
	#AR T-FBDJ3N ask for debug buckets/ go through one with niharika @gajith
	#AR T-VXJ5HE present debug bucket @gajith #priority P2

#task T-XZ5H94 Bucket automation/ updates @gajith #status in-progress
	#AR T-13PHJZ Update query to allow for both FM/IDC bucket owners @gajith
	#AR T-E8SXVZ script to parse csv and reassign bucket owners @gajith

