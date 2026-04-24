# FIT Val weekly ww16

#opens
    How often are we syncing main branch into MT1 and vice versa(TD and BU) - Supposed to be daily
@aboli
	#project gfc
        !task MCA – Disable IDQ proof assertion failing during XLAT Error #id T-32M9NF #priority P1 #eta 2026-W18    
    		RTL and FV IDQ assertions failing in XLAT Error tests
			!AR why failing now #eta WW17.5 #status todo #id T-RXDG03
	#project jnc
		!task Review assertions that have not reached a proven status #id T-80YHPA
            #update 2/4 assertions fixed, review pending
			#eta ww17.5
		!task Assumptions review updates model TI #id T-KZ23T7
			#status done
		!task Review starvation covers. #status done #id T-VBRGH9
            !AR converted cover into assertion - found an RTL bug #id T-SG6KJ7
		!task Val Plan development and review #eta ww18 #id T-NVA2A4
			!AR Track number of assertions/assumptions and COI for reporting progress #id T-K6CJ9M
        !task STSR bug fix done in GFC to be done in JNC - IQ bypass CB added #priority low #eta ww18 #id T-TN3JSQ
        !task Added new assertion on outputs with no assertions in GFC  #eta ww18 #id T-27XTX1
            !AR 25 new assertions added - debug in progress #id T-6JKMRF
        !task ARs from reviews to be completed #eta ww19 #id T-QKZERK
        !task Reviewing unconverged covers #id T-GBXPHD
            !AR 0/6 Done #id T-GG11Q4
@Namratha
	!task IDQ weekly bucket debug #id T-P64EJ5
        !AR fe::Formal::Assert::cex::idq.fv_IDQ_RAT_intf_chk.idq_git_assume_uop_GatherScatter_window*.T_IDQ_RAT_FPV_MRN_Legal_Ld_mrn::gfc-a0 #id T-894R39
            #update waiting on OOO resource for updates to RTL
        !AR fe::Formal::Assert::cex::idq.fv_idq_data_transfer._automatic_unique_case_LLJENG::gfc-a0 #id T-P6MCZY
            Input signals is being used as sel of unique case
        !AR fe::Formal::Assert::cex::idq.fv_idq_global._automatic_unique_case_LLJENG::gfc-a0 #id T-4MVG4V
            Input signals is being used as sel of unique case
        !AR fe::Formal::Assert::cex::idq.fv_IDQ_RAT_intf_chk.idq_rat_cover_uop_loop*.T_IDQ_RAT_FPV_No_TMUL_STA_uops_opcode::gfc-a0 #id T-F442W1
            #status wip failing since 12b
            !AR why failing now? #id T-1PF8YC
        !AR fe::Assert::fv_idq_assume_mite_assert_legal_valid_ordid_range::msid_idq_fv_idq_dsbe_intf_inst_genblk::gfc-a0 #id T-17P0K5
            #status wip
	!task SEC IDQ weekly failures to debug #id T-BCFVTR
        !AR fe::Formal::Assert(sec)::cex::idq.idimmCM*H::gfc-a0 #id T-AEAZVE
            #status wip
        !AR fe::Formal::Assert(sec)::cex::idq.IDBiqWrEnBranchEventM*H::gfc-a0" #id T-D9VN76
            #status blocked due to Jasper tool issue
	!task Prepare Presentation on IDQ Arch #id T-S19WZV
        !AR IDQ arch - IDQ high level proof stucture #status done #id T-N9FW7H
        !AR MRN deep dive #eta ww17.3 #id T-TJ5ZWD
	!task Understand the MRN assertions present in proof and prepare a test plan to add more if needed. #id T-65EF77
        !AR quantitative data to be reported here #eta ww17.5 #id T-BT1WQ2
	!task Cover buckets debug #id T-NPB0VS 
        !AR 10 covers unreachable #eta ww19 #id T-X55Y7X
	!task JNC bucket debug #id T-6C2FYG
        !AR MRN counter bucket debug  #id T-B003JJ
            #status done

@Muana
	#project jnc
        !task IFU ramp up #id T-CH2SNW
            !AR Study in use bit and array concept #id T-T9WGAV
            !AR Study MITE reduction penalty feature #status done #id T-PKVKMC
            !AR iTLB #id T-966MYB
        !task Use GHCP to study fv_VIdPid_chk #id T-JKF7V8
            !AR Convert to SMT #id T-YZF501
	#project gfc
        !task bucket debug #id T-NR3JQT
            !AR fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenAndTknErr::gfc-a0 #id T-YYSRQG
            !AR fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenErr::gfc-a0 #id T-ZQV2ZW

