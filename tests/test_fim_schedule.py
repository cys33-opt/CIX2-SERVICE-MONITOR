from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

import fim_schedule as fs

SAMPLE = Path(__file__).parent.parent / "snapshots" / "2026 11월 3일.html"


@pytest.fixture(scope="module")
def df():
    return fs.parse_html(SAMPLE)


def test_shape(df):
    assert df["vessel"].nunique() == 18
    assert df.groupby(["vessel", "voyage"]).ngroups == 32
    assert df["seq"].max() == 42


def test_first_call_of_hmm_lime(df):
    r = df[(df.vessel == "HMM LIME") & (df.voyage == "0001") & df.etb.notna()].iloc[0]
    assert r.port == "DALIAN, CHINA" and r.bound == "W"
    assert r.etb == pd.Timestamp("2026-05-01 18:09")
    assert r.etd == pd.Timestamp("2026-05-02 04:28")
    assert r.etb_status == "actual"


def test_skip(df):
    r = df[(df.vessel == "SEASPAN BELLWETHER") & (df.voyage == "0102") & (df.seq == 39)].iloc[0]
    assert r.skip and pd.isna(r.etb)
    assert df["skip"].sum() == 14


def test_vessel_status(df):
    st = fs.vessel_status(df).set_index(["vessel", "voyage"])
    assert st.loc[("HMM FOREST", "0004"), "state"] == "재항 중 (NHAVA SHEVA, INDIA)"
    assert st.loc[("HMM LIME", "0001"), "state"] == "항차 종료"
    assert st.loc[("SEASPAN BELLWETHER", "0102"), "next_port"] == "BUSAN, KOREA"


def test_diff_detects_delay_and_skip(df):
    new = df.copy()
    i = new[(new.vessel == "HMM SAGE") & (new.port == "VALENCIA, SPAIN") & new.etb.notna()].index[0]
    new.loc[i, "etb"] += pd.Timedelta(hours=30)
    j = new[(new.vessel == "HMM OCEAN") & new.etb.notna()].index[-1]
    new.loc[j, ["etb", "etd", "skip"]] = [pd.NaT, pd.NaT, True]

    ch = fs.diff_snapshots(df, new)
    delay = ch[(ch.vessel == "HMM SAGE") & (ch.field == "ETB")]
    assert delay["hours"].tolist() == [30.0]
    assert set(ch[ch.vessel == "HMM OCEAN"]["field"]) == {"SKIP", "ETB", "ETD"}
    assert fs.diff_snapshots(df, df).empty


def test_company_rows_matches_input_form(df):
    rows = fs.company_rows(df)
    oc = rows[(rows["선박명"] == "HMM OCEAN") & (rows["VYG"] == "0006")]
    # 회사 입력창(CIX2 HMM OCEAN 0006) 과 같은 기항지 순서
    assert oc["From"].tolist() == ["KRPUS", "CNSHA", "CNNGB", "CNSHK", "SGSIN", "INNSA", "INMUN", "PKKHI",
                                   "PKKHI", "INMUN", "INNSA", "SGSIN", "KRPUS"]
    khi = oc[(oc["From"] == "PKKHI") & (oc["B"] == "E")].iloc[0]
    assert (khi["Vessel"], khi["Vyg(B)"], khi["WHARF"]) == ("HMOC", "0006E", "KHI04")
    assert (khi["E.T.B"], khi["ETB Time"], khi["ETB Day"]) == ("2026-11-15", "18:00", "Sun")


def test_company_rows_delay_class(df):
    new = df.copy()
    i = new[(new.vessel == "HMM OCEAN") & (new.port == "KARACHI, PAKISTAN") & (new.bound == "E")].index[0]
    new.loc[i, "etd"] += pd.Timedelta(hours=14)
    rows = fs.company_rows(new, fs.diff_snapshots(df, new))
    hit = rows[rows["ΔETD"] != ""]
    assert hit[["From", "ΔETD", "구분", "이전 ETD"]].values.tolist() == [
        ["PKKHI", "+14:00", "12h~", "2026-11-16 15:00"]]
    assert rows["변경"].sum() == 1
    assert fs.delay_class(7) == "06~11h" and fs.delay_class(-2) == "<6h"


def test_company_rows_skip_change(df):
    new = df.copy()
    i = new[(new.vessel == "HMM OCEAN") & (new.port == "MUNDRA, INDIA") & (new.bound == "E")].index[0]
    new.loc[i, ["etb", "etd", "skip"]] = [pd.NaT, pd.NaT, True]
    rows = fs.company_rows(new, fs.diff_snapshots(df, new))
    hit = rows[rows["변경"]]
    assert hit[["From", "E.T.B", "구분", "이전 ETB"]].values.tolist() == [
        ["INMUN", "SKIP", "SKIP", "2026-11-17 12:00"]]


