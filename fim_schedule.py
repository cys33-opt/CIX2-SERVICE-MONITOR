"""HMM e-Service 'Long-term Schedule by Loop' (FIM) 저장 HTML 파서, 분석 및 자동 크롤러."""
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

COLUMNS = ["vessel", "voyage", "seq", "port", "bound", "terminal",
           "eta", "etb", "etd", "eta_status", "etb_status", "etd_status", "skip"]


class _TableParser(HTMLParser):
    """table#longTermScheduleTable 의 thead/tbody 셀을 수집한다."""

    def __init__(self):
        super().__init__()
        self.depth = 0            # 대상 table 내부 <table> 중첩 깊이
        self.section = None       # "thead" | "tbody"
        self.head, self.body = [], []
        self.row = self.cell = None
        self.in_status = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            if self.depth or a.get("id") == "longTermScheduleTable":
                self.depth += 1
            return
        if not self.depth:
            return
        if tag in ("thead", "tbody"):
            self.section = tag
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = {"text": [], "status": None,
                         "colspan": int(a.get("colspan", 1))}
        elif tag == "span" and "clsSkdStus" in (a.get("class") or ""):
            self.in_status = True

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag == "table":
            self.depth -= 1
        elif tag == "span":
            self.in_status = False
        elif tag in ("td", "th") and self.cell is not None:
            self.cell["text"] = " ".join(self.cell["text"]).strip()
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            (self.head if self.section == "thead" else self.body).append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is None or not data.strip():
            return
        if self.in_status:
            self.cell["status"] = data.strip().strip("()")
        else:
            self.cell["text"].append(data.strip())


def _to_dt(text):
    if not text:
        return None
    m = re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", str(text))
    return datetime.strptime(m.group(0), "%Y-%m-%d %H:%M") if m else None


def _extract_vessel_eta_records(html_text):
    """저장된 HTML 내 JSON 스크립트(#hmm-vessel-eta-data) 또는 #byVesselNameTable 에서 ETA 정보를 추출한다."""
    records = []
    m_json = re.search(
        r'<script[^>]*id=["\']hmm-vessel-eta-data["\'][^>]*>(.*?)</script>',
        html_text, re.DOTALL | re.IGNORECASE
    )
    if m_json:
        try:
            items = json.loads(m_json.group(1).strip())
            for it in items:
                vvd = str(it.get("ltdvvd") or "").strip()
                if len(vvd) < 9:
                    continue
                records.append({
                    "vessel": str(it.get("ltdvds") or "").strip(),
                    "voyage": vvd[4:8],
                    "bound": vvd[8:9],
                    "port": str(it.get("portNm") or "").strip(),
                    "terminal": str(it.get("tmnlNm") or "").strip(),
                    "eta": _to_dt(it.get("arrDt")),
                    "eta_status": (it.get("arrStNm") or None),
                })
        except Exception:
            pass

    if not records:
        m_vsl = re.search(r'id=["\']tab3_VslNm["\'][^>]*>\s*<div[^>]*>\s*([^<\n]+)', html_text)
        m_tbody = re.search(r'<tbody[^>]*id=["\']byVesselNameArea["\'][^>]*>(.*?)</tbody>', html_text, re.DOTALL)
        if m_vsl and m_tbody:
            vsl_name = m_vsl.group(1).strip()
            for tr_html in re.findall(r"<tr[^>]*>(.*?)</tr>", m_tbody.group(1), re.DOTALL):
                tds = re.findall(r"<td[^>]*>(.*?)</td>", tr_html, re.DOTALL)
                if len(tds) < 8:
                    continue
                clean = lambda s: re.sub(r"<[^>]+>", " ", s).strip()
                vvd = clean(tds[0])
                if len(vvd) < 9:
                    continue
                port_nm = re.sub(r"\s+", " ", clean(tds[2])).strip()
                tmnl_nm = re.sub(r"\s+", " ", clean(tds[3])).strip()
                arr_dt = _to_dt(clean(tds[5]))
                st_m = re.search(r'guide-icon\s+([a-zA-Z0-9_-]+)', tds[5])
                arr_st = st_m.group(1) if st_m else None
                records.append({
                    "vessel": vsl_name,
                    "voyage": vvd[4:8],
                    "bound": vvd[8:9],
                    "port": port_nm,
                    "terminal": tmnl_nm,
                    "eta": arr_dt,
                    "eta_status": arr_st,
                })
    return records


