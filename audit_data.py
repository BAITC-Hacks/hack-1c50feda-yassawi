"""Read-only, reproducible audit of supplied tables."""
import csv
import json
from pathlib import Path
import numpy as np
import pandas as pd


def audit():
    report = {}
    paths = [Path(x) for x in ("customer_profile.csv", "tariff_dictionary.csv", "feature_dictionary.csv")]
    paths += sorted(Path("data").glob("*.csv"))
    known = set(pd.read_csv("data/dict_tariff.csv").tariff_plan_code)
    for path in paths:
        raw = path.read_bytes()
        encoding = "utf-8-sig"
        text = raw.decode(encoding, errors="strict")
        separator = csv.Sniffer().sniff(text[:8192], delimiters=",;\t").delimiter
        df = pd.read_csv(path, sep=separator, encoding=encoding)
        entry = {"rows": len(df), "columns": list(df.columns), "encoding": encoding,
                 "separator": separator, "dtypes": df.dtypes.astype(str).to_dict(),
                 "missing": df.isna().sum().astype(int).to_dict(), "duplicate_rows": int(df.duplicated().sum()),
                 "arpu": {}, "unknown_tariffs": {}}
        if "ID_NUMBER" in df:
            entry["duplicate_ids"] = int(df.ID_NUMBER.duplicated().sum())
        for col in df:
            if "ARPU" in col.upper() and "SEGMENT" not in col.upper() and "TREND" not in col.upper():
                num = pd.to_numeric(df[col], errors="coerce")
                entry["arpu"][col] = {"nonnumeric": int((num.isna() & df[col].notna()).sum()),
                                      "zero": int((num == 0).sum()), "negative": int((num < 0).sum()),
                                      "nonfinite": int((~np.isfinite(num)).sum())}
            if col in ("current_tariff", "tariff_plan_code", "tariff_plan_code_from", "tariff_plan_code_to"):
                entry["unknown_tariffs"][col] = sorted(set(df[col].dropna()) - known)
        report[str(path)] = entry
    Path("diagnostics").mkdir(exist_ok=True)
    Path("diagnostics/data_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: {k: row[k] for k in ("rows", "duplicate_rows", "arpu", "unknown_tariffs")} for name, row in report.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    audit()
