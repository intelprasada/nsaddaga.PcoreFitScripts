# FIT Val weekly ww25

#opens

	#passdowns Send calendar invites for all OOO communications to both FM and IDC teams.
	#passdowns Send email reply for any tasks/debugs assigned to you before EOD

#project gfc
@aboli
	#project gfc
	#project jnc
		#task T-63C696 Review starvation covers. #status done
@Namratha
	#task T-C0WWN0 Cover buckets debug
@Muana
	#project jnc
		!task #id T-8V3204 IFU ramp up #status done
			!AR #id T-5DY4HK Study in use bit and array concept #status done
			!AR #id T-XHZD85 Study MITE reduction penalty feature #status done
			!AR #id T-1RMTZT iTLB #eta ww18 #status done
		!task #id T-SYNYEP Use GHCP to study fv_VIdPid_chk #eta ww19 #status done
			!AR #id T-J0DHG8 Convert to SMT #eta ww19 #status done
	#project gfc
		!task #id T-0C8D86 bucket debug #status done
			!AR #id T-V8XDP2 fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenAndTknErr::gfc-a0  #status done
			!AR #id T-191DAC fe::Formal::Assert::cex::bpu.bpsyncs.R_BpNextFLTakenErr::gfc-a0  #status done #eta ww18

@Kushwanth
	#project jnc
		#task T-N1KE10 IDQ Ramp up #eta ww17
	#project gfc
		#task T-ZRGPFZ MCA/Parity assertions for GFC #status blocked by HSD approval
@Kelsey
	#project jnc
@Ragavi
	#project jnc
		#task T-3CF48M ITLB preloader SMT coding and smart preloader #status in-progress
@Gautham
	#project jnc
		!task #id T-WCF4H6 updating coverage for fe_idq_tlm_cov.e for SMT #status done #eta WW25 #priority P0
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
		#task T-BZWKMW IDQ val plan review #eta ww21
@Edwin
	#project jnc
		#task T-R19WPR IFU formal plan review #eta ww26
@Sachin
	#project jnc
		#task T-5R1062 IFU val plan review #eta ww25.5 #status in-progress #priority P0
#task T-P7QS48 Full Code coverage GFC @Gautham #status in-progress #eta 2026-ww19
#task T-X1NSHZ Debug Apar Clock gating model fails @abolisaw #status in-progress #eta 2026-06-05
#task T-EZA452 ThreadMode/ThreadOperate logging @gajith
#task T-NCY35D MS Ramp @abolisaw #status in-progress #eta 2026-06-12
#task T-78A0MT MS SMT coding @abolisaw #status in-progress #eta 2026-06-12 #priority P0
#task T-Z3Z4VD SEC Proof for STSR @abolisaw #eta 2026-06-26
#task T-5WGW2J ww25_JNC_Bucket_Debug @khbyers #status in-progress #eta WW24.5
!task #id T-N3BSET Create Thread Mode Log @khbyers #status done #eta WW24.5

#task T-1QHX29 IDQ Ramp plan @gajith @Kushwanth #status in-progress #eta WW 25
#task T-XZ5H94 Bucket automation/ updates @gajith #status in-progress
#task T-7G2A2V fe_ifu_tlm is under ST_TE? #priority P0 #eta ww25 @sbhattad
#task T-CSEMPN updating BP LSD coverage @gajith #status in-progress
#task T-5HXD31 Bucket Debug #priority P0 #eta fe::Formal::Assert::cex::bpu.fv_bpsyncs_bpu_intf_chk.T_FPV_FE_BPSYNCS_Assume_TakenOfstCmp::gfc-a0
#task T-XXTHFF GFC_B0 Bucket debugs @njammala #status in-progress
#task T-9DSSTX MS EMU debugs @abolisaw #status in-progress #eta WW25.5
!task #id T-KCDNNA GFC a0 paranoia @abolisaw #status done
	#note remove CORE_OR_ABOVE in stsr_iq_intf files

!task #id T-QSV8YA Bucket Debug @mkasongo #priority P0 #eta fe::Formal::Assert::cex::bpu.fv_bpsyncs_bpu_intf_chk.T_FPV_FE_BPSYNCS_Assume_TakenOfstCmp::gfc-a0 #status done

#task T-6Z48Q7 iTLB coverage - thread scope @rnagarat #eta WW26
#task T-YWG1CV DSB flush modelling in iTLB @rnagarat #eta WW25 #status in-progress
#task T-S3HAWY JNC debugs @rnagarat #eta ww25 #status in-progress
#task T-1NW5RS MSID JNC SMT Critical violations model from Michal FV failures to be fixed #priority P0 #eta ww25.5 @abolisaw
#project gfc 
#task T-HJ5GFM fe::EID::1687794::actual_sb_way_is_different_than_exp,::gfc-a0	@sbhattad	Known RTL
#task T-TWJ0PZ fe::EID::1688704::TXTE_MSG_CSP_LOOKUP_OUT_SbAllocReq_mismatch ::gfc-a0	@sbhattad	Known RTL
#task T-1KK4GX fe::EID::1688704::TXTE_MSG_CSP_LOOKUP_OUT_SwPrefReqValid_mismatch ::gfc-a0	@sbhattad	Known RTL
#task T-Z66K31 fe::EID::212406::BPU_LSD_CHECKER:ABORT_M155H_MARKERS_INCONSISTENT::gfc-a0	@nchatla	Known RTL
#task T-X94PF3 fe::Assert::::gfc-a0	@nchatla	active
#task T-B311AW fe::Assert::T_FPV_FE_BPU_BAC_INTF_BPU_Assume_BAC_Assert_IFixedByBacUpdateM110H_underflow::fe_bpu_fv_bpu_bac_intf_chk_inst::gfc-a0	@mkasongo	active
#task T-6H85C6 fe::Assert::T_FPV_FE_BPU_COMMON_Assume_RoNukeT_M907H_BpRSMOClearVM803H_Mutex::fe_bpu_fv_fe_bpu_common_chk::gfc-a0	@mkasongo	active
#task T-096KXX fe::Assert::T_FPV_FE_IFU_ISSUEUOP_FSM_Assume_IfClearStartM123H_NO_PMH_RESPONSE::fe_ifu_fv_ifu_global_inst::gfc-a0	@efmendez	active
#task T-87ZD81 fe::Assert::T_IDQ_RAT_FPV_Relamination_correctness_stimuli_1::msid_idq_fv_IDQ_RAT_intf_chk_inst_idq_rat_assume_uop_loop_genblk::gfc-a0	@nchatla	active
#task T-NCWBS1 Review AI Val plan from Daher @abolisaw #eta ww26.3