def parse_html(path):
    """저장한 HTML 파일 → 기항(call) 단위 DataFrame."""
    html_text = Path(path).read_text(encoding="utf-8", errors="replace")
    p = _TableParser()
    p.feed(html_text)
    if len(p.head) < 2:
        raise ValueError(f"{path}: longTermScheduleTable 을 찾지 못했습니다 (by Loop 화면인지 확인).")

    ports = [c["text"] for c in p.head[0][1:]]          # 첫 칸은 'Vessel'
    terminals = [c["text"] for c in p.head[1]]
    if len(ports) != len(terminals):
        raise ValueError(f"{path}: 항구({len(ports)})와 터미널({len(terminals)}) 수가 다릅니다.")

    rows = []
    for tr in p.body:
        vessel, _, voyage = tr[0]["text"].partition(" / ")
        cells, seq, i = tr[1:], 0, 0
        while i < len(cells) and seq < len(ports):
            m = re.match(r"(.*)\s+\((\w)\)$", ports[seq])
            port, bound = (m.group(1), m.group(2)) if m else (ports[seq], "")
            base = {"vessel": vessel.strip(), "voyage": voyage.strip(), "seq": seq + 1,
                    "port": port, "bound": bound, "terminal": terminals[seq],
                    "eta": None, "eta_status": None}
            if cells[i]["colspan"] == 2:                  # SKIP 등 병합 셀
                rows.append({**base, "etb": None, "etd": None, "etb_status": None,
                             "etd_status": None, "skip": cells[i]["text"].upper() == "SKIP"})
                i += 1
            else:
                b, d = cells[i], cells[i + 1]
                rows.append({**base, "etb": _to_dt(b["text"]), "etd": _to_dt(d["text"]),
                             "etb_status": b["status"], "etd_status": d["status"], "skip": False})
                i += 2
            seq += 1
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["etb"] = pd.to_datetime(df["etb"])
    df["etd"] = pd.to_datetime(df["etd"])

    eta_records = _extract_vessel_eta_records(html_text)
    if eta_records:
        edf = pd.DataFrame(eta_records)
        edf["occ"] = edf.groupby(["vessel", "voyage", "port", "bound", "terminal"]).cumcount()
        eta_map = {
            (r.vessel, r.voyage, r.port, r.bound, r.terminal, r.occ): (r.eta, r.eta_status)
            for r in edf.itertuples()
        }
        act_mask = df["etb"].notna() | df["skip"]
        occ_series = df[act_mask].groupby(["vessel", "voyage", "port", "bound", "terminal"]).cumcount()
        for idx, occ_val in occ_series.items():
            r = df.loc[idx]
            hit = eta_map.get((r["vessel"], r["voyage"], r["port"], r["bound"], r["terminal"], occ_val))
            if hit is not None:
                df.at[idx, "eta"] = hit[0]
                df.at[idx, "eta_status"] = hit[1]

    df["eta"] = pd.to_datetime(df["eta"])
    return df


def normalize_snapshot_filenames(snap_dir):
    """Windows zip 업로드 시 깨진 한글 파일명을 UTF-8('월', '일')로 자동 복원한다."""
    p = Path(snap_dir)
    if not p.exists():
        return
    for f in list(p.iterdir()):
        safe_name = f.name.encode("utf-8", "replace").decode("utf-8")
        m = re.match(r"^((?:\d{4}\s+)?\d{1,2})[^\d\s]+\s+(\d{1,2})[^\d_]+(_\d+)?\.html$", safe_name)
        if m:
            suffix = m.group(3) or ""
            target_name = f"{m.group(1)}월 {m.group(2)}일{suffix}.html"
            if f.name != target_name:
                try:
                    f.rename(p / target_name)
                except Exception:
                    pass


def snapshot_date(path):
    """파일명 '2026 11월 3일.html' → datetime(2026, 11, 3). 없으면 파일 수정시각."""
    m = re.search(r"(\d{4})\D*?(\d{1,2})월\s*(\d{1,2})일", Path(path).stem)
    if m:
        return datetime(*map(int, m.groups()))
    return datetime.fromtimestamp(Path(path).stat().st_mtime)