def test_snapshot_date():
    assert fs.snapshot_date(SAMPLE) == datetime(2026, 11, 3)


def test_company_rows_preserves_html_vessel_order(df):
    rows = fs.company_rows(df)
    html_order = list(dict.fromkeys(zip(df["vessel"], df["voyage"])))
    row_order = list(dict.fromkeys(zip(rows["선박명"], rows["VYG"])))
    assert row_order == [vv for vv in html_order if vv in set(row_order)]


def test_eta_parsing_and_summary_table():
    p10 = SAMPLE.parent / "11월 10일.html"
    if not p10.exists():
        return
    d10 = fs.parse_html(p10)
    # 11월 10일.html 의 #byVesselNameTable 에 들어있는 HMM LEAF 0004 ETA 파싱 확인
    leaf = d10[(d10["vessel"] == "HMM LEAF") & (d10["voyage"] == "0004") & (d10["port"] == "BUSAN, KOREA") & d10["etb"].notna()].iloc[0]
    assert leaf["eta"] == pd.Timestamp("2026-11-08 14:03")
    assert leaf["eta_status"] == "actual"

    # ETA 변경 감지 및 요약 표 확인
    d_mod = d10.copy()
    idx = d_mod[(d_mod["vessel"] == "HMM LEAF") & (d_mod["voyage"] == "0004") & (d_mod["port"] == "SHANGHAI,CHINA") & d_mod["etb"].notna()].index[0]
    d_mod.loc[idx, "eta"] += pd.Timedelta(hours=15)
    ch = fs.diff_snapshots(d10, d_mod)
    eta_chg = ch[(ch["vessel"] == "HMM LEAF") & (ch["field"] == "ETA")]
    assert eta_chg["hours"].tolist() == [15.0]

    tbl = fs.vessel_eta_summary_table(d_mod, ch, only_changed=True, hide_actual=True)
    # 웹페이지 첫 모선(HMM LEAF 0003)은 변경사항 없음으로 표시
    assert tbl.iloc[0]["선박명"] == "HMM LEAF" and tbl.iloc[0]["항차"] == "0003"
    assert tbl.iloc[0]["구분"] == "변경사항 없음"
    # 변경된 HMM LEAF 0004 SHANGHAI 기항지의 ΔETA 표시 확인
    hit = tbl[(tbl["선박명"] == "HMM LEAF") & (tbl["항차"] == "0004")]
    assert hit.iloc[0]["ΔETA"] == "+15:00" and hit.iloc[0]["구분"] == "12h~"


def test_build_loop_html_and_parse(tmp_path):
    hdr = [
        {"vslCd": "HOLF", "portNm": "BUSAN, KOREA", "skdDirCd": "W", "tmnlCd": "KRPUSPN", "tmnlNm": "HMM PSA New-port Terminal"},
        {"vslCd": "HOLF", "portNm": "SHANGHAI,CHINA", "skdDirCd": "W", "tmnlCd": "CNSHAY4", "tmnlNm": "YANGSHAN PHASE 4 (Shangdong)"},
    ]
    dtl = [
        {"vslCd": "HOLF", "vslNm": "HMM LEAF", "skdVoyNo": "0004", "skdDirCd": "W", "tmnlCd": "KRPUSPN",
         "skdCngStusCd": "", "skdKindCdEtb": "A", "skdKindCdEtd": "A", "etbDt": "20261108160300", "etdDt": "20261110051400"},
        {"vslCd": "HOLF", "vslNm": "HMM LEAF", "skdVoyNo": "0004", "skdDirCd": "W", "tmnlCd": "CNSHAY4",
         "skdCngStusCd": "", "skdKindCdEtb": "C", "skdKindCdEtd": "C", "etbDt": "20261113000000", "etdDt": "20261113173000"},
    ]
    eta_rows = [
        {"ltdvds": "HMM LEAF", "ltdvvd": "HOLF0004W", "portNm": "BUSAN, KOREA", "tmnlNm": "HMM PSA New-port Terminal",
         "arrDt": "2026-11-08 14:03", "arrStNm": "actual"},
        {"ltdvds": "HMM LEAF", "ltdvvd": "HOLF0004W", "portNm": "SHANGHAI,CHINA", "tmnlNm": "YANGSHAN PHASE 4 (Shangdong)",
         "arrDt": "2026-11-11 05:00", "arrStNm": "coastal"},
    ]
    html_str = fs._build_loop_html(hdr, dtl, eta_rows)
    out_file = tmp_path / "crawled.html"
    out_file.write_text(html_str, encoding="utf-8")
    parsed = fs.parse_html(out_file)
    assert len(parsed) == 2
    assert parsed.iloc[0]["eta"] == pd.Timestamp("2026-11-08 14:03")
    assert parsed.iloc[1]["eta"] == pd.Timestamp("2026-11-11 05:00")
    assert parsed.iloc[1]["eta_status"] == "coastal"



