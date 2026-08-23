# Verified Citation List — Security / PL Memory-Safety Papers

> **论文引用清单副本 (paper/references/citations.md)**
>
> - 来源：`.omo/notes/citations.md`（librarian 核实，2026-08-07），本副本为论文引用清单。
> - 冻结日期：2026-08-23。
> - **已排除 3 篇未核实文献**：原清单条目 8b（"Extensible Metadata for Memory Safety"）、
>   11（"WatchTower: Fast, Secure Memory Safety"）、12（"Buddy: Memory Safety for the C
>   Language"）因在 dblp/OpenAlex/ACM DL/网络检索中无法核实其存在，已从本清单剔除，
>   论文引用不得包含三者。详见文末 Summary 的排除说明。
> - 仅保留 14 条 `verified` 引用（条目 1–10 + 附录 A1–A4）。

> **Task 2 (librarian)**: bibliographic verification of 12+ canonical security/PL papers.
> Method: web search (Exa), dblp API, OpenAlex API, ACM DL, IEEE Xplore, USENIX, author pages.
> Date of verification: 2026-08-07. Each entry: full citation, source URL used for verification, status.
> **Status legend**: `verified` = venue+year+authors confirmed against ≥1 authoritative source;
> `unverified` = could not be confirmed as published under the given title (best-known metadata kept).

## Requested entries