def vessel_status(df):
    """선박/항차별 현재 상태: 마지막 실적 이벤트와 다음 기항지."""
    out = []
    for (vessel, voyage), g in df.groupby(["vessel", "voyage"], sort=False):
        calls = g[g["etb"].notna()]
        events = pd.concat([
            calls.assign(kind="ETB", time=calls["etb"], status=calls["etb_status"]),
            calls.assign(kind="ETD", time=calls["etd"], status=calls["etd_status"]),
        ]).dropna(subset=["time"]).sort_values("time")
        actual = events[events["status"] == "actual"]
        last = actual.iloc[-1] if len(actual) else None
        future = events[(events["kind"] == "ETB") & (events["status"] != "actual")]
        if last is not None:
            future = future[future["time"] > last["time"]]
        nxt = future.iloc[0] if len(future) else None

        if last is None:
            state = "출항 전"
        elif last["kind"] == "ETB":
            state = f"재항 중 ({last['port']})"
        elif nxt is None:
            state = "항차 종료"
        else:
            state = "항해 중"
        out.append({
            "vessel": vessel, "voyage": voyage, "state": state,
            "last_port": None if last is None else last["port"],
            "last_event": None if last is None else f"{last['kind']} {last['time']:%Y-%m-%d %H:%M}",
            "next_port": None if nxt is None else nxt["port"],
            "next_etb": None if nxt is None else nxt["time"],
            "next_status": None if nxt is None else nxt["status"],
            "skips": int(g["skip"].sum()),
        })
    return pd.DataFrame(out)


def diff_snapshots(old, new):
    """두 스냅샷 비교: ETA/ETB/ETD 변경, SKIP 변경, 신규/삭제 기항."""
    # 스냅샷마다 빈 열 구성이 달라 seq 가 어긋나므로 실제 기항(etb 존재 또는 skip)만 추려
    # (항구, 방향, 터미널, n번째 기항)으로 짝을 맞춘다.
    key = ["vessel", "voyage", "port", "bound", "terminal", "occ"]
    act = lambda d: d[d["etb"].notna() | d["skip"]].copy()
    occ = lambda d: act(d).assign(occ=act(d).groupby(["vessel", "voyage", "port", "bound", "terminal"]).cumcount())
    common = set(zip(old["vessel"], old["voyage"])) & set(zip(new["vessel"], new["voyage"]))
    m = occ(old).merge(occ(new), on=key, how="outer", suffixes=("_old", "_new"), indicator=True)
    m = m[[(r.vessel, r.voyage) in common for r in m.itertuples()]]
    if m.empty:
        return pd.DataFrame(columns=["vessel", "voyage", "seq", "port", "field", "old", "new", "hours"])
    m["seq"] = m["seq_new"].fillna(m["seq_old"]).astype(int)
    vv_order = {vv: i for i, vv in enumerate(dict.fromkeys(zip(new["vessel"], new["voyage"])))}
    m["_vv"] = [vv_order.get((r.vessel, r.voyage), len(vv_order)) for r in m.itertuples()]
    m = m.sort_values(["_vv", "seq"])
    has_eta_both = ("eta" in old.columns and "eta" in new.columns
                    and old["eta"].notna().any() and new["eta"].notna().any())
    changes = []
    for _, r in m.iterrows():
        base = {"vessel": r["vessel"], "voyage": r["voyage"], "seq": r["seq"], "port": r["port"]}
        if r["_merge"] != "both":
            added = r["_merge"] == "right_only"
            changes.append({**base, "field": "기항", "old": "없음" if added else "있음",
                            "new": "있음" if added else "없음", "hours": None})
            continue
        if bool(r["skip_old"]) != bool(r["skip_new"]):
            changes.append({**base, "field": "SKIP", "old": r["skip_old"], "new": r["skip_new"], "hours": None})
        fields = ("eta", "etb", "etd") if ("eta_old" in r and "eta_new" in r) else ("etb", "etd")
        for f in fields:
            o, n = r[f"{f}_old"], r[f"{f}_new"]
            if pd.isna(o) and pd.isna(n):
                continue
            if f == "eta" and not has_eta_both and (pd.isna(o) or pd.isna(n)):
                continue
            if pd.isna(o) or pd.isna(n) or o != n:
                hours = None if pd.isna(o) or pd.isna(n) else (n - o).total_seconds() / 3600
                changes.append({**base, "field": f.upper(), "old": o, "new": n, "hours": hours})
    return pd.DataFrame(changes, columns=["vessel", "voyage", "seq", "port", "field", "old", "new", "hours"])


HERE = Path(__file__).parent


def delay_class(hours):
    """회사 입력창 범례: 12 Hours ~ / 06~11 Hours / Less than 6 hours."""
    if hours is None or pd.isna(hours):
        return ""
    h = abs(hours)
    return "12h~" if h >= 12 else "06~11h" if h >= 6 else "<6h"


def _hhmm(hours):
    if hours is None or pd.isna(hours):
        return ""
    sign, mins = ("+" if hours >= 0 else "-"), round(abs(hours) * 60)
    return f"{sign}{mins // 60:02d}:{mins % 60:02d}"


