"""FIM 항로 스케줄 모니터 — 실행: streamlit run app.py"""
from datetime import datetime, timedelta
import importlib
from pathlib import Path

import pandas as pd
import streamlit as st

import fim_schedule as fs
importlib.reload(fs)

SNAP_DIR = Path(__file__).parent / "snapshots"
SNAP_DIR.mkdir(exist_ok=True)
fs.normalize_snapshot_filenames(SNAP_DIR)

st.set_page_config(page_title="FIM 스케줄 모니터", layout="wide")

TODAY = datetime.now().date()
DEFAULT_FROM = TODAY - timedelta(days=145)
DEFAULT_TO = TODAY + timedelta(days=92)


@st.cache_data
def load(path, mtime, _ver=3):  # mtime: 파일이 바뀌면 캐시 무효화
    return fs.parse_html(path)


# ── 사이드바: 자동 크롤링 & 스냅샷 관리 ──────────────────────────────
with st.sidebar:
    st.header("🔄 HMM 자동 크롤링")
    st.caption("A: FIM 항로(Loop) 전체 표 수집  \nB: 'by Vessel Name' 탭에서 모선별 ETA·ETB·ETD 수집")
    d_col1, d_col2 = st.columns(2)
    sb_from = d_col1.date_input("시작 날짜", value=st.session_state.get("crawl_from", DEFAULT_FROM), key="sb_from_dt")
    sb_to = d_col2.date_input("끝 날짜", value=st.session_state.get("crawl_to", DEFAULT_TO), key="sb_to_dt")
    st.session_state["crawl_from"] = sb_from
    st.session_state["crawl_to"] = sb_to

    col_a, col_b = st.columns(2)
    run_a = col_a.button("1️⃣ 방식 A\n(Loop만)", use_container_width=True, help="Long-term Schedule by Loop (FIM) 표만 빠르게 가져옵니다.")
    run_ab = col_b.button("2️⃣ 방식 A+B\n(Loop+ETA)", type="primary", use_container_width=True, help="FIM 항로 표 조회 후 'by Vessel Name' 탭에서 모선별 ETA·ETB·ETD를 모두 크롤링합니다.")
    run_vsl_only = st.button("🚢 현재 스냅샷 모선별 ETA·ETB·ETD 크롤링\n(by Vessel Name 탭)", use_container_width=True, help="ScheduleMainPost.do 의 'by Vessel Name' 탭에서 현재 스냅샷 모선들의 ETA·ETB·ETD를 크롤링하여 반영합니다.")

    if run_a or run_ab:
        if sb_from > sb_to:
            st.error("시작 날짜는 끝 날짜보다 늦을 수 없습니다.")
        else:
            prog = st.progress(0, text="크롤링 준비 중...")
            try:
                saved_path = fs.crawl_hmm_schedule(
                    loop_code="FIM",
                    include_vessel_eta=bool(run_ab),
                    save_dir=SNAP_DIR,
                    on_progress=lambda step, total, msg: prog.progress(min(int(step * 100 / max(total, 1)), 100), text=msg),
                    date_from=sb_from,
                    date_to=sb_to,
                )
                st.cache_data.clear()
                st.success(f"크롤링 및 저장 완료: {saved_path.name}")
                st.rerun()
            except Exception as exc:
                st.error(f"크롤링 실패: {exc}")

    st.divider()
    st.header("스냅샷")
    st.caption("HMM e-Service › 스케쥴 › Long-term Schedule by Loop › FIM 조회 후 Ctrl+S 로 저장한 HTML")
    for up in st.file_uploader("HTML 추가", type=["html", "htm"], accept_multiple_files=True) or []:
        target = SNAP_DIR / up.name
        if not target.exists():
            target.write_bytes(up.getvalue())
            st.success(f"저장: {up.name}")

    files = sorted(SNAP_DIR.glob("*.htm*"), key=fs.snapshot_date, reverse=True)
    if not files:
        st.info("snapshots 폴더에 HTML 파일을 추가하세요.")
        st.stop()
    label = lambda p: f"{fs.snapshot_date(p):%Y-%m-%d} · {p.name}"
    cur = st.selectbox("기준 스냅샷", files, format_func=label)
    others = [f for f in files if f != cur]
    prev = st.selectbox("비교 스냅샷", [None] + others, index=1 if others else 0,
                        format_func=lambda p: "비교 안 함" if p is None else label(p))

    if run_vsl_only:
        v_prog = st.progress(0, text="by Vessel Name 크롤링 준비 중...")
        try:
            updated_path, v_df = fs.crawl_by_vessel_name(
                target_snapshot=cur,
                save_dir=SNAP_DIR,
                on_progress=lambda step, total, msg: v_prog.progress(min(int(step * 100 / max(total, 1)), 100), text=msg),
                update_etb_etd=True,
            )
            st.session_state["last_crawled_vessel_df"] = v_df
            st.cache_data.clear()
            st.success(f"모선별 ETA·ETB·ETD 반영 완료: {cur.name} ({len(v_df)}건)")
            st.rerun()
        except Exception as exc:
            st.error(f"모선별 크롤링 실패: {exc}")

