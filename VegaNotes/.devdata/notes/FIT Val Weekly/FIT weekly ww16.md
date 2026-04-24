# FIT Val weekly ww16

#opens
    How often are we syncing main branch into MT1 and vice versa(TD and BU) - Supposed to be daily
@aboli
	#project gfc
        !task #id T-DVND79 MCA – Disable IDQ proof assertion failing during XLAT Error #priority P1 #eta 2026-W18    
    		RTL and FV IDQ assertions failing in XLAT Error tests ok
			!AR #id T-8XTQ99 why failing now #eta WW17.5 #status todo 
	#project jnc
		!task #id T-S3YSN7 Review assertions that have not reached a proven status 
            #update 2/4 assertions fixed, review pending
			#eta ww17.5
		!task #id T-ARYRJN Assumptions review updates model TI 
			#status done
		!task #id T-63C696 Review starvation covers. #status done 
            !AR #id T-TC4QCP converted cover into assertion - found an RTL bug 
		!task #id T-CV97FD Val Plan development and review #eta ww18 
			!AR #id T-5GV7JM Track number of assertions/assumptions and COI for reporting progress 
        !task #id T-TAAGK0 STSR bug fix done in GFC to be done in JNC - IQ bypass CB added #priority low #eta ww18 
        !task #id T-9VNZDM Added new assertion on outputs with no assertions in GFC  #eta ww18 
            !AR #id T-H0NNND 25 new assertions added - debug in progress 
        !task #id T-PA0TBS ARs from reviews to be completed #eta ww19 
        !task #id T-FA6GDA Reviewing unconverged covers 
            !AR #id T-ED9XED 0/6 Done 
@Namratha
	!task #id T-MM5T3Z IDQ weekly bucket debug 
        !AR #id T-BVF295 fe::Formal::Assert::cex::idq.fv_IDQ_RAT_intf_chk.idq_git_assume_uop_GatherScatter_window*.T_IDQ_RAT_FPV_MRN_Legal_Ld_mrn::gfc-a0 
            #update waiting on OOO resource for updates to RTL
        !AR #id T-2JDX8G fe::Formal::Assert::cex::idq.fv_idq_data_transfer._automatic_unique_case_LLJENG::gfc-a0 
            Input signals is being used as sel of unique case
        !AR #id T-0VER3C fe::Formal::Assert::cex::idq.fv_idq_global._automatic_unique_case_LLJENG::gfc-a0 
            Input signals is being used as sel of unique case
        !AR #id T-V4J20P fe::Formal::Assert::cex::idq.fv_IDQ_RAT_intf_chk.idq_rat_cover_uop_loop*.T_IDQ_RAT_FPV_No_TMUL_STA_uops_opcode::gfc-a0 
            #status wip failing since 12b
            !AR #id T-8YJP85 why failing now? 
        !AR #id T-KABQ7A fe::Assert::fv_idq_assume_mite_assert_legal_valid_ordid_range::msid_idq_fv_idq_dsbe_intf_inst_genblk::gfc-a0 
            #status wip
	!task #id T-39NW4C SEC IDQ weekly failures to debug 
        !AR #id T-1MW9C8 fe::Formal::Assert(sec)::cex::idq.idimmCM*H::gfc-a0 
            #status wip
        !AR #id T-42QB7M fe::Formal::Assert(sec)::cex::idq.IDBiqWrEnBranchEventM*H::gfc-a0" 
            #status blocked due to Jasper tool issue
	!task #id T-MZ0P9M Prepare Presentation on IDQ Arch 
        !AR #id T-FVJTR5 IDQ arch - IDQ high level proof stucture #status done 
        !AR #id T-NG1CV5 MRN deep dive #eta ww17.3 
	!task #id T-2BWVK1 Understand the MRN assertions present in proof and prepare a test plan to add more if needed. 
        !AR #id T-K1CFFK quantitative data to be reported here #eta ww17.5 
	!task #id T-C0WWN0 Cover buckets debug 
        !AR #id T-MJ453V 10 covers unreachable #eta ww19 
	!task #id T-800631 JNC bucket debug 
        !AR #id T-W7M4KZ MRN counter bucket debug  
            #status done