def company_rows(df, changes=None, port_codes=HERE / "port_codes.csv",
                 vessel_codes=HERE / "vessel_codes.csv"):
    """HMM 스케줄 → 회사 입력창 형식. port_codes.csv 에 있는 기항(CIX2 기항지)만 포함."""
    pc = pd.read_csv(port_codes, dtype=str)
    vc = dict(pd.read_csv(vessel_codes, dtype=str).itertuples(index=False))
    calls = df[df["etb"].notna() | df["skip"]].merge(
        pc, left_on=["port", "terminal"], right_on=["hmm_port", "hmm_terminal"])

    delta = {}   # (vessel, voyage, seq, field) -> changes 행
    if changes is not None:
        for r in changes.itertuples():
            delta[(r.vessel, r.voyage, r.seq, r.field)] = r

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    fmt = lambda t, f: "" if pd.isna(t) else days[t.weekday()] if f == "%a" else t.strftime(f)
    old_of = lambda c: "" if c is None or not isinstance(c.old, pd.Timestamp) else c.old.strftime("%Y-%m-%d %H:%M")
    vv_order = {vv: i for i, vv in enumerate(dict.fromkeys(zip(df["vessel"], df["voyage"])))}
    calls["_vv"] = [vv_order[(r.vessel, r.voyage)] for r in calls.itertuples()]
    out = []
    for r in calls.sort_values(["_vv", "etb", "seq"]).itertuples():
        c_eta, c_etb, c_etd, c_skip = (delta.get((r.vessel, r.voyage, r.seq, f)) for f in ("ETA", "ETB", "ETD", "SKIP"))
        d_eta = None if c_eta is None else c_eta.hours
        d_etb = None if c_etb is None else c_etb.hours
        d_etd = None if c_etd is None else c_etd.hours
        known = [abs(h) for h in (d_eta, d_etb, d_etd) if h is not None and not pd.isna(h)]
        cls = "SKIP" if c_skip is not None else delay_class(max(known)) if known else ""
        eta_val = getattr(r, "eta", pd.NaT)
        out.append({
            "Vessel": vc.get(r.vessel, "?"), "선박명": r.vessel, "VYG": r.voyage,
            "Vyg(B)": f"{r.voyage}{r.bound}", "From": r.locode, "WHARF": r.wharf, "B": r.bound,
            "E.T.A": "SKIP" if r.skip else fmt(eta_val, "%Y-%m-%d"), "ETA Time": fmt(eta_val, "%H:%M"),
            "ETA Day": fmt(eta_val, "%a"),
            "E.T.B": "SKIP" if r.skip else fmt(r.etb, "%Y-%m-%d"), "ETB Time": fmt(r.etb, "%H:%M"),
            "ETB Day": fmt(r.etb, "%a"),
            "E.T.D": "SKIP" if r.skip else fmt(r.etd, "%Y-%m-%d"), "ETD Time": fmt(r.etd, "%H:%M"),
            "ETD Day": fmt(r.etd, "%a"),
            "ΔETA": _hhmm(d_eta), "ΔETB": _hhmm(d_etb), "ΔETD": _hhmm(d_etd), "구분": cls,
            "이전 ETA": old_of(c_eta), "이전 ETB": old_of(c_etb), "이전 ETD": old_of(c_etd),
            "변경": any(c is not None for c in (c_eta, c_etb, c_etd, c_skip)),
            "HMM 상태": "SKIP" if r.skip else (r.etb_status or ""),
            "seq": r.seq,
        })
    return pd.DataFrame(out)