@Kushwanth
	#project jnc
        !task IDQ Ramp up #eta ww17 #id T-C83H1K
            !AR issue checker review done #id T-KBTT56
        !task IDQ capsule chk #status done #id T-05X5PP
	#project gfc
        !task Enabling Swpf For the fsm #eta ww16 #id T-WYPS53 #status done
        !task MCA/Parity assertions for GFC #status blocked by HSD approval #id T-8A90E0
        !task PID deallocation check assertion added to fpv and run in simulations - level0 #id T-882R6D
            220/240 failing
            #eta ww17
        !task snp bit being set in wait for compl only state #id T-XKM802
            RTL is resetting the bit on allocaiton, so the assertion is not checking anything.
            !AR review the snp bit functionality - set and reset conditions should have checks #id T-VDNM4E

@Kelsey
	#project jnc
        !task JNC work on MT1/ST1 bring up #id T-JR0RWV
            10/191  SMT MinTE
            119/191 MT1 minTE
            5/191   typ MT1
            5/191   typ SMT
        !task JNC work on moving complex nukes from all thread to thread specific #id T-N7Y617
            #eta ww17
        !task JNC CTE ready for all state transitions mentioned in UCODE HAS #status done #id T-R4BNR3

@Ragavi
	#project jnc
        !task JNC bucket debug #id T-D5E4TS
            !AR ITLB cache miss bucket debug #status done. #id T-F5JFYD
            !AR ITLB MT1 enablement 3 issues #status done #id T-WMSKWK
            !AR ITLB msid mini EID: 1696677 , TXTE_MSG_FILL_BUFFER_OUT_FillBufUBit_mismatch #id T-TQKEH9
                #eta ww17
        !task ITLB preloader SMT coding and smart preloader #id T-MCFW0V
            !AR smt coding #status done #id T-BBAF2K
            !AR smart preloader #status wip #eta ww18.4 #id T-M2Q491
            !AR randomizing TIDs in preload #eta ww21 #id T-EK4G8C
        !task CB for tid based flush feature #status done #id T-MF1AS9
        !task CR for forced partition CTE support #eta ww19 #id T-VP5MRS
            !AR CTE coding #id T-HEQM83
            !AR Integration #id T-HA6VW5
        !task Validation plan for SMT ITLB #eta ww21 #priority high #id T-S9ETQR
            !AR Internal review for val plan #eta ww20 #id T-NC0MVF
        !task Snoop injector  SMT coding #eta ww19 #id T-PJSE9R
            !AR further breakdown of tasks #eta ww18.2 #id T-ANBJ3S

@Gautam
	#hproject jnc
        !task Signal refactoring for iq_tid_CM106L #status wip #id T-W2NA86
        !task updating coverage for fe_idq_tlm_cov.e for SMT #status wip #id T-Y56TD4
        !task predq tracker #status wip #id T-FGN8XX
        !task updating coverage for fe_ifu_tlm_cov.e for SMT #status wip #id T-J12WAA
        !task IDQ Ramp up #status wip #id T-QF0NVB
        !task MRQ injector/preloader #status done #id T-98H8WB
@Yongxi
	#project csk
        !task MRQ (mop recover queue) preloader/injector #status wip #id T-P507P5
            !AR Enable with genfeed #id T-9XMWNK
        !task SVA debug: btb_update_queue_valid_mismatch #status done #id T-SS8G6T
        !task SVA debug: redundancy array multiple hit #status done #id T-WAYKE8
        !task SVA debug: ONEHOT_DECODE_ic_mop_choose_way_mif2h ww15e #status done #id T-VMNHW6
        !task SVA debug: ltt_fetch_from_ip_match ww15e #status done #id T-4R26QD
        !task Extended run for btb duplicate queue feature with Marty’s new implementation #status done #id T-SDZWY7
        !task SVA debug: btb round robin check selected cluster for update mismatch when btb update conflict #id T-32KQ8K
            #status wip
        !task SVA debug: btp update queue valid mismatch #id T-YPQV8Q
            #status wip
        !task SVA debug: mop update data mismatch #id T-G1SZTZ
            #status wip
