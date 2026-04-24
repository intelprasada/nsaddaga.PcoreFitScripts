# FIT Val weekly ww17

#opens
    How often are we syncing main branch into MT1 and vice versa(TD and BU) - Supposed to be daily
@aboli
	#project gfc
        #task T-DVND79 MCA – Disable IDQ proof assertion failing during XLAT Error #priority P1 #eta 2026-W18
    		RTL and FV IDQ assertions failing in XLAT Error tests ok
			#AR T-8XTQ99 why failing now #eta WW17.5 #status todo
	#project jnc
		#task T-S3YSN7 Review assertions that have not reached a proven status
            #update 2/4 assertions fixed, review pending
			#eta ww17.5
		#task T-63C696 Review starvation covers. #status done
            #AR T-TC4QCP converted cover into assertion - found an RTL bug
		#task T-CV97FD Val Plan development and review #eta ww18
			#AR T-5GV7JM Track number of assertions/assumptions and COI for reporting progress
        #task T-TAAGK0 STSR bug fix done in GFC to be done in JNC - IQ bypass CB added #priority low #eta ww18
        #task T-9VNZDM Added new assertion on outputs with no assertions in GFC #eta ww18
            #AR T-H0NNND 25 new assertions added - debug in progress
        #task T-PA0TBS ARs from reviews to be completed #eta ww19
        #task T-FA6GDA Reviewing unconverged covers
            #AR T-ED9XED 0/6 Done
@Namratha
	#task T-MM5T3Z IDQ weekly bucket debug
        #AR T-BVF295 fe::Formal::Assert::cex::idq.fv_IDQ_RAT_intf_chk.idq_git_assume_uop_GatherScatter_window*.T_IDQ_RAT_FPV_MRN_Legal_Ld_mrn::gfc-a0
            #update waiting on OOO resource for updates to RTL
        #AR T-2JDX8G fe::Formal::Assert::cex::idq.fv_idq_data_transfer._automatic_unique_case_LLJENG::gfc-a0
            Input signals is being used as sel of unique case
        #AR T-0VER3C fe::Formal::Assert::cex::idq.fv_idq_global._automatic_unique_case_LLJENG::gfc-a0
            Input signals is being used as sel of unique case
        #AR T-V4J20P fe::Formal::Assert::cex::idq.fv_IDQ_RAT_intf_chk.idq_rat_cover_uop_loop*.T_IDQ_RAT_FPV_No_TMUL_STA_uops_opcode::gfc-a0
            #status wip failing since 12b
            #AR T-8YJP85 why failing now?
        #AR T-KABQ7A fe::Assert::fv_idq_assume_mite_assert_legal_valid_ordid_range::msid_idq_fv_idq_dsbe_intf_inst_genblk::gfc-a0
            #status wip
	#task T-39NW4C SEC IDQ weekly failures to debug
        #AR T-1MW9C8 fe::Formal::Assert(sec)::cex::idq.idimmCM*H::gfc-a0
            #status wip
        #AR T-42QB7M fe::Formal::Assert(sec)::cex::idq.IDBiqWrEnBranchEventM*H::gfc-a0"
            #status blocked due to Jasper tool issue
	#task T-MZ0P9M Prepare Presentation on IDQ Arch
        #AR T-NG1CV5 MRN deep dive #eta ww17.3
	#task T-2BWVK1 Understand the MRN assertions present in proof and prepare a test plan to add more if needed.
        #AR T-K1CFFK quantitative data to be reported here #eta ww17.5
	#task T-C0WWN0 Cover buckets debug
        #AR T-MJ453V 10 covers unreachable #eta ww19
	#task T-800631 JNC bucket debug
@Muana
	#project jnc
        #task T-8V3204 IFU ramp up
            #AR T-5DY4HK Study in use bit and array concept
            #AR T-1RMTZT iTLB
        #task T-SYNYEP Use GHCP to study fv_VIdPid_chk
            #AR T-J0DHG8 Convert to SMT
	#project gfc
        #task T-0C8D86 bucket debug
            #AR T-V8XDP2 fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenAndTknErr::gfc-a0
            #AR T-191DAC fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenErr::gfc-a0

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
        #task T-TWDSX5 JNC work on MT1/ST1 bring up
            10/191  SMT MinTE
            119/191 MT1 minTE
            5/191   typ MT1
            5/191   typ SMT
        #task T-WAHSH6 JNC work on moving complex nukes from all thread to thread specific
            #eta ww17
@Ragavi
	#project jnc
        #task T-CS2X6C JNC bucket debug
            #AR T-RXKF7Q ITLB msid mini EID: 1696677 , TXTE_MSG_FILL_BUFFER_OUT_FillBufUBit_mismatch
                #eta ww17
        #task T-3CF48M ITLB preloader SMT coding and smart preloader
            #AR T-022MGA smart preloader #status wip #eta ww18.4
            #AR T-E32SWW randomizing TIDs in preload #eta ww21
        #task T-EATM79 CR for forced partition CTE support #eta ww19
            #AR T-C2RA2C CTE coding
            #AR T-RBTWDT Integration
        #task T-NWXAKW Validation plan for SMT ITLB #eta ww21 #priority high
            #AR T-RDJYHX Internal review for val plan #eta ww20
        #task T-2953KW Snoop injector SMT coding #eta ww19
            #AR T-XN2WQY further breakdown of tasks #eta ww18.2

@Gautam
	#hproject jnc
        #task T-4PPA0R Signal refactoring for iq_tid_CM106L #status wip
        #task T-WCF4H6 updating coverage for fe_idq_tlm_cov.e for SMT #status wip
        #task T-D3K2BP predq tracker #status wip
        #task T-VREJ1D updating coverage for fe_ifu_tlm_cov.e for SMT #status wip
        #task T-TSRF81 IDQ Ramp up #status wip
@Yongxi
	#project csk
        #task T-JV6MZH MRQ (mop recover queue) preloader/injector #status wip
            #AR T-AA277Z Enable with genfeed
        #task T-AEGYJY SVA debug: btb round robin check selected cluster for update mismatch when btb update conflict
            #status wip
        #task T-NQZ949 SVA debug: btp update queue valid mismatch
            #status wip
        #task T-18XD5R SVA debug: mop update data mismatch
            #status wip