def vessel_eta_summary_table(df, changes=None, only_changed=False, hide_actual=True):
    """모선 순서대로 ETA · ETB · ETD 및 변경사항을 한눈에 보는 요약 표 생성.
    변경사항이 없는 모선도 웹페이지 순서대로 '변경사항 없음' 행으로 표시한다.
    """
    delta = {}
    if changes is not None:
        for r in changes.itertuples():
            delta[(r.vessel, r.voyage, r.seq, r.field)] = r

    fmt_dt = lambda t: "" if pd.isna(t) else t.strftime("%Y-%m-%d %H:%M")
    old_of = lambda c: "" if c is None or not isinstance(c.old, pd.Timestamp) else c.old.strftime("%Y-%m-%d %H:%M")

    all_voyages = list(dict.fromkeys(zip(df["vessel"], df["voyage"])))
    act = df[df["etb"].notna() | df["skip"]].copy()
    out = []

    for vessel, voyage in all_voyages:
        g = act[(act["vessel"] == vessel) & (act["voyage"] == voyage)].sort_values(["etb", "seq"])
        if hide_actual:
            g = g[g["etb_status"] != "actual"]

        vessel_rows = []
        has_vessel_change = False
        for r in g.itertuples():
            c_eta, c_etb, c_etd, c_skip = (
                delta.get((r.vessel, r.voyage, r.seq, f)) for f in ("ETA", "ETB", "ETD", "SKIP")
            )
            d_eta = None if c_eta is None else c_eta.hours
            d_etb = None if c_etb is None else c_etb.hours
            d_etd = None if c_etd is None else c_etd.hours
            is_changed = any(c is not None for c in (c_eta, c_etb, c_etd, c_skip))
            if is_changed:
                has_vessel_change = True
            if only_changed and not is_changed:
                continue
            known = [abs(h) for h in (d_eta, d_etb, d_etd) if h is not None and not pd.isna(h)]
            cls = "SKIP" if c_skip is not None else delay_class(max(known)) if known else ("변경 없음" if changes is not None else "")
            eta_val = getattr(r, "eta", pd.NaT)
            vessel_rows.append({
                "선박명": r.vessel,
                "항차": r.voyage,
                "방향": r.bound,
                "항구": r.port,
                "터미널": r.terminal,
                "ETA": "SKIP" if r.skip else fmt_dt(eta_val),
                "이전 ETA": old_of(c_eta),
                "ΔETA": _hhmm(d_eta),
                "ETB": "SKIP" if r.skip else fmt_dt(r.etb),
                "이전 ETB": old_of(c_etb),
                "ΔETB": _hhmm(d_etb),
                "ETD": "SKIP" if r.skip else fmt_dt(r.etd),
                "이전 ETD": old_of(c_etd),
                "ΔETD": _hhmm(d_etd),
                "상태": "SKIP" if r.skip else (r.etb_status or ""),
                "구분": cls if is_changed else "변경사항 없음",
            })

        if not vessel_rows:
            out.append({
                "선박명": vessel,
                "항차": voyage,
                "방향": "-",
                "항구": "변경사항 없음" if (only_changed or not has_vessel_change) else "표시할 기항 없음",
                "터미널": "-",
                "ETA": "", "이전 ETA": "", "ΔETA": "",
                "ETB": "", "이전 ETB": "", "ΔETB": "",
                "ETD": "", "이전 ETD": "", "ΔETD": "",
                "상태": "-",
                "구분": "변경사항 없음",
            })
        else:
            out.extend(vessel_rows)

    return pd.DataFrame(out)


