# FIT Val weekly ww16
@aboli
	#project gfc
		!task MCA – Disable IDQ proof assertion failing during XLAT Error #id T-32M9NF
		RTL and FV IDQ assertions failing in XLAT Error tests
			!AR why failing now #eta WW17.1 #status todo #id T-RXDG03
	#project jnc
		!task Review assertions that have not reached a proven status #id T-80YHPA
			#eta ww16.5
		!task Assumptions review updates model TI #id T-KZ23T7
			#status done
		!task Review starvation covers. #status in-progress #id T-VBRGH9
		!task Val Plan development and review #eta ww18 #id T-NVA2A4
			!AR Track number of assertions/assumptions and COI for reporting progress #id T-K6CJ9M
@namratha
	!task IDQ weekly bucket debug #id T-P64EJ5
        !AR fe::Formal::Assert::cex::idq.fv_IDQ_RAT_intf_chk.idq_git_assume_uop_GatherScatter_window*.T_IDQ_RAT_FPV_MRN_Legal_Ld_mrn::gfc-a0
        !AR fe::Formal::Assert::cex::idq.fv_idq_data_transfer._automatic_unique_case_LLJENG::gfc-a0
        !AR fe::Formal::Assert::cex::idq.fv_IDQ_RAT_intf_chk.idq_rat_cover_uop_loop*.T_IDQ_RAT_FPV_No_TMUL_STA_uops_opcode::gfc-a0
        !AR fe::Formal::Assert::cex::idq.fv_idq_global._automatic_unique_case_LLJENG::gfc-a0
        !AR fe::Assert::fv_idq_assume_mite_assert_legal_valid_ordid_range::msid_idq_fv_idq_dsbe_intf_inst_genblk::gfc-a0
	!task SEC IDQ weekly failures to debug #id T-BCFVTR
        !AR fe::Formal::Assert(sec)::cex::idq.idimmCM*H::gfc-a0
        !AR fe::Formal::Assert(sec)::cex::idq.IDBiqWrEnBranchEventM*H::gfc-a0"
	!task Prepare Presentation on IDQ Arch #id T-S19WZV
        !AR IDQ arch - IDQ high level proof stucture #status done
        !AR MRN deep dive ww16
	!task Understand the MRN assertions present in proof and prepare a test plan to add more if needed. #id T-65EF77
        !AR quantitative data to be reported here ETA ww17.2
	!task Cover buckets debug #id T-NPB0VS #status done
	!task JNC bucket debug #id T-6C2FYG
        !AR MRN counter bucket debug fe::Assert::fv_idq_assume_mite_assert_legal_valid_ordid_range::msid_idq_fv_idq_dsbe_intf_inst_genblk::gfc-a0

@Muana
	#project jnc
        !task IFU ramp up
            !AR Study in use bit and array concept
            !AR Study MITE reduction penalty feature #status done
            !AR iTLB
        !task Use GHCP to study fv_VIdPid_chk
            !AR Convert to SMT
	#project gfc
        !task bucket debug
            !AR fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenAndTknErr::gfc-a0
            !AR fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenErr::gfc-a0

@Kushwanth
	#project jnc
        !task IDQ Ramp up #eta ww17
	#project gfc
        !task Enabling Swpf For the fsm #eta ww16
        !task MCA/Parity assertions for GFC #blocked by HSD approval