df = load(str(cur), cur.stat().st_mtime)
status = fs.vessel_status(df)
status["구분"] = status["state"].str.replace(r" \(.*\)$", "", regex=True)
changes = fs.diff_snapshots(load(str(prev), prev.stat().st_mtime), df) if prev else None
if changes is not None:
    changes["hours"] = pd.to_numeric(changes["hours"], errors="coerce").astype("float64")

st.title("FIM 항로 스케줄 모니터")
st.caption(f"Far East India Mediterranean [FIM] · 기준 {fs.snapshot_date(cur):%Y-%m-%d}")

if changes is not None:
    if changes.empty:
        st.success(f"{label(prev)} 대비 변경 없음")
    else:
        n = changes["field"].value_counts()
        big = changes[changes["hours"].abs() >= 12][["vessel", "voyage", "seq"]].drop_duplicates()
        st.warning(f"**{label(prev)}** 대비 변경 {len(changes)}건 · 12h 이상 변동 기항 {len(big)}건 · "
                   f"SKIP 변경 {n.get('SKIP', 0)}건 · 기항 추가/삭제 {n.get('기항', 0)}건")

c = st.columns(4)
c[0].metric("항해 중", int((status["구분"] == "항해 중").sum()))
c[1].metric("재항 중", int((status["구분"] == "재항 중").sum()))
c[2].metric("출항 전 항차", int((status["구분"] == "출항 전").sum()))
c[3].metric("변경 건수", "-" if changes is None else len(changes))

STATUS_COLOR = {"actual": "background-color:#d9f2e3", "coastal": "background-color:#fff4cc"}

tab1, tab2, tab3, tab4, tab5, tab_crawl = st.tabs([
    "선박 현황", "스케줄", "변경 내역", "입력용 (CIX2)", "코드 관리", "🔄 자동 크롤링"
])

all_voyages = list(dict.fromkeys(zip(df["vessel"], df["voyage"])))