def _build_loop_html(hdr_list, dtl_list, eta_rows=None):
    """selectByLoopList.do 응답(hdrList, vskSkdDtls)과 선박별 ETA 목록으로 스냅샷 HTML 생성."""
    seen_vv = []
    seen_set = set()
    for d in dtl_list:
        vv = f"{d.get('vslCd')}-{d.get('skdVoyNo')}"
        if vv not in seen_set:
            seen_set.add(vv)
            seen_vv.append(vv)

    filtered_hdr = []
    for vv in seen_vv:
        arr_dtl = [d for d in dtl_list if f"{d.get('vslCd')}-{d.get('skdVoyNo')}" == vv]
        next_pos = 0
        for i, h in enumerate(hdr_list):
            matched = any(
                i == j + next_pos
                and h.get("tmnlCd") == d.get("tmnlCd")
                and h.get("skdDirCd") == d.get("skdDirCd")
                for j, d in enumerate(arr_dtl)
            )
            if matched:
                if h not in filtered_hdr:
                    filtered_hdr.append(h)
            else:
                next_pos += 1

    header_list = [
        h for h in hdr_list
        if any(
            fh.get("vslCd") == h.get("vslCd")
            and fh.get("skdDirCd") == h.get("skdDirCd")
            and fh.get("tmnlCd") == h.get("tmnlCd")
            for fh in filtered_hdr
        )
    ]

    th0 = ['<th rowspan="3"><div class="text">Vessel</div></th>']
    th1 = []
    th2 = []
    for o in header_list:
        th0.append(f'<th colspan="2"><div class="text">{o.get("portNm", "")} ({o.get("skdDirCd", "")})</div></th>')
        th1.append(f'<th colspan="2"><div class="text">{o.get("tmnlNm", "")}</div></th>')
        th2.append('<th><div class="text">ETB</div></th><th><div class="text">ETD</div></th>')
    thead_html = f"<tr>{''.join(th0)}</tr><tr>{''.join(th1)}</tr><tr>{''.join(th2)}</tr>"

    kind_map = {"L": "long-term", "C": "coastal", "A": "actual"}
    fmt_14 = lambda s: (
        f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"
        if isinstance(s, str) and len(s) >= 12 else ""
    )

    tbody_rows = []
    for vv in seen_vv:
        arr_dtl = [d for d in dtl_list if f"{d.get('vslCd')}-{d.get('skdVoyNo')}" == vv]
        if not arr_dtl:
            continue
        tds = [f'<td><div class="text left">{arr_dtl[0].get("vslNm", "")} / {arr_dtl[0].get("skdVoyNo", "")}</div></td>']
        next_pos = 0
        for i, h in enumerate(header_list):
            find_dtl = None
            for j, d in enumerate(arr_dtl):
                if (i == j + next_pos
                        and h.get("tmnlCd") == d.get("tmnlCd")
                        and h.get("skdDirCd") == d.get("skdDirCd")):
                    find_dtl = d
                    break
            if find_dtl is not None:
                if find_dtl.get("skdCngStusCd") == "S":
                    tds.append('<td colspan="2">SKIP</td>')
                else:
                    st_b = kind_map.get(find_dtl.get("skdKindCdEtb"), "")
                    st_d = kind_map.get(find_dtl.get("skdKindCdEtd"), "")
                    dt_b = fmt_14(find_dtl.get("etbDt"))
                    dt_d = fmt_14(find_dtl.get("etdDt"))
                    tds.append(
                        f'<td><div class="text"><span class="clsSkdStus hidden">({st_b})</span>{dt_b}</div></td>'
                        f'<td><div class="text"><span class="clsSkdStus hidden">({st_d})</span>{dt_d}</div></td>'
                    )
            else:
                next_pos += 1
                tds.append("<td></td><td></td>")
        tbody_rows.append(f"<tr>{''.join(tds)}</tr>")

    eta_script = ""
    if eta_rows:
        eta_script = f'<script id="hmm-vessel-eta-data" type="application/json">{json.dumps(eta_rows, ensure_ascii=False)}</script>'

    return (
        "<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><title>HMM Schedule Snapshot</title></head><body>"
        f'<table id="longTermScheduleTable"><thead>{thead_html}</thead><tbody>{"".join(tbody_rows)}</tbody></table>'
        f"{eta_script}</body></html>"
    )


def _crawl_via_requests(loop_code, include_vessel_eta, report):
    """requests.Session 으로 CSRF 토큰을 발급받아 빠르게 방식 A + 방식 B JSON API 를 호출한다."""
    from datetime import timedelta
    import requests

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    main_url = "https://www.hmm21.com/e-service/general/schedule/ScheduleMain.do"
    report(5, 100, "HMM e-Service 세션 및 CSRF 토큰 요청 중...")
    resp = sess.get(main_url, timeout=20)
    resp.raise_for_status()

    m_tok = re.search(r'<meta\s+name=["\']_csrf["\']\s+content=["\']([^"\']+)["\']', resp.text)
    m_hdr = re.search(r'<meta\s+name=["\']_csrf_header["\']\s+content=["\']([^"\']+)["\']', resp.text)
    if not m_tok:
        raise RuntimeError("CSRF 토큰을 찾을 수 없습니다.")
    csrf_tok = m_tok.group(1)
    csrf_hdr = m_hdr.group(1) if m_hdr else "X-CSRF-TOKEN"

    api_headers = {
        csrf_hdr: csrf_tok,
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.hmm21.com",
        "Referer": main_url,
    }

    now = datetime.now()
    from_str = now.strftime("%Y%m%d")
    to_str = (now + timedelta(days=92)).strftime("%Y%m%d")

    report(15, 100, f"[방식 A] Long-term Schedule by Loop ({loop_code}) API 조회 중...")
    loop_resp = sess.post(
        "https://www.hmm21.com/e-service/general/schedule/selectByLoopList.do",
        headers=api_headers,
        json={
            "srchByLoopOptLoop": loop_code,
            "srchLongDateFrom": from_str,
            "srchLongDateTo": to_str,
            "srchLongBound": "",
        },
        timeout=30,
    )
    loop_resp.raise_for_status()
    loop_json = loop_resp.json()
    rtn_data = loop_json.get("RTN_DATA") or {}
    hdr_list = rtn_data.get("hdrList") or []
    dtl_list = rtn_data.get("vskSkdDtls") or []
    if not hdr_list or not dtl_list:
        raise RuntimeError(f"Loop 스케줄 데이터가 비어 있습니다: {loop_json.get('RTN_MSG')}")

    vessels = []
    seen = set()
    for d in dtl_list:
        key = f"{d.get('vslCd')}-{d.get('skdVoyNo')}"
        if key not in seen:
            seen.add(key)
            vessels.append({"vslCd": d.get("vslCd"), "vslNm": d.get("vslNm"), "skdVoyNo": d.get("skdVoyNo")})

    all_eta_rows = []
    if include_vessel_eta and vessels:
        total_v = len(vessels)
        vsl_url = "https://www.hmm21.com/e-service/general/schedule/selectByVesselPagingList.do"
        for idx, v in enumerate(vessels, start=1):
            vsl_cd, vsl_nm, voy_no = v["vslCd"], v["vslNm"], v["skdVoyNo"]
            pct = 20 + int(75 * idx / max(total_v, 1))
            report(pct, 100, f"[방식 B] ({idx}/{total_v}) {vsl_nm} / {voy_no} ({vsl_cd}) ETA 조회 중...")

            def call_vsl(page_no, vvd):
                r = sess.post(
                    vsl_url,
                    headers=api_headers,
                    json={"page": str(page_no), "srchByVesselVslCd": vsl_cd, "vvdCd": vvd},
                    timeout=20,
                )
                r.raise_for_status()
                return (r.json() or {}).get("RTN_DATA") or {}

            v_data = call_vsl(1, f"{vsl_cd}{voy_no}")
            board_list = v_data.get("boardList") or []
            if not board_list:
                v_data = call_vsl(1, "")
                board = v_data.get("boardList") or []
                board_list = [b for b in board if str(b.get("ltdvvd") or "")[4:8] == voy_no]
                if not board_list:
                    min_p = int(v_data.get("minPage") or 1)
                    max_p = int(v_data.get("maxPage") or 1)
                    for p_no in range(min_p, max_p + 1):
                        if p_no == 1:
                            continue
                        p_data = call_vsl(p_no, "")
                        board = p_data.get("boardList") or []
                        board_list = [b for b in board if str(b.get("ltdvvd") or "")[4:8] == voy_no]
                        if board_list:
                            break
            if board_list:
                all_eta_rows.extend(board_list)

    return _build_loop_html(hdr_list, dtl_list, all_eta_rows)