@Muana
	#project jnc
        !task #id T-8V3204 IFU ramp up 
            !AR #id T-5DY4HK Study in use bit and array concept 
            !AR #id T-XHZD85 Study MITE reduction penalty feature #status done 
            !AR #id T-1RMTZT iTLB 
        !task #id T-SYNYEP Use GHCP to study fv_VIdPid_chk 
            !AR #id T-J0DHG8 Convert to SMT 
	#project gfc
        !task #id T-0C8D86 bucket debug 
            !AR #id T-V8XDP2 fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenAndTknErr::gfc-a0 
            !AR #id T-191DAC fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenErr::gfc-a0 

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
        !task #id T-TWDSX5 JNC work on MT1/ST1 bring up 
            10/191  SMT MinTE
            119/191 MT1 minTE
            5/191   typ MT1
            5/191   typ SMT
        !task #id T-WAHSH6 JNC work on moving complex nukes from all thread to thread specific 
            #eta ww17
        !task #id T-1Q99SZ JNC CTE ready for all state transitions mentioned in UCODE HAS #status done 

@Ragavi
	#project jnc
        !task #id T-CS2X6C JNC bucket debug 
            !AR #id T-PC96Y9 ITLB cache miss bucket debug #status done. 
            !AR #id T-V5ZQCR ITLB MT1 enablement 3 issues #status done 
            !AR #id T-RXKF7Q ITLB msid mini EID: 1696677 , TXTE_MSG_FILL_BUFFER_OUT_FillBufUBit_mismatch 
                #eta ww17
        !task #id T-3CF48M ITLB preloader SMT coding and smart preloader 
            !AR #id T-WZW8XS smt coding #status done 
            !AR #id T-022MGA smart preloader #status wip #eta ww18.4 
            !AR #id T-E32SWW randomizing TIDs in preload #eta ww21 
        !task #id T-A7KC90 CB for tid based flush feature #status done 
        !task #id T-EATM79 CR for forced partition CTE support #eta ww19 
            !AR #id T-C2RA2C CTE coding 
            !AR #id T-RBTWDT Integration 
        !task #id T-NWXAKW Validation plan for SMT ITLB #eta ww21 #priority high 
            !AR #id T-RDJYHX Internal review for val plan #eta ww20 
        !task #id T-2953KW Snoop injector  SMT coding #eta ww19 
            !AR #id T-XN2WQY further breakdown of tasks #eta ww18.2 

@Gautam
	#hproject jnc
        !task #id T-4PPA0R Signal refactoring for iq_tid_CM106L #status wip 
        !task #id T-WCF4H6 updating coverage for fe_idq_tlm_cov.e for SMT #status wip 
        !task #id T-D3K2BP predq tracker #status wip 
        !task #id T-VREJ1D updating coverage for fe_ifu_tlm_cov.e for SMT #status wip 
        !task #id T-TSRF81 IDQ Ramp up #status wip 
        !task #id T-RTGNR0 MRQ injector/preloader #status done 
@Yongxi
	#project csk
        !task #id T-JV6MZH MRQ (mop recover queue) preloader/injector #status wip 
            !AR #id T-AA277Z Enable with genfeed 
        !task #id T-MVTK3J SVA debug: btb_update_queue_valid_mismatch #status done 
        !task #id T-7DWZ43 SVA debug: redundancy array multiple hit #status done 
        !task #id T-8QA47X SVA debug: ONEHOT_DECODE_ic_mop_choose_way_mif2h ww15e #status done 
        !task #id T-VXA4K2 SVA debug: ltt_fetch_from_ip_match ww15e #status done 
        !task #id T-0XNJTM Extended run for btb duplicate queue feature with Marty’s new implementation #status done 
        !task #id T-AEGYJY SVA debug: btb round robin check selected cluster for update mismatch when btb update conflict 
            #status wip
        !task #id T-NQZ949 SVA debug: btp update queue valid mismatch 
            #status wip
        !task #id T-18XD5R SVA debug: mop update data mismatch 
            #status wip