with tab1:
    kinds = ["항해 중", "재항 중", "출항 전", "항차 종료"]
    pick = st.multiselect("상태", kinds, default=kinds)
    view = status[status["구분"].isin(pick)].copy()
    if changes is not None:
        chg_cnt = changes.groupby(["vessel", "voyage"]).size()
        view["변경"] = [
            f"변경 {chg_cnt.get((r.vessel, r.voyage), 0)}건"
            if chg_cnt.get((r.vessel, r.voyage), 0) > 0 else "변경사항 없음"
            for r in view.itertuples()
        ]
    st.dataframe(
        view.drop(columns="구분").rename(columns={
            "vessel": "선박", "voyage": "항차", "state": "상태", "last_port": "최근 실적 항구",
            "last_event": "최근 실적", "next_port": "다음 기항지", "next_etb": "다음 ETB",
            "next_status": "ETB 구분", "skips": "SKIP"}),
        hide_index=True, width="stretch",
        column_config={"다음 ETB": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")})

with tab2:
    a, b = st.columns(2)
    vs = a.multiselect("선박", list(dict.fromkeys(df["vessel"])))
    ps = b.multiselect("항구", list(dict.fromkeys(df["port"])))
    sched = df[df["etb"].notna() | df["skip"]]
    if vs:
        sched = sched[sched["vessel"].isin(vs)]
    if ps:
        sched = sched[sched["port"].isin(ps)]
    st_cols = [c for c in ("eta_status", "etb_status", "etd_status") if c in sched.columns]
    dt_fmt = {c: "{:%Y-%m-%d %H:%M}" for c in ("eta", "etb", "etd") if c in sched.columns}
    st.dataframe(
        sched.style.map(lambda v: STATUS_COLOR.get(v, ""), subset=st_cols)
                   .format(dt_fmt, na_rep=""),
        hide_index=True, width="stretch", height=600)
    st.caption("초록 = Actual(실적) · 노랑 = Coastal(예정)")

with tab3:
    st.subheader("모선별 ETA · ETB · ETD 변경사항 정리 표")
    tc1, tc2, tc3 = st.columns(3)
    t3_only_chg = tc1.checkbox("변경 항목만 보기 (미변경 모선은 '변경사항 없음' 표시)", value=False, key="t3_only")
    t3_hide_act = tc2.checkbox("지난 실적 숨기기", value=True, key="t3_act")
    only_delay = tc3.checkbox("12시간 이상 지연만 보기", value=False, key="t3_delay")

    src_changes = changes[changes["hours"].abs() >= 12] if (changes is not None and only_delay) else changes
    summary_tbl = fs.vessel_eta_summary_table(
        df, src_changes, only_changed=t3_only_chg, hide_actual=t3_hide_act
    )
    delta_color = lambda v: (
        "color:#c0392b; font-weight:bold" if isinstance(v, str) and v.startswith("+")
        else ("color:#2471a3; font-weight:bold" if isinstance(v, str) and v.startswith("-") else "")
    )
    st.dataframe(
        summary_tbl.style.map(delta_color, subset=["ΔETA", "ΔETB", "ΔETD"]),
        hide_index=True, width="stretch", height=600,
    )
    st.caption("저장한 웹페이지의 모선 순서대로 표시되며, 변경이 없는 모선은 '변경사항 없음'으로 표시됩니다. (Δ 양수 = 지연, 음수 = 단축)")

    if changes is not None and not changes.empty:
        with st.expander("필드별 상세 변경 로그 (ETA / ETB / ETD / SKIP)"):
            src = changes[changes["hours"] >= 12] if only_delay else changes
            fmt_val = lambda v: "" if pd.isna(v) else v.strftime("%Y-%m-%d %H:%M") if isinstance(v, pd.Timestamp) else str(v)
            tab3_rows = []
            for v_name, vyg in all_voyages:
                sub = src[(src["vessel"] == v_name) & (src["voyage"] == vyg)]
                if sub.empty:
                    tab3_rows.append({"vessel": v_name, "voyage": vyg, "seq": None,
                                      "port": "변경사항 없음", "field": "변경사항 없음",
                                      "old": "", "new": "", "hours": None})
                else:
                    for r in sub.itertuples():
                        tab3_rows.append({"vessel": r.vessel, "voyage": r.voyage, "seq": r.seq,
                                          "port": r.port, "field": r.field,
                                          "old": fmt_val(r.old), "new": fmt_val(r.new), "hours": r.hours})
            view = pd.DataFrame(tab3_rows)
            view["seq"] = pd.to_numeric(view["seq"], errors="coerce").astype("float64")
            view["hours"] = pd.to_numeric(view["hours"], errors="coerce").astype("float64")
            color = lambda h: "" if pd.isna(h) else ("color:#c0392b" if h > 0 else "color:#2471a3")
            st.dataframe(view.style.map(color, subset=["hours"]).format({"hours": "{:+.1f}h", "seq": "{:.0f}"}, na_rep=""),
                         hide_index=True, width="stretch")

VESSEL_CODES, PORT_CODES = fs.HERE / "vessel_codes.csv", fs.HERE / "port_codes.csv"
BADGE = {"12h~": ":red[**🔴 12 Hours ~**]", "06~11h": ":green[**🟢 06~11 Hours**]",
         "<6h": ":blue[**🔵 Less than 6 hours**]", "SKIP": ":red[**⛔ SKIP 변경**]", "": ":gray[변경사항 없음]"}

with tab4:
    st.caption(
        f"**기준 스냅샷** ({label(cur)}): 현재 HMM 스케줄 — 아래에 표시되는 ETA/ETB/ETD 값입니다.  \n"
        f"**비교 스냅샷** ({'없음' if prev is None else label(prev)}): 이전 시점 스케줄 — "
        "기준과의 차이가 이전 ETA/ETB/ETD, Δ(+지연/−단축), 구분으로 표시됩니다. "
        "회사 입력창에 마지막으로 반영한 시점의 파일을 고르면 그 이후 바뀐 것만 볼 수 있습니다.")
    rows = fs.company_rows(df, changes)
    vc_map = dict(pd.read_csv(VESSEL_CODES, dtype=str).itertuples(index=False))
    a, b, c = st.columns([4, 1, 1])
    sel = a.multiselect("선박 / 항차", all_voyages, format_func=lambda v: f"{v[0]} / {v[1]}")
    only = b.checkbox("변경 항목만", value=False)
    hide_actual = c.checkbox("지난 실적 숨기기", value=True)
    if hide_actual:
        rows = rows[rows["HMM 상태"] != "actual"]
    if changes is None:
        st.info("비교 스냅샷이 없어 현재 HMM 값만 표시합니다. 이전 날짜 HTML 을 추가하면 변경분이 표시됩니다.")

    with st.expander("📊 CIX2 기항지 모선별 ETA · ETB · ETD 변경사항 표로 보기", expanded=True):
        cix_tbl_rows = []
        for name, vyg in (sel or all_voyages):
            g_all = rows[(rows["선박명"] == name) & (rows["VYG"] == vyg)]
            has_chg = not g_all.empty and bool(g_all["변경"].any())
            g_sub = g_all[g_all["변경"]] if only else g_all
            if g_sub.empty:
                cix_tbl_rows.append({
                    "Vessel": vc_map.get(name, "?"), "선박명": name, "Vyg(B)": vyg,
                    "From": "변경사항 없음", "WHARF": "-",
                    "ETA": "", "이전 ETA": "", "ΔETA": "",
                    "ETB": "", "이전 ETB": "", "ΔETB": "",
                    "ETD": "", "이전 ETD": "", "ΔETD": "",
                    "구분": "변경사항 없음",
                })
            else:
                for _, r in g_sub.iterrows():
                    eta_str = "SKIP" if r["E.T.A"] == "SKIP" else f"{r['E.T.A']} {r['ETA Time']}".strip()
                    etb_str = "SKIP" if r["E.T.B"] == "SKIP" else f"{r['E.T.B']} {r['ETB Time']}".strip()
                    etd_str = "SKIP" if r["E.T.D"] == "SKIP" else f"{r['E.T.D']} {r['ETD Time']}".strip()
                    cix_tbl_rows.append({
                        "Vessel": r["Vessel"], "선박명": r["선박명"], "Vyg(B)": r["Vyg(B)"],
                        "From": r["From"], "WHARF": r["WHARF"],
                        "ETA": eta_str, "이전 ETA": r["이전 ETA"], "ΔETA": r["ΔETA"],
                        "ETB": etb_str, "이전 ETB": r["이전 ETB"], "ΔETB": r["ΔETB"],
                        "ETD": etd_str, "이전 ETD": r["이전 ETD"], "ΔETD": r["ΔETD"],
                        "구분": r["구분"] if r["변경"] else "변경사항 없음",
                    })
        cix_df = pd.DataFrame(cix_tbl_rows)
        st.dataframe(
            cix_df.style.map(delta_color, subset=["ΔETA", "ΔETB", "ΔETD"]),
            hide_index=True, width="stretch",
        )

    has_any_eta = bool((rows["E.T.A"].str.strip() != "").any()) if not rows.empty else False

    for name, vyg in (sel or all_voyages):
        g_all = rows[(rows["선박명"] == name) & (rows["VYG"] == vyg)]
        has_change = not g_all.empty and bool(g_all["변경"].any())
        g = g_all[g_all["변경"]] if only else g_all
        with st.container(border=True):
            code = vc_map.get(name, "?")
            code_txt = code if code != "?" else ":orange[코드 미등록 → 코드 관리 탭]"
            no_chg_badge = " — :gray[변경사항 없음]" if (changes is not None and not has_change) else ""
            st.markdown(f"#### {code_txt} · {name} · {vyg}{no_chg_badge}")
            if g.empty:
                st.caption("변경사항 없음")
            for _, r in g.iterrows():
                if has_any_eta:
                    c = st.columns([2, 2.5, 2.5, 2.5, 2])
                    time_cols = ((c[1], "ETA"), (c[2], "ETB"), (c[3], "ETD"))
                    badge_col = c[4]
                else:
                    c = st.columns([2, 3, 3, 2])
                    time_cols = ((c[1], "ETB"), (c[2], "ETD"))
                    badge_col = c[3]
                c[0].markdown(f"**{r['From']}** `{r['WHARF']}`  \n{r['Vyg(B)']} · {r['HMM 상태']}")
                for col, k in time_cols:
                    old = r[f"이전 {k}"]
                    day_lbl = f" ({r[f'{k} Day']})" if r[f"{k} Day"] else ""
                    col.caption(f"{k}{day_lbl}" + (f" · 이전 ~~{old}~~" if old else ""))
                    val_date = r[f"E.T.{k[-1]}"]
                    val_txt = "SKIP" if val_date == "SKIP" else (f"{val_date} {r[f'{k} Time']}".strip() or "-")
                    col.code(val_txt, language=None)
                deltas = "  \n".join(f"Δ{k} {r[f'Δ{k}']}" for k in ("ETA", "ETB", "ETD") if r[f"Δ{k}"])
                badge_col.markdown(BADGE[r["구분"]] + (f"  \n{deltas}" if deltas else ""))
    st.caption("CIX2 기항지(코드 관리 탭에서 LOCODE·WHARF 를 입력한 터미널)만 표시 · "
               "값 오른쪽 복사 아이콘으로 복사해 입력창 ETA/ETB/ETD 에 붙여넣기")

with tab5:
    st.subheader("선박 코드")
    vc = pd.read_csv(VESSEL_CODES, dtype=str)
    vtable = pd.DataFrame({"vessel": list(dict.fromkeys(df["vessel"]))}).merge(vc, how="outer", sort=False)
    v_edit = st.data_editor(vtable, hide_index=True, disabled=["vessel"], key="vc",
                            column_config={"vessel": "선박명", "code": "Vessel 코드 (예: HMOC)"})

    st.subheader("항구 / WHARF 코드")
    st.caption("LOCODE 와 WHARF 를 모두 입력한 터미널만 CIX2 기항지로 입력용 탭에 나옵니다. 비워두면 제외.")
    pc = pd.read_csv(PORT_CODES, dtype=str)
    pairs = (df[["port", "terminal"]].drop_duplicates()
             .rename(columns={"port": "hmm_port", "terminal": "hmm_terminal"}))
    ptable = pairs.merge(pc, how="outer")
    p_edit = st.data_editor(ptable, hide_index=True, disabled=["hmm_port", "hmm_terminal"], key="pc",
                            column_config={"hmm_port": "HMM 항구", "hmm_terminal": "HMM 터미널",
                                           "locode": "LOCODE (예: KRPUS)", "wharf": "WHARF (예: PNC03)"})

    if st.button("저장", type="primary"):
        filled = lambda s: s.fillna("").str.strip() != ""
        v_edit[filled(v_edit["code"])].to_csv(VESSEL_CODES, index=False)
        p_edit[filled(p_edit["locode"]) & filled(p_edit["wharf"])].to_csv(PORT_CODES, index=False)
        st.toast("코드를 저장했습니다.")
        st.rerun()

with tab_crawl:
    st.subheader("🚢 1. 'by Vessel Name' 탭 선박 선택 크롤링 (ETA · ETB · ETD)")
    st.caption(
        "HMM e-Service (`ScheduleMainPost.do`)의 **'by Vessel Name' 탭**에서 선박을 선택해 **ETA(Arrival) · ETB(Berthing) · ETD(Departure)** 전체 항차를 크롤링하고 현재 기준 스냅샷에 즉시 반영합니다."
    )
    with st.container(border=True):
        cur_vessels = list(dict.fromkeys(df["vessel"]))
        all_known_vessels = list(dict.fromkeys(cur_vessels + list(fs.FIM_VESSEL_CODES.keys())))
        picked_vessels = st.multiselect(
            "크롤링할 선박 선택 (비워두면 현재 스냅샷의 전체 모선을 순서대로 크롤링합니다)",
            options=all_known_vessels,
            default=cur_vessels,
            format_func=lambda v: f"{v} ({fs.FIM_VESSEL_CODES.get(v, '')})" if v in fs.FIM_VESSEL_CODES else v,
            key="crawl_vessel_pick",
        )
        target_vsls = picked_vessels if picked_vessels else cur_vessels
        if st.button(
            f"🚢 'by Vessel Name' 탭에서 선택한 선박({len(target_vsls)}척) ETA · ETB · ETD 크롤링 실행",
            type="primary",
            use_container_width=True,
            key="btn_crawl_by_vsl",
        ):
            v_prog = st.progress(0, text="ScheduleMainPost.do › by Vessel Name 접속 준비 중...")
            try:
                updated_path, v_df = fs.crawl_by_vessel_name(
                    vessel_names=target_vsls,
                    target_snapshot=cur,
                    save_dir=SNAP_DIR,
                    on_progress=lambda step, total, msg: v_prog.progress(
                        min(int(step * 100 / max(total, 1)), 100), text=msg
                    ),
                    update_etb_etd=True,
                )
                st.session_state["last_crawled_vessel_df"] = v_df
                st.cache_data.clear()
                st.success(f"'{cur.name}' 스냅샷에 모선별 ETA·ETB·ETD 반영 완료! (총 {len(v_df)}개 기항 수집)")
                st.rerun()
            except Exception as exc:
                st.error(f"by Vessel Name 크롤링 실패: {exc}")

        if "last_crawled_vessel_df" in st.session_state and not st.session_state["last_crawled_vessel_df"].empty:
            with st.expander("📋 방금 'by Vessel Name' 탭에서 수집된 선박별 ETA · ETB · ETD 원본 표 보기", expanded=True):
                st.dataframe(st.session_state["last_crawled_vessel_df"], hide_index=True, width="stretch", height=350)

    st.subheader("🔄 2. HMM 항로(Loop) + 모선별(by Vessel Name) 기간 지정 크롤링")
    st.caption(
        "HMM e-Service (`ScheduleMainPost.do`)에서 **시작 날짜 ~ 끝 날짜** 기간의 FIM 항로 스케줄과 모선별 ETA·ETB·ETD를 자동으로 수집하여 새 스냅샷으로 저장합니다."
    )

    with st.container(border=True):
        st.markdown("##### 📅 조회 기간 선택")
        p1, p2, p3, p4 = st.columns(4)
        if p1.button("전체 운항모선 (-5개월~+3개월)", use_container_width=True, key="preset_all"):
            st.session_state["tab_from_dt"] = DEFAULT_FROM
            st.session_state["tab_to_dt"] = DEFAULT_TO
            st.rerun()
        if p2.button("오늘 ~ +1개월", use_container_width=True, key="preset_1m"):
            st.session_state["tab_from_dt"] = TODAY
            st.session_state["tab_to_dt"] = TODAY + timedelta(days=31)
            st.rerun()
        if p3.button("오늘 ~ +3개월", use_container_width=True, key="preset_3m"):
            st.session_state["tab_from_dt"] = TODAY
            st.session_state["tab_to_dt"] = DEFAULT_TO
            st.rerun()
        if p4.button("오늘 ~ +6개월", use_container_width=True, key="preset_6m"):
            st.session_state["tab_from_dt"] = TODAY
            st.session_state["tab_to_dt"] = TODAY + timedelta(days=183)
            st.rerun()

        dc1, dc2 = st.columns(2)
        t_from = dc1.date_input(
            "시작 날짜 (From)",
            value=st.session_state.get("tab_from_dt", sb_from),
            key="tab_from_dt",
        )
        t_to = dc2.date_input(
            "끝 날짜 (To)",
            value=st.session_state.get("tab_to_dt", sb_to),
            key="tab_to_dt",
        )
        days_diff = (t_to - t_from).days
        if days_diff >= 0:
            st.info(f"선택된 조회 기간: **{t_from:%Y-%m-%d}** ~ **{t_to:%Y-%m-%d}** (총 **{days_diff + 1}일**)")
        else:
            st.error("시작 날짜가 끝 날짜보다 늦습니다. 날짜를 다시 선택해 주세요.")

        bc1, bc2 = st.columns(2)
        t_run_a = bc1.button(
            "1️⃣ 방식 A 크롤링 시작 (FIM Loop 표만 빠르게 수집)",
            use_container_width=True,
            disabled=days_diff < 0,
            key="tab_btn_a",
        )
        t_run_ab = bc2.button(
            "2️⃣ 방식 A+B 크롤링 시작 (FIM Loop + by Vessel Name ETA·ETB·ETD 전체 수집)",
            type="primary",
            use_container_width=True,
            disabled=days_diff < 0,
            key="tab_btn_ab",
        )

        if t_run_a or t_run_ab:
            t_prog = st.progress(0, text="크롤링 준비 중...")
            try:
                saved_path = fs.crawl_hmm_schedule(
                    loop_code="FIM",
                    include_vessel_eta=bool(t_run_ab),
                    save_dir=SNAP_DIR,
                    on_progress=lambda step, total, msg: t_prog.progress(
                        min(int(step * 100 / max(total, 1)), 100), text=msg
                    ),
                    date_from=t_from,
                    date_to=t_to,
                )
                st.cache_data.clear()
                st.success(f"크롤링 및 저장 완료: {saved_path.name}")
                st.rerun()
            except Exception as exc:
                st.error(f"크롤링 실패: {exc}")

    st.subheader("📂 저장된 스냅샷 목록")
    snap_rows = [
        {
            "파일명": f.name,
            "기준일자": f"{fs.snapshot_date(f):%Y-%m-%d}",
            "수정시각": f"{datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d %H:%M:%S}",
            "크기 (KB)": round(f.stat().st_size / 1024, 1),
        }
        for f in files
    ]
    st.dataframe(pd.DataFrame(snap_rows), hide_index=True, width="stretch")

