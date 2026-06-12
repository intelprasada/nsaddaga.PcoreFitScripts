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
		#task T-63C696 Review starvation covers. #status in-progress
@Namratha
	!task #id T-39NW4C SEC IDQ weekly failures to debug #status done
		!AR #id T-1MW9C8 fe::Formal::Assert(sec)::cex::idq.idimmCM*H::gfc-a0 #status done
		!AR #id T-42QB7M fe::Formal::Assert(sec)::cex::idq.IDBiqWrEnBranchEventM*H::gfc-a0" #status done
			due to Jasper tool issue
	!task #id T-MZ0P9M Prepare Presentation on IDQ Arch #status done
		!AR #id T-FVJTR5 IDQ arch - IDQ high level proof stucture #status done
		!AR #id T-NG1CV5 MRN deep dive #eta ww17.3 #status done
	#task T-C0WWN0 Cover buckets debug
@Muana
	#project jnc
		#task T-8V3204 IFU ramp up #status in-progress
	#project gfc
		#task T-0C8D86 bucket debug #status in-progress
@Kushwanth
	#project jnc
		#task T-N1KE10 IDQ Ramp up #eta ww17
	#project gfc
		!task #id T-A33BGA Enabling Swpf For the fsm #eta ww16  #status done
		#task T-ZRGPFZ MCA/Parity assertions for GFC #status blocked by HSD approval
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
		#task T-3CF48M ITLB preloader SMT coding and smart preloader #status in-progress
@Gautham
	#project jnc
		!task #id T-4PPA0R Signal refactoring for iq_tid_CM106L #status done
		#task T-WCF4H6 updating coverage for fe_idq_tlm_cov.e for SMT #status wip #eta WW25 #priority P0
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
		#task T-BZWKMW IDQ val plan review #eta ww21
@Edwin
	#project jnc
		#task T-R19WPR IFU formal plan review #eta ww21
@Sachin
	#project jnc
		#task T-5R1062 IFU val plan review #eta ww21 #status in-progress
#task T-P7QS48 Full Code coverage GFC @Gautham #status in-progress #eta 2026-ww19
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

#task T-X1NSHZ Debug Apar Clock gating model fails @abolisaw #status in-progress #eta 2026-06-05
#task T-EZA452 ThreadMode/ThreadOperate logging @gajith
#task T-NCY35D MS Ramp @abolisaw #status in-progress #eta 2026-06-12
#task T-78A0MT MS SMT coding @abolisaw #status in-progress #eta 2026-06-12 #priority P0
!task #id T-TJC2BC New Thread Hang assertion @abolisaw #status done
	#note added new assertion, after multiple debugs and reviews, ready to TI

!task #id T-RJ7269 GFC A0 FV Paranoia Tasks @njammala #status done

!task #id T-QPX0D6 JNC - CTE support from fe_test for PEBs init to both threads @khbyers #priority P1 #eta W22 ww22 #status done
	#note #jnc
	#note #jnc

!task #id T-RMQ98B JNC - CTE fix for split alloc stall stress for delta between MT vs ST modes @khbyers #priority P0 #eta W22 ww22 #status done

!task #id T-4CGN9Y JNC - MJEU Issue with JeClear & MoNuke at Same Time @khbyers #priority P0 #eta W22 ww22 #status done


#task T-JVQYB7 JNC - CTE Infra Valplan @khbyers #priority P0 #eta ww24.3 #status in-progress
!task #id T-4MRVAX JNC - Sar Violation SMT Bucket @khbyers #priority P0 #status done #eta W21
	#note MJEU needing to support two FFEIP on different threads at same time

!task #id T-PR3BCP JNC - Thread Hang SMT Bucket @khbyers #priority P0 #eta W21 #status done
	#note Root cause issue in MS due to missing threading on pending load loopcnt

!task #id T-JYG7V8 JNC - Uop Checker UIP SMT Bucket @khbyers #priority P0 #eta W21 #status done
	#note Root cause MS issue on missing threading on stallclear

!task #id T-Z0P4P4 JNC - Thread Hang SMT Bucket CTE Issue @khbyers #priority P0 #status done #eta W21
	#note root cause to CTE issue in MJEU logic

#task T-P9AYEF GFC - Qa/Qb Immediate Gating Uop Checker Support @khbyers
!task #id T-DVDE14 WW22_JNC_Bucket_Debug @khbyers #status done
	!AR #id T-25RDYP UIP Mismatch, MS threading issue on stallclear @khbyers #status done
	!AR #id T-Q3PT59 ROB_EMU_IFU_ADDRESS, IFU RTL Poison Issue @khbyers #status done
	!AR #id T-QJPAA5 Core Debug BIQID Mismatch Causing Hang @khbyers #status done

#task T-DDPYQY WW23_JNC_Bucket_Debug @khbyers #eta 2026-ww23 #status in-progress
!task #id T-66WRAH Enable MBB Agent in MT @khbyers #status done

#task T-Z3Z4VD SEC Proof for STSR @abolisaw #eta 2026-06-26
!task #id T-W3J83X GFC a0 Paranoia @abolisaw #status done #eta ww24.1
	#note 2 STSR assertions to check, one is in FPV_RESTRICT need to check why and other has a wrong format
	#note 1: this fails a lot in simulation. Edwin suggested it can be removed if assume was not used,I checked and proof is not affected, so can be removed, Chen suggested to run coi_proof too, need to run that and make the final finishes.
	#note 2. STSR_Assume_DSBE_Assert_if_dsFirstVec_than_dsbqtid_and_DSBOwnership
	#note Assume properties should not be in interface files, if needed it should be in a different format. This was flagged for using assume property but when I checked this is assert property itself in interface file. have sent analysis to Daher and chen, waiting to see if anything else was required for this.

#task T-DB7HX1 WW24 GFC - A0 Bucket Debug @khbyers #status in-progress #eta WW24.5
#task T-5WGW2J WW24_JNC_Bucket_Debug @khbyers #status in-progress #eta WW24.5
#task T-N3BSET Create Thread Mode Log @khbyers #status in-progress #eta WW24.5
#task T-1QHX29 IDQ Ramp plan @gajith @Kushwanth #status in-progress #eta WW 25
#task T-XZ5H94 Bucket automation/ updates @gajith #status in-progress
!task #id T-EWGPDY Cover property clean up in GFC @njammala #status done

#task T-MW39KD GFC Bucket Debug @njammala #eta WW25.2