def crawl_hmm_schedule(loop_code="FIM", include_vessel_eta=True,
                       save_dir=HERE / "snapshots", on_progress=None, headless=True):
    """HMM e-Service 에 실제 Chrome(Playwright)으로 접속하여(Akamai TLS/봇 차단 통과):
    1) [방식 A] Long-term Schedule by Loop (FIM) 조회 및 테이블 생성
    2) [방식 B] (include_vessel_eta=True 시) 조회된 모선 순서대로 by Vessel Name API 를 호출해
       선박별 ETA(Arrival) · ETB · ETD 상세 데이터를 수집하여 HTML 내부에 함께 저장한다.
    """
    from playwright.sync_api import sync_playwright

    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    def report(step, total, msg):
        if on_progress is not None:
            on_progress(step, total, msg)

    report(5, 100, "Chrome 브라우저 세션 시작 및 HMM e-Service 접속 중...")
    with sync_playwright() as pw:
        browser = None
        page = None
        for use_new_headless in ((True, False) if headless else (False,)):
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
            if use_new_headless:
                launch_args.append("--headless=new")
            else:
                launch_args.append("--window-position=-2400,-2400")

            for pw_headless in ((False, True) if use_new_headless else (False,)):
                for channel in ("chrome", "msedge", None):
                    try:
                        kwargs = {"headless": pw_headless, "args": launch_args}
                        if channel:
                            kwargs["channel"] = channel
                        browser = pw.chromium.launch(**kwargs)
                        break
                    except Exception:
                        continue
                if browser is not None:
                    break
            if browser is None:
                continue

            try:
                context = browser.new_context(
                    viewport={"width": 1600, "height": 1000},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    locale="ko-KR",
                )
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                page = context.new_page()
                page.goto(
                    "https://www.hmm21.com/e-service/general/schedule/ScheduleMain.do",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => typeof comAjaxPromise === 'function' && typeof fnToday === 'function'",
                    timeout=15000,
                )
                break
            except Exception:
                try:
                    browser.close()
                except Exception:
                    pass
                browser = None
                page = None

        if browser is None or page is None:
            raise RuntimeError("HMM e-Service 페이지에 접속할 수 없습니다 (네트워크 또는 브라우저 확인).")

        try:

            report(15, 100, f"[방식 A] Long-term Schedule by Loop ({loop_code}) 조회 중...")
            loop_res = page.evaluate(
                """async (loopCode) => {
                    const fromDt = fnToday('yyyy-mm-dd').replaceAll('-', '');
                    const toDt = fnCalcMonth(3).replaceAll('-', '');
                    const reqObj = {
                        srchByLoopOptLoop: loopCode,
                        srchLongDateFrom: fromDt,
                        srchLongDateTo: toDt,
                        srchLongBound: ''
                    };
                    const rtn = await comAjaxPromise({
                        url: '/e-service/general/schedule/selectByLoopList.do',
                        type: 'POST',
                        contentType: 'application/json; charset=UTF-8',
                        data: JSON.stringify(reqObj),
                        targetId: 'NONE',
                        callBackFn: 'NONE',
                        loadingScreen: false
                    });
                    const data = (rtn && rtn.RTN_DATA) ? rtn.RTN_DATA : {};
                    const hdrList = data.hdrList || [];
                    const dtlList = data.vskSkdDtls || [];
                    const seen = new Set();
                    const vessels = [];
                    for (const d of dtlList) {
                        const key = `${d.vslCd}-${d.skdVoyNo}`;
                        if (!seen.has(key)) {
                            seen.add(key);
                            vessels.push({ vslCd: d.vslCd, vslNm: d.vslNm, skdVoyNo: d.skdVoyNo });
                        }
                    }
                    return { hdrList, dtlList, vessels };
                }""",
                loop_code,
            )

            hdr_list = loop_res.get("hdrList") or []
            dtl_list = loop_res.get("dtlList") or []
            loop_info = loop_res.get("vessels") or []
            if not hdr_list or not dtl_list:
                raise RuntimeError("HMM 서버에서 Loop 스케줄 데이터를 받지 못했습니다.")

            all_eta_rows = []
            if include_vessel_eta and loop_info:
                total_v = len(loop_info)
                for idx, v in enumerate(loop_info, start=1):
                    vsl_cd = v["vslCd"]
                    vsl_nm = v["vslNm"]
                    voy_no = v["skdVoyNo"]
                    pct = 20 + int(75 * idx / max(total_v, 1))
                    report(pct, 100, f"[방식 B] ({idx}/{total_v}) {vsl_nm} / {voy_no} ({vsl_cd}) 선박별 ETA 조회 중...")

                    board_list = page.evaluate(
                        """async ({ vslCd, voyNo }) => {
                            const callApi = async (pageNo, vvd) => {
                                const r = await comAjaxPromise({
                                    url: '/e-service/general/schedule/selectByVesselPagingList.do',
                                    type: 'POST',
                                    contentType: 'application/json; charset=UTF-8',
                                    data: JSON.stringify({ page: String(pageNo), srchByVesselVslCd: vslCd, vvdCd: vvd }),
                                    targetId: 'NONE',
                                    callBackFn: 'NONE',
                                    loadingScreen: false
                                });
                                return (r && r.RTN_DATA) ? r.RTN_DATA : null;
                            };
                            let data = await callApi(1, vslCd + voyNo);
                            if (data && data.boardList && data.boardList.length > 0) {
                                return data.boardList;
                            }
                            data = await callApi(1, '');
                            if (!data || !data.boardList) return [];
                            let matched = data.boardList.filter(b => (b.ltdvvd || '').substring(4, 8) === voyNo);
                            if (matched.length > 0) return matched;
                            const minP = Number(data.minPage || 1);
                            const maxP = Number(data.maxPage || 1);
                            for (let p = minP; p <= maxP; p++) {
                                if (p === 1) continue;
                                const pData = await callApi(p, '');
                                if (pData && pData.boardList) {
                                    matched = pData.boardList.filter(b => (b.ltdvvd || '').substring(4, 8) === voyNo);
                                    if (matched.length > 0) return matched;
                                }
                            }
                            return [];
                        }""",
                        {"vslCd": vsl_cd, "voyNo": voy_no},
                    )
                    if board_list:
                        all_eta_rows.extend(board_list)

            report(98, 100, "스냅샷 HTML 파일 저장 중...")
            html_content = _build_loop_html(hdr_list, dtl_list, all_eta_rows)
            now = datetime.now()
            base_name = f"{now.year} {now.month:02d}월 {now.day:02d}일"
            out_path = save_dir / f"{base_name}.html"
            if out_path.exists():
                out_path = save_dir / f"{base_name}_{now:%H%M%S}.html"
            out_path.write_text(html_content, encoding="utf-8")
            report(100, 100, f"저장 완료: {out_path.name}")
            return out_path
        finally:
            browser.close()