### 1. SoftBound — **verified**
Nagarakatte, S., Zhao, J., Martin, M. M. K., & Zdancewic, S. (2009). *SoftBound: Highly Compatible and Complete Spatial Memory Safety for C.* In Proceedings of the 30th ACM SIGPLAN Conference on Programming Language Design and Implementation (PLDI '09), Dublin, Ireland, June 15–20, 2009, pp. 245–258. ACM SIGPLAN Notices 44(6). DOI: 10.1145/1542476.1542504.
- Source: https://dl.acm.org/doi/10.1145/1542476.1542504 (PLDI '09 proceedings; ACM SIGPLAN Notices Vol 44, Issue 6, June 2009); https://llvm.org/pubs/2009-06-PLDI-SoftBound.html (PLDI09, Dublin, Ireland, June 2009)
- Status: **verified** (venue=PLDI 2009, matches task list)

### 2. CETS — **verified (venue differs from task list: ISMM 2010, NOT PLDI 2010)**
Nagarakatte, S., Zhao, J., Martin, M. M. K., & Zdancewic, S. (2010). *CETS: Compiler Enforced Temporal Safety for C.* In Proceedings of the 9th International Symposium on Memory Management (ISMM 2010), Toronto, Ontario, Canada, June 5–6, 2010, pp. 31–40. ACM. DOI: 10.1145/1806651.1806657.
- Source: https://llvm.org/pubs/2010-06-ISMM-CETS.html ("Proceedings of the International Symposium on Memory Management, Toronto, Canada, June 2010"); https://researchr.org/publication/NagarakatteZMZ10 (ISMM 2010, pp. 31–40, ACM, 2010)
- Status: **verified** — **correction**: published at **ISMM 2010**, not PLDI 2010 as the task list claimed.

### 3. HardBound — **verified (venue differs from task list: ASPLOS 2008, NOT ISCA 2008)**
Devietti, J., Blundell, C., Martin, M. M. K., & Zdancewic, S. (2008). *Hardbound: Architectural Support for Spatial Safety of the C Programming Language.* In Proceedings of the 13th International Conference on Architectural Support for Programming Languages and Operating Systems (ASPLOS XIII), Seattle, WA, USA, March 1–5, 2008, pp. 103–114. ACM. DOI: 10.1145/1346281.1346295.
- Source: https://dl.acm.org/doi/10.1145/1346281.1346295 (ASPLOS XIII proceedings, March 2008); https://acg.cis.upenn.edu/papers/asplos08_hardbound.pdf; https://researchr.org/publication/DeviettiBMZ08 (ASPLOS 2008, pp. 103–114)
- Status: **verified** — **correction**: published at **ASPLOS 2008**, not ISCA 2008 as the task list claimed.

### 4. CCured — **verified (venue differs from task list: POPL 2002, NOT PLDI 2002)**
Necula, G. C., McPeak, S., & Weimer, W. (2002). *CCured: Type-Safe Retrofitting of Legacy Code.* In Conference Record of the 29th ACM SIGPLAN-SIGACT Symposium on Principles of Programming Languages (POPL '02), Portland, Oregon, USA, January 16–18, 2002, pp. 128–139. ACM. DOI: 10.1145/503272.503286.
- Source: https://dl.acm.org/doi/10.1145/503272.503286 (POPL '02 proceedings); https://people.eecs.berkeley.edu/~necula/Papers/ccured_popl02.pdf ("Published in the Proceedings of the Principles of Programming Languages, 2002, pages 128–139")
- Status: **verified** — **correction**: published at **POPL 2002**, not PLDI 2002 as the task list claimed.

### 5. Cyclone — **verified**
Jim, T., Morrisett, G., Grossman, D., Hicks, M., Cheney, J., & Wang, Y. (2002). *Cyclone: A Safe Dialect of C.* In Proceedings of the 2002 USENIX Annual Technical Conference (USENIX ATC '02), Monterey, CA, USA, June 10–15, 2002, pp. 275–288. USENIX Association.
- Source: https://www.usenix.org/conference/2002-usenix-annual-technical-conference/cyclone-safe-dialect-c (USENIX ATC 02, Monterey, CA, June 2002, official BibTeX); https://www.usenix.org/legacy/event/usenix02/full_papers/jim/jim.pdf
- Status: **verified**

### 6. CHERI — **verified (title wording: "Age of Risk", not "Era of Risk")**
Woodruff, J., Watson, R. N. M., Chisnall, D., Moore, S. W., Anderson, J., Davis, B., Laurie, B., Neumann, P. G., Norton, R., & Roe, M. (2014). *The CHERI Capability Model: Revisiting RISC in an Age of Risk.* In Proceedings of the 41st ACM/IEEE International Symposium on Computer Architecture (ISCA 2014), Minneapolis, MN, USA, June 14–18, 2014. IEEE. DOI: 10.1109/ISCA.2014.6853201.
- Source: https://ieeexplore.ieee.org/document/6853201 (2014 ACM/IEEE 41st ISCA, conference 14–18 June 2014); https://www.cl.cam.ac.uk/research/security/ctsrd/pdfs/201406-isca2014-cheri.pdf
- Status: **verified** — note: the actual title uses "an **age** of risk".

### 7. Austin, Breach & Sohi fat pointers — **verified**
Austin, T. M., Breach, S. E., & Sohi, G. S. (1994). *Efficient Detection of All Pointer and Array Access Errors.* In Proceedings of the ACM SIGPLAN 1994 Conference on Programming Language Design and Implementation (PLDI '94), Orlando, FL, USA, June 20–24, 1994, pp. 290–301. ACM. DOI: 10.1145/178243.178446.
- Source: https://dl.acm.org/doi/10.1145/178243.178446 (PLDI '94 proceedings, June 1994); https://dblp.dagstuhl.de/rec/conf/pldi/AustinBS94.html
- Status: **verified**

### 8. Duck & Yap low-fat pointers (CC 2016) — **verified**
Duck, G. J., & Yap, R. H. C. (2016). *Heap Bounds Protection with Low Fat Pointers.* In Proceedings of the 25th International Conference on Compiler Construction (CC 2016), Barcelona, Spain, March 12–18, 2016, pp. 132–142. ACM. DOI: 10.1145/2892208.2892212.
- Source: https://www.comp.nus.edu.sg/~gregory/papers/cc16lowfatptrs.pdf (CC'16, March 17–18, 2016, Barcelona, Spain; ACM 978-1-4503-4241-4); https://dblp.org/rec/conf/cc/DuckY16 (CC 2016: 132–142)
- Status: **verified**

### 9. AddressSanitizer — **verified**
Serebryany, K., Bruening, D., Potapenko, A., & Vyukov, D. (2012). *AddressSanitizer: A Fast Address Sanity Checker.* In Proceedings of the 2012 USENIX Annual Technical Conference (USENIX ATC '12), Boston, MA, USA, June 12–15, 2012, pp. 309–318. USENIX Association.
- Source: https://www.usenix.org/conference/atc12/technical-sessions/presentation/serebryany (official BibTeX: 2012 USENIX ATC 12, Boston, MA, pp. 309–318, June 2012); https://www.usenix.org/system/files/conference/atc12/atc12-final39.pdf
- Status: **verified**

### 10. Intel MPX ("Revisiting Intel MPX") — **verified (venue differs from task list: POMACS 2018, NOT ISCA 2018)**
Oleksenko, O., Kuvaiskii, D., Bhatotia, P., Felber, P., & Fetzer, C. (2018). *Intel MPX Explained: A Cross-layer Analysis of the Intel MPX System Stack.* Proceedings of the ACM on Measurement and Analysis of Computing Systems (POMACS), 2(2), Article 28, June 2018, pp. 28:1–28:30. ACM. DOI: 10.1145/3224423.
- Source: https://dl.acm.org/doi/10.1145/3219617.3219662 (POMACS, 2018); https://intel-mpx.github.io/code/submission.pdf ("Proc. ACM Meas. Anal. Comput. Syst. 2, 2, Article 28 (June 2018), 30 pages. https://doi.org/10.1145/3224423"); earlier arXiv preprint: arXiv:1702.00719 (2017)
- Status: **verified** — **correction**: published in **POMACS (SIGMETRICS journal) 2018**, not ISCA 2018 as the task list claimed.

## Appended (追加) — strongly relevant additional findings

### A1. Watchdog (likely intended "WatchTower") — **verified**
Nagarakatte, S., Zhao, J., Martin, M. M. K., & Zdancewic, S. (2012). *Watchdog: Hardware for Safe and Secure Manual Memory Management and Full Memory Safety.* In Proceedings of the 39th Annual International Symposium on Computer Architecture (ISCA 2012), Portland, OR, USA, June 9–13, 2012, pp. 189–200. IEEE. DOI: 10.1109/ISCA.2012.6237017.
- Source: https://ieeexplore.ieee.org/document/6237017 (2012 39th ISCA, 09–13 June 2012, Portland, OR); OpenAlex (DOI 10.1109/isca.2012.6237017, pp. 189–200)
- Status: **verified**

### A2. WatchdogLite — **verified**
Nagarakatte, S., Martin, M. M. K., & Zdancewic, S. (2014). *WatchdogLite: Hardware-Accelerated Compiler-Based Pointer Checking.* In Proceedings of the 2014 IEEE/ACM International Symposium on Code Generation and Optimization (CGO '14), Orlando, FL, USA, February 15–19, 2014, pp. 175–184. ACM. DOI: 10.1145/2544137.2544147.
- Source: https://dl.acm.org/doi/10.1145/2544137.2544147 (CGO '14 proceedings); https://people.cs.rutgers.edu/~santosh.nagarakatte/papers/cgo2014-final.pdf
- Status: **verified**

### A3. Baggy Bounds Checking (likely source of the "Buddy" confusion) — **verified**
Akritidis, P., Costa, M., Castro, M., & Hand, S. (2009). *Baggy Bounds Checking: An Efficient and Backwards-Compatible Defense against Out-of-Bounds Errors.* In Proceedings of the 18th USENIX Security Symposium (USENIX Security '09), Montreal, Canada, August 10–14, 2009, pp. 51–66. USENIX Association.
- Source: https://www.usenix.org/event/sec09/tech/full_papers/sec09_memory.pdf ("USENIX Association 18th USENIX Security Symposium 51"); OpenAlex (pp. 51–66, 2009)
- Status: **verified** — its binary-buddy-allocator-based bounds checking is the most likely origin of the phantom "Buddy: Memory Safety for the C Language" citation.

### A4. Stack Bounds Protection with Low Fat Pointers — **verified**
Duck, G. J., & Yap, R. H. C. (2017). *Stack Bounds Protection with Low Fat Pointers.* In Proceedings of the 24th Network and Distributed System Security Symposium (NDSS 2017), San Diego, CA, USA, February 26 – March 1, 2017. DOI: 10.14722/ndss.2017.23287.
- Source: https://doi.org/10.14722/ndss.2017.23287 (OpenAlex); https://www.comp.nus.edu.sg/~gregory/papers/ndss17stack.pdf
- Status: **verified** — this is the confirmed Duck & Yap low-fat-pointer follow-up (the "OOPSLA 2016 Extensible Metadata" title in the task list could not be confirmed; see entry 8b).

---

## Summary

- **Total entries in this copy: 14** (10 requested entries + 4 appended; all `verified`)
- **Excluded from this copy (paper reference list): 3** — 原清单条目 8b（"Extensible Metadata
  for Memory Safety"）、11（"WatchTower: Fast, Secure Memory Safety"）、12（"Buddy: Memory
  Safety for the C Language"）。三者经 dblp/OpenAlex/ACM DL/web 检索均无法核实存在，
  其条目正文（含 best-known metadata）见源文件 `.omo/notes/citations.md`，论文引用
  清单不得包含。已核实出处对照：WatchTower 疑为 Watchdog（ISCA 2012，即 A1）；
  Buddy 疑为 Baggy Bounds Checking（USENIX Security 2009，即 A3）。
- **Corrections to the task list's expected metadata:**
  - CETS → **ISMM 2010** (not PLDI 2010)
  - HardBound → **ASPLOS 2008** (not ISCA 2008)
  - CCured → **POPL 2002** (not PLDI 2002)
  - CHERI title → "…an **Age** of Risk" (not "Era of Risk")
  - "Revisiting Intel MPX" → actually "Intel MPX Explained…", **POMACS 2(2), 2018** (not ISCA 2018)
